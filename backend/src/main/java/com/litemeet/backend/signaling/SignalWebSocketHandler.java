package com.litemeet.backend.signaling;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.litemeet.backend.store.EndedRoomCache;
import com.litemeet.backend.store.MeetingStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import java.io.IOException;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.regex.Pattern;

/**
 * 业务信令服务器与房间管理
 * 架构：SFU（LiveKit 媒体服务器转发媒体流）。本服务器只承载业务信令
 *      （聊天/角色/踢人/锁定/解散等）与房间状态；WebRTC 媒体协商与转发由 LiveKit 完成。
 *
 * 角色模型（三级）：
 *   创建者 owner：房间首个加入者。拥有全部权限（解散会议/踢任何人/任命主持人/全员静音/锁定）。
 *                 创建者离开 => 会议直接解散。
 *   主持人 host：可由创建者/主持人任命。拥有除"解散会议"外的管理权限，但不能管理创建者与其他主持人。
 *   普通成员：无管理权限。
 *
 * 消息协议（客户端 -> 服务端）：
 *   join { roomId, name, audio, video }            加入会议
 *   leave                                            离开会议
 *   state { audio, video, screen }                   自身状态广播
 *   rename { name }                                  改名广播
 *   chat { text, targetId? }                         聊天（带 targetId 为私聊）
 *   set-host { targetId }                            任命主持人（创建者/主持人，对象为普通成员）
 *   kick { targetId }                                移出会议（创建者任意；主持人仅普通成员）
 *   mute-remote { targetId }                         远程静音（创建者任意；主持人仅普通成员）
 *   mute-all                                         一键全员静音（创建者/主持人）
 *   set-mute-lock { lock }                           禁止成员自行解除静音（创建者/主持人）
 *   set-room-lock { lock }                           锁定会议：禁止新成员加入（创建者/主持人）
 *   disband                                          解散会议（仅创建者）
 *
 * 消息协议（服务端 -> 客户端）：
 *   joined { selfId, roomId, ownerId, hostId, locked, muteLocked, peers }
 *   peer-joined { peer } / peer-left { peerId }
 *   state { peerId, audio, video, screen }
 *   rename { peerId, name }
 *   chat { fromId, name, text, ts, targetId? }
 *   host-changed { hostId }                          主持人变更
 *   room-locked { locked } / mute-locked { locked }  锁定状态变更
 *   force-mute { fromId, name }                      你被（远程/全员）静音
 *   kicked { message }                               你被移出会议
 *   meeting-ended { message }                        会议已结束（创建者离开或解散）
 *   error { message }
 */
@Component
public class SignalWebSocketHandler extends TextWebSocketHandler {

    private static final Logger log = LoggerFactory.getLogger(SignalWebSocketHandler.class);
    private static final Pattern ROOM_ID_PATTERN = Pattern.compile("^[A-Z0-9-]{4,16}$");
    /** SFU 架构可支撑更多参会者，与 livekit.yaml max_participants 保持一致 */
    private static final int MAX_PEERS_PER_ROOM = 50;
    private static final SecureRandom RANDOM = new SecureRandom();

    private final ObjectMapper mapper = new ObjectMapper();

    /** 已结束会议号缓存（Redis，TTL 自动过期释放会议号）+ 生命周期持久化（MySQL） */
    private final EndedRoomCache endedRoomCache;
    private final MeetingStore meetingStore;

    /** 连接会话 -> 参会者信息（断连清理用） */
    private final Map<WebSocketSession, Peer> sessionMap = new ConcurrentHashMap<>();
    /** roomId -> Room */
    private final Map<String, Room> rooms = new ConcurrentHashMap<>();
    /** 会议时长到期自动结束用的定时器 */
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor();

    public SignalWebSocketHandler(EndedRoomCache endedRoomCache, MeetingStore meetingStore) {
        this.endedRoomCache = endedRoomCache;
        this.meetingStore = meetingStore;
        // 服务器重启后：从 MySQL 恢复未过期的已结束会议号到 Redis
        this.endedRoomCache.restore(meetingStore.loadRecentlyEndedRooms());
    }

