import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'
import { oauthDevApi } from './server/oauthDevApi.js'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // .env의 모든 값(VITE_ 아닌 Client Secret 포함)을 서버 쪽 플러그인에만 넘긴다. 브라우저 번들에는 VITE_ 값만 들어간다.
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react(), oauthDevApi(env)],
    server: { port: 5173, strictPort: true },
    preview: { port: 4173 },
  }
})
