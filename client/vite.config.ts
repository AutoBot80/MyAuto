import { defineConfig, type Plugin, type ProxyOptions } from 'vite'
import react from '@vitejs/plugin-react'
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import type { Server as Http1Server } from 'node:http'

/** Shared agent for long proxies: keep-alive + no socket timeout (reduces ECONNRESET on large POSTs to uvicorn on Windows). */
const longRunningAgent = new http.Agent({
  keepAlive: true,
  keepAliveMsecs: 60_000,
  timeout: 0,
})

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
    agent: longRunningAgent,
    configure(proxy) {
      proxy.on('proxyReq', (proxyReq, req) => {
        req.setTimeout(0)
        proxyReq.setTimeout(0)
        const sock = req.socket
        if (sock && !sock.destroyed) {
          sock.setTimeout(0)
          sock.setKeepAlive(true, 60_000)
        }
      })
      proxy.on('proxyRes', (proxyRes) => {
        proxyRes.setTimeout(0)
      })
      proxy.on('open', (proxySocket) => {
        proxySocket.setTimeout(0)
        proxySocket.setKeepAlive(true, 60_000)
      })
    },
  }
}

// https://vite.dev/config/
// Production builds must use a relative base so `file://` loads work in the Electron shell
// (absolute "/assets/..." breaks when opening packaged `client-dist/index.html`).
function readAppVersion(): string {
  const candidates = [
    path.resolve(__dirname, '..', 'electron', 'package.json'),
    path.resolve(process.cwd(), '..', 'electron', 'package.json'),
    path.resolve(process.cwd(), 'electron', 'package.json'),
  ]
  for (const p of candidates) {
    try {
      if (fs.existsSync(p)) {
        const pkg = JSON.parse(fs.readFileSync(p, 'utf-8'))
        return pkg.version || '0.0.0'
      }
    } catch { /* skip */ }
  }
  return '0.0.0'
}

export default defineConfig(({ command }) => ({
  base: command === 'build' ? './' : '/',
  define: { __APP_VERSION__: JSON.stringify(readAppVersion()) },
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
      /** Playwright teardown (tab close, Release Browsers, challan retry prep) — same host as uvicorn. */
      '/system': 'http://127.0.0.1:8000',
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
}))