    private static class Peer {
        String id;
        String roomId;
        String name;
        boolean audio;
        boolean video;
        boolean screen;
        WebSocketSession session;

        ObjectNode brief(ObjectMapper mapper) {
            ObjectNode n = mapper.createObjectNode();
            n.put("id", id);
            n.put("name", name);
            n.put("audio", audio);
            n.put("video", video);
            n.put("screen", screen);
            return n;
        }
    }

    /** 房间：参会者 + 创建者/主持人角色 + 锁定状态 */
    private static class Room {
        final LinkedHashMap<String, Peer> peers = new LinkedHashMap<>();
        volatile String ownerId;      // 创建者（首个加入者，唯一且不转移；离开即解散）
        volatile String hostId;       // 主持人（可转让；创建者离开由它接管，否则交回创建者）
        volatile boolean locked;      // 锁定会议：禁止新成员加入
        volatile boolean muteLocked;  // 禁止成员自行解除静音
        volatile Long createdAt;      // 会议创建时间戳（首个加入者入会时间），用于统一会议计时

        // 会议配置（创建时设置，仅首次创建生效）
        String title;                 // 会议名称
        int maxPeers = MAX_PEERS_PER_ROOM;   // 允许的最大人数（默认 50）
        int durationMinutes = 0;      // 会议时长（分钟，0 = 不限时长）
        String onExpire = "none";     // 时长到期处理：auto=自动结束 / remind=仅提醒 / none=不限
        long deadline = 0;            // 时长截止时间戳（毫秒），0 表示不限
        ScheduledFuture<?> endTask;   // 到期自动结束的定时任务
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        Peer peer = sessionMap.remove(session);
        if (peer != null) {
            removePeer(peer.roomId, peer.id);
        }
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) throws Exception {
        JsonNode msg;
        try {
            msg = mapper.readTree(message.getPayload());
        } catch (Exception e) {
            return;
        }
        if (msg == null || !msg.hasNonNull("type")) return;

        String type = msg.get("type").asText();
        switch (type) {
            case "join" -> handleJoin(session, msg);
            case "leave" -> {
                Peer peer = sessionMap.remove(session);
                if (peer != null) removePeer(peer.roomId, peer.id);
            }
            case "state" -> handleState(session, msg);
            case "rename" -> handleRename(session, msg);
            case "chat" -> handleChat(session, msg);
            case "set-host" -> handleSetHost(session, msg);
            case "kick" -> handleKick(session, msg);
            case "mute-remote" -> handleMuteRemote(session, msg);
            case "mute-all" -> handleMuteAll(session);
            case "set-mute-lock" -> handleSetMuteLock(session, msg);
            case "set-room-lock" -> handleSetRoomLock(session, msg);
            case "disband" -> handleDisband(session);
            default -> { /* 忽略未知消息 */ }
        }
    }

    // ---------- 加入 / 离开 ----------

