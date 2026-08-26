package com.litemeet.backend.api;

import com.litemeet.backend.ai.AiClient;
import com.litemeet.backend.store.JsonStore;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.file.*;
import java.util.*;

/**
 * 会议记录 / 转写 / AI 摘要 REST API
 * 路径与请求/响应格式与前端约定完全一致
 */
@RestController
@RequestMapping("/api")
public class RecordController {

    private final JsonStore store;
    private final AiClient ai;

    public RecordController(JsonStore store, AiClient ai) {
        this.store = store;
        this.ai = ai;
    }

    // ---------- 会议记录 ----------

    @GetMapping("/records")
    public List<Map<String, Object>> listRecords(
            @RequestParam(value = "ownerId", required = false) String ownerId) {
        List<Map<String, Object>> all = store.loadRecords();
        // 归属过滤：传入 ownerId 时只返回属于本设备的记录；
        // 无归属的旧版记录不再对任何人可见（避免"所有人都有记录"）
        if (ownerId == null || ownerId.isBlank()) return all;
        List<Map<String, Object>> mine = new ArrayList<>();
        for (Map<String, Object> r : all) {
            if (ownerId.equals(str(r.get("ownerId")))) mine.add(r);
        }
        return mine;
    }

    @PostMapping("/records")
    public Map<String, Object> createRecord(@RequestBody(required = false) Map<String, Object> body) {
        body = body == null ? Map.of() : body;
        return store.createRecord(
                str(body.get("title")), str(body.get("roomId")), str(body.get("host")),
                str(body.get("ownerId")), str(body.get("ownerName")), str(body.get("mode")));
    }

    @GetMapping("/records/{id}")
    public ResponseEntity<Map<String, Object>> getRecord(
            @PathVariable String id,
            @RequestParam(value = "ownerId", required = false) String ownerId) {
        Map<String, Object> record = store.getRecord(id);
        if (record == null) return notFound();
        // 归属校验：传入 ownerId 时只允许本人访问自己的记录
        if (ownerId != null && !ownerId.isBlank()
                && !ownerId.equals(str(record.get("ownerId")))) {
            return notFound();
        }
        return ResponseEntity.ok(record);
    }

    @PatchMapping("/records/{id}")
    public ResponseEntity<Map<String, Object>> updateRecord(
            @PathVariable String id, @RequestBody(required = false) Map<String, Object> body) {
        Map<String, Object> patch = new LinkedHashMap<>();
        if (body != null) {
            for (String k : List.of("title", "endedAt", "duration", "status")) {
                if (body.containsKey(k)) patch.put(k, body.get(k));
            }
        }
        Map<String, Object> record = store.updateRecord(id, patch);
        if (record == null) return notFound();
        return ResponseEntity.ok(record);
    }

    @DeleteMapping("/records/{id}")
    public ResponseEntity<Map<String, Object>> deleteRecord(@PathVariable String id) {
        if (!store.deleteRecord(id)) return notFound();
        return ResponseEntity.ok(Map.of("ok", true));
    }

    // ---------- 实时转写：上传音频片段（WAV），转写后追加到记录 ----------

