import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// /api 요청은 FastAPI 백엔드(8000)로 프록시 → 같은 오리진처럼 동작, CORS 불필요
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
