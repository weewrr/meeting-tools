package com.litemeet.backend.store;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import javax.sql.DataSource;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.*;

/**
 * 会议业务数据存 MySQL 版存储层。
 * 录制元数据 / 实时转写 / AI 摘要 / 应用配置全部落库（InnoDB），
 * 音频 / 录屏等原始媒体文件仍存本机 data/audio 目录。
 * 对外 API 与旧 JsonStore 完全一致，REST 控制器无需改动逻辑。
 * 构成本类时自动建表（IF NOT EXISTS），幂等可重复执行。
 */
@Component
public class JdbcRecordStore {

    private static final Logger log = LoggerFactory.getLogger(JdbcRecordStore.class);
    private final ObjectMapper mapper = new ObjectMapper();
    private final JdbcTemplate jdbc;
    private final Path audioDir;

    private static final Map<String, Object> DEFAULT_TRANSCRIBE = Map.of(
            "baseUrl", "https://api.openai.com/v1",
            "apiKey", "",
            "model", "whisper-1",
            "language", "zh"
    );
    private static final Map<String, Object> DEFAULT_LLM = Map.of(
            "baseUrl", "https://api.openai.com/v1",
            "apiKey", "",
            "model", "gpt-4o-mini"
    );

    public JdbcRecordStore(DataSource ds, @Value("${litemeet.data-dir:data}") String dataDir) {
        this.jdbc = new JdbcTemplate(ds);
        this.audioDir = Path.of(dataDir).resolve("audio");
        ensureDirs();
        initSchema();
        log.info("MySQL 业务数据表已就绪（records / record_transcripts / record_summaries / settings）");
    }

    private void ensureDirs() {
        try {
            Files.createDirectories(audioDir);
        } catch (IOException e) {
            throw new RuntimeException("无法创建媒体目录: " + e.getMessage(), e);
        }
    }