    private void handleJoin(WebSocketSession session, JsonNode msg) {
        String roomId = trimUpper(msg.path("roomId").asText(""));
        String name = msg.path("name").asText("参会者").trim();
        if (name.isEmpty()) name = "参会者";
        if (name.length() > 32) name = name.substring(0, 32);

        if (!ROOM_ID_PATTERN.matcher(roomId).matches()) {
            send(session, errorNode("会议号格式不正确（4-16位字母/数字/短横线）"));
            return;
        }

        // 已解散/已结束的会议号（TTL 内禁止重新创建，过期自动释放）
        if (endedRoomCache.isEnded(roomId)) {
            send(session, errorNode("该会议已结束，无法加入，请创建新会议"));
            return;
        }

        Room room = rooms.get(roomId);
        if (room != null && room.peers.size() >= room.maxPeers) {
            send(session, errorNode(room.maxPeers >= MAX_PEERS_PER_ROOM ? "该会议已满（最多 50 人）" : "该会议人数已满（最多 " + room.maxPeers + " 人）"));
            return;
        }
        if (room != null && room.locked) {
            send(session, errorNode("会议已锁定，暂不能加入"));
            return;
        }
        if (room == null) {
            room = new Room();
            room.createdAt = System.currentTimeMillis();
            // 仅首次创建时应用创建者带来的会议配置；后续加入者忽略 cfg，避免覆盖
            applyCreateConfig(room, msg);
            rooms.put(roomId, room);
            // 会议号被重新创建为新会议：清除旧 ended 记录，避免重启后误拉黑
            endedRoomCache.clearEnded(roomId);
            meetingStore.clearEnded(roomId);
            // 只要设了时长就计算截止时间（前端据此倒计时/提醒）；仅 auto 策略安排自动结束
            if (room.durationMinutes > 0) {
                room.deadline = room.createdAt + room.durationMinutes * 60_000L;
                if ("auto".equals(room.onExpire)) {
                    room.endTask = scheduler.schedule(() -> forceEndRoom(roomId, "已达会议时长，会议已结束"),
                            room.durationMinutes, TimeUnit.MINUTES);
                }
            }
        }

        Peer peer = new Peer();
        peer.id = genId();
        peer.roomId = roomId;
        peer.name = name;
        peer.audio = msg.path("audio").asBoolean(false);
        peer.video = msg.path("video").asBoolean(false);
        peer.screen = false;
        peer.session = session;
        room.peers.put(peer.id, peer);
        sessionMap.put(session, peer);

        // 空房间（仅当房间首次创建时）首个加入者 = 创建者 + 主持人。
        // 创建者身份唯一、不随连接变化转移：即使原创建者暂不在 peers 中也绝不重新授权，
        // 避免"夺舍"（后端重启导致内存房间丢失后，重入的用户会变成新创建者并可能解散他人会议）。
        if (room.ownerId == null) {
            room.ownerId = peer.id;
            room.hostId = peer.id;
        }

        // 应答：自己 + 已在会成员 + 角色/锁定状态
        ObjectNode joined = mapper.createObjectNode();
        joined.put("type", "joined");
        joined.put("selfId", peer.id);
        joined.put("roomId", roomId);
        joined.put("ownerId", room.ownerId);
        joined.put("hostId", room.hostId);
        joined.put("locked", room.locked);
        joined.put("muteLocked", room.muteLocked);
        joined.put("createdAt", room.createdAt);
        joined.put("title", room.title == null ? "" : room.title);
        joined.put("maxPeers", room.maxPeers);
        joined.put("durationMinutes", room.durationMinutes);
        joined.put("onExpire", room.onExpire);
        joined.put("deadline", room.deadline);
        ArrayNode peers = joined.putArray("peers");
        for (Peer p : room.peers.values()) {
            if (p != peer) peers.add(p.brief(mapper));
        }
        send(session, joined);

        // 通知房间其他人
        ObjectNode peerJoined = mapper.createObjectNode();
        peerJoined.put("type", "peer-joined");
        peerJoined.set("peer", peer.brief(mapper));
        broadcastToRoom(roomId, peerJoined, peer.id);
    }

    // ---------- 状态 / 聊天 ----------

    private void handleState(WebSocketSession session, JsonNode msg) {
        Peer peer = sessionMap.get(session);
        if (peer == null) return;
        if (msg.hasNonNull("audio")) peer.audio = msg.get("audio").asBoolean();
        if (msg.hasNonNull("video")) peer.video = msg.get("video").asBoolean();
        if (msg.hasNonNull("screen")) peer.screen = msg.get("screen").asBoolean();

        ObjectNode state = mapper.createObjectNode();
        state.put("type", "state");
        state.put("peerId", peer.id);
        state.put("audio", peer.audio);
        state.put("video", peer.video);
        state.put("screen", peer.screen);
        broadcastToRoom(peer.roomId, state, null);
    }

    private void handleRename(WebSocketSession session, JsonNode msg) {
        Peer peer = sessionMap.get(session);
        if (peer == null) return;
        String name = msg.path("name").asText("");
        if (!name.isEmpty()) {
            peer.name = name.length() > 32 ? name.substring(0, 32) : name;
        }
        ObjectNode rename = mapper.createObjectNode();
        rename.put("type", "rename");
        rename.put("peerId", peer.id);
        rename.put("name", peer.name);
        broadcastToRoom(peer.roomId, rename, null);
    }

