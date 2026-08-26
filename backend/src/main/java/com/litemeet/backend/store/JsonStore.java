package com.litemeet.backend.store;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/**
 * 本地数据存储：所有数据保存在本机 data/ 目录，不上传任何云端
 * 文件格式与旧版完全一致（records.json / config.json），数据无缝迁移
 */
@Component
public class JsonStore {

    private final ObjectMapper mapper = new ObjectMapper();
    private final Object fileLock = new Object();

    private final Path dataDir;
    private final Path audioDir;
    private final Path recordsFile;
    private final Path configFile;

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

    public JsonStore(@Value("${litemeet.data-dir:data}") String dataDir) {
        this.dataDir = Path.of(dataDir);
        this.audioDir = this.dataDir.resolve("audio");
        this.recordsFile = this.dataDir.resolve("records.json");
        this.configFile = this.dataDir.resolve("config.json");
        ensureDirs();
    }

    public void ensureDirs() {
        try {
            Files.createDirectories(dataDir);
            Files.createDirectories(audioDir);
        } catch (IOException e) {
            throw new RuntimeException("无法创建数据目录: " + e.getMessage(), e);
        }
    }

    public Path audioDir() {
        return audioDir;
    }

    // ---------- 通用读写 ----------

    private Object readJson(Path file, Object fallback) {
        synchronized (fileLock) {
            try {
                if (!Files.exists(file)) return fallback;
                return mapper.readValue(file.toFile(), Object.class);
            } catch (Exception e) {
                return fallback;
            }
        }
    }

    private void writeJson(Path file, Object data) {
        synchronized (fileLock) {
            try {
                ensureDirs();
                Path tmp = file.resolveSibling(file.getFileName() + ".tmp");
                Files.write(tmp, mapper.writerWithDefaultPrettyPrinter()
                        .writeValueAsBytes(data));
                Files.move(tmp, file, StandardCopyOption.REPLACE_EXISTING,
                        StandardCopyOption.ATOMIC_MOVE);
            } catch (IOException e) {
                throw new RuntimeException("写入文件失败: " + e.getMessage(), e);
            }
        }
    }

    // ---------- 会议记录 ----------

    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> loadRecords() {
        Object data = readJson(recordsFile, new ArrayList<Map<String, Object>>());
        if (data instanceof List<?> list) {
            List<Map<String, Object>> result = new ArrayList<>();
            for (Object o : list) {
                if (o instanceof Map) result.add((Map<String, Object>) o);
            }
            return result;
        }
        return new ArrayList<>();
    }

    public void saveRecords(List<Map<String, Object>> records) {
        writeJson(recordsFile, records);
    }

    public Map<String, Object> createRecord(String title, String roomId, String host,
                                            String ownerId, String ownerName, String mode) {
        List<Map<String, Object>> records = loadRecords();
        Map<String, Object> record = new LinkedHashMap<>();
        record.put("id", "rec_" + Long.toString(System.currentTimeMillis(), 36)
                + randomSuffix());
        record.put("title", title == null || title.isEmpty() ? "未命名会议" : title);
        record.put("roomId", roomId == null ? "" : roomId);
        record.put("host", host == null ? "" : host);
        record.put("ownerId", ownerId == null ? "" : ownerId);
        record.put("ownerName", ownerName == null ? "" : ownerName);
        record.put("mode", mode == null || mode.isEmpty() ? "audio" : mode);
        record.put("createdAt", new Date().toInstant().toString());
        record.put("endedAt", null);
        record.put("duration", 0);
        record.put("status", "recording");
        record.put("transcript", new ArrayList<Map<String, Object>>());
        record.put("summary", null);
        record.put("audioFile", null);
        record.put("videoFile", null);
        records.add(0, record);
        saveRecords(records);
        return record;
    }

    public Map<String, Object> getRecord(String id) {
        for (Map<String, Object> r : loadRecords()) {
            if (id.equals(r.get("id"))) return r;
        }
        return null;
    }

    public Map<String, Object> updateRecord(String id, Map<String, Object> patch) {
        List<Map<String, Object>> records = loadRecords();
        for (Map<String, Object> r : records) {
            if (id.equals(r.get("id"))) {
                r.putAll(patch);
                saveRecords(records);
                return r;
            }
        }
        return null;
    }

    public Map<String, Object> appendTranscript(String id, Map<String, Object> entry) {
        List<Map<String, Object>> records = loadRecords();
        for (Map<String, Object> r : records) {
            if (id.equals(r.get("id"))) {
                Object raw = r.get("transcript");
                List<Map<String, Object>> transcript = raw instanceof List
                        ? (List<Map<String, Object>>) raw : new ArrayList<>();
                transcript.add(entry);
                r.put("transcript", transcript);
                saveRecords(records);
                return r;
            }
        }
        return null;
    }

    public boolean deleteRecord(String id) {
        List<Map<String, Object>> records = loadRecords();
        Map<String, Object> removed = null;
        for (Iterator<Map<String, Object>> it = records.iterator(); it.hasNext(); ) {
            Map<String, Object> r = it.next();
            if (id.equals(r.get("id"))) {
                removed = r;
                it.remove();
                break;
            }
        }
        if (removed == null) return false;
        saveRecords(records);
        Object audioFile = removed.get("audioFile");
        if (audioFile instanceof String name && !name.isEmpty()) {
            try {
                Files.deleteIfExists(audioPath(name));
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

    // ---------- 配置 ----------

    @SuppressWarnings("unchecked")
    public Map<String, Object> loadConfig() {
        Object raw = readJson(configFile, new LinkedHashMap<String, Object>());
        Map<String, Object> saved = raw instanceof Map ? (Map<String, Object>) raw : new LinkedHashMap<>();

        Map<String, Object> transcribe = new LinkedHashMap<>(DEFAULT_TRANSCRIBE);
        Object t = saved.get("transcribe");
        if (t instanceof Map) transcribe.putAll((Map<String, Object>) t);
        Map<String, Object> llm = new LinkedHashMap<>(DEFAULT_LLM);
        Object l = saved.get("llm");
        if (l instanceof Map) llm.putAll((Map<String, Object>) l);

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("transcribe", transcribe);
        result.put("llm", llm);
        return result;
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> saveConfig(Map<String, Object> patch) {
        Map<String, Object> cfg = loadConfig();
        if (patch != null) {
            Object t = patch.get("transcribe");
            if (t instanceof Map) {
                Map<String, Object> cur = (Map<String, Object>) cfg.get("transcribe");
                cur.putAll((Map<String, Object>) t);
            }
            Object l = patch.get("llm");
            if (l instanceof Map) {
                Map<String, Object> cur = (Map<String, Object>) cfg.get("llm");
                cur.putAll((Map<String, Object>) l);
            }
        }
        writeJson(configFile, cfg);
        return cfg;
    }

    public Map<String, Object> configSection(String key) {
        Object v = loadConfig().get(key);
        return v instanceof Map ? (Map<String, Object>) v : new LinkedHashMap<>();
    }

    private static String randomSuffix() {
        Random r = new Random();
        StringBuilder sb = new StringBuilder();
        String chars = "abcdefghijklmnopqrstuvwxyz0123456789";
        for (int i = 0; i < 6; i++) sb.append(chars.charAt(r.nextInt(chars.length())));
        return sb.toString();
    }
}
