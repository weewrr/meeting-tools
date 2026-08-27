package com.litemeet.backend.api;

import com.litemeet.backend.signaling.SignalWebSocketHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * 会议监控 REST API：暴露信令服务器内存中的活跃会议快照，
 * 供服务器管理器「会议监听」页面轮询展示。
 */
@RestController
@RequestMapping("/api")
public class MeetingMonitorController {

    private final SignalWebSocketHandler signal;

    public MeetingMonitorController(SignalWebSocketHandler signal) {
        this.signal = signal;
    }

    /** 当前正在进行的会议列表（含参会人与音视频状态），每 2-3 秒轮询一次 */
    @GetMapping("/meetings/active")
    public List<Map<String, Object>> activeMeetings() {
        return signal.activeMeetings();
    }
}
