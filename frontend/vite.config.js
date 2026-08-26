import { fileURLToPath, URL } from 'node:url'
import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // 多页应用(MPA)：每个页面都是独立 Vue 入口，产线由后端单端口统一静态托管
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        records: resolve(__dirname, 'records.html'),
        settings: resolve(__dirname, 'settings.html'),
        record: resolve(__dirname, 'record.html'),
        room: resolve(__dirname, 'room.html')
      }
    }
  },
  server: {
    port: 5173,
    proxy: {
      // 开发环境把 API / 信令 WS 代理到后端（后端单端口 5678）
      '/api': { target: 'http://localhost:5678', changeOrigin: true },
      '/ws': { target: 'ws://localhost:5678', ws: true }
    }
  }
})