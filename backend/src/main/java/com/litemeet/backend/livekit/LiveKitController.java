package com.litemeet.backend.livekit;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * LiveKit SFU 媒体服务器接入 API
 * GET /api/livekit/token?room=ROOM&name=昵称 -> { token, wsUrl }
 * 前端拿到 token 后连接 LiveKit 推流/订阅；业务信令（聊天/角色/踢人等）仍走 /ws 信令
 */
@RestController
@RequestMapping("/api/livekit")
public class LiveKitController {

    private final LiveKitTokenService tokenService;

    public LiveKitController(LiveKitTokenService tokenService) {
        this.tokenService = tokenService;
    }

    @GetMapping("/token")
    public Map<String, Object> token(
            @RequestParam String room,
            @RequestParam(defaultValue = "参会者") String name,
            @RequestParam(required = false) String identity) {
        String safeName = name == null || name.isBlank() ? "参会者" : name.trim();
        if (safeName.length() > 32) safeName = safeName.substring(0, 32);
        String roomUp = room == null ? "" : room.trim().toUpperCase();
        // identity 默认等于昵称；业务信令已生成 peerId 时传 selfId，便于前端按 identity 关联参会者
        String lkIdentity = (identity == null || identity.isBlank()) ? safeName : identity;

        Map<String, Object> m = new LinkedHashMap<>();
        m.put("token", tokenService.createToken(roomUp, lkIdentity));
        m.put("wsUrl", tokenService.wsUrl());
        return m;
    }
}
