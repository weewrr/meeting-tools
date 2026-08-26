<script setup>
import { ref, computed, onMounted } from 'vue'
import { API, toast, fmtDate, fmtDuration, getUserId, escapeHtml } from '@/utils/common'
import AppTopbar from '@/components/AppTopbar.vue'

const allRecords = ref([])
const keyword = ref('')
const loadError = ref('')

const list = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return allRecords.value
  return allRecords.value.filter(r => {
    if ((r.title || '').toLowerCase().includes(kw)) return true
    return (r.transcript || []).some(s => (s.text || '').toLowerCase().includes(kw))
  })
})

function statusBadge(r) {
  if (r.summary) return { cls: 'blue', text: '已生成纪要' }
  if (r.transcript.length) return { cls: 'green', text: '已转写' }
  if (r.status === 'recording') return { cls: 'gray', text: '录制中' }
  return { cls: 'gray', text: '未转写' }
}

async function load() {
  try {
    allRecords.value = await API.get('/records?ownerId=' + encodeURIComponent(getUserId()))
    loadError.value = ''
  } catch (e) {
    loadError.value = '加载失败：' + e.message
  }
}

async function remove(r) {
  if (!confirm('确定删除该会议记录及其录音？此操作不可恢复。')) return
  try {
    await API.del('/records/' + r.id)
    load()
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function onImportFile(e) {
  const file = e.target.files[0]
  e.target.value = ''
  if (!file) return
  const title = prompt('请为这段录音命名：', file.name.replace(/\.[^.]+$/, '')) || file.name
  const tid = toast('正在转写，请稍候（时长取决于音频长度与转写服务）…')
  try {
    const fd = new FormData()
    fd.append('audio', file, file.name)
    const resp = await fetch(`/api/transcribe/file`, { method: 'POST', body: fd })
    const data = await resp.json()
    if (!resp.ok) throw new Error(data.error || '转写失败')
    await API.post('/records/import', {
      title,
      text: data.text,
      audioFile: data.audioFile,
      ownerId: getUserId(),
      ownerName: localStorage.getItem('litemeet.name') || '我'
    })
    toast('导入并转写完成', 'ok')
    load()
  } catch (err) {
    toast('转写失败：' + err.message, 'error')
  }
}

onMounted(load)
</script>

<template>
  <AppTopbar active="records" />

  <main class="page">
    <h1 class="page-title">会议记录</h1>
    <p class="page-sub">会议记录、转写与纪要均保存在本服务 data 目录</p>

    <div class="toolbar-row">
      <input v-model="keyword" class="input" placeholder="搜索标题或转写内容…">
      <div class="spacer"></div>
      <input type="file" id="importFile" accept="audio/*,video/*" style="display:none" @change="onImportFile">
      <button class="btn" @click="document.getElementById('importFile').click()">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
        </svg>
        导入音频转写
      </button>
    </div>

    <div class="card record-table">
      <div v-if="loadError" class="empty">{{ loadError }}</div>
      <div v-else-if="!list.length" class="empty">暂无会议记录</div>
      <table v-else>
        <thead>
          <tr>
            <th style="width:34%">标题</th>
            <th>时间</th>
            <th>时长</th>
            <th>转写</th>
            <th>状态</th>
            <th style="width:90px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in list" :key="r.id" class="rowlink" @click="$event.target.closest('.del-btn') || (location.href='/record.html?id=' + r.id)">
            <td class="td-title">{{ r.title }}</td>
            <td>{{ fmtDate(r.createdAt) }}</td>
            <td>{{ r.duration ? fmtDuration(r.duration) : '—' }}</td>
            <td>{{ r.transcript.length }} 段</td>
            <td>
              <span class="badge" :class="statusBadge(r).cls">{{ statusBadge(r).text }}</span>
            </td>
            <td class="td-actions">
              <button class="btn small danger del-btn" @click.stop="remove(r)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </main>
</template>