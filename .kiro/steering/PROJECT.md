# osa-kiro — Project Context

## What This Is
A visual agent control surface (inspired by OpenAI's Codex Micro hardware) that monitors and dispatches Kiro CLI sessions. Watches `~/.kiro/sessions/cli/` for live session state and presents it as a real-time dashboard with dispatch capabilities.

## Key Artifacts
- `app.py` — macOS app entry point (pywebview + backend thread)
- `backend/api.py` — FastAPI: session listing, detail, dispatch
- `backend/config.py` — paths and constants
- `frontend/src/App.jsx` — React UI (agent grid, dispatch box, detail panel)
- `frontend/src/App.css` — dark control-surface styling
- `start.sh` — dev launcher (backend + frontend)
- `docs/SPEC.md` — doctrine and design

## How It Works
- Scans `~/.kiro/sessions/cli/*.lock` to find active sessions (pid check)
- Reads `*.json` for metadata (title, cwd, timestamps)
- Tails `*.jsonl` for status detection (running/idle/awaiting-approval/error)
- Frontend polls `/api/sessions` every 2 seconds
- Dispatch spawns new terminal tabs via osascript

## Tech Stack
- Python 3.14, FastAPI, uvicorn (backend, port 8000)
- React 19, Vite (frontend, port 5173 in dev)
- pywebview + PyInstaller for native macOS app
- No database, no external deps beyond filesystem watching

## Conventions
- Read-only on session files (never modify `~/.kiro/sessions/cli/`)
- No TypeScript (plain JSX)
- Dark UI, SF Pro font stack, minimal chrome
- Status colors: 🟢 running, 🟡 awaiting, ⚪ idle/done, 🔴 error

## What NOT to Re-explain
- How Kiro session files work (.lock, .json, .jsonl, .history)
- The corpus-intel app pattern (same architecture)
- Why local-first and no database