    private void initSchema() {
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS records (
              id          VARCHAR(40)  NOT NULL PRIMARY KEY,
              title       VARCHAR(200) NOT NULL DEFAULT '',
              room_id     VARCHAR(16)  NOT NULL DEFAULT '',
              host        VARCHAR(128) NOT NULL DEFAULT '',
              owner_id    VARCHAR(64)  NOT NULL DEFAULT '',
              owner_name  VARCHAR(128) NOT NULL DEFAULT '',
              mode        VARCHAR(16)  NOT NULL DEFAULT 'audio',
              status      VARCHAR(16)  NOT NULL DEFAULT 'recording',
              created_at  BIGINT       NOT NULL,
              ended_at    BIGINT       NULL,
              duration    BIGINT       NOT NULL DEFAULT 0,
              audio_file  VARCHAR(255) NULL,
              video_file  VARCHAR(255) NULL,
              updated_at  BIGINT       NOT NULL DEFAULT 0,
              KEY idx_records_owner (owner_id),
              KEY idx_records_room (room_id),
              KEY idx_records_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """);
        // 幂等迁移：老库（无 updated_at 列）补列，失败说明已存在则忽略
        addColumnIfMissing("records", "updated_at", "updated_at BIGINT NOT NULL DEFAULT 0");
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS record_transcripts (
              id        BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
              record_id VARCHAR(40)  NOT NULL,
              ts        BIGINT       NOT NULL,
              offset_ms BIGINT       NOT NULL DEFAULT 0,
              content   TEXT,
              KEY idx_transcripts_record (record_id),
              CONSTRAINT fk_transcripts_record FOREIGN KEY (record_id)
                REFERENCES records(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """);
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS record_summaries (
              record_id    VARCHAR(40) NOT NULL PRIMARY KEY,
              content      MEDIUMTEXT,
              model        VARCHAR(128) NOT NULL DEFAULT '',
              generated_at BIGINT      NOT NULL,
              CONSTRAINT fk_summaries_record FOREIGN KEY (record_id)
                REFERENCES records(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """);
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS litemeet_settings (
              section VARCHAR(32) NOT NULL PRIMARY KEY,
              payload JSON       NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """);
    }

    /** 幂等补列：列不存在时执行 ALTER（表结构平滑升级，兼容已存在的旧表） */
    private void addColumnIfMissing(String table, String column, String ddl) {
        try {
            Integer cnt = jdbc.queryForObject(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()"
                            + " AND TABLE_NAME = ? AND COLUMN_NAME = ?",
                    Integer.class, table, column);
            if (cnt == null || cnt == 0) {
                jdbc.execute("ALTER TABLE " + table + " ADD COLUMN " + ddl);
            }
        } catch (Exception e) {
            log.warn("补列 {}.{} 失败: {}", table, column, e.getMessage());
        }
    }

    // ---------- 会议记录 ----------

    private final RowMapper<Map<String, Object>> recordRow = (rs, i) -> {
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("id", rs.getString("id"));
        r.put("title", rs.getString("title"));
        r.put("roomId", rs.getString("room_id"));
        r.put("host", rs.getString("host"));
        r.put("ownerId", rs.getString("owner_id"));
        r.put("ownerName", rs.getString("owner_name"));
        r.put("mode", rs.getString("mode"));
        r.put("status", rs.getString("status"));
        r.put("createdAt", iso(rs.getLong("created_at")));
        long ended = rs.getLong("ended_at");
        r.put("endedAt", rs.wasNull() ? null : iso(ended));
        r.put("duration", rs.getLong("duration"));
        r.put("audioFile", nullIfBlank(rs.getString("audio_file")));
        r.put("videoFile", nullIfBlank(rs.getString("video_file")));
        long updated = rs.getLong("updated_at");
        r.put("updatedAt", updated == 0 ? null : iso(updated));
        return r;
    };

    public List<Map<String, Object>> loadRecords() {
        List<Map<String, Object>> records = jdbc.query(
                "SELECT * FROM records ORDER BY created_at DESC, id DESC", recordRow);
        attachTranscripts(records);
        attachSummaries(records);
        return records;
    }

    @Transactional
    public Map<String, Object> createRecord(String title, String roomId, String host,
                                            String ownerId, String ownerName, String mode) {
        String id = "rec_" + Long.toString(System.currentTimeMillis(), 36) + randomSuffix();
        long now = System.currentTimeMillis();
        jdbc.update("""
                INSERT INTO records (id, title, room_id, host, owner_id, owner_name, mode, status, created_at, duration)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'recording', ?, 0)
                """,
                id,
                title == null || title.isEmpty() ? "未命名会议" : title,
                roomId == null ? "" : roomId,
                host == null ? "" : host,
                ownerId == null ? "" : ownerId,
                ownerName == null ? "" : ownerName,
                mode == null || mode.isEmpty() ? "audio" : mode,
                now);
        return getRecord(id);
    }

    public Map<String, Object> getRecord(String id) {
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT * FROM records WHERE id = ?", recordRow, id);
        if (rows.isEmpty()) return null;
        Map<String, Object> record = rows.get(0);
        record.put("transcript", loadTranscripts(id));
        record.put("summary", loadSummary(id));
        return record;
    }

    @Transactional
    public Map<String, Object> updateRecord(String id, Map<String, Object> patch) {
        Map<String, Object> current = getRecord(id);
        if (current == null) return null;
        if (patch == null || patch.isEmpty()) return current;

        List<String> sets = new ArrayList<>();
        List<Object> args = new ArrayList<>();
        addScalar(sets, args, patch, "title", "title", "STRING");
        addScalar(sets, args, patch, "status", "status", "STRING");
        addLong(sets, args, patch, "duration", "duration");
        addIso(sets, args, patch, "endedAt", "ended_at");
        addColumn(sets, args, patch, "audioFile", "audio_file");
        addColumn(sets, args, patch, "videoFile", "video_file");
        boolean dirty = false;
        if (!sets.isEmpty()) {
            args.add(id);
            jdbc.update("UPDATE records SET " + String.join(", ", sets)
                    + " WHERE id = ?", args.toArray());
            dirty = true;
        }

        if (patch.get("transcript") instanceof List<?> list) {
            replaceTranscript(id, list);
            dirty = true;
        }
        if (patch.containsKey("summary")) {
            replaceSummary(id, patch.get("summary"));
            dirty = true;
        }
        if (dirty) {
            jdbc.update("UPDATE records SET updated_at = ? WHERE id = ?",
                    System.currentTimeMillis(), id);
        }
        return getRecord(id);
    }

    @Transactional
    public Map<String, Object> appendTranscript(String id, Map<String, Object> entry) {
        if (getRecord(id) == null) return null;
        long ts = entry.get("t") instanceof Number n ? n.longValue() : System.currentTimeMillis();
        long offset = entry.get("offset") instanceof Number n2 ? n2.longValue() : 0L;
        String text = entry.get("text") == null ? "" : String.valueOf(entry.get("text"));
        jdbc.update("""
                INSERT INTO record_transcripts (record_id, ts, offset_ms, content)
                VALUES (?, ?, ?, ?)
                """, id, ts, offset, text);
        jdbc.update("UPDATE records SET updated_at = ? WHERE id = ?",
                System.currentTimeMillis(), id);
        return getRecord(id);
    }

    @Transactional
    public boolean deleteRecord(String id) {
        List<String> files = jdbc.query(
                "SELECT audio_file FROM records WHERE id = ?", (rs, i) -> rs.getString("audio_file"), id);
        if (files.isEmpty()) return false;
        jdbc.update("DELETE FROM records WHERE id = ?", id);
        String audioFile = files.get(0);
        if (audioFile != null && !audioFile.isBlank()) {
            try {
                Files.deleteIfExists(audioPath(audioFile));
            } catch (IOException ignored) { }
        }
        return true;
    }

    /** 防目录穿越：仅取文件名部分 */
    public Path audioPath(String name) {
        String safe = name == null ? "" : name;
        int idx = Math.max(safe.lastIndexOf('/'), safe.lastIndexOf('\\'));
        if (idx >= 0) safe = safe.substring(idx + 1);
        return audioDir.resolve(safe);
    }

    // ---------- 转写 / 摘要 ----------

    private List<Map<String, Object>> loadTranscripts(String recordId) {
        return jdbc.query("""
                SELECT ts, offset_ms, content FROM record_transcripts
                WHERE record_id = ? ORDER BY id
                """, (rs, i) -> {
            Map<String, Object> e = new LinkedHashMap<>();
            e.put("t", rs.getLong("ts"));
            e.put("offset", rs.getLong("offset_ms"));
            e.put("text", rs.getString("content"));
            return e;
        }, recordId);
    }

    private void replaceTranscript(String recordId, List<?> entries) {
        jdbc.update("DELETE FROM record_transcripts WHERE record_id = ?", recordId);
        for (Object o : entries) {
            if (o instanceof Map<?, ?> m) {
                long ts = m.get("t") instanceof Number n ? n.longValue() : System.currentTimeMillis();
                long offset = m.get("offset") instanceof Number n2 ? n2.longValue() : 0L;
                String text = m.get("text") == null ? "" : String.valueOf(m.get("text"));
                jdbc.update("""
                        INSERT INTO record_transcripts (record_id, ts, offset_ms, content)
                        VALUES (?, ?, ?, ?)
                        """, recordId, ts, offset, text);
            }
        }
    }

    private Map<String, Object> loadSummary(String recordId) {
        List<Map<String, Object>> rows = jdbc.query("""
                SELECT content, model, generated_at FROM record_summaries WHERE record_id = ?
                """, (rs, i) -> {
            Map<String, Object> s = new LinkedHashMap<>();
            s.put("content", rs.getString("content"));
            s.put("model", rs.getString("model"));
            s.put("generatedAt", iso(rs.getLong("generated_at")));
            return s;
        }, recordId);
        return rows.isEmpty() ? null : rows.get(0);
    }

    private void replaceSummary(String recordId, Object summary) {
        jdbc.update("DELETE FROM record_summaries WHERE record_id = ?", recordId);
        if (summary instanceof Map<?, ?> m) {
            long now = System.currentTimeMillis();
            Object generatedAt = m.get("generatedAt");
            if (generatedAt instanceof String s && !s.isBlank()) {
                try {
                    now = Instant.parse(s).toEpochMilli();
                } catch (Exception ignored) { }
            }
            jdbc.update("""
                    INSERT INTO record_summaries (record_id, content, model, generated_at)
                    VALUES (?, ?, ?, ?)
                    """, recordId, m.get("content") == null ? "" : String.valueOf(m.get("content")),
                    m.get("model") == null ? "" : String.valueOf(m.get("model")), now);
        }
    }

    private void attachTranscripts(List<Map<String, Object>> records) {
        if (records.isEmpty()) return;
        Map<String, List<Map<String, Object>>> byRecord = new LinkedHashMap<>();
        for (Map<String, Object> r : records) byRecord.put((String) r.get("id"), new ArrayList<>());
        jdbc.query("""
                SELECT record_id, ts, offset_ms, content FROM record_transcripts ORDER BY record_id, id
                """, rs -> {
            String rid = rs.getString("record_id");
            List<Map<String, Object>> list = byRecord.get(rid);
            if (list != null) {
                Map<String, Object> e = new LinkedHashMap<>();
                e.put("t", rs.getLong("ts"));
                e.put("offset", rs.getLong("offset_ms"));
                e.put("text", rs.getString("content"));
                list.add(e);
            }
        });
        for (Map<String, Object> r : records) r.put("transcript", byRecord.get(r.get("id")));
    }

    private void attachSummaries(List<Map<String, Object>> records) {
        if (records.isEmpty()) return;
        Map<String, Map<String, Object>> byRecord = new LinkedHashMap<>();
        for (Map<String, Object> r : records) byRecord.put((String) r.get("id"), null);
        jdbc.query("""
                SELECT record_id, content, model, generated_at FROM record_summaries
                """, rs -> {
            Map<String, Object> s = new LinkedHashMap<>();
            s.put("content", rs.getString("content"));
            s.put("model", rs.getString("model"));
            s.put("generatedAt", iso(rs.getLong("generated_at")));
            byRecord.put(rs.getString("record_id"), s);
        });
        for (Map<String, Object> r : records) r.put("summary", byRecord.get(r.get("id")));
    }

    // ---------- 配置（settings 表，JSON 列） ----------

    public Map<String, Object> loadConfig() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("transcribe", readSection("transcribe", DEFAULT_TRANSCRIBE));
        result.put("llm", readSection("llm", DEFAULT_LLM));
        return result;
    }