    private void handleChat(WebSocketSession session, JsonNode msg) {
        Peer peer = sessionMap.get(session);
        if (peer == null) return;
        String text = msg.path("text").asText("");
        if (text.length() > 2000) text = text.substring(0, 2000);
        if (text.trim().isEmpty()) return;

        String targetId = msg.hasNonNull("targetId") ? msg.get("targetId").asText(null) : null;

        ObjectNode chat = mapper.createObjectNode();
        chat.put("type", "chat");
        chat.put("fromId", peer.id);
        chat.put("name", peer.name);
        chat.put("text", text);
        chat.put("ts", System.currentTimeMillis());

        if (targetId != null && !targetId.isEmpty()) {
            Peer target = findPeer(peer.roomId, targetId);
            if (target == null) return;
            chat.put("targetId", targetId);
            send(target.session, chat);
            send(session, chat);
        } else {
            // 公共聊天：发给除发送者外的所有人，再由 send(session) 回显一次（避免发送者收到两条）
            broadcastToRoom(peer.roomId, chat, peer.id);
            send(session, chat);
        }
    }

    // ---------- 权限管理 ----------

    private boolean canManage(Room room, Peer operator) {
        return operator != null && (operator.id.equals(room.ownerId) || operator.id.equals(room.hostId));
    }

    /** [创建者/主持人] 任命主持人：对象为普通成员（非创建者、非现任主持人） */
    private void handleSetHost(WebSocketSession session, JsonNode msg) {
        Peer self = sessionMap.get(session);
        if (self == null) return;
        Room room = rooms.get(self.roomId);
        if (room == null || !canManage(room, self)) {
            send(session, errorNode("只有主持人或创建者可以执行此操作"));
            return;
        }
        String targetId = msg.path("targetId").asText(null);
        Peer target = findPeer(self.roomId, targetId);
        if (target == null || target.id.equals(room.ownerId) || target.id.equals(room.hostId)) return;

        room.hostId = targetId;
        broadcastHostChanged(self.roomId, targetId);
    }

    /** [创建者/主持人] 移出会议：创建者任意；主持人仅限普通成员 */
    private void handleKick(WebSocketSession session, JsonNode msg) {
        Peer self = sessionMap.get(session);
        if (self == null) return;
        Room room = rooms.get(self.roomId);
        if (room == null || !canManage(room, self)) {
            send(session, errorNode("只有主持人或创建者可以执行此操作"));
            return;
        }
        String targetId = msg.path("targetId").asText(null);
        Peer target = findPeer(self.roomId, targetId);
        if (target == null || target == self) return;
        // 主持人不能移出创建者与其他主持人；创建者可以移出任何人
        if (!self.id.equals(room.ownerId) &&
                (target.id.equals(room.ownerId) || target.id.equals(room.hostId))) {
            send(session, errorNode("无法移出创建者或其他主持人"));
            return;
        }

        ObjectNode kicked = mapper.createObjectNode();
        kicked.put("type", "kicked");
        kicked.put("message", "你已被移出会议");
        kicked.put("byName", self.name);
        send(target.session, kicked);

        sessionMap.remove(target.session);
        removePeer(self.roomId, targetId);
    }

    /** [创建者/主持人] 远程静音：创建者任意；主持人仅限普通成员 */
    private void handleMuteRemote(WebSocketSession session, JsonNode msg) {
        Peer self = sessionMap.get(session);
        if (self == null) return;
        Room room = rooms.get(self.roomId);
        if (room == null || !canManage(room, self)) {
            send(session, errorNode("只有主持人或创建者可以执行此操作"));
            return;
        }
        Peer target = findPeer(self.roomId, msg.path("targetId").asText(null));
        if (target == null || target == self) return;
        if (!self.id.equals(room.ownerId) &&
                (target.id.equals(room.ownerId) || target.id.equals(room.hostId))) {
            send(session, errorNode("无法静音创建者或其他主持人"));
            return;
        }
        sendForceMute(target, self.name);
    }

