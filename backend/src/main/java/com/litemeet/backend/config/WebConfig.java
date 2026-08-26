package com.litemeet.backend.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry;
import org.springframework.web.servlet.config.annotation.ViewControllerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * CORS 全局开放：后端与前端分离部署，任意来源的前端均可对接
 * 同时托管前端静态资源（file:./frontend/），实现"单端口同源"部署：
 * 局域网设备只需信任一个 https://host:5679 证书，页面/API/信令WS/LiveKit WSS 全部同源，
 * 手机端（HTTPS 安全上下文）即可正常使用麦克风/摄像头/屏幕共享。
 */
@Configuration
public class WebConfig implements WebMvcConfigurer {

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/api/**")
                .allowedOriginPatterns("*")
                .allowedMethods("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
                .allowedHeaders("*")
                .maxAge(3600);
    }

    @Override
    public void addResourceHandlers(ResourceHandlerRegistry registry) {
        // 优先托管 Vite 构建产物（file:./frontend/dist/）；未构建时回退原生前端（file:./frontend/）
        registry.addResourceHandler("/**")
                .addResourceLocations("file:./frontend/dist/", "file:./frontend/")
                .setCachePeriod(0);
    }

    @Override
    public void addViewControllers(ViewControllerRegistry registry) {
        // 自定义 file: 静态位置不参与 Boot 的欢迎页解析，手动把根路径指到首页
        registry.addViewController("/").setViewName("forward:/index.html");
    }
}
