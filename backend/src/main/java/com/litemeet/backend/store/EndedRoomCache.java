package com.litemeet.backend.store;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.List;

/**
 * 已结束会议号缓存（Redis）：
 * key = ended:room:{roomId}，写入时带 TTL（ended-room-ttl）。
 * TTL 到期后 Redis 自动删除该 key => 会议号自动释放、可复用，
 * 彻底避免"已结束会议号被永久占用，未来随机碰撞后无法使用"的问题。
 */
@Component
public class EndedRoomCache {

    private static final Logger log = LoggerFactory.getLogger(EndedRoomCache.class);
    private static final String KEY_PREFIX = "ended:room:";

    private final StringRedisTemplate redis;
    private final long ttlSeconds;

    public EndedRoomCache(StringRedisTemplate redis,
                          @Value("${litemeet.ended-room-ttl:86400}") long ttlSeconds) {
        this.redis = redis;
        this.ttlSeconds = ttlSeconds;
    }

    /** 会议号是否处于"已结束"失效期（未过期） */
    public boolean isEnded(String roomId) {
        try {
            return Boolean.TRUE.equals(redis.hasKey(KEY_PREFIX + roomId));
        } catch (Exception e) {
            log.warn("Redis 查询失败（按未结束处理）: {}", e.getMessage());
            return false;
        }
    }

    /** 标记会议号已结束，写入 TTL（到期自动释放） */
    public void markEnded(String roomId) {
        if (roomId == null || roomId.isEmpty()) return;
        try {
            redis.opsForValue().set(KEY_PREFIX + roomId, "1", Duration.ofSeconds(ttlSeconds));
        } catch (Exception e) {
            log.warn("Redis 写入失败: {}", e.getMessage());
        }
    }

    /** 清除已结束标记（会议号被重新创建为新会议时调用，避免旧记录误拉黑） */
    public void clearEnded(String roomId) {
        if (roomId == null || roomId.isEmpty()) return;
        try {
            redis.delete(KEY_PREFIX + roomId);
        } catch (Exception e) {
            log.warn("Redis 删除失败: {}", e.getMessage());
        }
    }

    /** 服务器重启后：从 MySQL 恢复未过期的已结束会议号（重新带 TTL 写入） */
    public void restore(List<String> roomIds) {
        if (roomIds == null || roomIds.isEmpty()) return;
        int restored = 0;
        for (String roomId : roomIds) {
            String key = KEY_PREFIX + roomId;
            try {
                if (!Boolean.TRUE.equals(redis.hasKey(key))) {
                    redis.opsForValue().set(key, "1", Duration.ofSeconds(ttlSeconds));
                    restored++;
                }
            } catch (Exception e) {
                log.warn("恢复已结束会议号失败 {}: {}", roomId, e.getMessage());
            }
        }
        log.info("已从 MySQL 恢复 {} 个未过期的已结束会议号", restored);
    }
}
