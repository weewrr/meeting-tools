<script setup>
import { ref, reactive, onMounted } from 'vue'
import { API, toast } from '@/utils/common'
import AppTopbar from '@/components/AppTopbar.vue'

const form = reactive({
  transcribe: { baseUrl: '', apiKey: '', model: '', language: 'zh', hasKey: false },
  llm: { baseUrl: '', apiKey: '', model: '', hasKey: false }
})
const saving = ref(false)
const saveHint = ref('')
const testing = ref('') // '' | 'transcribe' | 'llm'
const tTestResult = reactive({ cls: '', text: '' })
const lTestResult = reactive({ cls: '', text: '' })

onMounted(async () => {
  try {
    const cfg = await API.get('/config')
    form.transcribe.baseUrl = cfg.transcribe.baseUrl
    form.transcribe.model = cfg.transcribe.model
    form.transcribe.language = cfg.transcribe.language || 'zh'
    form.transcribe.hasKey = !!cfg.transcribe.hasKey
    form.llm.baseUrl = cfg.llm.baseUrl
    form.llm.model = cfg.llm.model
    form.llm.hasKey = !!cfg.llm.hasKey
  } catch (e) {
    toast(e.message, 'error')
  }
})

async function save() {
  const cfg = {
    transcribe: { baseUrl: form.transcribe.baseUrl.trim(), model: form.transcribe.model.trim(), language: form.transcribe.language },
    llm: { baseUrl: form.llm.baseUrl.trim(), model: form.llm.model.trim() }
  }
  if (form.transcribe.apiKey.trim()) cfg.transcribe.apiKey = form.transcribe.apiKey.trim()
  if (form.llm.apiKey.trim()) cfg.llm.apiKey = form.llm.apiKey.trim()

  saving.value = true
  saveHint.value = ''
  try {
    await API.post('/config', cfg)
    saveHint.value = '已保存 ' + new Date().toLocaleTimeString()
    toast('设置已保存', 'ok')
    form.transcribe.hasKey = !!cfg.transcribe.apiKey
    form.llm.hasKey = !!cfg.llm.apiKey
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    saving.value = false
  }
}

async function test(kind) {
  const cfg = {
    transcribe: { baseUrl: form.transcribe.baseUrl.trim(), model: form.transcribe.model.trim(), language: form.transcribe.language },
    llm: { baseUrl: form.llm.baseUrl.trim(), model: form.llm.model.trim() }
  }
  if (!form[kind].apiKey.trim()) delete cfg[kind].apiKey
  try { await API.post('/config', cfg); } catch { /* 测试仍继续 */ }

  const res = kind === 'transcribe' ? tTestResult : lTestResult
  res.cls = ''
  res.text = '测试中…'
  testing.value = kind
  try {
    const r = await API.post('/config/test', { kind })
    res.cls = r.ok ? 'ok' : 'err'
    res.text = r.message
  } catch (e) {
    res.cls = 'err'
    res.text = e.message
  } finally {
    testing.value = ''
  }
}
</script>

<template>
  <AppTopbar active="settings" />

  <main class="page">
    <h1 class="page-title">设置</h1>
    <p class="page-sub">转写与 AI 服务采用 OpenAI 兼容接口，可指向任意云端或本地服务（如 Groq、DeepSeek、Ollama、本地 Whisper 服务等）</p>

    <div class="settings-grid">
      <div class="card settings-card">
        <h3>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--brand)" aria-hidden="true">
            <path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/>
          </svg>
          语音转写服务
        </h3>
        <p class="sc-desc">用于实时字幕与录音转写。任何兼容 <code>POST /v1/audio/transcriptions</code> 的服务均可。</p>
        <div class="field">
          <label>服务地址（Base URL）</label>
          <el-input v-model="form.transcribe.baseUrl" placeholder="https://api.openai.com/v1" clearable />
          <div class="hint">以 /v1 结尾。本地部署示例：http://localhost:8000/v1</div>
        </div>
        <div class="field">
          <label>API Key</label>
          <el-input v-model="form.transcribe.apiKey" :placeholder="form.transcribe.hasKey ? '已保存（输入可覆盖）' : 'sk-…'" type="password" show-password autocomplete="new-password" />
          <div class="hint">本地服务无需 Key 可留空</div>
        </div>
        <div class="field">
          <label>模型</label>
          <el-input v-model="form.transcribe.model" placeholder="whisper-1" clearable />
          <div class="hint">OpenAI：whisper-1 · Groq：whisper-large-v3 · 本地部署以实际模型名为准</div>
        </div>
        <div class="field">
          <label>识别语言</label>
          <el-select v-model="form.transcribe.language" style="width:100%">
            <el-option value="zh" label="中文" />
            <el-option value="en" label="英文" />
            <el-option value="auto" label="自动检测" />
          </el-select>
        </div>
        <el-button :danger="tTestResult.cls === 'err'" @click="test('transcribe')" :loading="testing === 'transcribe'">测试连接</el-button>
        <div class="test-result" :class="tTestResult.cls">{{ tTestResult.text }}</div>
      </div>

      <div class="card settings-card">
        <h3>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--brand)" aria-hidden="true">
            <rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>
          </svg>
          AI 摘要服务
        </h3>
        <p class="sc-desc">用于生成会议纪要。任何兼容 <code>POST /v1/chat/completions</code> 的服务均可。</p>
        <div class="field">
          <label>服务地址（Base URL）</label>
          <el-input v-model="form.llm.baseUrl" placeholder="https://api.openai.com/v1" clearable />
          <div class="hint">本地 Ollama 示例：http://localhost:11434/v1</div>
        </div>
        <div class="field">
          <label>API Key</label>
          <el-input v-model="form.llm.apiKey" :placeholder="form.llm.hasKey ? '已保存（输入可覆盖）' : 'sk-…'" type="password" show-password autocomplete="new-password" />
          <div class="hint">本地服务无需 Key 可留空</div>
        </div>
        <div class="field">
          <label>模型</label>
          <el-input v-model="form.llm.model" placeholder="gpt-4o-mini" clearable />
          <div class="hint">示例：gpt-4o-mini / deepseek-chat / qwen2.5:7b</div>
        </div>
        <el-button :danger="lTestResult.cls === 'err'" @click="test('llm')" :loading="testing === 'llm'">测试连接</el-button>
        <div class="test-result" :class="lTestResult.cls">{{ lTestResult.text }}</div>
      </div>
    </div>

    <div style="margin-top:24px;display:flex;gap:12px">
      <el-button type="primary" :disabled="saving" :loading="saving" @click="save">{{ saving ? '保存中…' : '保存设置' }}</el-button>
      <span style="align-self:center;color:var(--text-3);font-size:13px">{{ saveHint }}</span>
    </div>

    <div class="card" style="margin-top:32px;padding:24px 28px">
      <h3 style="font-size:15px;margin-bottom:10px">关于隐私</h3>
      <p style="font-size:13px;color:var(--text-2);line-height:1.8">
        轻会议 LiteMeet 基于 WebRTC 与 LiveKit 构建，为在线会议提供音视频转发、实时转写与 AI 纪要，无需安装，复制一条链接即可加入。会议记录、录音与配置保存在服务的 <code>data/</code> 目录。若你配置了远程 AI 服务，转写与纪要功能会将音频文本发送到该服务；指向服务本机环境时则不会外传。
      </p>
    </div>
  </main>
</template>