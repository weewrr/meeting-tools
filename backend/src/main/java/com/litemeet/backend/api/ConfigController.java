package com.litemeet.backend.api;

import com.litemeet.backend.ai.AiClient;
import com.litemeet.backend.store.JsonStore;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 系统配置 API（转写 / LLM 服务配置）
 * apiKey 以掩码形式返回，避免泄露
 */
@RestController
@RequestMapping("/api/config")
public class ConfigController {

    private final JsonStore store;
    private final AiClient ai;

    public ConfigController(JsonStore store, AiClient ai) {
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

    /** 测试连接：transcribe 用 0.5s 静音音频，llm 用一句话请求 */
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
                ai.summarizeText("测试：请回复\"连接正常\"。", store.configSection("llm"));
                return ResponseEntity.ok(Map.of("ok", true,
                        "message", "AI 服务连接正常（模型: "
                                + store.configSection("llm").get("model") + "）"));
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
        result.put("llm", maskSection((Map<String, Object>) cfg.get("llm")));
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
}
