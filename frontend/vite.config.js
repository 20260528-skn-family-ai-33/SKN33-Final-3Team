import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Only VITE_ values enter the browser bundle; the proxy target stays server-side.
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react()],
    server: { port: 5173, strictPort: true, proxy: { '/api/v1': { target: env.API_PROXY_TARGET || 'http://127.0.0.1:8000' } } },
    preview: { port: 4173, proxy: { '/api/v1': { target: env.API_PROXY_TARGET || 'http://127.0.0.1:8000' } } },
  }
})
