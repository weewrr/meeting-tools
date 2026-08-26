<script setup>
// 会议房间页：Vue 壳渲染静态会议 DOM，实时媒体控制逻辑由 roomMedia.initRoom() 接管
// （设计：室页是重型实时控制器，采用"Vue 提供壳 + 复用已实战验证的媒体控制器"的渐进式方案，
//   避免把 2000+ 行 WebRTC/录制/转写命令式逻辑重写为响应式而引入回归风险）
import { onMounted } from 'vue'
import { initRoom } from './roomMedia.js'

onMounted(() => {
  initRoom()
})
</script>

<template>
  <header class="room-header">
    <span class="r-name" id="roomTitle">会议</span>
    <span class="r-info" id="roomInfo"></span>
    <button class="rename-btn" id="btnRename" title="修改昵称">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 114 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
      改名
    </button>
    <div class="spacer" style="flex:1"></div>
    <span class="rec-dot" id="recIndicator"><i></i>录制中</span>
    <span class="r-info" id="meetingTimer">00:00</span>
    <span class="r-info" id="meetingMeta"></span>
  </header>

  <div class="video-stage">
    <div class="video-grid v1" id="videoGrid"></div>

    <div class="subtitle-live" id="liveSubtitle"></div>

    <aside class="side-panel" id="sidePanel">
      <div class="panel-tabs">
        <button data-tab="chat" class="active">聊天</button>
        <button data-tab="members">参会人</button>
        <button data-tab="subtitle">实时字幕</button>
      </div>
      <div class="panel-body" id="panelChat"></div>
      <div class="panel-body" id="panelMembers" style="display:none"></div>
      <div class="panel-body" id="panelSubtitle" style="display:none"></div>
      <div class="chat-input-row" id="chatInputRow">
        <select id="chatTarget" title="选择消息接收者（选具体的人为私聊）">
          <option value="">所有人</option>
        </select>
        <input id="chatInput" placeholder="发送消息…" maxlength="2000">
        <button class="btn small" id="chatSend">发送</button>
      </div>
    </aside>
  </div>

  <footer class="room-toolbar">
    <button class="tool-btn" id="btnMic" aria-haspopup="menu" aria-pressed="false">
      <span class="circle">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/></svg>
      </span>
      <span class="label">麦克风</span>
    </button>
    <button class="tool-btn" id="btnSpeaker" title="扬声器设置" aria-haspopup="menu">
      <span class="circle">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M15.54 8.46a5 5 0 010 7.07"/><path d="M19.07 4.93a10 10 0 010 14.14"/></svg>
      </span>
      <span class="label">扬声器</span>
    </button>
    <button class="tool-btn" id="btnCam" aria-pressed="false">
      <span class="circle">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>
      </span>
      <span class="label">视频</span>
    </button>
    <button class="tool-btn" id="btnScreen" aria-pressed="false">
      <span class="circle">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
      </span>
      <span class="label">共享屏幕</span>
    </button>
    <button class="tool-btn" id="btnScreenAudio" style="display:none" title="同时共享电脑声音开关" aria-pressed="false">
      <span class="circle">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/><path d="M8 9a3 3 0 000 6"/><path d="M12 8.5a4.5 4.5 0 010 7"/></svg>
      </span>
      <span class="label">屏幕声音</span>
    </button>
    <button class="tool-btn" id="btnRec" aria-pressed="false">
      <span class="circle">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4" fill="currentColor" stroke="none"/></svg>
      </span>
      <span class="label">录制</span>
    </button>
    <button class="tool-btn" id="btnPanel" title="聊天 / 参会人 / 字幕">
      <span class="circle">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="18" rx="1.5"/><rect x="14" y="3" width="7" height="10" rx="1.5"/><path d="M14 17h7"/></svg>
      </span>
      <span class="label">面板</span>
      <span class="panel-dot" id="panelDot"></span>
    </button>
    <button class="tool-btn" id="btnShare">
      <span class="circle">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>
      </span>
      <span class="label">分享</span>
    </button>
    <button class="tool-btn" id="btnFullscreen">
      <span class="circle">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 00-2 2v3"/><path d="M21 8V5a2 2 0 00-2-2h-3"/><path d="M3 16v3a2 2 0 002 2h3"/><path d="M16 21h3a2 2 0 002-2v-3"/></svg>
      </span>
      <span class="label">全屏</span>
    </button>
    <button class="tool-btn leave" id="btnLeave">
      <span class="circle">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.68 13.31a16 16 0 003.41 2.6l1.27-1.27a2 2 0 012.11-.45c1.13.38 2.33.59 3.53.62a2 2 0 012 2V20a2 2 0 01-2 2c-9.39 0-17-7.61-17-17a2 2 0 012-2h2.19a2 2 0 012 2c.03 1.2.24 2.4.62 3.53a2 2 0 01-.45 2.11l-1.27 1.27z" transform="rotate(135 12 12)"/></svg>
      </span>
      <span class="label">离开</span>
    </button>
  </footer>
</template>