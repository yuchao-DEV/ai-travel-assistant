import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    vueDevTools(),
  ],
  resolve: {
    alias: {
      // 双端共用 src/api、src/utils、src/composables
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    rollupOptions: {
      input: {
        // 移动端 H5（Vant 4）：访问 /
        mobile: fileURLToPath(new URL('./index.html', import.meta.url)),
        // 桌面端 Web（Element Plus）：访问 /pc/
        pc: fileURLToPath(new URL('./pc/index.html', import.meta.url)),
      },
    },
  },
  server: {
    proxy: {
      // 开发环境代理，前端请求 /api 转发到后端 8080，避免跨域
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
})
