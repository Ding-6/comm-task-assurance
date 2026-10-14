import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import AutoImport from 'unplugin-auto-import/vite';
import Components from 'unplugin-vue-components/vite';
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers';

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({ resolvers: [ElementPlusResolver()] }),
    Components({ resolvers: [ElementPlusResolver()] }),
  ],
  server: {
    port: 5173,
    host: '0.0.0.0', // 允许局域网 / 隧道访问，避免远端调试黑屏
    proxy: {
      // 后端 WebSocket 改挂到 /api/scene/ws，统一走 /api 代理（已 ws: true）
      '/api': { target: 'http://localhost:8000', changeOrigin: true, ws: true },
    },
  },
});
