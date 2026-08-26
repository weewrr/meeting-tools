package com.litemeet.backend.livekit;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.BinaryMessage;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.SubProtocolCapable;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ConcurrentHashMap;

/**
 * LiveKit WSS 代理（端点 /livekit）
 * 浏览器安全限制：HTTPS 页面无法连接 ws://，因此将 wss://host:5679/livekit
 * 的 WebSocket 请求代理到本机 LiveKit 的 ws://localhost:7880/rtc（LiveKit 的
 * WebSocket 端点为 /rtc，根路径是健康检查、不会升级握手），
 * 并保留原始 query 参数（access_token），使 LiveKit 认证通过。
 * 子协议：客户端 SDK 会自动发送 protobuf；本代理通过 SubProtocolCapable
 * 向浏览器回选 protobuf（否则 livekit-client 校验 ws.protocol 会失败）。
 * 注意：上游连 LiveKit 时【不能】声明子协议——LiveKit /rtc 不会回选 protobuf，
 * Java HttpClient 会因此抛 WebSocketHandshakeException，导致上游连接失败。
 */
@Component
public class LiveKitWsProxyHandler extends TextWebSocketHandler implements SubProtocolCapable {

    private static final Logger log = LoggerFactory.getLogger(LiveKitWsProxyHandler.class);
    private static final int CONNECT_TIMEOUT_SECONDS = 10;

    @Value("${litemeet.livekit.ws-url:ws://localhost:7880}")
    private String upstream;

    private final HttpClient httpClient = HttpClient.newHttpClient();
    private final java.util.Map<WebSocketSession, WebSocket> sessionToUpstream = new ConcurrentHashMap<>();

    @Override
    public List<String> getSubProtocols() {
        return List.of("protobuf");
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession client) {
        // 构造上游 URI：LiveKit WebSocket 端点为 /rtc；保留前端请求的 query（access_token 等）
        String query = null;
        try {
            URI clientUri = client.getUri();
            if (clientUri != null) query = clientUri.getQuery();
        } catch (Exception ignored) {
        }
        URI upstreamUri = URI.create(upstream + "/rtc" + (query != null && !query.isEmpty() ? "?" + query : ""));

        httpClient.newWebSocketBuilder()
                .connectTimeout(Duration.ofSeconds(CONNECT_TIMEOUT_SECONDS))
                .buildAsync(upstreamUri, new UpstreamListener(client))
                .whenComplete((upstreamWs, err) -> {
                    if (err != null) {
                        log.warn("LiveKit 上游连接失败: {}", err.getMessage());
                        closeClient(client, CloseStatus.SERVER_ERROR, "无法连接 LiveKit 媒体服务器");
                        return;
                    }
                    sessionToUpstream.put(client, upstreamWs);
                });
    }

    @Override
    protected void handleTextMessage(WebSocketSession client, TextMessage message) {
        WebSocket upstream = sessionToUpstream.get(client);
        if (upstream != null) {
            upstream.sendText(message.getPayload(), message.isLast()).join();
        }
    }

    @Override
    protected void handleBinaryMessage(WebSocketSession client, BinaryMessage message) {
        WebSocket upstream = sessionToUpstream.get(client);
        if (upstream != null) {
            // 必须透传消息的 last 标志（消息可能被 WebSocket 层分片），
            // 硬编码 last=true 会把分片当作完整消息发送，导致 LiveKit 收到截断/错位的 protobuf
            upstream.sendBinary(message.getPayload(), message.isLast()).join();
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession client, CloseStatus status) {
        WebSocket upstream = sessionToUpstream.remove(client);
        if (upstream != null) {
            try {
                upstream.sendClose(status.getCode() == 1005 ? WebSocket.NORMAL_CLOSURE : status.getCode(), status.getReason()).join();
            } catch (Exception ignored) {
            }
        }
    }

    @Override
    public void handleTransportError(WebSocketSession client, Throwable exception) {
        closeClient(client, CloseStatus.SERVER_ERROR, "WebSocket 传输错误");
    }

    @Override
    public boolean supportsPartialMessages() {
        return true;
    }

    private void closeClient(WebSocketSession client, CloseStatus status, String reason) {
        try {
            if (client != null && client.isOpen()) {
                client.close(status.withReason(reason == null ? "" : reason));
            }
        } catch (IOException ignored) {
        }
    }

    /** 上游 LiveKit -> 前端 的监听器 */
    private class UpstreamListener implements WebSocket.Listener {

        private final WebSocketSession client;

        UpstreamListener(WebSocketSession client) {
            this.client = client;
        }

        @Override
        public void onOpen(WebSocket webSocket) {
            webSocket.request(1);
        }

        @Override
        public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
            try {
                synchronized (client) {
                    if (client.isOpen()) {
                        client.sendMessage(new TextMessage(data.toString(), last));
                    }
                }
            } catch (IOException e) {
                log.debug("转发文本到前端失败: {}", e.getMessage());
            }
            webSocket.request(1);
            return null;
        }

        @Override
        public CompletionStage<?> onBinary(WebSocket webSocket, ByteBuffer data, boolean last) {
            try {
                synchronized (client) {
                    if (client.isOpen()) {
                        client.sendMessage(new BinaryMessage(data, last));
                    }
                }
            } catch (IOException e) {
                log.debug("转发二进制到前端失败: {}", e.getMessage());
            }
            webSocket.request(1);
            return null;
        }

        @Override
        public CompletionStage<?> onPing(WebSocket webSocket, ByteBuffer message) {
            webSocket.request(1);
            return null;
        }

        @Override
        public CompletionStage<?> onPong(WebSocket webSocket, ByteBuffer message) {
            webSocket.request(1);
            return null;
        }

        @Override
        public CompletionStage<?> onClose(WebSocket webSocket, int statusCode, String reason) {
            log.warn("LiveKit 上游关闭: code={} reason={}", statusCode, reason);
            closeClient(client, CloseStatus.NORMAL, "LiveKit 连接已关闭");
            return null;
        }

        @Override
        public void onError(WebSocket webSocket, Throwable error) {
            log.error("LiveKit 上游 WebSocket 错误: {}", error.toString(), error);
            closeClient(client, CloseStatus.SERVER_ERROR, "LiveKit 连接异常");
        }
    }
}
