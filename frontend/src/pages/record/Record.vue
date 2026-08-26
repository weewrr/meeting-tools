<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { API, toast, fmtDate, fmtDuration, getParam, getUserId, renderMarkdown } from '@/utils/common'
import AppTopbar from '@/components/AppTopbar.vue'

const API_BASE = window.LM_BACKEND || location.origin
const id = getParam('id')

const record = ref(null)
const title = ref('')
const segment = ref('transcript')
const videoUrl = ref('')
const videoRef = ref(null)
const transcriptHtml = computed(() => '') // 转写用 v-for，不生成 HTML
const summaryHtml = computed(() => record.value && record.value.summary ? renderMarkdown(record.value.summary.content) : '')
const busySummary = ref(false)
const busyTranscribe = ref(false)
const invalid = ref(false)

const canDownloadVideo = computed(() => record.value && record.value.videoFile && !record.value.audioFile)
const canDownloadAudio = computed(() => record.value && record.value.audioFile)

let saveTimer = null

function onTitleInput() {
  clearTimeout(saveTimer)
  saveTimer = setTimeout(async () => {
    try {
      const t = title.value.trim()
      await API.patch('/records/' + id, { title: t || '未命名会议' })
      if (record.value) record.value.title = t || '未命名会议'
    } catch { /* 忽略 */ }
  }, 600)
}

async function load() {
  try {
    const r = await API.get('/records/' + id + '?ownerId=' + encodeURIComponent(getUserId()))
    record.value = r
    title.value = r.title
    // 视频
    if (r.videoFile) {
      videoUrl.value = API_BASE + '/api/media/' + encodeURIComponent(r.videoFile)
    } else {
      videoUrl.value = ''
    }
    if (getParam('fresh') === '1') segment.value = 'transcript'
  } catch (e) {
    invalid.value = true
    toast(e.message, 'error')
    setTimeout(() => (location.href = '/records.html'), 1200)
  }
}

async function genSummary() {
  busySummary.value = true
  try {
    record.value = await API.post(`/records/${id}/summary`)
    await load()
    segment.value = 'summary'
    toast('纪要已生成', 'ok')
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    busySummary.value = false
  }
}

async function retranscribe() {
  if (!confirm('将对完整录音重新转写，现有转写内容会被覆盖。继续？')) return
  busyTranscribe.value = true
  toast('正在转写完整录音…')
  try {
    await API.post(`/records/${id}/transcribe`)
    await load()
    toast('转写完成', 'ok')
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    busyTranscribe.value = false
  }
}

function download() {
  if (!record.value) return
  const a = document.createElement('a')
  if (canDownloadVideo.value) {
    a.href = API_BASE + '/api/media/' + encodeURIComponent(record.value.videoFile)
    a.download = (record.value.title || 'recording') + (record.value.videoFile.match(/\.[^.]+$/) || [''])[0]
  } else if (canDownloadAudio.value) {
    a.href = API_BASE + '/api/audio/' + encodeURIComponent(record.value.audioFile)
    a.download = (record.value.title || 'recording') + (record.value.audioFile.match(/\.[^.]+$/) || [''])[0]
  } else return
  document.body.appendChild(a)
  a.click()
  a.remove()
}

async function remove() {
  if (!confirm('确定删除该会议记录及其录音？此操作不可恢复。')) return
  try {
    await API.del('/records/' + id)
    location.href = '/records.html'
  } catch (e) {
    toast(e.message, 'error')
  }
}

onBeforeUnmount(() => clearTimeout(saveTimer))

if (!id) { location.href = '/records.html' }
onMounted(load)
</script>

<template>
  <AppTopbar active="records" />

  <main class="page">
    <template v-if="record">
      <div class="detail-head">
        <div style="flex:1;min-width:0">
          <input v-model="title" class="d-title" placeholder="会议标题" @input="onTitleInput">
          <div class="detail-meta">
            {{ fmtDate(record.createdAt) }}
            {{ record.duration ? '· 时长 ' + fmtDuration(record.duration) : '' }}
            {{ record.host ? '· 发起人 ' + record.host : '' }}
            {{ record.roomId ? '· 会议号 ' + record.roomId : '' }}
            {{ record.status === 'recording' ? '（录制进行中）' : '' }}
          </div>
        </div>
        <div style="display:flex;gap:10px;flex-shrink:0;align-items:flex-start">
          <button v-if="canDownloadVideo || canDownloadAudio" class="btn small secondary" @click="download">{{ canDownloadVideo ? '下载录屏' : '下载录音' }}</button>
          <button v-if="canDownloadAudio" class="btn small secondary" :disabled="busyTranscribe" @click="retranscribe">重新转写</button>
          <button class="btn small" :disabled="busySummary" @click="genSummary">{{ record.summary ? '重新生成纪要' : '生成 AI 纪要' }}</button>
          <button class="btn small danger" @click="remove">删除</button>
        </div>
      </div>

      <div v-if="videoUrl" class="card" style="margin-bottom:16px;padding:0;overflow:hidden">
        <video ref="videoRef" :src="videoUrl" controls playsinline style="display:block;width:100%;max-height:56vh;background:#000"></video>
      </div>

      <div class="detail-body">
        <div>
          <div class="segment-tabs">
            <button :class="{ active: segment === 'transcript' }" @click="segment = 'transcript'">转写文字</button>
            <button :class="{ active: segment === 'summary' }" @click="segment = 'summary'">AI 会议纪要</button>
          </div>

          <div v-show="segment === 'transcript'" class="card transcript-box">
            <template v-if="record.transcript.length">
              <div v-for="(seg, i) in record.transcript" :key="i" class="seg">
                <span class="seg-time">{{ seg.offset ? fmtDuration(seg.offset) : fmtDate(seg.t) }}</span>{{ seg.text }}
              </div>
            </template>
            <div v-else class="empty">
              {{ record.audioFile ? '暂无转写内容，可点击右上角「重新转写」对录音进行转写' : '暂无转写内容' }}
            </div>
          </div>

          <div v-show="segment === 'summary'" class="card summary-box">
            <div v-if="record.summary">
              <div v-html="summaryHtml"></div>
              <div style="margin-top:24px;color:var(--text-3);font-size:12px">由 {{ record.summary.model }} 生成于 {{ fmtDate(record.summary.generatedAt) }}</div>
            </div>
            <div v-else class="empty">尚未生成 AI 纪要。点击右上角「生成 AI 纪要」，将基于转写内容自动生成结构化纪要。</div>
          </div>
        </div>
      </div>
    </template>
  </main>
</template>