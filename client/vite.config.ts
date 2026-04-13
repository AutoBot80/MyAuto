import { defineConfig, type Plugin, type ProxyOptions } from 'vite'
import react from '@vitejs/plugin-react'
import type { Server as Http1Server } from 'node:http'

/** Must exceed longest Playwright/DMS run; keep in sync with `fillForms.ts` abort timers. */
const LONG_RUNNING_MS = 900_000 // 15 min

/**
 * Node 18+ defaults `http.Server.requestTimeout` to 5 minutes, which can drop the browser→Vite
 * connection while uvicorn is still working — the proxy then surfaces **502**. Disable on dev only.
 */
function devServerDisableRequestTimeout(): Plugin {
  return {
    name: 'dev-server-disable-request-timeout',
    configureServer(server) {
      const apply = () => {
        const httpServer = server.httpServer
        if (!httpServer) return
        const s = httpServer as Http1Server
        s.requestTimeout = 0
        s.headersTimeout = 0
      }
      server.httpServer?.once('listening', apply)
    },
  }
}

function longProxy(target: string): ProxyOptions {
  return {
    target,
    changeOrigin: true,
    timeout: LONG_RUNNING_MS,
    proxyTimeout: LONG_RUNNING_MS,
    configure(proxy) {
      proxy.on('proxyReq', (proxyReq, req) => {
        req.setTimeout(0)
        proxyReq.setTimeout(0)
      })
      proxy.on('proxyRes', (proxyRes) => {
        proxyRes.setTimeout(0)
      })
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), devServerDisableRequestTimeout()],
  server: {
    proxy: {
      '/rto-queue': 'http://127.0.0.1:8000',
      '/fill-forms': longProxy('http://127.0.0.1:8000'),
      '/submit-info': 'http://127.0.0.1:8000',
      '/uploads': longProxy('http://127.0.0.1:8000'),
      '/subdealer-challan': longProxy('http://127.0.0.1:8000'),
      '/ai-reader-queue': 'http://127.0.0.1:8000',
      '/vision': 'http://127.0.0.1:8000',
      '/dealers': 'http://127.0.0.1:8000',
      '/textract': 'http://127.0.0.1:8000',
      '/qr-decode': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/auth': 'http://127.0.0.1:8000',
      '/settings': 'http://127.0.0.1:8000',
      '/customer-search': 'http://127.0.0.1:8000',
      '/vehicle-search': 'http://127.0.0.1:8000',
      '/add-sales': 'http://127.0.0.1:8000',
      '/bulk-loads': 'http://127.0.0.1:8000',
      '/admin': 'http://127.0.0.1:8000',
      '/documents': 'http://127.0.0.1:8000',
    },
  },
})