    /** [创建者/主持人] 一键全员静音：静音除操作者外的所有成员 */
    private void handleMuteAll(WebSocketSession session) {
        Peer self = sessionMap.get(session);
        if (self == null) return;
        Room room = rooms.get(self.roomId);
        if (room == null || !canManage(room, self)) {
            send(session, errorNode("只有主持人或创建者可以执行此操作"));
            return;
        }
        List<Peer> targets = new ArrayList<>();
        synchronized (room) {
            for (Peer p : room.peers.values()) {
                if (!p.id.equals(self.id)) targets.add(p);
            }
        }
        for (Peer t : targets) {
            sendForceMute(t, self.name);
        }
    }

    /** [创建者/主持人] 禁止成员自行解除静音（锁定状态广播全房间） */
    private void handleSetMuteLock(WebSocketSession session, JsonNode msg) {
        Peer self = sessionMap.get(session);
        if (self == null) return;
        Room room = rooms.get(self.roomId);
        if (room == null || !canManage(room, self)) {
            send(session, errorNode("只有主持人或创建者可以执行此操作"));
            return;
        }
        room.muteLocked = msg.path("lock").asBoolean(false);

        ObjectNode lock = mapper.createObjectNode();
        lock.put("type", "mute-locked");
        lock.put("locked", room.muteLocked);
        broadcastToRoom(self.roomId, lock, null);
    }

    /** [创建者/主持人] 锁定会议：禁止新成员加入 */
    private void handleSetRoomLock(WebSocketSession session, JsonNode msg) {
        Peer self = sessionMap.get(session);
        if (self == null) return;
        Room room = rooms.get(self.roomId);
        if (room == null || !canManage(room, self)) {
            send(session, errorNode("只有主持人或创建者可以执行此操作"));
            return;
        }
        room.locked = msg.path("lock").asBoolean(false);

        ObjectNode lock = mapper.createObjectNode();
        lock.put("type", "room-locked");
        lock.put("locked", room.locked);
        broadcastToRoom(self.roomId, lock, null);
    }

    /** [仅创建者] 解散会议：所有人结束 + 房间销毁 */
    private void handleDisband(WebSocketSession session) {
        Peer self = sessionMap.get(session);
        if (self == null) return;
        Room room = rooms.get(self.roomId);
        if (room == null || !self.id.equals(room.ownerId)) {
            send(session, errorNode("只有创建者可以解散会议"));
            return;
        }
        List<Peer> all = new ArrayList<>(room.peers.values());
        cancelEndTask(room);
        rooms.remove(self.roomId);
        markRoomEnded(self.roomId, self.name, "disband");
        for (Peer p : all) {
            sessionMap.remove(p.session);
            ObjectNode ended = mapper.createObjectNode();
            ended.put("type", "meeting-ended");
            ended.put("message", "会议已被创建者解散");
            send(p.session, ended);
        }
    }

    // ---------- 房间工具 ----------

    /** 记录已结束会议号：Redis 带 TTL 立即生效（到期自动释放）+ MySQL 持久化（重启恢复） */
    private void markRoomEnded(String roomId, String endedBy, String reason) {
        if (roomId == null || roomId.isEmpty()) return;
        endedRoomCache.markEnded(roomId);
        meetingStore.markRoomEnded(roomId, endedBy, reason);
    }

    /** 仅首次创建时，把创建者带来的会议配置落到 Room 上；非法值回退默认 */
    private void applyCreateConfig(Room room, JsonNode msg) {
        JsonNode cfg = msg.get("cfg");
        if (cfg == null || !cfg.isObject()) return;
        String title = cfg.path("title").asText("").trim();
        if (!title.isEmpty()) room.title = title.length() > 64 ? title.substring(0, 64) : title;
        int maxPeers = cfg.path("maxPeers").asInt(0);
        if (maxPeers >= 1) room.maxPeers = Math.min(maxPeers, MAX_PEERS_PER_ROOM);
        int dur = cfg.path("durationMinutes").asInt(0);
        if (dur >= 1) room.durationMinutes = Math.min(dur, 24 * 60); // 上限 24 小时
        String onExpire = cfg.path("onExpire").asText("remind");
        if ("auto".equals(onExpire) || "remind".equals(onExpire)) room.onExpire = onExpire;
    }

