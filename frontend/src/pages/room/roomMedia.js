/* 轻会议 LiteMeet - 会议房间核心逻辑（LiveKit SFU 转发 + 录制 + 实时转写）
 * 由原生 IIFE 改造为 ESM 模块：Room.vue 挂载后调用 initRoom()。
 * 依赖的通用工具改为显式导入；livekit-client 仍为全局对象 window.LivekitClient（room.html 以 UMD 引入）。
 */
import { API_BASE, API, backendWsUrl, getParam, getUserName, setUserName, getUserId, toast, fmtTime, fmtDuration, escapeHtml } from '@/utils/common';

export function initRoom() {
  'use strict';

  // ---------- URL 参数 ----------
  const roomId = (getParam('room') || '').toUpperCase();
  // 昵称只取本机记忆（主页输入），不信任 URL 的 name 参数（防止任何人冒用任意昵称直接进会）
  let myName = getUserName().trim();
  if (!myName) {
    // 未输入过昵称（如直接通过 URL 访问）：跳回主页输入昵称
    location.href = '/?room=' + encodeURIComponent(roomId);
    return;
  }
  // 默认关闭摄像头，仅当 URL video=1 时默认开启
  const initialVideo = getParam('video') === '1';
  if (!roomId) {
    location.href = '/';
    return;
  }

  document.getElementById('roomTitle').textContent = '会议 ' + roomId;
  document.getElementById('roomInfo').textContent = '你：' + myName;

  // 创建会议时的配置（仅创建一个新会议时携带；加入他人会议时 URL 无这些参数）
  const createCfg = buildCreateCfg();
  function buildCreateCfg() {
    const t = getParam('t');
    const mx = parseInt(getParam('mx') || '', 10);
    const du = parseInt(getParam('du') || '', 10);
    const ex = getParam('ex');
    if (!t && !(mx > 0) && !(du > 0)) return null;
    return {
      title: t || '会议 ' + roomId,
      maxPeers: mx > 0 && mx <= 50 ? mx : 8,
      durationMinutes: du > 0 && du <= 1440 ? du : 60,
      onExpire: (ex === 'auto' || ex === 'remind') ? ex : 'remind'
    };
  }

  // ---------- 状态 ----------
  const peers = new Map();          // peerId -> { participant, name, audio, video, screen, stream, screenStream, tile }
  let selfId = null;
  let ownerId = null;               // 创建者（首个加入者；离开即解散会议）
  let isOwner = false;              // 自己是否创建者
  let hostId = null;                // 当前主持人 peerId（可转让；离开后移交）
  let isHost = false;               // 自己是否主持人
  let roomLocked = false;           // 会议是否锁定（禁止新成员加入）
  let muteLocked = false;           // 是否禁止成员自行解除静音
  let localStream = null;           // 麦克风 + 摄像头（本地预览 / 录制混音 / 设备管理）
  let camTrack = null;              // 当前摄像头轨道（供共享恢复）
  let screenStream = null;
  let screenAudioTrack = null;      // 共享屏幕时采集到的系统声音轨道
  let screenAudioOn = false;        // 屏幕声音是否正在发送（可单独关闭，不影响麦克风）
  let speakerMuted = false;         // 关闭扬声器：本地听不到对方，但对方仍能听到自己
  let speakerDeviceId = null;       // 扬声器输出设备 id（setSinkId）
  let micOn = false, camOn = initialVideo, sharing = false; // 默认静音 + 默认关摄像头
  let ws = null;
  let wsReady = false;
  // 会议配置（服务端下发）：最大人数 / 时长截止 / 到期策略
  let meetingMaxPeers = 0;
  let meetingDeadline = 0;
  let meetingOnExpire = 'remind';
  let remindShown = false;
  let metaTimer = null;

  // ---------- LiveKit SFU ----------
  const LK = window.LivekitClient || null;  // livekit-client SDK 全局对象
  const LK_SRC = { mic: 'microphone', cam: 'camera', screen: 'screen_share', screenAudio: 'screen_share_audio' };
  // 手机端判定：iOS Safari 无法通过 WebRTC 采集手机屏幕，安卓能采集但实测不稳定，故手机端统一禁用共享屏幕
  const IS_MOBILE = (
    /Android|iPhone|iPad|iPod|Windows Phone/i.test(navigator.userAgent) ||
    (navigator.maxTouchPoints > 0 && window.matchMedia('(max-width: 768px)').matches)
  );
  // 单层高质量编码：局域网会议优先清晰度与低延迟（不启用 simulcast，避免服务端转码与切层卡顿）
  const CAM_ENC = { maxBitrate: 2_500_000, maxFramerate: 30 };   // 摄像头 ~720p / 30fps
  const SCREEN_ENC = { maxBitrate: 4_000_000, maxFramerate: 15 }; // 屏幕 ~1080p / 15fps
  let lkRoom = null;                // LiveKit Room
  let lkLocal = null;               // room.localParticipant
  let lkConnected = false;          // 媒体层是否已连上 SFU

  // 录制状态
  let rec = null;                   // { id, ctx, workletNode, mediaRecorder, chunks, startTime, uploading }
  let meetingStart = Date.now();

  // ---------- DOM ----------
  const videoGrid = document.getElementById('videoGrid');
  const chatPanel = document.getElementById('panelChat');
  const membersPanel = document.getElementById('panelMembers');
  const subtitlePanel = document.getElementById('panelSubtitle');
  const chatInputRow = document.getElementById('chatInputRow');
  const liveSubtitle = document.getElementById('liveSubtitle');

  // 同步工具栏切换按钮的无障碍状态（屏幕阅读器可感知开关与菜单展开）
  const btnMic = document.getElementById('btnMic');
  const btnSpeaker = document.getElementById('btnSpeaker');
  const btnCam = document.getElementById('btnCam');
  const btnScreen = document.getElementById('btnScreen');
  const btnScreenAudio = document.getElementById('btnScreenAudio');
  // 手机端不支持共享屏幕：按钮置灰禁用，提示用户改为电脑端操作
  if (IS_MOBILE) {
    btnScreen.classList.add('disabled');
    btnScreen.title = '手机端不支持共享屏幕';
  }
  const btnRec = document.getElementById('btnRec');
  const btnPanel = document.getElementById('btnPanel');
  const btnShare = document.getElementById('btnShare');
  const btnFullscreen = document.getElementById('btnFullscreen');
  const btnLeave = document.getElementById('btnLeave');
  function setPressed(btn, on) { if (btn) btn.setAttribute('aria-pressed', on ? 'true' : 'false'); }
  function setExpanded(btn, on) { if (btn) btn.setAttribute('aria-expanded', on ? 'true' : 'false'); }

  // 弹窗无障碍：role=dialog + aria-modal + ESC 关闭 + 焦点移入/还原
  function setupDialog(mask, opts = {}) {
    const card = mask.querySelector('.room-modal, .share-card');
    const prevFocus = document.activeElement;
    const doClose = () => {
      mask.remove();
      if (prevFocus && typeof prevFocus.focus === 'function') prevFocus.focus();
    };
    if (card) {
      card.setAttribute('role', 'dialog');
      card.setAttribute('aria-modal', 'true');
      if (opts.labelledBy) card.setAttribute('aria-labelledby', opts.labelledBy);
    }
    mask.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); doClose(); }
    });
    const focusEl = opts.focusEl ? opts.focusEl() : (card ? card.querySelector('[data-close], button, input') : null);
    if (focusEl && typeof focusEl.focus === 'function') focusEl.focus();
    return doClose;
  }

  // ---------- 工具 ----------

  // 设备获取失败的友好提示
  function deviceErrMsg(e) {
    if (e && (e.name === 'NotAllowedError' || e.name === 'SecurityError')) {
      return '浏览器拒绝了访问。若此前拒绝过，请点击地址栏左侧的锁/相机图标重新允许；若通过局域网 IP 访问，请改用 localhost 或 HTTPS 地址';
    }
    if (e && e.name === 'NotFoundError') return '未检测到可用设备';
    if (e && e.name === 'NotReadableError') return '设备被其他程序占用';
    return (e && e.message) || '未知错误';
  }

  function firstChar(name) {
    return (name || '?').trim().charAt(0).toUpperCase() || '?';
  }

  function colorFromString(s) {
    let hash = 0;
    for (let i = 0; i < s.length; i++) hash = (hash * 31 + s.charCodeAt(i)) >>> 0;
    const h = hash % 360;
    return `hsl(${h}, 55%, 45%)`;
  }

  // ---------- WebSocket ----------
  function connectWS() {
    ws = new WebSocket(backendWsUrl());

    ws.onopen = async () => {
      wsReady = true;
      const joinMsg = { type: 'join', roomId, name: myName, audio: micOn, video: camOn };
      if (createCfg) joinMsg.cfg = createCfg;
      sendWS(joinMsg);
    };

    ws.onclose = () => {
      wsReady = false;
      // 意外断线：自动重连
      setTimeout(() => {
        if (!wsReady) connectWS();
      }, 1500);
    };

    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      handleSignal(msg);
    };
  }

  function sendWS(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }

  // ---------- 信令处理 ----------
  function handleSignal(msg) {
    switch (msg.type) {
      case 'joined':
        selfId = msg.selfId;
        ownerId = msg.ownerId || null;
        hostId = msg.hostId || null;
        roomLocked = !!msg.locked;
        muteLocked = !!msg.muteLocked;
        // 会议计时统一基于服务端的会议创建时间，不受先后加入影响
        meetingStart = msg.createdAt || Date.now();
        isOwner = ownerId === selfId;
        isHost = hostId === selfId;
        // 会议配置：标题 / 最大人数 / 时长到期
        if (msg.title) document.getElementById('roomTitle').textContent = msg.title;
        meetingMaxPeers = msg.maxPeers || 0;
        meetingOnExpire = msg.onExpire || 'remind';
        meetingDeadline = msg.deadline || 0;
        remindShown = false;
        startMetaTimer();
        updateMeetingMeta();
        // 记录"我参加过的会议"到本机（首页最近会议用）
        saveMeetingHistory();
        renderSelfTile();
        // 已在会成员：先建"媒体占位"（LiveKit participant 就绪后自动绑定轨道）
        msg.peers.forEach(p => addPeer(p));
        updateMembersPanel();
        // 业务信令就绪后连接 LiveKit SFU（媒体流走媒体服务器转发）
        connectLiveKit();
        break;
      case 'peer-joined':
        addPeer(msg.peer);
        toast(msg.peer.name + ' 加入了会议');
        updateMembersPanel();
        updateMeetingMeta();
        break;
      case 'peer-left':
        removePeer(msg.peerId);
        updateMembersPanel();
        updateMeetingMeta();
        break;
      case 'state': {
        const p = peers.get(msg.peerId);
        if (p) {
          p.audio = msg.audio; p.video = msg.video; p.screen = msg.screen;
          updateTile(p);
          updateMembersPanel();
          layoutGrid(); // 远端开始/停止共享时切换主画面布局
        }
        break;
      }
      case 'rename': {
        if (msg.peerId === selfId) {
          // 自己改名：更新本机昵称与显示
          myName = msg.name;
          setUserName(myName);
          updateSelfTag();
          updateMembersPanel();
        } else {
          const p = peers.get(msg.peerId);
          if (p) { p.name = msg.name; updateTile(p); updateMembersPanel(); }
        }
        break;
      }
      case 'chat': {
        const isSelf = msg.fromId === selfId;
        let targetName = '';
        if (msg.targetId) {
          const tp = peers.get(msg.targetId);
          targetName = tp ? tp.name : '参会者';
        }
        appendChat(msg.name, msg.text, msg.ts, isSelf, { isPrivate: !!msg.targetId, targetName });
        break;
      }
      case 'host-changed': {
        const wasHost = isHost;
        hostId = msg.hostId;
        isHost = hostId === selfId;
        if (isHost && !wasHost) {
          toast('你已成为主持人', 'ok');
        } else if (!isHost && wasHost) {
          const np = peers.get(hostId);
          toast('主持人已转让给 ' + (np ? np.name : '其他参会者'));
        }
        updateMembersPanel();
        updateSelfTag();
        peers.forEach(p => updateTile(p));
        break;
      }
      case 'room-locked':
        roomLocked = !!msg.locked;
        updateMembersPanel();
        toast(roomLocked ? '会议已锁定，新人无法加入' : '会议已解除锁定', 'ok');
        break;
      case 'mute-locked':
        muteLocked = !!msg.locked;
        updateMembersPanel();
        if (muteLocked) toast('主持人已开启全体静音，成员暂不能自行解除', 'error');
        else toast('已解除全体静音限制', 'ok');
        break;
      case 'meeting-ended':
        handleMeetingEnded(msg);
        break;
      case 'force-mute':
        // 远程/全员静音：本地静音并广播状态
        if (micOn) {
          micOn = false;
          if (localStream) localStream.getAudioTracks().forEach(t => (t.enabled = false));
          btnMic.classList.add('off');
          btnMic.querySelector('.label').textContent = '麦克风';
          sendWS({ type: 'state', audio: false, video: camOn, screen: sharing });
          updateSelfTag();
        }
        toast(msg.name ? `${msg.name} 已将你静音` : '你已被静音', 'error');
        break;
      case 'kicked':
        handleKicked(msg);
        break;
      case 'error':
        toast(msg.message, 'error');
        setTimeout(() => location.href = '/', 1800);
        break;
    }
  }

  // ---------- Peer 管理（SFU：媒体由 LiveKit 转发，业务信令只维护角色/状态） ----------
  function addPeer(info) {
    if (peers.has(info.id)) return;
    const peer = {
      id: info.id,
      participant: null,   // LiveKit RemoteParticipant（按 identity=peerId 绑定）
      name: info.name,
      audio: info.audio,
      video: info.video,
      screen: info.screen,
      stream: null,        // 常规音视频流（mic + camera）
      screenStream: null,  // 屏幕画面 + 屏幕声音流
      tile: null
    };
    peers.set(info.id, peer);
    renderPeerTile(peer);
    // 若 LiveKit 侧已就绪（participant 先于业务信令到达），立即绑定并拉取已订阅轨道
    if (lkRoom && lkRoom.remoteParticipants) {
      const p = lkRoom.remoteParticipants.get(info.id);
      if (p) bindParticipant(peer, p);
    }
  }

  // 绑定 LiveKit participant 到业务 peer（按 identity=peerId 关联）
  function bindParticipant(peer, participant) {
    if (peer.participant && peer.participant !== participant) return;
    peer.participant = participant;
    // 已订阅的轨道补挂（事件可能在绑定前触发）；v2 的 Participant 无 getTracks()，用 trackPublications 映射
    participant.trackPublications.forEach(tp => {
      if (tp.isSubscribed && tp.track) handleTrackSubscribed(tp.track, tp, participant);
    });
    handleParticipantState(participant);
  }

  // 连接 LiveKit SFU（业务信令 joined 后调用）
  async function connectLiveKit() {
    if (!LK || !selfId) return;
    if (lkRoom) return; // 已连接
    let token, url;
    try {
      const resp = await fetch(`${API_BASE}/api/livekit/token?room=${encodeURIComponent(roomId)}&identity=${encodeURIComponent(selfId)}&name=${encodeURIComponent(myName)}`);
      const data = await resp.json();
      if (!data.token) throw new Error('获取媒体令牌失败');
      token = data.token;
      // HTTPS 页面必须走 WSS 代理（浏览器禁止 HTTPS 页连 ws://）；HTTP 页面直连本机 LiveKit（hostname 取当前访问地址，跨设备访问局域网 IP 时才能连上）
      // livekit-client 会自动在 URL 后追加 "/rtc/v1"：HTTPS 走代理时最终为 /livekit/rtc/v1（后端通配代理转发到 LiveKit /rtc）；
      // HTTP 直连时基础 URL 不带路径，SDK 追加后为 /rtc/v1（若写成 /rtc 会拼成 /rtc/rtc/v1 导致连接失败）
      url = location.protocol === 'https:'
        ? `wss://${location.hostname}:5679/livekit`
        : `ws://${location.hostname}:7880`;
    } catch (e) {
      toast('媒体服务初始化失败：' + (e.message || e), 'error');
      return;
    }
    try {
      lkRoom = new LK.Room({
        // 关闭自适应/动态编码：观看端始终订阅最高清层，避免画面模糊与切层卡顿（局域网会议优先画质）
        adaptiveStream: false,
        dynacast: false,
        disconnectOnPageLeave: false
      });
      bindLiveKitEvents(lkRoom);
      await lkRoom.connect(url, token);
      lkLocal = lkRoom.localParticipant;
      lkConnected = true;
      // 推流：麦克风 + 摄像头（保持各自 muted 状态）
      await publishInitialTracks();
      // 已存在的远端参会者绑定（加入时房间已有人的场景）
      lkRoom.remoteParticipants.forEach((p, id) => {
        let peer = peers.get(id);
        if (!peer) {
          peer = { id, participant: null, name: id, audio: false, video: false, screen: false, stream: null, screenStream: null, tile: null };
          peers.set(id, peer);
        }
        bindParticipant(peer, p);
      });
      updateMembersPanel();
    } catch (e) {
      console.error('LiveKit 连接失败', e);
      toast('媒体服务连接失败：' + (e.message || e), 'error');
      lkRoom = null; lkLocal = null; lkConnected = false;
    }
  }

  function bindLiveKitEvents(room) {
    const E = LK.RoomEvent;
    room
      .on(E.ParticipantConnected, (participant) => {
        const peerId = participant.identity;
        let peer = peers.get(peerId);
        if (!peer) {
          peer = { id: peerId, participant: null, name: peerId, audio: false, video: false, screen: false, stream: null, screenStream: null, tile: null };
          peers.set(peerId, peer);
        }
        bindParticipant(peer, participant);
        updateMembersPanel();
      })
      .on(E.ParticipantDisconnected, (participant) => {
        // 媒体层断开：移除贴片；业务信令的 peer-left 也会触发一次，幂等
        removePeer(participant.identity);
        updateMembersPanel();
      })
      .on(E.TrackSubscribed, (track, pub, participant) => {
        if (participant === room.localParticipant) return;
        handleTrackSubscribed(track, pub, participant);
      })
      .on(E.TrackUnsubscribed, (track, pub, participant) => {
        if (participant === room.localParticipant) return;
        handleTrackUnsubscribed(track, pub, participant);
      })
      .on(E.TrackMuted, (pub, participant) => {
        if (participant !== room.localParticipant) handleParticipantState(participant);
      })
      .on(E.TrackUnmuted, (pub, participant) => {
        if (participant !== room.localParticipant) handleParticipantState(participant);
      })
      .on(E.ConnectionStateChanged, (state) => {
        if (state === 'connected') console.log('LiveKit 媒体连接已建立');
      })
      .on(E.Disconnected, () => {
        lkConnected = false;
      });
  }

  // 远端轨道订阅：按来源归类到常规流或屏幕流（track 为 livekit-client 的 RemoteTrack，取 .mediaStreamTrack）
  function handleTrackSubscribed(track, pub, participant) {
    const peerId = participant.identity;
    let peer = peers.get(peerId);
    if (!peer) {
      peer = { id: peerId, participant: null, name: peerId, audio: false, video: false, screen: false, stream: null, screenStream: null, tile: null };
      peers.set(peerId, peer);
    }
    const mt = (track && track.mediaStreamTrack) || track; // MediaStreamTrack
    const src = pub.source || '';
    if (src === LK_SRC.screen) {
      if (!peer.screenStream) peer.screenStream = new MediaStream();
      if (mt && !peer.screenStream.getTracks().includes(mt)) peer.screenStream.addTrack(mt);
      peer.screen = true;
    } else if (src === LK_SRC.screenAudio) {
      if (!peer.screenStream) peer.screenStream = new MediaStream();
      if (mt && !peer.screenStream.getTracks().includes(mt)) peer.screenStream.addTrack(mt);
    } else {
      if (!peer.stream) peer.stream = new MediaStream();
      if (mt && !peer.stream.getTracks().includes(mt)) peer.stream.addTrack(mt);
      if (mt && mt.kind === 'audio') peer.audio = true;
      if (mt && mt.kind === 'video') peer.video = true;
    }
    renderPeerTile(peer);
    if (rec && rec.ctx && mt && mt.kind === 'audio') connectStreamToMix(new MediaStream([mt]));
  }

  function handleTrackUnsubscribed(track, pub, participant) {
    const peer = peers.get(participant.identity);
    if (!peer) return;
    const mt = (track && track.mediaStreamTrack) || track;
    const src = pub.source || '';
    if (src === LK_SRC.screen || src === LK_SRC.screenAudio) {
      if (peer.screenStream && mt) {
        peer.screenStream.removeTrack(mt);
        if (!peer.screenStream.getTracks().length) peer.screenStream = null;
      }
      if (src === LK_SRC.screen && !peer.screenStream) peer.screen = false;
    } else {
      if (peer.stream && mt) {
        peer.stream.removeTrack(mt);
        if (mt.kind === 'audio') peer.audio = !!peer.stream.getAudioTracks().length;
        if (mt.kind === 'video') peer.video = !!peer.stream.getVideoTracks().length;
      }
    }
    renderPeerTile(peer);
    layoutGrid();
  }

  // 远端参会者状态变化（静音/视频/共享）→ 刷新 UI
  function handleParticipantState(participant) {
    const peer = peers.get(participant.identity);
    if (!peer) return;
    peer.audio = !!participant.isMicrophoneEnabled;
    peer.video = !!participant.isCameraEnabled;
    peer.screen = !!participant.isScreenShareEnabled;
    updateTile(peer);
    updateMembersPanel();
    layoutGrid();
  }

  // 推流初始设备：已有本地轨道则发布，保持 muted 状态
  async function publishInitialTracks() {
    if (!lkLocal) return;
    try {
      const audioTrack = localStream ? localStream.getAudioTracks()[0] : null;
      const videoTrack = localStream ? localStream.getVideoTracks()[0] : null;
      if (audioTrack) await publishLocalTrack(audioTrack, LK_SRC.mic, micOn);
      if (videoTrack) await publishLocalTrack(videoTrack, LK_SRC.cam, camOn);
    } catch (e) {
      console.warn('推流失败', e);
    }
  }

  // 发布本地轨道（source 为 mic/cam）；isOn 为 false 时保持静音/熄屏
  async function publishLocalTrack(mediaTrack, source, isOn) {
    if (!lkLocal) return;
    const pub = lkLocal.getTrackPublication(source);
    if (pub && pub.track && pub.track.mediaStreamTrack === mediaTrack) {
      // 已发布同一轨道：仅同步 muted 状态
      if (isOn) { try { await pub.unmute(); } catch { /* 忽略 */ } }
      else { try { await pub.mute(); } catch { /* 忽略 */ } }
      return;
    }
    // 重新发布（更换轨道）
    if (pub) {
      try { await lkLocal.unpublishTrack(pub.track); } catch { /* 忽略 */ }
    }
    try {
      const opts = { source };
      // 摄像头采用单层高质量编码（清晰度优先，避免 simulcast 低层模糊）
      if (source === LK_SRC.cam) opts.videoEncoding = CAM_ENC;
      await lkLocal.publishTrack(mediaTrack, opts);
      const newPub = lkLocal.getTrackPublication(source);
      if (newPub) {
        if (isOn) { try { await newPub.unmute(); } catch { /* 忽略 */ } }
        else { try { await newPub.mute(); } catch { /* 忽略 */ } }
      }
    } catch (e) {
      console.warn('发布轨道失败', source, e);
    }
  }

  // 移除参会者：清理贴片与混音源
  function removePeer(peerId) {
    const peer = peers.get(peerId);
    if (!peer) return;
    if (peer.tile) peer.tile.remove();
    if (rec && peer._mixSource) {
      try { peer._mixSource.disconnect(); } catch { /* 忽略 */ }
    }
    peers.delete(peerId);
    layoutGrid();
  }

  // ---------- 视频渲染 ----------
  const FS_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 00-2 2v3"/><path d="M21 8V5a2 2 0 00-2-2h-3"/><path d="M3 16v3a2 2 0 002 2h3"/><path d="M16 21h3a2 2 0 002-2v-3"/></svg>';
  const FS_BTN = `<button class="tile-fs" title="全屏" aria-label="全屏">${FS_ICON}</button>`;
  // 共享画面"铺满/适应"切换图标：COVER=铺满（裁剪），FIT=适应（完整显示）
  const COVER_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/></svg>';
  const FIT_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/></svg>';
  const FIT_BTN = `<button class="tile-fit" title="铺满屏幕" aria-label="铺满屏幕">${COVER_ICON}</button>`;

  function renderSelfTile() {
    let tile = document.getElementById('tile-self');
    if (!tile) {
      tile = document.createElement('div');
      tile.className = 'tile mirrored';
      tile.id = 'tile-self';
      tile.innerHTML = `
        <div class="avatar" style="background:${colorFromString(myName)}">${escapeHtml(firstChar(myName))}</div>
        <video autoplay playsinline muted></video>
        <div class="tag"></div>
        ${FIT_BTN}${FS_BTN}`;
      videoGrid.appendChild(tile);
    }
    const video = tile.querySelector('video');
    if (localStream) {
      video.srcObject = sharing && screenStream ? screenStream : localStream;
    }
    updateSelfTag();
    layoutGrid();
  }

  function updateSelfTag() {
    const tile = document.getElementById('tile-self');
    if (!tile) return;
    const tag = tile.querySelector('.tag');
    const micIcon = micOn
      ? '<svg class="mic-on" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2"/></svg>'
      : '<svg class="mic-off" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="1" y1="1" x2="23" y2="23"/><path d="M9 9v3a3 3 0 005.12 2.12M15 9.34V4a3 3 0 00-5.94-.6"/><path d="M17 16.95A7 7 0 0119 10v-2m-7 7a7 7 0 01-7-7v-2"/></svg>';
    tag.innerHTML = `${isOwner ? '<span class="host-mark owner">创建者</span>' : (isHost ? '<span class="host-mark">主持人</span>' : '')}${micIcon}<span class="name">${escapeHtml(myName)}（我）</span>${sharing ? '<span class="corner">共享中</span>' : ''}`;
    const video = tile.querySelector('video');
    video.style.display = (camOn || sharing) ? '' : 'none';
    const avatar = tile.querySelector('.avatar');
    avatar.style.display = (camOn || sharing) ? 'none' : 'flex';
    tile.classList.toggle('screen-share', sharing);
  }

  function renderPeerTile(peer) {
    if (!peer.tile) {
      peer.tile = document.createElement('div');
      peer.tile.className = 'tile';
      peer.tile.innerHTML = `
        <div class="avatar">${escapeHtml(firstChar(peer.name))}</div>
        <video autoplay playsinline></video>
        <div class="tag"></div>
        ${FIT_BTN}${FS_BTN}`;
      videoGrid.appendChild(peer.tile);
    }
    const video = peer.tile.querySelector('video');
    // 应用扬声器设置：关闭扬声器 → 本地静音（不影响对方听到自己）；指定输出设备 → setSinkId
    video.muted = speakerMuted;
    if (!speakerMuted && speakerDeviceId && video.setSinkId) {
      video.setSinkId(speakerDeviceId).catch(() => {});
    }
    // 共享屏幕：视频元素绑定合并流（屏幕视频 + 屏幕声音 + 对方麦克风声音，腾讯会议式）；否则绑常规音视频流
    if (peer.screen && peer.screenStream && peer.screenStream.getVideoTracks().length) {
      const mix = new MediaStream();
      peer.screenStream.getTracks().forEach(t => mix.addTrack(t));
      if (peer.stream) peer.stream.getAudioTracks().forEach(t => mix.addTrack(t));
      video.srcObject = mix;
      video.play().catch(() => {});
    } else if (peer.stream) {
      video.srcObject = peer.stream;
      // 主动触发播放，失败（自动播放策略）时由全局点击监听兜底重试
      video.play().catch(() => {});
    }
    peer.tile.querySelector('.avatar').style.background = colorFromString(peer.name);
    updateTile(peer);
    layoutGrid();
  }

  function updateTile(peer) {
    if (!peer.tile) return;
    const tag = peer.tile.querySelector('.tag');
    const micIcon = peer.audio
      ? '<svg class="mic-on" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2"/></svg>'
      : '<svg class="mic-off" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="1" y1="1" x2="23" y2="23"/><path d="M9 9v3a3 3 0 005.12 2.12M15 9.34V4a3 3 0 00-5.94-.6"/><path d="M17 16.95A7 7 0 0119 10v-2m-7 7a7 7 0 01-7-7v-2"/></svg>';
    tag.innerHTML = `${peer.id === ownerId ? '<span class="host-mark owner">创建者</span>' : (peer.id === hostId ? '<span class="host-mark">主持人</span>' : '')}${micIcon}<span class="name">${escapeHtml(peer.name)}</span>${peer.screen ? '<span class="corner">共享中</span>' : ''}`;
    const showVideo = peer.video || peer.screen;
    peer.tile.querySelector('video').style.display = showVideo ? '' : 'none';
    peer.tile.querySelector('.avatar').style.display = showVideo ? 'none' : 'flex';
    peer.tile.classList.toggle('screen-share', !!peer.screen);
  }

  function layoutGrid() {
    // 腾讯会议式：有人共享屏幕时，共享画面占主窗口，其他人缩成底部小窗条
    const anyScreen = sharing || [...peers.values()].some(p => p.screen);
    if (anyScreen) {
      videoGrid.className = 'video-grid screen-mode';
      return;
    }
    const count = videoGrid.children.length;
    const n = Math.max(1, Math.min(count, 8));
    videoGrid.className = 'video-grid v' + n;
  }

  // ---------- 音频混合 & 录制 ----------
  function getOrCreateRecContext() {
    if (!rec) return null;
    if (!rec.ctx) {
      rec.ctx = new AudioContext();
      rec.mixDest = rec.ctx.createMediaStreamDestination();
    }
    return rec.ctx;
  }

  function connectStreamToMix(stream) {
    if (!rec || !rec.ctx || !stream) return;
    try {
      const src = rec.ctx.createMediaStreamSource(stream);
      src.connect(rec.mixDest);
      src.connect(rec.workletNode);
      rec._sources = rec._sources || [];
      rec._sources.push(src);
    } catch (e) { /* 忽略 */ }
  }

  // Float32 PCM -> WAV Blob（16bit PCM 单声道）
  function pcmToWavBlob(pcm, sampleRate) {
    const buffer = new ArrayBuffer(44 + pcm.length * 2);
    const view = new DataView(buffer);
    const writeStr = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
    writeStr(0, 'RIFF');
    view.setUint32(4, 36 + pcm.length * 2, true);
    writeStr(8, 'WAVE');
    writeStr(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeStr(36, 'data');
    view.setUint32(40, pcm.length * 2, true);
    let off = 44;
    for (let i = 0; i < pcm.length; i++, off += 2) {
      const s = Math.max(-1, Math.min(1, pcm[i]));
      view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    }
    return new Blob([buffer], { type: 'audio/wav' });
  }

  let chunkIndex = 0;

  async function onPcmChunk({ pcm, sampleRate }) {
    if (!rec) return;
    const blob = pcmToWavBlob(pcm, sampleRate);
    const offset = Math.round((Date.now() - rec.startTime) / 1000);
    const idx = ++chunkIndex;
    try {
      const fd = new FormData();
      fd.append('audio', blob, `chunk_${idx}.wav`);
      const resp = await fetch(`${API_BASE}/api/records/${rec.id}/transcribe-chunk?offset=${offset}`, { method: 'POST', body: fd });
      const data = await resp.json();
      if (data.text) {
        appendSubtitle(data.text, offset);
        showLiveSubtitle(data.text);
      }
    } catch (e) {
      // 转写失败（可能未配置 API），静默降级：本地保留完整录音
    }
  }

  function appendSubtitle(text, offset) {
    const item = document.createElement('div');
    item.className = 'subtitle-item';
    item.innerHTML = `<span class="s-time">${fmtDuration(offset)}</span>${escapeHtml(text)}`;
    subtitlePanel.appendChild(item);
    subtitlePanel.scrollTop = subtitlePanel.scrollHeight;
  }

  function showLiveSubtitle(text) {
    liveSubtitle.textContent = text;
    liveSubtitle.classList.add('on');
    clearTimeout(showLiveSubtitle._t);
    showLiveSubtitle._t = setTimeout(() => liveSubtitle.classList.remove('on'), 6000);
  }

  // 录屏：canvas 实时绘制会议主画面（视频网格区域），用于"会议录屏"
  function createStageCapture() {
    const grid = document.getElementById('videoGrid');
    const rect = grid.getBoundingClientRect();
    const scale = Math.min(2, Math.max(0.75, 1280 / Math.max(rect.width || 1, 1)));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(2, Math.round((rect.width || 320) * scale));
    canvas.height = Math.max(2, Math.round((rect.height || 240) * scale));
    const ctx = canvas.getContext('2d');

    const draw = () => {
      ctx.fillStyle = '#0f172a';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      const gRect = grid.getBoundingClientRect();
      grid.querySelectorAll('.tile').forEach((tile) => {
        const tRect = tile.getBoundingClientRect();
        const x = (tRect.left - gRect.left) * scale;
        const y = (tRect.top - gRect.top) * scale;
        const w = tRect.width * scale;
        const h = tRect.height * scale;
        if (w <= 1 || h <= 1) return;
        const video = tile.querySelector('video');
        if (video && video.srcObject && video.readyState >= 2 && video.videoWidth) {
          // 与 CSS object-fit: cover 一致的裁剪填充
          const s = Math.max(w / video.videoWidth, h / video.videoHeight);
          const dw = video.videoWidth * s;
          const dh = video.videoHeight * s;
          ctx.drawImage(video, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh);
        } else {
          const avatar = tile.querySelector('.avatar');
          if (avatar) {
            ctx.fillStyle = avatar.style.background || '#1e293b';
            ctx.fillRect(x, y, w, h);
            ctx.fillStyle = '#fff';
            ctx.font = `${Math.max(12, Math.round(h * 0.35))}px "Segoe UI", "Microsoft YaHei", sans-serif`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText((avatar.textContent || '?').trim().slice(0, 2), x + w / 2, y + h / 2);
          }
        }
      });
      if (rec) rec._raf = requestAnimationFrame(draw);
    };
    draw();
    const stream = canvas.captureStream(30);
    return { canvas, stream };
  }

  async function startRecording(mode) {
    if (rec) return;
    mode = mode === 'video' ? 'video' : 'audio';
    try {
      const record = await API.post('/records', {
        title: `会议 ${roomId}`,
        roomId,
        host: myName,
        ownerId: getUserId(),   // 记录归属本设备，其他人看不到
        ownerName: myName,
        mode
      });
      rec = {
        id: record.id,
        mode,
        ctx: null,
        workletNode: null,
        mediaRecorder: null,
        chunks: [],
        startTime: Date.now(),
        _sources: [],
        _cap: null,
        _raf: 0
      };

      const ctx = getOrCreateRecContext();
      await ctx.resume();
      await ctx.audioWorklet.addModule('/js/pcm-worklet.js');
      rec.workletNode = new AudioWorkletNode(ctx, 'pcm-collector', { numberOfOutputs: 1 });
      rec.workletNode.port.onmessage = (ev) => onPcmChunk(ev.data);
      // worklet 需要连接 destination 才被驱动（输出为空，不影响）
      rec.workletNode.connect(ctx.destination);

      // 本地流 + 所有远端流接入混合
      if (localStream) connectStreamToMix(localStream);
      for (const peer of peers.values()) {
        if (peer.stream) connectStreamToMix(peer.stream);
      }

      // 组装录制流：录屏模式 = 会议画面 + 混合音频；录音模式 = 仅混合音频
      let recStream = rec.mixDest.stream;
      if (mode === 'video') {
        rec._cap = createStageCapture();
        recStream = new MediaStream();
        rec._cap.stream.getVideoTracks().forEach(t => recStream.addTrack(t));
        rec.mixDest.stream.getAudioTracks().forEach(t => recStream.addTrack(t));
      }

      // 选择合适的编码容器（录屏 webm 视频 / 录音 webm 音频）
      let mimeType;
      if (mode === 'video') {
        mimeType = ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm']
          .find(m => MediaRecorder.isTypeSupported(m)) || 'video/webm';
      } else {
        mimeType = ['audio/webm;codecs=opus', 'audio/webm']
          .find(m => MediaRecorder.isTypeSupported(m)) || 'audio/webm';
      }
      rec.mediaRecorder = new MediaRecorder(recStream, { mimeType });
      rec.mediaRecorder.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) rec.chunks.push(ev.data);
      };
      rec.mediaRecorder.start(5000);

      document.getElementById('recIndicator').classList.add('on');
      const btn = document.getElementById('btnRec');
      btn.classList.add('recording');
      btn.querySelector('.label').textContent = '停止录制';
      setPressed(btnRec, true);
      toast(mode === 'video' ? '录屏已开始，将录制会议画面与声音' : '录制已开始，语音将实时转写', 'ok');
    } catch (e) {
      rec = null;
      toast('录制启动失败：' + e.message, 'error');
    }
  }

  function stopRecording() {
    return new Promise((resolve) => {
      if (!rec) return resolve();
      const mr = rec.mediaRecorder;
      const mode = rec.mode || 'audio';
      const finish = async () => {
        const dur = Math.round((Date.now() - rec.startTime) / 1000);
        const recId = rec.id;
        const title = `会议 ${roomId}`;
        try {
          if (rec.chunks.length) {
            const blob = new Blob(rec.chunks, { type: mr.mimeType || (mode === 'video' ? 'video/webm' : 'audio/webm') });
            // 上传服务器：记录内可回放 / 转写
            const fd = new FormData();
            if (mode === 'video') {
              fd.append('video', blob, `${recId}.webm`);
              await fetch(`${API_BASE}/api/records/${recId}/video`, { method: 'POST', body: fd });
            } else {
              fd.append('audio', blob, `${recId}.webm`);
              await fetch(`${API_BASE}/api/records/${recId}/audio`, { method: 'POST', body: fd });
            }
            // 同时保存到本地（浏览器下载到本机）
            try {
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = `${title}_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.webm`;
              document.body.appendChild(a);
              a.click();
              a.remove();
              setTimeout(() => URL.revokeObjectURL(url), 5000);
            } catch { /* 忽略 */ }
          }
          await API.patch(`/records/${recId}`, {
            status: 'completed',
            endedAt: new Date().toISOString(),
            duration: dur
          });
        } catch (e) {
          toast((mode === 'video' ? '录屏上传失败：' : '录音上传失败：') + e.message, 'error');
        }
        try { rec.workletNode.disconnect(); } catch { /* 忽略 */ }
        try { rec.ctx.close(); } catch { /* 忽略 */ }
        // 释放录屏画布与绘制循环
        if (rec._raf) cancelAnimationFrame(rec._raf);
        if (rec._cap) {
          try { rec._cap.stream.getTracks().forEach(t => t.stop()); } catch { /* 忽略 */ }
          try { rec._cap.canvas.width = 1; rec._cap.canvas.height = 1; } catch { /* 忽略 */ }
        }
        rec = null;
        document.getElementById('recIndicator').classList.remove('on');
        const btn = document.getElementById('btnRec');
        btn.classList.remove('recording');
        btn.querySelector('.label').textContent = '录制';
        setPressed(btnRec, false);
        toast('录制已结束：文件已保存到本机，可在「会议记录」查看', 'ok');
        resolve(recId);
      };

      if (mr && mr.state !== 'inactive') {
        mr.onstop = finish;
        mr.stop();
      } else {
        finish();
      }
    });
  }

  // ---------- 工具栏 ----------

  // 按需获取麦克风（旁听模式 / 此前获取失败时，点击"解除静音"重新获取）
  async function acquireMic() {
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      const t = s.getAudioTracks()[0];
      if (!localStream) localStream = new MediaStream();
      if (!localStream.getAudioTracks().includes(t)) localStream.addTrack(t);
      micOn = true;
      await publishLocalTrack(t, LK_SRC.mic, true);
      if (rec && rec.ctx) connectStreamToMix(s);
      btnMic.classList.remove('off');
      btnMic.querySelector('.label').textContent = '麦克风';
      renderSelfTile();
      updateMembersPanel();
      sendWS({ type: 'state', audio: true, video: camOn, screen: sharing });
      toast('麦克风已开启', 'ok');
    } catch (e) {
      toast('无法开启麦克风：' + deviceErrMsg(e), 'error');
    }
  }

  // 按需获取摄像头（无摄像头轨道时，点击"开启视频"重新获取）
  async function acquireCam() {
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: true });
      const t = s.getVideoTracks()[0];
      if (!localStream) localStream = new MediaStream();
      if (!localStream.getVideoTracks().includes(t)) localStream.addTrack(t);
      camTrack = t;
      camOn = true;
      await publishLocalTrack(t, LK_SRC.cam, true);
      btnCam.classList.remove('off');
      btnCam.querySelector('.label').textContent = '视频';
      renderSelfTile();
      updateMembersPanel();
      sendWS({ type: 'state', audio: micOn, video: true, screen: sharing });
      toast('摄像头已开启', 'ok');
    } catch (e) {
      toast('无法开启视频：' + deviceErrMsg(e), 'error');
    }
  }

  // ---------- 音频弹出菜单共享图标与工具 ----------
  const AP_ICONS = {
    back: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
    mic: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2"/></svg>',
    micOff: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="1" y1="1" x2="23" y2="23"/><path d="M9 9v3a3 3 0 005.12 2.12M15 9.34V4a3 3 0 00-5.94-.6"/><path d="M17 16.95A7 7 0 0119 10v-2m-7 7a7 7 0 01-7-7v-2"/></svg>',
    speaker: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M15.54 8.46a5 5 0 010 7.07"/><path d="M19.07 4.93a10 10 0 010 14.14"/></svg>',
    speakerOff: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5L6 9H2v6h4l5 4V5z"/><line x1="22" y1="9" x2="16" y2="15"/><line x1="16" y1="9" x2="22" y2="15"/></svg>'
  };

  function closeAudioPop() {
    const pop = document.querySelector('.audio-pop');
    if (pop) pop.remove();
    setExpanded(btnMic, false);
    setExpanded(btnSpeaker, false);
  }
  // 创建弹出菜单并挂到 body 下（fixed 定位，避开移动端 toolbar 的 overflow 裁剪）
  function mountAudioPop(btn, owner) {
    const pop = document.createElement('div');
    pop.className = 'audio-pop';
    pop.dataset.owner = owner;
    document.body.appendChild(pop);
    return pop;
  }
  // 将菜单固定定位到按钮正上方，并平移回视口内（窄窗口 / 靠边按钮）
  function positionAudioPop(btn, pop) {
    const r = btn.getBoundingClientRect();
    pop.style.position = 'fixed';
    pop.style.bottom = 'auto';
    pop.style.transform = 'none';
    const ph = pop.offsetHeight;
    const pw = pop.offsetWidth;
    const m = 8;
    let left = r.left + r.width / 2 - pw / 2;
    left = Math.max(m, Math.min(left, window.innerWidth - pw - m));
    pop.style.left = left + 'px';
    pop.style.top = (r.top - ph - 12) + 'px';
  }
  // 点击菜单外任意位置关闭弹出菜单
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.audio-pop') && !e.target.closest('#btnMic') && !e.target.closest('#btnSpeaker')) {
      closeAudioPop();
    }
  });

  // 点击"麦克风"按钮：向上展开菜单（选择麦克风 / 静音），仿腾讯会议
  btnMic.addEventListener('click', (e) => {
    e.stopPropagation();
    const existing = document.querySelector('.audio-pop');
    if (existing && existing.dataset.owner === 'mic') { closeAudioPop(); return; }
    closeAudioPop();
    openMicPop();
  });

  // 静音/解除静音（菜单项）
  function toggleMic() {
    if (muteLocked && !isOwner && !isHost && !micOn) {
      toast('主持人已开启全体静音，暂不能自行解除', 'error');
      return;
    }
    const audioTrack = localStream ? localStream.getAudioTracks()[0] : null;
    if (!audioTrack) {
      acquireMic();
      return;
    }
    micOn = !micOn;
    localStream.getAudioTracks().forEach(t => (t.enabled = micOn));
    // 同步 LiveKit 发布状态：静音/解除静音广播给远端
    publishLocalTrack(audioTrack, LK_SRC.mic, micOn);
    btnMic.classList.toggle('off', !micOn);
    btnMic.querySelector('.label').textContent = '麦克风';
    setPressed(btnMic, micOn);
    sendWS({ type: 'state', audio: micOn, video: camOn, screen: sharing });
    updateSelfTag();
  }

  // 切换麦克风输入设备（更换轨道并重新发布到 SFU）
  async function selectMicDevice(deviceId) {
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: { exact: deviceId } } });
      const t = s.getAudioTracks()[0];
      if (!localStream) localStream = new MediaStream();
      const old = localStream.getAudioTracks()[0];
      if (old) {
        try { localStream.removeTrack(old); old.stop(); } catch { /* 忽略 */ }
      }
      localStream.addTrack(t);
      t.enabled = micOn;
      await publishLocalTrack(t, LK_SRC.mic, micOn);
      if (rec && rec.ctx) connectStreamToMix(s);
      btnMic.classList.toggle('off', !micOn);
      renderSelfTile();
      updateMembersPanel();
      sendWS({ type: 'state', audio: micOn, video: camOn, screen: sharing });
      toast('麦克风已切换', 'ok');
    } catch (e) {
      toast('切换麦克风失败：' + deviceErrMsg(e), 'error');
    }
  }

  function openMicPop() {
    setExpanded(btnMic, true);
    const pop = mountAudioPop(btnMic, 'mic');

    const renderMain = () => {
      pop.innerHTML = `
        <div class="ap-item" data-action="devices">${AP_ICONS.mic}<span>选择麦克风</span></div>
        <div class="ap-item ${micOn ? '' : 'danger'}" data-action="toggle">${micOn ? AP_ICONS.mic : AP_ICONS.micOff}<span>${micOn ? '静音' : '解除静音'}</span></div>`;
      positionAudioPop(btnMic, pop);
    };
    const renderDevices = () => {
      pop.innerHTML = `
        <div class="ap-item" data-back="1">${AP_ICONS.back}<span>选择麦克风</span></div>
        <div class="ap-sep"></div>
        <div class="ap-devlist"><div class="ap-load">加载设备中…</div></div>`;
      positionAudioPop(btnMic, pop);
      const list = pop.querySelector('.ap-devlist');
      navigator.mediaDevices.enumerateDevices()
        .then(devs => {
          const mics = devs.filter(d => d.kind === 'audioinput');
          const cur = localStream && localStream.getAudioTracks()[0]
            ? localStream.getAudioTracks()[0].getSettings().deviceId : null;
          list.innerHTML = mics.length
            ? mics.map((d, i) => `
              <div class="ap-item ${d.deviceId === cur ? 'sel' : ''}" data-device="${escapeHtml(d.deviceId)}">
                ${AP_ICONS.mic}<span class="ap-name">${escapeHtml(d.label || ('麦克风 ' + (i + 1)))}</span>
              </div>`).join('')
            : '<div class="ap-load">未检测到麦克风</div>';
        })
        .catch(() => { list.innerHTML = '<div class="ap-load">无法读取设备列表</div>'; });
    };
    pop.addEventListener('click', (e) => {
      e.stopPropagation(); // 阻止冒泡到 document 的"点击外部关闭"，否则 innerHTML 重建会使目标脱离 DOM 被误判为外部点击
      const item = e.target.closest('.ap-item');
      if (!item) return;
      if (item.dataset.back) { renderMain(); return; }
      if (item.dataset.action === 'devices') { renderDevices(); return; }
      if (item.dataset.action === 'toggle') { closeAudioPop(); toggleMic(); return; }
      if (item.dataset.device) { closeAudioPop(); selectMicDevice(item.dataset.device); }
    });
    renderMain();
  }

  btnCam.addEventListener('click', () => {
    if (!camTrack) {
      acquireCam();
      return;
    }
    camOn = !camOn;
    localStream.getVideoTracks().forEach(t => (t.enabled = camOn));
    // 同步 LiveKit 发布状态：关闭/开启摄像头广播给远端
    publishLocalTrack(camTrack, LK_SRC.cam, camOn);
    btnCam.classList.toggle('off', !camOn);
    btnCam.querySelector('.label').textContent = camOn ? '视频' : '开启视频';
    setPressed(btnCam, camOn);
    sendWS({ type: 'state', audio: micOn, video: camOn, screen: sharing });
    updateSelfTag();
  });

  // 共享屏幕：先弹确认框（可选"同时共享电脑声音"，仿腾讯会议），再调起系统选窗
  btnScreen.addEventListener('click', () => {
    if (IS_MOBILE) {
      if (sharing) { stopScreenShare(); return; }
      toast('手机端不支持共享屏幕，请在电脑端操作', 'error');
      return;
    }
    if (sharing) {
      stopScreenShare();
      return;
    }
    // 已共享过一次或浏览器不支持弹确认框的场景可直接复用；这里统一走确认流程
    showScreenShareConfirm();
  });

  function showScreenShareConfirm() {
    const mask = document.createElement('div');
    mask.className = 'room-mask';
    mask.innerHTML = `
      <div class="room-modal">
        <div class="rm-title" id="scTitle">共享屏幕
          <button class="share-close" data-close="1" aria-label="关闭">×</button>
        </div>
        <div class="rm-desc">对方将看到你的屏幕画面。</div>
        <label class="rm-label">
          <input type="checkbox" id="screenAudioChk" checked>
          <span>同时共享电脑声音</span>
        </label>
        <div class="rm-hint">勾选后，你设备播放的音乐 / 视频声音也会同步给参会者；共享中可随时单独关闭屏幕声音，不影响说话。</div>
        <div class="rm-actions">
          <button class="btn secondary small" data-close="1">取消</button>
          <button class="btn small" id="scConfirmBtn">开始共享</button>
        </div>
      </div>`;
    document.body.appendChild(mask);
    const close = setupDialog(mask, { labelledBy: 'scTitle' });
    mask.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', close));
    mask.addEventListener('click', (e) => { if (e.target === mask) close(); });
    mask.querySelector('#scConfirmBtn').addEventListener('click', async () => {
      const includeAudio = mask.querySelector('#screenAudioChk').checked;
      close();
      await startScreenShare(includeAudio);
    });
  }

  async function startScreenShare(includeAudio) {
    try {
      // audio: true 会采集系统声音（播放的音乐/视频声），需浏览器与系统支持
      screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: !!includeAudio });
    } catch (e) {
      if (!window.isSecureContext) {
        toast('共享屏幕需要安全上下文：请使用 localhost 或 HTTPS 地址访问', 'error');
      }
      return; // 用户取消选择
    }
    screenAudioTrack = screenStream.getAudioTracks()[0] || null;
    screenAudioOn = !!screenAudioTrack;
    sharing = true;
    const screenTrack = screenStream.getVideoTracks()[0];
    screenTrack.onended = () => stopScreenShare();
    // 通过 LiveKit 发布屏幕轨道（SFU 转发给所有参会者）
    if (lkLocal) {
      try {
        await lkLocal.publishTrack(screenTrack, { source: LK_SRC.screen, screenShareEncoding: SCREEN_ENC });
        if (screenAudioTrack) {
          await lkLocal.publishTrack(screenAudioTrack, { source: LK_SRC.screenAudio });
          if (rec && rec.ctx) connectStreamToMix(new MediaStream([screenAudioTrack]));
        } else if (includeAudio) {
          toast('不支持共享电脑声音，已仅共享画面。如需共享声音：请分享「标签页」并勾选「分享标签页音频」', 'error');
        }
      } catch (e) {
        console.error('发布屏幕轨道失败', e);
        toast('共享屏幕失败：' + (e.message || e), 'error');
        sharing = false;
        screenStream.getTracks().forEach(t => t.stop());
        screenStream = null; screenAudioTrack = null; screenAudioOn = false;
        updateScreenAudioBtn();
        renderSelfTile();
        return;
      }
    }
    updateScreenAudioBtn();
    btnScreen.classList.add('active');
    setPressed(btnScreen, true);
    sendWS({ type: 'state', audio: micOn, video: camOn, screen: true });
    renderSelfTile();
  }

  // 共享中单独开关"屏幕声音"（只影响屏幕声音，麦克风不受影响）
  btnScreenAudio.addEventListener('click', () => {
    if (!sharing || !screenAudioTrack) return;
    screenAudioOn = !screenAudioOn;
    screenStream.getAudioTracks().forEach(t => (t.enabled = screenAudioOn));
    // 同步 LiveKit 发布状态：屏幕声音独立静音/解除，不影响麦克风
    const pub = lkLocal ? lkLocal.getTrackPublication(LK_SRC.screenAudio) : null;
    if (pub) {
      if (screenAudioOn) pub.unmute().catch(() => {});
      else pub.mute().catch(() => {});
    }
    updateScreenAudioBtn();
  });

  function updateScreenAudioBtn() {
    if (!sharing || !screenAudioTrack) {
      btnScreenAudio.style.display = 'none';
      return;
    }
    btnScreenAudio.style.display = '';
    btnScreenAudio.classList.toggle('active', screenAudioOn);
    btnScreenAudio.classList.toggle('off', !screenAudioOn);
    btnScreenAudio.querySelector('.label').textContent = screenAudioOn ? '屏幕声音' : '屏幕静音';
    setPressed(btnScreenAudio, screenAudioOn);
  }

  async function stopScreenShare() {
    if (!sharing && !screenStream) return; // 防止 onended 与按钮点击双重触发
    sharing = false;
    if (screenStream) {
      screenStream.getTracks().forEach(t => t.stop());
      screenStream = null;
    }
    screenAudioTrack = null;
    screenAudioOn = false;
    updateScreenAudioBtn();
    // 取消发布屏幕轨道与屏幕声音（LiveKit 自动恢复远端看到自己的摄像头画面）
    if (lkLocal) {
      try {
        const pub = lkLocal.getTrackPublication(LK_SRC.screen);
        if (pub) await lkLocal.unpublishTrack(pub.track);
        const aPub = lkLocal.getTrackPublication(LK_SRC.screenAudio);
        if (aPub) await lkLocal.unpublishTrack(aPub.track);
      } catch (e) { /* 忽略 */ }
    }
    btnScreen.classList.remove('active');
    setPressed(btnScreen, false);
    sendWS({ type: 'state', audio: micOn, video: camOn, screen: false });
    renderSelfTile();
  }

  // 录制按钮：未录制时向上弹出"录音 / 会议录屏"选择；录制中点击即停止
  btnRec.addEventListener('click', (e) => {
    if (rec) {
      stopRecording();
      return;
    }
    e.stopPropagation();
    const existing = document.querySelector('.rec-pop');
    if (existing) { existing.remove(); return; }
    const pop = document.createElement('div');
    pop.className = 'audio-pop rec-pop';
    pop.innerHTML = `
      <div class="ap-item" data-mode="audio">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/></svg>
        <span class="ap-txt"><b>录音</b><small class="ap-desc">仅录制会议声音，实时转写</small></span>
      </div>
      <div class="ap-sep"></div>
      <div class="ap-item" data-mode="video">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>
        <span class="ap-txt"><b>会议录屏</b><small class="ap-desc">录制会议画面与声音，保存为视频</small></span>
      </div>`;
    document.body.appendChild(pop);
    positionAudioPop(btnRec, pop);
    pop.addEventListener('click', (ev) => {
      const item = ev.target.closest('.ap-item[data-mode]');
      if (!item) return;
      pop.remove();
      startRecording(item.dataset.mode);
    });
  });

  // ---------- 扬声器：关闭扬声器（本地不听，但对方仍能听到自己）+ 输出设备选择（setSinkId） ----------
  // 点击"扬声器"按钮：向上展开菜单（选择扬声器 / 关闭扬声器），仿腾讯会议
  btnSpeaker.addEventListener('click', (e) => {
    e.stopPropagation();
    const existing = document.querySelector('.audio-pop');
    if (existing && existing.dataset.owner === 'speaker') { closeAudioPop(); return; }
    closeAudioPop();
    openSpeakerPop();
  });

  // 应用扬声器设置到所有远端视频（新 peer 渲染时也会应用）
  async function applySpeakerSettings() {
    const videos = document.querySelectorAll('.tile video');
    for (const v of videos) {
      if (v.closest('#tile-self')) continue; // 自己的画面始终静音，避免听到自己麦克风回声
      v.muted = speakerMuted;
      // 关闭→开启扬声器时，unmuted 播放可能被自动播放策略拦截，需在用户手势里重试
      if (!speakerMuted && v.srcObject && v.srcObject.getAudioTracks().length && v.paused) {
        v.play().catch(() => {});
      }
      if (!speakerMuted && v.setSinkId) {
        // 未选设备（''）时恢复跟随系统默认
        try { await v.setSinkId(speakerDeviceId || ''); } catch (e) { /* 设备不可用 */ }
      }
    }
    btnSpeaker.classList.toggle('off', speakerMuted);
    btnSpeaker.querySelector('.label').textContent = '扬声器';
  }

  function openSpeakerPop() {
    setExpanded(btnSpeaker, true);
    const pop = mountAudioPop(btnSpeaker, 'speaker');

    const renderMain = () => {
      pop.innerHTML = `
        <div class="ap-item" data-action="devices">${AP_ICONS.speaker}<span>选择扬声器</span></div>
        <div class="ap-item ${speakerMuted ? 'danger' : ''}" data-action="toggle">${speakerMuted ? AP_ICONS.speakerOff : AP_ICONS.speaker}<span>${speakerMuted ? '开启扬声器' : '关闭扬声器'}</span></div>`;
      positionAudioPop(btnSpeaker, pop);
    };
    const renderDevices = () => {
      pop.innerHTML = `
        <div class="ap-item" data-back="1">${AP_ICONS.back}<span>选择扬声器</span></div>
        <div class="ap-sep"></div>
        <div class="ap-devlist"><div class="ap-load">加载设备中…</div></div>`;
      positionAudioPop(btnSpeaker, pop);
      const list = pop.querySelector('.ap-devlist');
      navigator.mediaDevices.enumerateDevices()
        .then(devs => {
          const outs = devs.filter(d => d.kind === 'audiooutput');
          list.innerHTML = [
            `<div class="ap-item ${speakerDeviceId ? '' : 'sel'}" data-device="">${AP_ICONS.speaker}<span class="ap-name">跟随系统默认</span></div>`
          ].concat(outs.map((d, i) => `
            <div class="ap-item ${d.deviceId === speakerDeviceId ? 'sel' : ''}" data-device="${escapeHtml(d.deviceId)}">
              ${AP_ICONS.speaker}<span class="ap-name">${escapeHtml(d.label || ('扬声器 ' + (i + 1)))}</span>
            </div>`)).join('');
        })
        .catch(() => { list.innerHTML = '<div class="ap-load">无法读取设备列表</div>'; });
    };
    pop.addEventListener('click', (e) => {
      e.stopPropagation(); // 阻止冒泡到 document 的"点击外部关闭"，否则 innerHTML 重建会使目标脱离 DOM 被误判为外部点击
      const item = e.target.closest('.ap-item');
      if (!item) return;
      if (item.dataset.back) { renderMain(); return; }
      if (item.dataset.action === 'devices') { renderDevices(); return; }
      if (item.dataset.action === 'toggle') { closeAudioPop(); speakerMuted = !speakerMuted; applySpeakerSettings(); return; }
      if (item.dataset.device !== undefined) { closeAudioPop(); speakerDeviceId = item.dataset.device || null; applySpeakerSettings(); }
    });
    renderMain();
  }

  // ---------- 侧边面板（聊天 / 参会人 / 字幕） ----------
  const sidePanel = document.getElementById('sidePanel');
  const panelDot = document.getElementById('panelDot');
  let currentTab = 'chat';
  let panelUnread = 0;

  // 切换面板内标签内容
  function setPanelTab(tab) {
    currentTab = tab;
    sidePanel.querySelectorAll('.panel-tabs button').forEach(b => {
      b.classList.toggle('active', b.dataset.tab === tab);
    });
    chatPanel.style.display = tab === 'chat' ? '' : 'none';
    membersPanel.style.display = tab === 'members' ? '' : 'none';
    subtitlePanel.style.display = tab === 'subtitle' ? '' : 'none';
    chatInputRow.style.display = tab === 'chat' ? '' : 'none';
  }

  // 打开面板并切换到指定标签
  function openPanel(tab) {
    setPanelTab(tab);
    sidePanel.classList.add('open');
    btnPanel.classList.add('active');
    setExpanded(btnPanel, true);
    if (tab === 'chat') clearPanelUnread();
  }

  // 关闭面板
  function closePanel() {
    sidePanel.classList.remove('open');
    btnPanel.classList.remove('active');
    setExpanded(btnPanel, false);
  }

  // 清除未读提示（面板正在查看聊天时调用）
  function clearPanelUnread() {
    panelUnread = 0;
    panelDot.classList.remove('show');
  }

  // 面板按钮：点击开/关侧边栏（关闭后再次点击回到上次标签）
  btnPanel.addEventListener('click', () => {
    if (sidePanel.classList.contains('open')) closePanel();
    else openPanel(currentTab);
  });
  // 侧边栏内部标签：点击切换内容
  sidePanel.querySelectorAll('.panel-tabs button').forEach(b => {
    b.addEventListener('click', () => openPanel(b.dataset.tab));
  });

  function appendChat(name, text, ts, isSelf, opts = {}) {
    const item = document.createElement('div');
    item.className = 'chat-item' + (opts.isPrivate ? ' private' : '');
    let head;
    if (opts.isPrivate) {
      const label = isSelf
        ? `我 <span class="pv-arrow">→</span> ${escapeHtml(opts.targetName || '参会者')}`
        : escapeHtml(name);
      head = `<b class="pv-name">${label}</b><span class="pv-tag">私聊</span>`;
    } else {
      head = `<b style="${isSelf ? 'color:#7aa5ff' : ''}">${escapeHtml(name)}</b>`;
    }
    item.innerHTML = `
      <div class="c-head">${head}${fmtTime(ts)}</div>
      <div class="c-body">${escapeHtml(text).replace(/\n/g, '<br>')}</div>`;
    chatPanel.appendChild(item);
    chatPanel.scrollTop = chatPanel.scrollHeight;
    // 当前未在查看聊天时，标记面板按钮未读
    if (!(sidePanel.classList.contains('open') && currentTab === 'chat')) {
      panelUnread++;
      panelDot.classList.add('show');
    }
  }

  // 刷新私聊对象下拉（保留当前选择）
  function refreshChatTargets() {
    const sel = document.getElementById('chatTarget');
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">所有人</option>' +
      [...peers.values()].map(p => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join('');
    sel.value = (cur && peers.has(cur)) ? cur : '';
  }

  function sendChat() {
    const input = document.getElementById('chatInput');
    const text = input.value.trim();
    if (!text) return;
    const sel = document.getElementById('chatTarget');
    const targetId = sel ? sel.value : '';
    const msg = { type: 'chat', text };
    if (targetId) msg.targetId = targetId; // 私聊
    sendWS(msg);
    input.value = '';
  }
  document.getElementById('chatSend').addEventListener('click', sendChat);
  document.getElementById('chatInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendChat();
  });

  // 主持人操作按钮图标（转让 / 静音 / 移出）
  const HOST_ACT_SVGS = {
    promote: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8l4 4 5-7 5 7 4-4v9H3z"/><line x1="4" y1="20" x2="20" y2="20"/></svg>',
    mute: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="1" y1="1" x2="23" y2="23"/><path d="M9 9v3a3 3 0 005.12 2.12M15 9.34V4a3 3 0 00-5.94-.6"/><path d="M17 16.95A7 7 0 0119 10v-2m-7 7a7 7 0 01-7-7v-2"/></svg>',
    kick: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="17" y1="8" x2="22" y2="13"/><line x1="22" y1="8" x2="17" y2="13"/></svg>'
  };

  function updateMembersPanel() {
    const ownerBadge = '<span class="host-badge owner">创建者</span>';
    const hostBadge = '<span class="host-badge">主持人</span>';
    const myBadge = isOwner ? ownerBadge : (isHost ? hostBadge : '');
    const items = [];

    // 管理工具条（创建者/主持人可见）
    const disbandBtn = isOwner
      ? '<button class="t-btn danger" id="tbDisband" title="结束并解散会议">' + TOOL_SVGS.ban + '<span class="tl">解散会议</span></button>'
      : '';
    if (isOwner || isHost) {
      items.push(`
        <div class="m-toolbar">
          <button class="t-btn ${muteLocked ? 'on' : ''}" id="tbMuteLock" title="禁止成员自行解除静音">
            ${TOOL_SVGS.lock}${muteLocked ? '已开' : ''}<span class="tl">禁解除静音</span>
          </button>
          <button class="t-btn" id="tbMuteAll" title="一键全员静音">${TOOL_SVGS.muteAll}<span class="tl">全员静音</span></button>
          <button class="t-btn ${roomLocked ? 'on' : ''}" id="tbRoomLock" title="锁定会议，禁止新成员加入">
            ${TOOL_SVGS.lock}${roomLocked ? '已开' : ''}<span class="tl">锁定会议</span>
          </button>
          ${disbandBtn}
        </div>`);
    }

    items.push(`
      <div class="member-item">
        <div class="m-avatar" style="background:${colorFromString(myName)}">${escapeHtml(firstChar(myName))}</div>
        <div class="m-name">${myBadge}<span class="m-nm">${escapeHtml(myName)}（我）</span></div>
        <div class="m-state">${micOn ? '' : '· 静音'}${sharing ? ' · 共享中' : ''}</div>
      </div>`);
    for (const peer of peers.values()) {
      const pIsOwner = peer.id === ownerId;
      const pIsHost = peer.id === hostId;
      const badge = pIsOwner ? ownerBadge : (pIsHost ? hostBadge : '');
      let acts = '';
      if (isOwner && !pIsOwner) {
        // 创建者：可管理除自己外的所有人（含主持人）
        acts = memberActs(peer, pIsHost);
      } else if (isHost && !isOwner && !pIsOwner && !pIsHost) {
        // 主持人：仅可管理普通成员
        acts = memberActs(peer, false);
      }
      items.push(`
        <div class="member-item">
          <div class="m-avatar" style="background:${colorFromString(peer.name)}">${escapeHtml(firstChar(peer.name))}</div>
          <div class="m-name">${badge}<span class="m-nm">${escapeHtml(peer.name)}</span></div>
          <div class="m-state">${peer.audio ? '' : '· 静音'}${peer.screen ? ' · 共享中' : ''}</div>
          ${acts}
        </div>`);
    }
    membersPanel.innerHTML = items.join('') + `<div style="color:#6b7387;font-size:12px;padding:10px 8px">共 ${items.length - (isOwner || isHost ? 1 : 0)} 人 · SFU 媒体服务器转发${isOwner ? ' · 你是创建者' : (isHost ? ' · 你是主持人' : '')}</div>`;
    document.getElementById('roomInfo').textContent = `${items.length - (isOwner || isHost ? 1 : 0)} 人 · 你：${myName}`;

    // 操作按钮事件
    membersPanel.querySelectorAll('.m-act').forEach(btn => {
      btn.addEventListener('click', () => hostAction(btn.dataset.act, btn.dataset.peer, btn.dataset.name));
    });
    // 管理工具条事件
    const tbMuteAll = document.getElementById('tbMuteAll');
    if (tbMuteAll) tbMuteAll.addEventListener('click', () => {
      sendWS({ type: 'mute-all' });
      toast('已全员静音', 'ok');
    });
    const tbMuteLock = document.getElementById('tbMuteLock');
    if (tbMuteLock) tbMuteLock.addEventListener('click', () => {
      sendWS({ type: 'set-mute-lock', lock: !muteLocked });
    });
    const tbRoomLock = document.getElementById('tbRoomLock');
    if (tbRoomLock) tbRoomLock.addEventListener('click', () => {
      sendWS({ type: 'set-room-lock', lock: !roomLocked });
    });
    const tbDisband = document.getElementById('tbDisband');
    if (tbDisband) tbDisband.addEventListener('click', () => {
      if (confirm('确定解散本场会议？所有成员将被移出且无法重进。')) {
        sendWS({ type: 'disband' });
        if (localStream) localStream.getTracks().forEach(t => t.stop());
        if (screenStream) screenStream.getTracks().forEach(t => t.stop());
        if (rec) stopRecording();
        showMeetingOverOverlay('你已解散本场会议', '会议已结束', '#e8b447');
      }
    });
    refreshChatTargets();
  }

  // 管理按钮组（promote 仅对普通成员显示；纯图标按钮需 aria-label 供屏幕阅读器使用）
  function memberActs(peer, isHostTarget) {
    const promote = isHostTarget ? '' : `<button class="m-act promote" data-act="promote" data-peer="${peer.id}" data-name="${escapeHtml(peer.name)}" title="设为主持人" aria-label="将 ${escapeHtml(peer.name)} 设为主持人">${HOST_ACT_SVGS.promote}</button>`;
    return `<div class="m-actions">
      ${promote}
      <button class="m-act mute" data-act="mute" data-peer="${peer.id}" data-name="${escapeHtml(peer.name)}" title="静音" aria-label="静音 ${escapeHtml(peer.name)}">${HOST_ACT_SVGS.mute}</button>
      <button class="m-act kick" data-act="kick" data-peer="${peer.id}" data-name="${escapeHtml(peer.name)}" title="移出会议" aria-label="将 ${escapeHtml(peer.name)} 移出会议">${HOST_ACT_SVGS.kick}</button>
    </div>`;
  }

  // 管理工具条图标
  const TOOL_SVGS = {
    lock: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0110 0v4"/></svg>',
    muteAll: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="1" y1="1" x2="23" y2="23"/><path d="M9 9v3a3 3 0 005.12 2.12M15 9.34V4a3 3 0 00-5.94-.6"/><path d="M17 16.95A7 7 0 0119 10v-2m-7 7a7 7 0 01-7-7v-2"/></svg>',
    ban: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>'
  };

  // ---------- 主持人管理 ----------
  function hostAction(act, peerId, name) {
    if (!(isOwner || isHost)) return;
    if (act === 'promote') {
      sendWS({ type: 'set-host', targetId: peerId });
    } else if (act === 'kick') {
      if (!confirm(`确定将 ${name} 移出会议？`)) return;
      sendWS({ type: 'kick', targetId: peerId });
    } else if (act === 'mute') {
      sendWS({ type: 'mute-remote', targetId: peerId });
      toast(`已静音 ${name}`);
    }
  }

  // 离开/被踢/会议结束：断开 LiveKit 媒体连接并释放本地媒体
  function leaveLiveKit() {
    if (lkRoom) {
      try { lkRoom.disconnect(); } catch { /* 忽略 */ }
      lkRoom = null; lkLocal = null; lkConnected = false;
    }
    if (localStream) localStream.getTracks().forEach(t => t.stop());
    if (screenStream) screenStream.getTracks().forEach(t => t.stop());
  }

  // 被主持人移出：停止本地媒体与录制，全屏提示后返回首页
  function handleKicked(msg) {
    try { if (ws) ws.close(); } catch { /* 忽略 */ }
    leaveLiveKit();
    if (rec) stopRecording();
    showMeetingOverOverlay(msg.message || '你已被移出会议', '已移出会议', '#e35d5d');
  }

  // 会议被解散/创建者离开：停止一切后跳回首页
  function handleMeetingEnded(msg) {
    try { if (ws) ws.close(); } catch { /* 忽略 */ }
    leaveLiveKit();
    if (rec) stopRecording();
    showMeetingOverOverlay(msg.message || '会议已结束', '会议已结束', '#e8b447');
  }

  // 全屏结束/被踢提示（2.4 秒后返回首页）
  function showMeetingOverOverlay(sub, title, color) {
    let ov = document.getElementById('kickedOverlay');
    if (!ov) {
      ov = document.createElement('div');
      ov.id = 'kickedOverlay';
      document.body.appendChild(ov);
    }
    ov.innerHTML = `<div class="k-title" style="color:${color}">${escapeHtml(title)}</div>
      <div class="k-sub">${escapeHtml(sub)}</div>`;
    setTimeout(() => location.href = '/', 2400);
  }

  // ---------- 分享会议 ----------
  function copyText(text, msg) {
    const done = () => toast(msg || '已复制', 'ok');
    const fallback = () => {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); done(); }
      catch { toast('复制失败，请手动选择复制', 'error'); }
      document.body.removeChild(ta);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(fallback);
    } else fallback();
  }

  async function openShare() {
    // 分享链接只带会议号、不带昵称：被邀请者通过主页输入自己的昵称加入，避免复用分享者名字
    const localLink = `${location.origin}/?room=${encodeURIComponent(roomId)}`;
    let lanLinks = [];
    try {
      const resp = await fetch('/api/share?room=' + encodeURIComponent(roomId));
      if (resp.ok) lanLinks = (await resp.json()).lanLinks || [];
    } catch { /* 局域网地址获取失败不影响分享 */ }

    const lanText = lanLinks.length
      ? lanLinks.map((u, i) => `局域网${lanLinks.length > 1 ? (i + 1) : ''}链接：${u}`).join('\n')
      : '（局域网设备可用 https://本机IP:3001 打开后输入会议号加入）';

    const invite = [
      `我在「轻会议 LiteMeet」发起会议，诚邀你加入`,
      `会议号：${roomId}`,
      `点击链接加入会议：${localLink}`,
      `（打开后输入你的昵称即可加入）`,
      lanText
    ].join('\n');

    let ov = document.getElementById('shareOverlay');
    if (!ov) {
      ov = document.createElement('div');
      ov.id = 'shareOverlay';
      document.body.appendChild(ov);
    }
    ov.innerHTML = `
      <div class="share-card">
        <div class="share-head" id="shareTitle">分享会议<button class="share-close" id="shareClose" aria-label="关闭">×</button></div>
        <div class="share-room-row">
          <span class="share-room-label">会议号</span>
          <span class="share-room">${escapeHtml(roomId)}</span>
          <button class="share-btn" id="shareCopyRoom">复制</button>
        </div>
        <div class="share-tip">把下面的邀请信息发给要参会的人，点击链接或用会议号即可加入</div>
        <textarea class="share-area" id="shareArea" readonly spellcheck="false">${escapeHtml(invite)}</textarea>
        <button class="share-btn primary" id="shareCopyAll">复制邀请信息</button>
      </div>`;
    const close = setupDialog(ov, {
      labelledBy: 'shareTitle',
      focusEl: () => { const ta = document.getElementById('shareArea'); if (ta) ta.select(); return ta; }
    });
    document.getElementById('shareClose').addEventListener('click', close);
    ov.addEventListener('click', e => { if (e.target === ov) close(); });
    document.getElementById('shareCopyRoom').addEventListener('click', () => copyText(roomId, '会议号已复制'));
    document.getElementById('shareCopyAll').addEventListener('click', () => copyText(invite, '邀请信息已复制，去发给参会人吧'));
  }

  document.getElementById('btnShare').addEventListener('click', openShare);

  // ---------- 修改昵称 ----------
  function openRenameModal() {
    let ov = document.getElementById('renameOverlay');
    if (!ov) {
      ov = document.createElement('div');
      ov.id = 'renameOverlay';
      document.body.appendChild(ov);
    }
    ov.innerHTML = `
      <div class="share-card">
        <div class="share-head" id="renameTitle">修改昵称<button class="share-close" id="renameClose" aria-label="关闭">×</button></div>
        <div class="share-room-row">
          <span class="share-room-label">昵称</span>
          <input class="rename-input" id="renameInput" maxlength="32" value="${escapeHtml(myName)}" spellcheck="false">
        </div>
        <div class="share-tip">修改后其他参会人会看到你的新昵称</div>
        <button class="share-btn primary" id="renameOk">确定</button>
      </div>`;
    const close = setupDialog(ov, { labelledBy: 'renameTitle', focusEl: () => document.getElementById('renameInput') });
    document.getElementById('renameClose').addEventListener('click', close);
    ov.addEventListener('click', e => { if (e.target === ov) close(); });
    const input = document.getElementById('renameInput');
    input.select();
    const confirmRename = () => {
      const name = input.value.trim();
      if (!name) { toast('昵称不能为空', 'error'); input.focus(); return; }
      close();
      if (name === myName) return;
      // 乐观更新本地显示（服务端广播回来时再次同步，保证一致）
      myName = name;
      setUserName(myName);
      updateSelfTag();
      updateMembersPanel();
      sendWS({ type: 'rename', name });
      toast('昵称已更新为 ' + name, 'ok');
    };
    document.getElementById('renameOk').addEventListener('click', confirmRename);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') confirmRename(); });
  }
  document.getElementById('btnRename').addEventListener('click', openRenameModal);

  // ---------- 离开 ----------
  document.getElementById('btnLeave').addEventListener('click', async () => {
    if (isOwner) {
      // 创建者离开 => 会议解散（服务端对所有人广播结束）
      const ok = confirm('你是本场会议的创建者，离开将解散会议并让所有成员退出。确定离开并结束会议？');
      if (!ok) return;
      sendWS({ type: 'disband' });
    } else {
      sendWS({ type: 'leave' });
    }
    let recId = null;
    if (rec) recId = await stopRecording();
    try { if (ws) ws.close(); } catch { /* 忽略 */ }
    leaveLiveKit();
    if (recId) {
      location.href = '/record.html?id=' + recId + '&fresh=1';
    } else {
      location.href = '/';
    }
  });

  window.addEventListener('beforeunload', () => {
    sendWS({ type: 'leave' });
    leaveLiveKit();
    if (rec) {
      // 浏览器关闭：尽力保存
      stopRecording();
    }
  });

  // ---------- 会议计时 ----------
  setInterval(() => {
    document.getElementById('meetingTimer').textContent = fmtDuration(Math.round((Date.now() - meetingStart) / 1000));
    updateMeetingMeta();
  }, 1000);

  // 会议人数 / 剩余时长指示（含到期提醒策略）
  function startMetaTimer() {
    if (metaTimer) return;
    metaTimer = setInterval(updateMeetingMeta, 1000);
    updateMeetingMeta();
  }
  function updateMeetingMeta() {
    const el = document.getElementById('meetingMeta');
    if (!el) return;
    const count = peers.size + 1; // 自己在内
    let txt = `人数 ${count}`;
    if (meetingMaxPeers > 0) txt += `/${meetingMaxPeers}`;
    if (meetingDeadline > 0) {
      const remain = Math.floor((meetingDeadline - Date.now()) / 1000);
      if (remain > 0) {
        txt += ` · 剩余 ${fmtDuration(remain)}`;
      } else {
        txt += ' · 已达设定时长';
        if (meetingOnExpire === 'remind' && !remindShown) {
          remindShown = true;
          toast('已达设定时长，如需继续请创建者另行创建新会议', 'ok');
        }
      }
    }
    el.textContent = txt;
  }

  // 记录"我参加过的会议"到本机（首页"最近会议"展示，无需点录制）
  function saveMeetingHistory() {
    try {
      let hist = [];
      try { hist = JSON.parse(localStorage.getItem('litemeet.history') || '[]'); } catch { /* 忽略 */ }
      if (!Array.isArray(hist)) hist = [];
      hist = hist.filter(h => h && h.roomId !== roomId);
      hist.unshift({ roomId, name: myName, joinedAt: Date.now() });
      localStorage.setItem('litemeet.history', JSON.stringify(hist.slice(0, 50)));
    } catch { /* 忽略 */ }
  }

  // ---------- 启动 ----------
  // 非安全上下文（如通过局域网 IP 的 http 访问）：浏览器禁止摄像头/麦克风/屏幕共享
  function showInsecureBanner() {
    const banner = document.createElement('div');
    banner.style.cssText = [
      'position:fixed', 'top:64px', 'left:50%', 'transform:translateX(-50%)',
      'z-index:300', 'background:#b3353a', 'color:#fff',
      'padding:14px 22px', 'border-radius:12px', 'font-size:13px', 'line-height:1.7',
      'max-width:92%', 'text-align:center',
      'box-shadow:0 8px 30px rgba(0,0,0,.4)'
    ].join(';');
    banner.innerHTML =
      '当前通过非安全地址访问，浏览器已禁用摄像头 / 麦克风 / 屏幕共享。<br>' +
      '本机请改用 <a href="http://localhost:5678" style="color:#ffd7d9">http://localhost:5678</a>；' +
      '其他设备请使用服务器控制台显示的 <b>https://…:5679</b> 地址（首次访问点击"高级→继续前往"信任证书）。';
    const close = document.createElement('div');
    close.textContent = '×';
    close.style.cssText = 'position:absolute;top:4px;right:10px;cursor:pointer;font-size:16px;opacity:.8';
    close.addEventListener('click', () => banner.remove());
    banner.appendChild(close);
    document.body.appendChild(banner);
  }

  // 启动：先建立业务信令立即入会，设备获取异步进行（信令与媒体解耦，避免权限弹窗阻塞入会）
  async function init() {
    if (!window.isSecureContext) {
      showInsecureBanner();
    }
    // 初始按钮状态（默认静音 + 默认关摄像头）
    btnMic.classList.toggle('off', !micOn);
    btnMic.querySelector('.label').textContent = '麦克风';
    setPressed(btnMic, micOn);
    btnCam.classList.toggle('off', !camOn);
    btnCam.querySelector('.label').textContent = camOn ? '视频' : '开启视频';
    setPressed(btnCam, camOn);

    connectWS();
    updateMembersPanel();
    openPanel('chat');
    acquireDevices(); // 异步获取设备，不阻塞入会
  }

  // 获取本地音视频设备（10 秒超时兜底：设备被占用 / 权限弹窗未响应时以旁听模式进入，之后可会中再开）
  async function acquireDevices() {
    const gUM = (constraints) => new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new DOMException('获取设备超时', 'TimeoutError')), 10000);
      navigator.mediaDevices.getUserMedia(constraints).then(
        (s) => { clearTimeout(timer); resolve(s); },
        (e) => { clearTimeout(timer); reject(e); }
      );
    });
    try {
      localStream = await gUM({ audio: true, video: initialVideo });
    } catch (e) {
      try {
        localStream = await gUM({ audio: true });
        camOn = false;
        toast('未获取到摄像头，已以音频模式加入（之后可点击"开启视频"重新尝试）');
      } catch (e2) {
        localStream = null;
        micOn = false;
        camOn = false;
        toast('未获取到麦克风/摄像头，以旁听模式加入：' + deviceErrMsg(e2));
      }
    }
    if (localStream) {
      localStream.getAudioTracks().forEach(t => (t.enabled = micOn));
      localStream.getVideoTracks().forEach(t => (t.enabled = camOn));
      camTrack = localStream.getVideoTracks()[0] || null;
      if (!camTrack) camOn = false;
    }
    // 设备就绪后刷新按钮状态与贴片；若 SFU 已连上则补推本地轨道（入会早于设备就绪的场景）
    btnMic.classList.toggle('off', !micOn);
    setPressed(btnMic, micOn);
    btnCam.classList.toggle('off', !camOn);
    btnCam.querySelector('.label').textContent = camOn ? '视频' : '开启视频';
    setPressed(btnCam, camOn);
    renderSelfTile();
    if (lkConnected && lkLocal) {
      await publishInitialTracks();
    }
  }

  // 自动播放策略兜底：任意点击后重试播放被暂停的远端视频（含音频）
  document.addEventListener('click', () => {
    document.querySelectorAll('.tile video').forEach(v => {
      if (v.paused) v.play().catch(() => {});
    });
  });

  // ---------- 全屏：双击画面 / 画面右上角按钮 / 工具栏全屏按钮 ----------
  // 浏览器不支持 Element.requestFullscreen（如 iOS Safari）时，回退到应用内伪全屏
  let pseudoFsEl = null;
  let fsRequesting = false; // 原生全屏请求在途标志：防重入，避免连点触发二次请求被拒而误叠加伪全屏

  function syncFsButton(on) {
    const btn = document.getElementById('btnFullscreen');
    if (!btn) return;
    btn.classList.toggle('active', on);
    setPressed(btn, on);
    btn.title = on ? '退出全屏' : '全屏';
    btn.setAttribute('aria-label', on ? '退出全屏' : '全屏');
  }

  function enterPseudoFullscreen(el) {
    if (pseudoFsEl === el) return;
    exitPseudoFullscreen();
    pseudoFsEl = el;
    if (el.classList && el.classList.contains('tile')) {
      el.classList.add('pseudo-fs');
      const fsBtn = el.querySelector('.tile-fs');
      if (fsBtn) { fsBtn.setAttribute('aria-label', '退出全屏'); fsBtn.title = '退出全屏'; }
      // 伪全屏固定完整显示，"铺满/适应"切换无意义，先隐藏
      const fitBtn = el.querySelector('.tile-fit');
      if (fitBtn) fitBtn.style.display = 'none';
    } else {
      document.body.classList.add('pseudo-fs-app');
    }
    syncFsButton(true);
  }

  function exitPseudoFullscreen() {
    if (!pseudoFsEl) return;
    const el = pseudoFsEl;
    if (el.classList && el.classList.contains('tile')) {
      el.classList.remove('pseudo-fs');
      const fsBtn = el.querySelector('.tile-fs');
      if (fsBtn) { fsBtn.setAttribute('aria-label', '全屏'); fsBtn.title = '全屏'; }
      const fitBtn = el.querySelector('.tile-fit');
      if (fitBtn) fitBtn.style.display = '';
    } else {
      document.body.classList.remove('pseudo-fs-app');
    }
    pseudoFsEl = null;
    syncFsButton(false);
  }

  function enterFullscreen(el) {
    // 防重入：在途请求未落定时忽略新的进入请求，避免连点/双击触发两次 requestFullscreen，
    // 第二次请求被浏览器拒绝(AbortError)而误回退到伪全屏，导致真实全屏退出失效。
    if (fsRequesting || document.fullscreenElement === el || document.webkitFullscreenElement === el) return;
    const req = el.requestFullscreen || el.webkitRequestFullscreen;
    if (req) {
      fsRequesting = true;
      try {
        const p = req.call(el);
        if (p && p.catch) {
          p.finally(() => { fsRequesting = false; });
          // 仅当确实未进入原生全屏时才回退伪全屏
          p.catch(() => { if (!document.fullscreenElement) enterPseudoFullscreen(el); });
          return;
        }
        fsRequesting = false;
        return;
      } catch { /* 走到下方伪全屏 */ }
      fsRequesting = false;
    }
    enterPseudoFullscreen(el);
  }
  function exitFullscreen() {
    // 先清理伪全屏，再无条件发起原生退出：
    // 双击进入全屏时可能叠加了伪全屏，若只清伪全屏就 return，真实全屏会退不出去（只能 ESC）。
    if (pseudoFsEl) exitPseudoFullscreen();
    const ex = document.exitFullscreen || document.webkitExitFullscreen;
    if (ex) {
      try {
        const p = ex.call(document);
        if (p && p.catch) p.catch(() => {});
      } catch { /* 忽略 */ }
    }
  }
  // 已全屏则退出，否则进入（画面全屏可点击画面自身再次退出）
  function toggleFullscreen(el) {
    const isFs = document.fullscreenElement === el
      || document.webkitFullscreenElement === el
      || pseudoFsEl === el;
    if (isFs) exitFullscreen();
    else enterFullscreen(el);
  }
  // 双击视频画面：进入/退出全屏（点画面按钮时不触发双击）
  document.addEventListener('dblclick', (e) => {
    const tile = e.target.closest('.tile');
    if (tile && !e.target.closest('.tile-fs, .tile-fit')) toggleFullscreen(tile);
  });
  // 画面右上角全屏按钮：进入/退出全屏
  document.addEventListener('click', (e) => {
    const fsBtn = e.target.closest('.tile-fs');
    if (fsBtn) {
      const tile = fsBtn.closest('.tile');
      if (tile) toggleFullscreen(tile);
    }
  });
  // 共享画面"铺满/适应"切换按钮
  document.addEventListener('click', (e) => {
    const fitBtn = e.target.closest('.tile-fit');
    if (fitBtn) {
      const tile = fitBtn.closest('.tile');
      if (!tile) return;
      const cover = tile.classList.toggle('fit-cover');
      fitBtn.title = cover ? '适应屏幕' : '铺满屏幕';
      fitBtn.setAttribute('aria-label', cover ? '适应屏幕' : '铺满屏幕');
      fitBtn.innerHTML = cover ? FIT_ICON : COVER_ICON;
    }
  });
  // 工具栏全屏按钮：整个应用全屏
  document.getElementById('btnFullscreen').addEventListener('click', () => {
    if (pseudoFsEl || document.fullscreenElement) exitFullscreen();
    else enterFullscreen(document.documentElement);
  });
  document.addEventListener('fullscreenchange', () => {
    fsRequesting = false; // 兜底：全屏状态一旦变化即清除在途标志
    const on = !!document.fullscreenElement;
    syncFsButton(on);
    document.querySelectorAll('.tile-fs').forEach(b => {
      const t = b.closest('.tile');
      const active = on && t === document.fullscreenElement;
      b.setAttribute('aria-label', active ? '退出全屏' : '全屏');
      b.title = active ? '退出全屏' : '全屏';
    });
  });
  // ESC 退出伪全屏（桌面键鼠 / 外接键盘兜底）
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && pseudoFsEl) exitPseudoFullscreen();
  });

  // 调试钩子：控制台查看连接与轨道状态
  window.__LM_DEBUG__ = () => ({
    selfId,
    ownerId, isOwner,
    hostId, isHost,
    roomLocked, muteLocked,
    micOn, camOn, sharing,
    lkConnected,
    localTracks: localStream ? localStream.getTracks().map(t => t.kind + ':' + t.readyState) : [],
    localPubs: lkLocal ? [...lkLocal.trackPublications.values()].map(x => `${x.source}:${x.isMuted ? 'muted' : 'live'}`) : [],
    peers: [...peers.values()].map(p => ({
      name: p.name,
      remoteTracks: p.stream ? p.stream.getTracks().map(t => `${t.kind}:${t.readyState}${t.muted ? '(muted)' : ''}`) : [],
      screenTracks: p.screenStream ? p.screenStream.getTracks().map(t => t.kind) : []
    }))
  });

  init();
}
