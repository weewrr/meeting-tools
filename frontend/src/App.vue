<script setup>
import { ref, onMounted } from 'vue'
import { API, toast, fmtDate, getParam, getUserName, setUserName, getUserId } from '@/utils/common'

const name = ref('')
const roomId = ref('')
const modalOpen = ref(false)
const cfgTitle = ref('')
const cfgMax = ref(8)
const cfgDur = ref(60)
const cfgExpire = ref('remind')
const recent = ref([])

let composing = false

function onNameInput(e) {
  setUserName(e.target.value.trim())
}

// 会议号输入：IME（手机拼音键盘）组合期间不重写，组合结束/非组合转大写并过滤非法字符
function onRoomCompositionStart() { composing = true; }
function onRoomCompositionEnd(e) { composing = false; sanitizeRoom(); }
function onRoomInput(e) {
  if (composing || e.isComposing) return
  sanitizeRoom()
}
function sanitizeRoom() {
  // 只保留字母数字与已有短横线，转大写
  let v = roomId.value.toUpperCase().replace(/[^A-Z0-9-]/g, '')
  // 自动补 '-'：前面已输入满 4 位、且尚未出现连字符时，在该位置自动插入一条
  if (!v.includes('-') && v.replace(/-/g, '').length > 4) {
    v = v.slice(0, 4) + '-' + v.slice(4)
  }
  // 会议号为 XXXX-XXXX 格式，最多 9 个字符；残留非法多出的部分截断
  roomId.value = v.slice(0, 9)
}

function enterRoom(id, opts = {}) {
  const n = name.value.trim()
  if (!n) {
    toast('请先输入昵称', 'error')
    focusName()
    return
  }
  // 非安全上下文（如局域网 IP 的 http）：浏览器会禁用摄像头/麦克风/屏幕共享
  if (!window.isSecureContext) {
    const ok = confirm(
      '当前通过非安全地址访问，浏览器将禁用摄像头、麦克风和屏幕共享，只能以旁听模式加入。\n\n' +
      '· 本机使用：请改用 http://localhost:3000\n' +
      '· 其他设备：请使用 https://本机IP:5679 地址\n' +
      '  （首次访问需在浏览器中点击"高级 → 继续前往"信任证书）\n\n' +
      '是否仍以旁听模式加入？'
    )
    if (!ok) return
  }
  setUserName(n)
  const params = new URLSearchParams({ room: id, name: n })
  if (opts.video !== undefined) params.set('video', opts.video ? '1' : '0')
  if (opts.cfg) {
    if (opts.cfg.title) params.set('t', opts.cfg.title)
    if (opts.cfg.maxPeers) params.set('mx', String(opts.cfg.maxPeers))
    if (opts.cfg.durationMinutes) params.set('du', String(opts.cfg.durationMinutes))
    if (opts.cfg.onExpire) params.set('ex', opts.cfg.onExpire)
  }
  location.href = '/room.html?' + params.toString()
}

const nameRef = ref(null)
function focusName() { nameRef.value && nameRef.value.focus() }

function join() {
  const id = roomId.value.trim().toUpperCase()
  if (!id) {
    toast('请输入会议号', 'error')
    return
  }
  if (!/^[A-Z0-9-]{4,16}$/.test(id)) {
    toast('会议号为 4-16 位字母 / 数字 / 短横线', 'error')
    return
  }
  enterRoom(id)
}

function openCreate() {
  cfgTitle.value = ''
  cfgMax.value = 8
  cfgDur.value = 60
  cfgExpire.value = 'remind'
  modalOpen.value = true
}
function closeCreate() { modalOpen.value = false; }

