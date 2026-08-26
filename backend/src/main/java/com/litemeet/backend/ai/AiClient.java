package com.litemeet.backend.ai;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;

/**
 * OpenAI 兼容 API 客户端：语音转写 + LLM 会议纪要
 * 使用 JDK 原生 HttpClient，无额外依赖
 */
@Component
public class AiClient {

    private static final String SUMMARY_SYSTEM_PROMPT = """
            你是一名专业的会议纪要撰写助手。请根据用户提供的会议文字记录，用中文输出一份结构清晰的会议纪要，使用 Markdown 格式，包含以下部分：
            ## 会议概要
            （2-3 句话概括本次会议）
            ## 主要讨论点
            （分条列出讨论的核心内容）
            ## 决定事项
            （会议达成的决定；如无则写"无"）
            ## 待办事项
            （列出需要跟进的任务；如无则写"无"）
            要求：忠于原文，不要编造记录中不存在的内容；语言简洁专业。""";

    private final ObjectMapper mapper = new ObjectMapper();
    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(15))
            .build();

    /** 语音转写（multipart 上传到 /audio/transcriptions） */
    public String transcribeAudio(byte[] audio, String filename, Map<String, Object> cfg) throws Exception {
        String baseUrl = joinUrl(str(cfg.get("baseUrl")), "/audio/transcriptions");
        String boundary = "----LiteMeet" + Long.toHexString(System.currentTimeMillis());

        String model = str(cfg.get("model"));
        String language = str(cfg.get("language"));
        String apiKey = str(cfg.get("apiKey"));

        StringBuilder head = new StringBuilder();
        head.append("--").append(boundary).append("\r\n")
                .append("Content-Disposition: form-data; name=\"model\"\r\n\r\n")
                .append(model).append("\r\n");
        if (language != null && !language.isEmpty() && !"auto".equals(language)) {
            head.append("--").append(boundary).append("\r\n")
                    .append("Content-Disposition: form-data; name=\"language\"\r\n\r\n")
                    .append(language).append("\r\n");
        }
        head.append("--").append(boundary).append("\r\n")
                .append("Content-Disposition: form-data; name=\"file\"; filename=\"")
                .append(filename == null || filename.isEmpty() ? "chunk.wav" : filename)
                .append("\"\r\n")
                .append("Content-Type: application/octet-stream\r\n\r\n");

        byte[] headBytes = head.toString().getBytes(StandardCharsets.UTF_8);
        byte[] tailBytes = ("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8);

        byte[] body = new byte[headBytes.length + audio.length + tailBytes.length];
        System.arraycopy(headBytes, 0, body, 0, headBytes.length);
        System.arraycopy(audio, 0, body, headBytes.length, audio.length);
        System.arraycopy(tailBytes, 0, body, headBytes.length + audio.length, tailBytes.length);

        HttpRequest.Builder rb = HttpRequest.newBuilder(URI.create(baseUrl))
                .header("Content-Type", "multipart/form-data; boundary=" + boundary)
                .POST(HttpRequest.BodyPublishers.ofByteArrays(java.util.List.of(body)));
        if (apiKey != null && !apiKey.isEmpty()) rb.header("Authorization", "Bearer " + apiKey);

        HttpResponse<String> resp = safeSend(rb.build());
        if (resp.statusCode() / 100 != 2) {
            throw new RuntimeException("转写服务返回 " + resp.statusCode() + ": "
                    + truncate(resp.body()));
        }
        JsonNode data = mapper.readTree(resp.body());
        String text = data.path("text").asText("");
        return text.trim();
    }

    /** LLM 摘要（/chat/completions） */
    public String summarizeText(String transcriptText, Map<String, Object> cfg) throws Exception {
        String baseUrl = joinUrl(str(cfg.get("baseUrl")), "/chat/completions");
        String model = str(cfg.get("model"));
        String apiKey = str(cfg.get("apiKey"));

        ObjectNode reqBody = mapper.createObjectNode();
        reqBody.put("model", model);
        reqBody.put("temperature", 0.3);
        ArrayNode messages = reqBody.putArray("messages");
        ObjectNode sys = messages.addObject();
        sys.put("role", "system");
        sys.put("content", SUMMARY_SYSTEM_PROMPT);
        ObjectNode user = messages.addObject();
        user.put("role", "user");
        user.put("content", "以下是会议的文字记录：\n\n" + transcriptText);

        HttpRequest.Builder rb = HttpRequest.newBuilder(URI.create(baseUrl))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(
                        mapper.writeValueAsString(reqBody), StandardCharsets.UTF_8));
        if (apiKey != null && !apiKey.isEmpty()) rb.header("Authorization", "Bearer " + apiKey);

        HttpResponse<String> resp = safeSend(rb.build());
        if (resp.statusCode() / 100 != 2) {
            throw new RuntimeException("AI 服务返回 " + resp.statusCode() + ": "
                    + truncate(resp.body()));
        }
        JsonNode data = mapper.readTree(resp.body());
        String content = data.path("choices").path(0).path("message").path("content").asText("");
        return content.trim();
    }

    /** 生成 0.5s 静音 WAV（用于转写连通性测试） */
    public byte[] silenceWav() {
        int sampleRate = 16000;
        int samples = sampleRate / 2;
        int dataLen = samples * 2;
        java.nio.ByteBuffer buf = java.nio.ByteBuffer.allocate(44 + dataLen)
                .order(java.nio.ByteOrder.LITTLE_ENDIAN);
        buf.put("RIFF".getBytes(StandardCharsets.US_ASCII)).putInt(36 + dataLen);
        buf.put("WAVE".getBytes(StandardCharsets.US_ASCII));
        buf.put("fmt ".getBytes(StandardCharsets.US_ASCII)).putInt(16);
        buf.putShort((short) 1).putShort((short) 1);
        buf.putInt(sampleRate).putInt(sampleRate * 2);
        buf.putShort((short) 2).putShort((short) 16);
        buf.put("data".getBytes(StandardCharsets.US_ASCII)).putInt(dataLen);
        for (int i = 0; i < samples; i++) buf.putShort((short) 0);
        return buf.array();
    }

    // ---------- 工具 ----------

    private HttpResponse<String> safeSend(HttpRequest request) throws Exception {
        try {
            return http.send(request, HttpResponse.BodyHandlers.ofString());
        } catch (Exception e) {
            String reason = e.getMessage() == null ? "未知错误" : e.getMessage();
            throw new RuntimeException("无法连接服务（" + reason + "），请检查服务地址与网络连接");
        }
    }

    private static String joinUrl(String base, String suffix) {
        if (base == null || base.isEmpty()) base = "https://api.openai.com/v1";
        return base.replaceAll("/+$", "") + suffix;
    }

    private static String str(Object o) {
        return o == null ? "" : String.valueOf(o);
    }

    private static String truncate(String s) {
        if (s == null) return "";
        return s.length() > 300 ? s.substring(0, 300) : s;
    }
}
