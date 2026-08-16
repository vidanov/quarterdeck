import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { execSync } from 'child_process'

// Dev proxies to the dev backend (DEV_PORT in backend/config.py), not to the
// installed app on 19418 — otherwise `npm run dev` shows you the installed
// app's sessions while you edit code that is not running. DECK_PORT overrides,
// and start.sh exports it for you.
const BACKEND_PORT = process.env.DECK_PORT || 19419

// In dev mode the browser cannot inject X-Local-Token (that's done by
// pywebview in app.py). Read the token from the macOS keychain here so Vite's
// proxy can inject it on every forwarded request — same security boundary as
// the running app (local process on this Mac).
function readLocalToken() {
  try {
    return execSync(
      'security find-generic-password -s "com.vidanov.quarterdeck" -a "local-token" -w',
      { encoding: 'utf8', stdio: ['pipe', 'pipe', 'ignore'] }
    ).trim()
  } catch {
    return ''
  }
}

const LOCAL_TOKEN = readLocalToken()

export default defineConfig(({ command }) => ({
  plugins: [react()],
  define: {
    // process.env.HOME is used to filter the home directory from cwd suggestions.
    // In dev mode Vite doesn't shim process, so define it explicitly.
    'process.env.HOME': JSON.stringify(process.env.HOME || '/Users/' + (process.env.USER || 'user')),
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${BACKEND_PORT}`,
        // Inject the local token so the backend's loopback auth check passes.
        // Without this, dev-mode browser requests get 401 because the browser
        // has no way to call security(1) or the keychain directly.
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            if (LOCAL_TOKEN) {
              proxyReq.setHeader('X-Local-Token', LOCAL_TOKEN)
            }
          })
        },
      },
    },
  },
  build: { outDir: 'dist' },
  base: command === 'build' ? '/app/' : '/',
}))
