package com.litemeet.backend;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * 轻会议 LiteMeet - 后端入口
 * 纯 API 服务：REST（会议记录/转写/AI 摘要/配置）+ WebSocket（WebRTC 信令）
 * 前端独立部署，通过 HTTP API 与 WebSocket 对接（Swagger 文档见 /swagger-ui.html）
 */
@SpringBootApplication
public class BackendApplication {

    public static void main(String[] args) {
        SpringApplication.run(BackendApplication.class, args);
    }
}
