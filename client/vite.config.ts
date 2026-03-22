import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/rto-queue': 'http://127.0.0.1:8000',
      // Fill DMS runs Playwright (often 1–3+ min); default proxy timeout causes 502 Bad Gateway.
      '/fill-dms': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        timeout: 200_000,
        proxyTimeout: 200_000,
      },
      '/submit-info': 'http://127.0.0.1:8000',
      // Upload endpoints run OCR in the same request; default proxy timeout → browser "Failed to fetch".
      '/uploads': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        timeout: 600_000,
        proxyTimeout: 600_000,
      },
      '/ai-reader-queue': 'http://127.0.0.1:8000',
      '/vision': 'http://127.0.0.1:8000',
      '/dealers': 'http://127.0.0.1:8000',
      '/textract': 'http://127.0.0.1:8000',
      '/qr-decode': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/settings': 'http://127.0.0.1:8000',
      '/customer-search': 'http://127.0.0.1:8000',
      '/bulk-loads': 'http://127.0.0.1:8000',
      '/admin': 'http://127.0.0.1:8000',
      '/documents': 'http://127.0.0.1:8000',
      '/dummy-vaahan': 'http://127.0.0.1:8000',
      '/dummy-dms': 'http://127.0.0.1:8000',
      '/dummy-insurance': 'http://127.0.0.1:8000',
    },
  },
})
