package com.litemeet.backend.livekit;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;

/**
 * LiveKit 访问令牌签发服务
 * 采用 HS256 签名的 JWT，密钥与 livekit.yaml 中 keys 一致
 * video claim 声明房间、身份与发布/订阅/数据权限
 */
@Service
public class LiveKitTokenService {

    private static final long TOKEN_TTL_SECONDS = 24 * 3600;

    private final ObjectMapper mapper = new ObjectMapper();

    @Value("${litemeet.livekit.api-key:litemeet}")
    private String apiKey;

    @Value("${litemeet.livekit.api-secret:}")
    private String apiSecret;

    @Value("${litemeet.livekit.ws-url:ws://localhost:7880}")
    private String wsUrl;

    /** 生成 LiveKit 访问令牌（HS256） */
    public String createToken(String room, String identity) {
        long now = Instant.now().getEpochSecond();

        ObjectNode video = mapper.createObjectNode();
        video.put("room", room);
        video.put("roomJoin", true);
        video.put("roomCreate", true);
        video.put("canPublish", true);
        video.put("canSubscribe", true);
        video.put("canPublishData", true);
        video.put("identity", identity);

        ObjectNode claims = mapper.createObjectNode();
        claims.put("exp", now + TOKEN_TTL_SECONDS);
        claims.put("nbf", now - 10);
        claims.put("iss", apiKey);
        claims.put("sub", identity);
        claims.set("video", video);

        ObjectNode header = mapper.createObjectNode();
        header.put("alg", "HS256");
        header.put("typ", "JWT");

        String signingInput = base64Url(header.toString()) + "." + base64Url(claims.toString());
        // hmacSha256 已返回 base64url 编码的签名（勿再次 base64Url 编码，否则双重编码导致签名无效）
        String signature = hmacSha256(signingInput, apiSecret);
        return signingInput + "." + signature;
    }

    /** 局域网直连地址：HTTP 页面可连的 ws:// */
    public String wsUrl() {
        return wsUrl;
    }

    /** WSS 地址：经后端 5679 HTTPS 端口的 /livekit 代理（HTTPS 页面必需） */
    public String wssUrl(String host) {
        String safeHost = (host == null || host.isBlank()) ? "localhost" : host;
        return "wss://" + safeHost + ":5679/livekit";
    }

    private String base64Url(String data) {
        return Base64.getUrlEncoder().withoutPadding()
                .encodeToString(data.getBytes(StandardCharsets.UTF_8));
    }

    private String hmacSha256(String data, String secret) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            return Base64.getUrlEncoder().withoutPadding()
                    .encodeToString(mac.doFinal(data.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            throw new IllegalStateException("HMAC-SHA256 签名失败", e);
        }
    }
}