    public Map<String, Object> saveConfig(Map<String, Object> patch) {
        Map<String, Object> cfg = loadConfig();
        if (patch != null) {
            if (patch.get("transcribe") instanceof Map<?, ?> t) {
                ((Map<String, Object>) cfg.get("transcribe")).putAll((Map<? extends String, ?>) t);
                writeSection("transcribe", (Map<String, Object>) cfg.get("transcribe"));
            }
            if (patch.get("llm") instanceof Map<?, ?> l) {
                ((Map<String, Object>) cfg.get("llm")).putAll((Map<? extends String, ?>) l);
                writeSection("llm", (Map<String, Object>) cfg.get("llm"));
            }
        }
        return cfg;
    }

    public Map<String, Object> configSection(String key) {
        Object v = loadConfig().get(key);
        return v instanceof Map ? (Map<String, Object>) v : new LinkedHashMap<>();
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> readSection(String section, Map<String, Object> defaults) {
        Map<String, Object> out = new LinkedHashMap<>(defaults);
        List<String> payload = jdbc.query(
                "SELECT payload FROM litemeet_settings WHERE section = ?",
                (rs, i) -> rs.getString("payload"), section);
        if (!payload.isEmpty() && payload.get(0) != null) {
            try {
                Object parsed = mapper.readValue(payload.get(0), Object.class);
                if (parsed instanceof Map<?, ?> m) out.putAll((Map<? extends String, ?>) m);
            } catch (IOException e) {
                log.warn("解析配置节 {} 失败: {}", section, e.getMessage());
            }
        }
        return out;
    }

    private void writeSection(String section, Map<String, Object> data) {
        try {
            String json = mapper.writeValueAsString(data);
            jdbc.update("""
                    INSERT INTO litemeet_settings (section, payload) VALUES (?, ?)
                    ON DUPLICATE KEY UPDATE payload = ?
                    """, section, json, json);
        } catch (IOException e) {
            log.warn("保存配置节 {} 失败: {}", section, e.getMessage());
        }
    }

    // ---------- 工具 ----------

    private void addScalar(List<String> sets, List<Object> args, Map<String, Object> patch,
                           String key, String column, String type) {
        if (!patch.containsKey(key) || patch.get(key) == null) return;
        sets.add(column + " = ?");
        args.add(String.valueOf(patch.get(key)));
    }

    private void addLong(List<String> sets, List<Object> args, Map<String, Object> patch, String key, String column) {
        if (!patch.containsKey(key)) return;
        Object v = patch.get(key);
        sets.add(column + " = ?");
        args.add(v instanceof Number n ? n.longValue() : safeLong(v, 0L));
    }

    /** endedAt 等 ISO 时间字段：可传 ISO 字符串（存毫秒）或 null（清空） */
    private void addIso(List<String> sets, List<Object> args, Map<String, Object> patch, String key, String column) {
        if (!patch.containsKey(key)) return;
        Object v = patch.get(key);
        sets.add(column + " = ?");
        args.add(v == null || String.valueOf(v).isBlank() ? null : toMillis(String.valueOf(v)));
    }

    private void addColumn(List<String> sets, List<Object> args, Map<String, Object> patch, String key, String column) {
        if (!patch.containsKey(key)) return;
        Object v = patch.get(key);
        sets.add(column + " = ?");
        args.add(v == null || String.valueOf(v).isBlank() ? null : String.valueOf(v));
    }

    private static String iso(long millis) {
        return Instant.ofEpochMilli(millis).toString();
    }

    private static Long toMillis(String iso) {
        try {
            return Instant.parse(iso).toEpochMilli();
        } catch (Exception e) {
            try {
                return Long.parseLong(iso);
            } catch (Exception e2) {
                return null;
            }
        }
    }

    private static long safeLong(Object v, long def) {
        if (v instanceof Number n) return n.longValue();
        try {
            return Long.parseLong(String.valueOf(v));
        } catch (Exception e) {
            return def;
        }
    }

    private static String nullIfBlank(String s) {
        return s == null || s.isBlank() ? null : s;
    }

    private static String randomSuffix() {
        Random r = new Random();
        StringBuilder sb = new StringBuilder();
        String chars = "abcdefghijklmnopqrstuvwxyz0123456789";
        for (int i = 0; i < 6; i++) sb.append(chars.charAt(r.nextInt(chars.length())));
        return sb.toString();
    }
}