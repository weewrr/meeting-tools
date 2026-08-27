import { fileURLToPath, URL } from 'node:url'
import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  plugins: [
    vue(),
    // Element Plus 按需自动引入：组件 + 脚本 API（ElMessage/ElMessageBox…）+ 对应样式
    AutoImport({
      imports: ['vue'],
      resolvers: [ElementPlusResolver()],
      dts: false
    }),
    Components({
      resolvers: [ElementPlusResolver()],
      dts: false
    })
  ],
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
    host: true,   // 监听 0.0.0.0：局域网设备可通过 http://<本机IP>:5173 直接访问 dev 页面（实时热更新）
    port: 5173,
    proxy: {
      // 开发环境把 API / 信令 WS 代理到后端（后端单端口 5678）
      '/api': { target: 'http://localhost:5678', changeOrigin: true },
      '/ws': { target: 'ws://localhost:5678', ws: true }
    }
  }
})