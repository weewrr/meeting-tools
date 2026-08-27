<script setup>
import { ref, computed, onMounted } from 'vue'
import { API, toast, fmtDate, fmtDuration, getUserId } from '@/utils/common'
import AppTopbar from '@/components/AppTopbar.vue'
import { Search, Upload } from '@element-plus/icons-vue'

const allRecords = ref([])
const keyword = ref('')
const loadError = ref('')
const loading = ref(false)

const list = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return allRecords.value
  return allRecords.value.filter(r => {
    if ((r.title || '').toLowerCase().includes(kw)) return true
    return (r.transcript || []).some(s => (s.text || '').toLowerCase().includes(kw))
  })
})

function statusMeta(r) {
  if (r.summary) return { type: 'primary', text: '已生成纪要' }
  if (r.transcript.length) return { type: 'success', text: '已转写' }
  if (r.status === 'recording') return { type: 'info', text: '录制中' }
  return { type: 'info', text: '未转写' }
}

async function load() {
  loading.value = true
  try {
    allRecords.value = await API.get('/records?ownerId=' + encodeURIComponent(getUserId()))
    loadError.value = ''
  } catch (e) {
    loadError.value = '加载失败：' + e.message
  } finally {
    loading.value = false
  }
}

function go(r) {
  location.href = '/record.html?id=' + encodeURIComponent(r.id)
}

async function remove(r) {
  try {
    await ElMessageBox.confirm('确定删除该会议记录及其录音？此操作不可恢复。', '删除记录', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning'
    })
  } catch { return }
  try {
    await API.del('/records/' + r.id)
    toast('已删除', 'ok')
    load()
  } catch (e) {
    toast(e.message, 'error')
  }
}

const importRef = ref(null)
function pickImport() { importRef.value && importRef.value.click() }

async function onImportFile(e) {
  const file = e.target.files[0]
  e.target.value = ''
  if (!file) return
  const title = await ElMessageBox.prompt('请为这段录音命名：', '导入音频转写', {
    inputValue: file.name.replace(/\.[^.]+$/, ''),
    confirmButtonText: '开始转写', cancelButtonText: '取消'
  }).then(({ value }) => value || file.name).catch(() => null)
  if (title === null) return
  toast('正在转写，请稍候（时长取决于音频长度与转写服务）…')
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
      <el-input v-model="keyword" :prefix-icon="Search" clearable placeholder="搜索标题或转写内容…" style="max-width:320px" />
      <div class="spacer"></div>
      <input ref="importRef" type="file" id="importFile" accept="audio/*,video/*" style="display:none" @change="onImportFile">
      <el-button type="primary" :icon="Upload" @click="pickImport">导入音频转写</el-button>
    </div>

    <div class="card record-table">
      <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" show-icon margin-top="8px" />

      <el-table v-else :data="list" v-loading="loading" style="width:100%" :row-class-name="'rowlink'"
        empty-text="暂无会议记录" @row-click="go">
        <el-table-column label="标题" min-width="220">
          <template #default="{ row }">
            <span class="td-title">{{ row.title }}</span>
          </template>
        </el-table-column>
        <el-table-column label="时间" min-width="150">
          <template #default="{ row }">{{ fmtDate(row.createdAt) }}</template>
        </el-table-column>
        <el-table-column label="时长" width="90">
          <template #default="{ row }">{{ row.duration ? fmtDuration(row.duration) : '—' }}</template>
        </el-table-column>
        <el-table-column label="转写" width="90">
          <template #default="{ row }">{{ row.transcript.length }} 段</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusMeta(row).type" effect="light" round size="small">{{ statusMeta(row).text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="right">
          <template #default="{ row }">
            <el-button size="small" type="danger" plain @click.stop="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </main>
</template>

<style scoped>
.record-table :deep(.el-table) { border-radius: 16px; }
.record-table :deep(.el-table__row) { cursor: pointer; }
.record-table :deep(.el-table__row:hover) { background: var(--brand-50); }
.td-title { font-weight: 600; }
</style>