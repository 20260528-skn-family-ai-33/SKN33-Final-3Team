import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Only VITE_ values enter the browser bundle; the proxy target stays server-side.
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react()],
    // 국기 SVG(flag-icons)는 작은 파일도 CSS에 그림 데이터로 넣지 않는다. 넣으면 모든 화면의 첫 CSS가
    // 수백 KB로 커진다. 파일로 두면 화면에 보이는 국기만 그때 내려받는다(#132).
    build: { assetsInlineLimit: file => (file.includes('flag-icons') ? false : undefined) },
    server: { port: 5173, strictPort: true, proxy: { '/api/v1': { target: env.API_PROXY_TARGET || 'http://127.0.0.1:8000' } } },
    preview: { port: 4173, proxy: { '/api/v1': { target: env.API_PROXY_TARGET || 'http://127.0.0.1:8000' } } },
  }
})
