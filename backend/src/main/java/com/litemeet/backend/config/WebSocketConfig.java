package com.litemeet.backend.config;

import com.litemeet.backend.livekit.LiveKitWsProxyHandler;
import com.litemeet.backend.signaling.SignalWebSocketHandler;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

/**
 * WebSocket 端点注册
 *  /ws      业务信令（聊天/角色/踢人/锁定等；媒体协商已移交 LiveKit SFU）
 *  /livekit/** LiveKit WSS 代理（wss://host:5679/livekit/rtc/v1 -> ws://localhost:7880/rtc）
 * 注意：livekit-client 会在传入 URL 后自动追加 "/rtc/v1"，因此代理必须注册
 *       "/livekit/**" 通配路径，否则 SDK 的请求落到静态资源处理导致连接失败。
 *      信号端点不在 "/"（避免与静态首页 index.html 冲突，保证单端口同源部署）
 */
@Configuration
@EnableWebSocket
public class WebSocketConfig implements WebSocketConfigurer {

    private final SignalWebSocketHandler signalHandler;
    private final LiveKitWsProxyHandler liveKitWsProxyHandler;

    public WebSocketConfig(SignalWebSocketHandler signalHandler, LiveKitWsProxyHandler liveKitWsProxyHandler) {
        this.signalHandler = signalHandler;
        this.liveKitWsProxyHandler = liveKitWsProxyHandler;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(signalHandler, "/ws")
                .setAllowedOriginPatterns("*");
        registry.addHandler(liveKitWsProxyHandler, "/livekit", "/livekit/**")
                .setAllowedOriginPatterns("*");
    }
}
