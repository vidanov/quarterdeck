import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev proxies to the dev backend (DEV_PORT in backend/config.py), not to the
// installed app on 19418 — otherwise `npm run dev` shows you the installed
// app's sessions while you edit code that is not running. DECK_PORT overrides,
// and start.sh exports it for you.
const BACKEND_PORT = process.env.DECK_PORT || 19419

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
      '/api': `http://127.0.0.1:${BACKEND_PORT}`,
    },
  },
  build: { outDir: 'dist' },
  base: command === 'build' ? '/app/' : '/',
}))
