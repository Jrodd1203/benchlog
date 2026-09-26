import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Forward API calls to the local FastAPI service (`benchlog serve`).
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
    // Allow importing ../examples (fallback data when the API isn't running).
    fs: { allow: ['..'] },
  },
})
