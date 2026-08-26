package com.litemeet.backend.store;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;
import java.util.List;

/**
 * 会议生命周期存储（MySQL）：记录每次会议的结束事实。
 * 作为"会议号已结束"的权威数据源——Redis 负责时效性（TTL 自动释放会议号），
 * MySQL 负责持久性（服务器重启后仍能从磁盘恢复未过期的已结束会议号）。
 */
@Component
public class MeetingStore {

    private static final Logger log = LoggerFactory.getLogger(MeetingStore.class);

    private final JdbcTemplate jdbc;
    private final long ttlSeconds;

    public MeetingStore(DataSource ds, @Value("${litemeet.ended-room-ttl:86400}") long ttlSeconds) {
        this.jdbc = new JdbcTemplate(ds);
        this.ttlSeconds = ttlSeconds;
        initSchema();
        log.info("MySQL 会议记录表已就绪（ended-room-ttl={}s）", ttlSeconds);
    }

    private void initSchema() {
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS meetings (
              id          BIGINT AUTO_INCREMENT PRIMARY KEY,
              room_id     VARCHAR(16)  NOT NULL,
              status      VARCHAR(16)  NOT NULL DEFAULT 'ended',
              created_at  BIGINT       NOT NULL,
              ended_at    BIGINT       NOT NULL,
              ended_by    VARCHAR(64)  NOT NULL DEFAULT '',
              ended_reason VARCHAR(32) NOT NULL DEFAULT '',
              KEY idx_room_id (room_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """);
    }

    /** 记录一次会议结束（解散 / 创建者离开），用于会议号时效性判断 */
    public void markRoomEnded(String roomId, String endedBy, String reason) {
        if (roomId == null || roomId.isEmpty()) return;
        long now = System.currentTimeMillis();
        try {
            jdbc.update("""
                INSERT INTO meetings (room_id, status, created_at, ended_at, ended_by, ended_reason)
                VALUES (?, 'ended', ?, ?, ?, ?)
                """, roomId, now, now, endedBy == null ? "" : endedBy, reason == null ? "" : reason);
        } catch (Exception e) {
            log.warn("记录会议结束到 MySQL 失败: {}", e.getMessage());
        }
    }

    /** 查询失效期内结束过的会议号（服务器重启后恢复到 Redis） */
    public List<String> loadRecentlyEndedRooms() {
        long since = System.currentTimeMillis() - ttlSeconds * 1000;
        try {
            return jdbc.queryForList(
                    "SELECT DISTINCT room_id FROM meetings WHERE status = 'ended' AND ended_at >= ?",
                    String.class, since);
        } catch (Exception e) {
            log.warn("查询已结束会议号失败: {}", e.getMessage());
            return List.of();
        }
    }

    /** 清除该会议号的已结束记录（会议号被重新创建为新会议时调用，避免重启后误恢复） */
    public void clearEnded(String roomId) {
        if (roomId == null || roomId.isEmpty()) return;
        try {
            jdbc.update("DELETE FROM meetings WHERE room_id = ? AND status = 'ended'", roomId);
        } catch (Exception e) {
            log.warn("清除已结束会议记录失败: {}", e.getMessage());
        }
    }
}
