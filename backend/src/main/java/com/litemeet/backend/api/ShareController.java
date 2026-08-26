package com.litemeet.backend.api;

import com.litemeet.backend.config.CertManager;
import org.springframework.web.bind.annotation.*;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 分享会议 API
 * 返回本机局域网可用的会议加入链接（https 前端端口 3001），供"分享会议"弹窗使用
 */
@RestController
@RequestMapping("/api/share")
public class ShareController {

    /** 前端 HTTPS 端口（与 tools/FrontendServer.java --https 3001 保持一致） */
    private static final int FRONT_HTTPS_PORT = 3001;

    @GetMapping
    public Map<String, Object> share(@RequestParam(required = false) String room) {
        String safeRoom = room == null ? "" : room.trim().toUpperCase();
        // 分享链接只带会议号、不带昵称：被邀请者在主页输入自己的昵称加入
        List<String> lanLinks = CertManager.lanIPv4s().stream()
                .map(ip -> "https://" + ip + ":" + FRONT_HTTPS_PORT + "/?room=" + safeRoom)
                .toList();
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("lanLinks", lanLinks);
        return m;
    }
}