    /** 取消会议时长到期自动结束的定时任务 */
    private void cancelEndTask(Room room) {
        if (room != null && room.endTask != null) {
            room.endTask.cancel(false);
            room.endTask = null;
        }
    }

    /** 会议时长到期：解散会议并通知所有成员 */
    private void forceEndRoom(String roomId, String message) {
        Room room = rooms.remove(roomId);
        if (room == null) return;
        cancelEndTask(room);
        markRoomEnded(roomId, "计时到期", "duration_expired");
        for (Peer p : new ArrayList<>(room.peers.values())) {
            sessionMap.remove(p.session);
            ObjectNode ended = mapper.createObjectNode();
            ended.put("type", "meeting-ended");
            ended.put("message", message);
            send(p.session, ended);
        }
    }

    private void removePeer(String roomId, String peerId) {
        Room room = rooms.get(roomId);
        if (room == null) return;
        Peer removed;
        synchronized (room) {
            removed = room.peers.remove(peerId);
            if (removed == null) return;
        }

        // 创建者离开 => 会议直接解散
        if (peerId.equals(room.ownerId)) {
            List<Peer> rest = new ArrayList<>(room.peers.values());
            cancelEndTask(room);
            rooms.remove(roomId);
            markRoomEnded(roomId, removed.name, "owner_left");
            for (Peer p : rest) {
                sessionMap.remove(p.session);
                ObjectNode ended = mapper.createObjectNode();
                ended.put("type", "meeting-ended");
                ended.put("message", "创建者已离开，会议已结束");
                send(p.session, ended);
            }
            return;
        }

        ObjectNode left = mapper.createObjectNode();
        left.put("type", "peer-left");
        left.put("peerId", peerId);
        broadcastToRoom(roomId, left, null);

        // 主持人（非创建者）离开：主持人交回创建者，创建者不在则移交给最早加入的成员
        if (peerId.equals(room.hostId) && !room.peers.isEmpty()) {
            String newHost;
            if (room.ownerId != null && room.peers.containsKey(room.ownerId)) {
                newHost = room.ownerId;
            } else {
                newHost = room.peers.keySet().iterator().next();
            }
            room.hostId = newHost;
            broadcastHostChanged(roomId, newHost);
        }
    }

    private void sendForceMute(Peer target, String byName) {
        ObjectNode fm = mapper.createObjectNode();
        fm.put("type", "force-mute");
        fm.put("fromId", "");
        fm.put("name", byName == null ? "" : byName);
        send(target.session, fm);
    }

    private void broadcastHostChanged(String roomId, String hostId) {
        ObjectNode hc = mapper.createObjectNode();
        hc.put("type", "host-changed");
        hc.put("hostId", hostId);
        broadcastToRoom(roomId, hc, null);
    }

    private Peer findPeer(String roomId, String peerId) {
        if (peerId == null) return null;
        Room room = rooms.get(roomId);
        return room == null ? null : room.peers.get(peerId);
    }

    private void broadcastToRoom(String roomId, ObjectNode msg, String excludePeerId) {
        Room room = rooms.get(roomId);
        if (room == null) return;
        List<WebSocketSession> targets = new ArrayList<>();
        synchronized (room) {
            for (Map.Entry<String, Peer> e : room.peers.entrySet()) {
                if (!e.getKey().equals(excludePeerId)) targets.add(e.getValue().session);
            }
        }
        for (WebSocketSession s : targets) send(s, msg);
    }

    private void send(WebSocketSession session, ObjectNode msg) {
        if (session == null || !session.isOpen()) return;
        try {
            synchronized (session) {
                session.sendMessage(new TextMessage(mapper.writeValueAsString(msg)));
            }
        } catch (IOException e) {
            log.debug("发送消息失败: {}", e.getMessage());
        }
    }

    private ObjectNode errorNode(String message) {
        ObjectNode n = mapper.createObjectNode();
        n.put("type", "error");
        n.put("message", message);
        return n;
    }

    private String genId() {
        byte[] bytes = new byte[6];
        RANDOM.nextBytes(bytes);
        StringBuilder sb = new StringBuilder("p_");
        for (byte b : bytes) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    private static String trimUpper(String s) {
        return s == null ? "" : s.trim().toUpperCase();
    }
}
