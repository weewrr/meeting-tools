<script setup>
defineProps({
  active: { type: String, default: '' } // '' | 'records' | 'settings'
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
      <a href="/" :class="{ active: active === '' }">首页</a>
      <a href="/records.html" :class="{ active: active === 'records' }">会议记录</a>
      <a href="/settings.html" :class="{ active: active === 'settings' }">设置</a>
    </nav>
    <div class="spacer"></div>
  </header>
</template>