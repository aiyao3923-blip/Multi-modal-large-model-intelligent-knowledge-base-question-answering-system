import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const proxy = { '/api': { target: env.VITE_API_PROXY || 'http://127.0.0.1:8001', changeOrigin: true } }
  return { plugins: [vue()], resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } }, server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy }, preview: { host: '127.0.0.1', port: 4173, strictPort: true, proxy }, build: { manifest: true, rollupOptions: { output: { manualChunks(id) { if (id.includes('node_modules/zrender/')) return 'chart-renderer'; if (id.includes('node_modules/echarts/')) return 'charts'; if (/node_modules\/(?:@vue|vue|vue-router|pinia)\//.test(id)) return 'vue' } } } } }
})
