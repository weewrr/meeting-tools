package com.litemeet.backend.api;

import com.litemeet.backend.ai.AiClient;
import com.litemeet.backend.store.JdbcRecordStore;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 系统配置 API（转写服务配置）
 * apiKey 以掩码形式返回，避免泄露。
 * LLM（AI 摘要）配置由前端存于本机浏览器，测试连接时随请求体传入。
 */
@RestController
@RequestMapping("/api/config")
public class ConfigController {

    private final JdbcRecordStore store;
    private final AiClient ai;

    public ConfigController(JdbcRecordStore store, AiClient ai) {
        this.store = store;
        this.ai = ai;
    }

    @GetMapping
    public Map<String, Object> getConfig() {
        return maskConfig(store.loadConfig());
    }

    @PostMapping
    public Map<String, Object> saveConfig(@RequestBody(required = false) Map<String, Object> body) {
        return maskConfig(store.saveConfig(body));
    }

    /** 测试连接：transcribe 用保存的配置 + 0.5s 静音音频，llm 用请求体传入的配置 */
    @PostMapping("/test")
    public ResponseEntity<Map<String, Object>> testConnection(
            @RequestBody(required = false) Map<String, Object> body) {
        String kind = body == null ? "" : String.valueOf(body.getOrDefault("kind", ""));
        try {
            if ("transcribe".equals(kind)) {
                String text = ai.transcribeAudio(ai.silenceWav(), "test.wav",
                        store.configSection("transcribe"));
                String shown = text.isEmpty() ? "(静音)" : text;
                return ResponseEntity.ok(Map.of("ok", true,
                        "message", "转写服务连接正常（返回: \"" + shown + "\"）"));
            } else if ("llm".equals(kind)) {
                Map<String, Object> llm = extractSection(body, "llm");
                ai.summarizeText("测试：请回复\"连接正常\"。", llm);
                return ResponseEntity.ok(Map.of("ok", true,
                        "message", "AI 服务连接正常（模型: " + llm.get("model") + "）"));
            }
            return ResponseEntity.badRequest().body(Map.of("error", "未知的测试类型"));
        } catch (Exception e) {
            return ResponseEntity.ok(Map.of("ok", false, "message", e.getMessage()));
        }
    }

    // ---------- 掩码 ----------

    private Map<String, Object> maskConfig(Map<String, Object> cfg) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("transcribe", maskSection((Map<String, Object>) cfg.get("transcribe")));
        return result;
    }

    private Map<String, Object> maskSection(Map<String, Object> section) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("baseUrl", section.get("baseUrl"));
        String key = String.valueOf(section.getOrDefault("apiKey", ""));
        m.put("apiKey", key.isEmpty() ? "" : "************" + key.substring(Math.max(0, key.length() - 4)));
        m.put("hasKey", !key.isEmpty());
        m.put("model", section.get("model"));
        if (section.containsKey("language")) m.put("language", section.get("language"));
        return m;
    }

    /** 从请求体中提取指定配置段（非空字段，供 LLM 测试使用） */
    private Map<String, Object> extractSection(Map<String, Object> body, String key) {
        Map<String, Object> m = new LinkedHashMap<>();
        if (body != null && body.get(key) instanceof Map<?, ?> src) {
            for (Map.Entry<?, ?> e : src.entrySet()) {
                if (e.getValue() != null) m.put(String.valueOf(e.getKey()), e.getValue());
            }
        }
        return m;
    }
}