function submitCreate() {
  const title = cfgTitle.value.trim()
  if (!title) { toast('请填写会议名称', 'error'); return }
  const maxPeers = Math.max(1, Math.min(50, parseInt(cfgMax.value, 10) || 8))
  const dur = Math.max(1, Math.min(1440, parseInt(cfgDur.value, 10) || 60))
  // 生成形如 8F3K-2Q9D 的会议号
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
  const pick = () => Array.from({ length: 4 }, () => chars[Math.floor(Math.random() * chars.length)]).join('')
  modalOpen.value = false
  enterRoom(pick() + '-' + pick(), { cfg: { title, maxPeers, durationMinutes: dur, onExpire: cfgExpire.value } })
}

async function loadRecent() {
  let hist = []
  try { hist = JSON.parse(localStorage.getItem('litemeet.history') || '[]') } catch { /* 忽略 */ }
  if (!Array.isArray(hist)) hist = []

  // 我的录制按会议号归类（只取每场最新一条）
  const recByRoom = new Map()
  try {
    const recs = await API.get('/records?ownerId=' + encodeURIComponent(getUserId()))
    for (const r of recs) {
      if (r.roomId && !recByRoom.has(r.roomId)) recByRoom.set(r.roomId, r)
    }
  } catch { /* 静默 */ }

  if (!hist.length && !recByRoom.size) { recent.value = []; return }

  const seen = new Set()
  const rows = []
  for (const h of hist) {
    if (!h || seen.has(h.roomId)) continue
    seen.add(h.roomId)
    rows.push({ roomId: h.roomId, name: h.name || '', joinedAt: h.joinedAt || 0, rec: recByRoom.get(h.roomId) || null })
  }
  for (const [roomId2, rec] of recByRoom) {
    if (seen.has(roomId2)) continue
    seen.add(roomId2)
    rows.push({ roomId: roomId2, name: rec.ownerName || '', joinedAt: new Date(rec.createdAt).getTime() || 0, rec })
  }
  rows.sort((a, b) => b.joinedAt - a.joinedAt)
  recent.value = rows.slice(0, 5)
}

function openRecent(r) {
  if (r.rec) {
    location.href = '/record.html?id=' + encodeURIComponent(r.rec.id)
  } else {
    roomId.value = r.roomId
    toast('会议号已填入，点击「加入会议」即可重新进入', 'ok')
  }
}