    @PostMapping("/records/{id}/transcribe-chunk")
    public ResponseEntity<?> transcribeChunk(
            @PathVariable String id,
            @RequestParam(value = "offset", defaultValue = "0") long offset,
            @RequestParam("audio") MultipartFile file) {
        if (store.getRecord(id) == null) return notFound();
        if (file == null || file.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("error", "未收到音频片段"));
        }
        try {
            byte[] data = file.getBytes();
            String text = ai.transcribeAudio(data,
                    file.getOriginalFilename() == null ? "chunk.wav" : file.getOriginalFilename(),
                    store.configSection("transcribe"));
            if (!text.isEmpty()) {
                appendTranscriptEntry(id, offset, text);
            }
            return ResponseEntity.ok(Map.of("ok", true, "text", text));
        } catch (Exception e) {
            return badGateway(e.getMessage());
        }
    }

    // ---------- 实时转写：直接追加文字片段 ----------

    @PostMapping("/records/{id}/transcript")
    public ResponseEntity<?> appendTranscript(
            @PathVariable String id, @RequestBody(required = false) Map<String, Object> body) {
        String text = body == null ? "" : str(body.get("text")).trim();
        if (text.isEmpty()) return ResponseEntity.ok(Map.of("ok", true, "skipped", true));
        long offset = body.get("offset") instanceof Number n ? n.longValue() : 0;
        Map<String, Object> record = appendTranscriptEntry(id, offset, text);
        if (record == null) return notFound();
        Object transcript = record.get("transcript");
        int count = transcript instanceof List<?> l ? l.size() : 0;
        return ResponseEntity.ok(Map.of("ok", true, "count", count));
    }

    // ---------- 上传录音文件（webm/wav/mp3 等） ----------

    @PostMapping("/records/{id}/audio")
    public ResponseEntity<?> uploadAudio(
            @PathVariable String id, @RequestParam("audio") MultipartFile file) {
        Map<String, Object> record = store.getRecord(id);
        if (record == null) return notFound();
        if (file == null || file.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("error", "未收到音频文件"));
        }
        try {
            String ext = extOf(file.getOriginalFilename(), ".webm");
            String finalName = id + ext;
            Files.copy(file.getInputStream(), store.audioPath(finalName),
                    StandardCopyOption.REPLACE_EXISTING);
            store.updateRecord(id, Map.of("audioFile", finalName));
            return ResponseEntity.ok(Map.of("ok", true, "audioFile", finalName));
        } catch (IOException e) {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(Map.of("error", "保存音频失败: " + e.getMessage()));
        }
    }

    // ---------- 上传录屏视频文件（webm/mp4 等） ----------

    @PostMapping("/records/{id}/video")
    public ResponseEntity<?> uploadVideo(
            @PathVariable String id, @RequestParam("video") MultipartFile file) {
        Map<String, Object> record = store.getRecord(id);
        if (record == null) return notFound();
        if (file == null || file.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("error", "未收到视频文件"));
        }
        try {
            String ext = extOf(file.getOriginalFilename(), ".webm");
            String finalName = id + ext;
            Files.copy(file.getInputStream(), store.audioPath(finalName),
                    StandardCopyOption.REPLACE_EXISTING);
            store.updateRecord(id, Map.of("videoFile", finalName));
            return ResponseEntity.ok(Map.of("ok", true, "videoFile", finalName));
        } catch (IOException e) {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(Map.of("error", "保存视频失败: " + e.getMessage()));
        }
    }

    // ---------- 播放/下载录屏视频 ----------

    @GetMapping("/media/{file}")
    public ResponseEntity<?> media(@PathVariable String file) {
        Path path = store.audioPath(file);
        if (!Files.exists(path)) return notFound();
        try {
            String type = file.toLowerCase().endsWith(".mp4") ? "video/mp4"
                    : file.toLowerCase().endsWith(".webm") ? "video/webm" : "application/octet-stream";
            return ResponseEntity.ok()
                    .header("Content-Type", type)
                    .body(Files.readAllBytes(path));
        } catch (IOException e) {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(Map.of("error", "读取文件失败"));
        }
    }

    // ---------- 对完整录音执行（重新）转写 ----------

    @PostMapping("/records/{id}/transcribe")
    public ResponseEntity<?> transcribeFull(@PathVariable String id) {
        Map<String, Object> record = store.getRecord(id);
        if (record == null) return notFound();
        Object audioFile = record.get("audioFile");
        if (!(audioFile instanceof String name) || name.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("error", "该记录没有录音文件"));
        }
        Path path = store.audioPath(name);
        if (!Files.exists(path)) {
            return ResponseEntity.badRequest().body(Map.of("error", "录音文件已丢失"));
        }
        try {
            byte[] data = Files.readAllBytes(path);
            String text = ai.transcribeAudio(data, name, store.configSection("transcribe"));
            Map<String, Object> patch = new LinkedHashMap<>();
            patch.put("transcript", text.isEmpty() ? new ArrayList<>() : List.of(entry(0, text)));
            patch.put("status", "completed");
            return ResponseEntity.ok(store.updateRecord(id, patch));
        } catch (Exception e) {
            return badGateway(e.getMessage());
        }
    }

    // ---------- 生成 AI 摘要 ----------

    @PostMapping("/records/{id}/summary")
    public ResponseEntity<?> summarize(@PathVariable String id) {
        Map<String, Object> record = store.getRecord(id);
        if (record == null) return notFound();
        StringBuilder sb = new StringBuilder();
        Object transcript = record.get("transcript");
        if (transcript instanceof List<?> list) {
            for (Object o : list) {
                if (o instanceof Map<?, ?> m) sb.append(str(m.get("text"))).append('\n');
            }
        }
        if (sb.toString().isBlank()) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "没有可用的文字记录，请先完成转写"));
        }
        try {
            String content = ai.summarizeText(sb.toString(), store.configSection("llm"));
            Map<String, Object> summary = new LinkedHashMap<>();
            summary.put("content", content);
            summary.put("model", store.configSection("llm").get("model"));
            summary.put("generatedAt", new Date().toInstant().toString());
            return ResponseEntity.ok(store.updateRecord(id, Map.of("summary", summary)));
        } catch (Exception e) {
            return badGateway(e.getMessage());
        }
    }

    // ---------- 导入音频 ----------

    @PostMapping("/transcribe/file")
    public ResponseEntity<?> transcribeFile(@RequestParam("audio") MultipartFile file) {
        if (file == null || file.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("error", "未收到音频文件"));
        }
        try {
            byte[] data = file.getBytes();
            String origName = file.getOriginalFilename() == null ? "audio.webm" : file.getOriginalFilename();
            String text = ai.transcribeAudio(data, origName, store.configSection("transcribe"));
            // 保留为潜在录音
            String keepName = "import_" + Long.toString(System.currentTimeMillis(), 36)
                    + extOf(origName, ".webm");
            Files.copy(file.getInputStream(), store.audioPath(keepName),
                    StandardCopyOption.REPLACE_EXISTING);
            return ResponseEntity.ok(Map.of("ok", true, "text", text, "audioFile", keepName));
        } catch (Exception e) {
            return badGateway(e.getMessage());
        }
    }

    @PostMapping("/records/import")
    public Map<String, Object> importRecord(@RequestBody(required = false) Map<String, Object> body) {
        body = body == null ? Map.of() : body;
        String title = str(body.get("title"));
        Map<String, Object> record = store.createRecord(
                title.isEmpty() ? "导入的会议录音" : title, "",
                str(body.get("ownerName")), str(body.get("ownerId")),
                str(body.get("ownerName")), "audio");
        Map<String, Object> patch = new LinkedHashMap<>();
        patch.put("status", "completed");
        patch.put("endedAt", new Date().toInstant().toString());
        String text = str(body.get("text")).trim();
        patch.put("transcript", text.isEmpty() ? new ArrayList<>() : List.of(entry(0, text)));

        String audioFile = str(body.get("audioFile"));
        if (!audioFile.isEmpty()) {
            Path src = store.audioPath(audioFile);
            if (Files.exists(src)) {
                String destName = str(record.get("id")) + extOf(audioFile, ".webm");
                try {
                    Files.move(src, store.audioPath(destName), StandardCopyOption.REPLACE_EXISTING);
                    patch.put("audioFile", destName);
                } catch (IOException ignored) { }
            }
        }
        return store.updateRecord(str(record.get("id")), patch);
    }

    // ---------- 下载录音 ----------

    @GetMapping("/audio/{file}")
    public ResponseEntity<?> downloadAudio(@PathVariable String file) {
        Path path = store.audioPath(file);
        if (!Files.exists(path)) return notFound();
        try {
            return ResponseEntity.ok()
                    .header("Content-Type", contentTypeOf(file))
                    .body(Files.readAllBytes(path));
        } catch (IOException e) {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(Map.of("error", "读取文件失败"));
        }
    }

    // ---------- 工具 ----------

    private Map<String, Object> appendTranscriptEntry(String id, long offset, String text) {
        return store.appendTranscript(id, entry(offset, text));
    }

    private static Map<String, Object> entry(long offset, String text) {
        Map<String, Object> e = new LinkedHashMap<>();
        e.put("t", System.currentTimeMillis());
        e.put("offset", offset);
        e.put("text", text);
        return e;
    }

    private static String extOf(String filename, String defaultExt) {
        if (filename == null) return defaultExt;
        int idx = filename.lastIndexOf('.');
        if (idx < 0) return defaultExt;
        String ext = filename.substring(idx).toLowerCase();
        return ext.length() > 8 ? defaultExt : ext;
    }

    private static String contentTypeOf(String filename) {
        String lower = filename == null ? "" : filename.toLowerCase();
        if (lower.endsWith(".wav")) return "audio/wav";
        if (lower.endsWith(".mp3")) return "audio/mpeg";
        if (lower.endsWith(".m4a")) return "audio/mp4";
        return "audio/webm";
    }

    private static String str(Object o) {
        return o == null ? "" : String.valueOf(o);
    }

    private static ResponseEntity<Map<String, Object>> notFound() {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(Map.of("error", "记录不存在"));
    }

    private static ResponseEntity<Map<String, Object>> badGateway(String message) {
        return ResponseEntity.status(HttpStatus.BAD_GATEWAY).body(Map.of("error", message));
    }
}