// 分享链接进入（?room=会议号）：自动填入会议号
const presetRoom = getParam('room')
name.value = getUserName()
if (presetRoom) roomId.value = presetRoom.trim().toUpperCase()
onMounted(() => {
  loadRecent()
  if (presetRoom) focusName()
})
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <svg width="30" height="30" viewBox="0 0 32 32" fill="none" aria-hidden="true">
        <rect x="1" y="1" width="30" height="30" rx="8" fill="#6366f1"/>
        <rect x="7" y="10" width="12" height="12" rx="3" stroke="#fff" stroke-width="2.2"/>
        <path d="M19 14l5-3v10l-5-3" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
      </svg>
      轻会议<span class="lite">LiteMeet</span>
    </div>
    <nav>
      <a href="/" class="active">首页</a>
      <a href="/records.html">会议记录</a>
      <a href="/settings.html">设置</a>
    </nav>
    <div class="spacer"></div>
  </header>

  <main class="page">
    <div class="home-hero">
      <section class="hero-intro">
        <div class="hero-badge">
          <span class="dot"></span>
          一条链接 · 即刻开会
        </div>
        <h1 class="hero-title">开个会，<br>就这么简单</h1>
        <p class="hero-sub">轻会议 LiteMeet 是一个简单易用的音视频会议与智能纪要工具——无需安装注册，复制一条链接即可和他人开会同视频，实时转写、AI 纪要一应俱全。</p>
        <div class="hero-points">
          <span>多人音视频</span>
          <span>屏幕共享</span>
          <span>实时字幕</span>
          <span>AI 纪要</span>
        </div>
      </section>

      <div class="card join-card">
        <h2>开始会议</h2>
        <p class="desc">输入昵称后即可创建或加入会议，无需注册账号</p>
        <div class="field">
          <label>我的昵称</label>
          <input ref="nameRef" v-model="name" class="input" placeholder="例如：张三" maxlength="32" @input="onNameInput">
        </div>
        <div class="field">
          <label>会议号</label>
          <div class="room-input-group">
            <input v-model="roomId" class="input" placeholder="XXXX-XXXX" maxlength="16" autocomplete="off"
              autocapitalize="characters" autocorrect="off" spellcheck="false"
              @keydown.enter="join"
              @compositionstart="onRoomCompositionStart"
              @compositionend="onRoomCompositionEnd"
              @input="onRoomInput">
            <button class="btn" @click="join">加入会议</button>
          </div>
          <div class="hint">加入已有会议请输入对方分享的会议号</div>
        </div>
        <button class="btn secondary" style="width:100%" @click="openCreate">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
          创建新会议（设置名称 / 人数 / 时长）
        </button>
      </div>
    </div>

    <div class="home-side">
      <div class="card feature-card">
        <div class="ic">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2"/>
          </svg>
        </div>
        <div>
          <b>高清音视频会议</b>
          <span>多人实时音视频通话与屏幕共享，画面自适应布局</span>
        </div>
      </div>
      <div class="card feature-card">
        <div class="ic">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/>
          </svg>
        </div>
        <div>
          <b>实时转写字幕</b>
          <span>会议中边说边转写，实时生成字幕与完整文字记录</span>
        </div>
      </div>
      <div class="card feature-card">
        <div class="ic">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
          </svg>
        </div>
        <div>
          <b>AI 智能纪要</b>
          <span>会后一键生成结构化会议纪要：要点、决定、待办</span>
        </div>
      </div>
      <div class="card feature-card">
        <div class="ic">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0110 0v4"/>
          </svg>
        </div>
        <div>
          <b>即点即用</b>
          <span>无需安装下载，复制一条链接即可加入会议</span>
        </div>
      </div>
    </div>

    <div class="recent-list">
      <h3>最近会议记录</h3>
      <div class="card">
        <div v-if="!recent.length" class="empty">暂无会议记录</div>
        <div v-for="r in recent" :key="r.roomId" class="record-row" @click="openRecent(r)">
          <div style="flex:1;min-width:0">
            <div class="r-title">会议 {{ r.roomId }}</div>
            <div class="r-meta">{{ fmtDate(r.joinedAt) }} · {{ r.name || '我' }}</div>
          </div>
          <span v-if="r.rec" class="badge blue">{{ r.rec.mode === 'video' ? '有录屏' : '有录音' }}</span>
          <span v-else class="badge green">已加入</span>
        </div>
      </div>
    </div>

    <!-- 创建会议设置弹窗 -->
    <div v-if="modalOpen" class="room-mask" @click.self="closeCreate" @keydown.esc="closeCreate" tabindex="-1">
      <div class="room-modal" role="dialog" aria-modal="true" aria-labelledby="createTitle">
        <div class="rm-title">
          <span id="createTitle">创建新会议</span>
          <button type="button" class="share-close" @click="closeCreate" aria-label="关闭">×</button>
        </div>
        <div class="rm-field">
          <span>会议名称</span>
          <input v-model="cfgTitle" class="rename-input" maxlength="64" placeholder="例如：产品评审会">
        </div>
        <div class="rm-field">
          <span>最大人数（1–50）</span>
          <input v-model="cfgMax" class="rename-input" type="number" min="1" max="50">
        </div>
        <div class="rm-field">
          <span>会议时长（分钟）</span>
          <input v-model="cfgDur" class="rename-input" type="number" min="1" max="1440">
        </div>
        <div class="rm-field">
          <span>时长到期后</span>
          <select v-model="cfgExpire" class="rename-input">
            <option value="remind">仅提醒，不结束</option>
            <option value="auto">自动结束会议</option>
          </select>
        </div>
        <div class="rm-actions">
          <button type="button" class="btn" @click="closeCreate">取消</button>
          <button type="button" class="btn primary" @click="submitCreate">创建并进入</button>
        </div>
      </div>
    </div>
  </main>
</template>