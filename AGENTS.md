# Quarterdeck — Agent Context

## What this project is

Quarterdeck is a macOS control surface for Kiro CLI sessions. It watches
`~/.kiro/sessions/cli/` and presents a real-time dashboard with dispatch,
approval gating, and remote phone access.

Repo: `/Users/a.vidanov/Documents/PROJECTS/PERSONAL/osa-kiro`

## Dev workflow

Two modes, two ports — never mix them:

| Mode | Start | Backend | Frontend |
|---|---|---|---|
| Dev (use this) | `./start.sh` | `http://127.0.0.1:19419` | `http://localhost:5173` (Vite HMR) |
| Stable app | Open `/Applications/Quarterdeck.app` | `http://127.0.0.1:19418` | bundled |

Before writing any code, confirm dev is running:
```
lsof -i :19419 | grep LISTEN
lsof -i :5173  | grep LISTEN
```

If not running: `cd /Users/a.vidanov/Documents/PROJECTS/PERSONAL/osa-kiro && source venv/bin/activate && ./start.sh`

Backend auto-reloads on save (uvicorn `--reload`). Frontend auto-reloads via Vite HMR. No manual restarts needed for either.

**Never rebuild the stable app to test a change.** Test in dev first.

## Stack

- Python 3.14, FastAPI, uvicorn — `backend/`
- React 19, plain JSX (no TypeScript), Vite — `frontend/src/`
- No database. State in `~/.osa-kiro/` (JSON files, JSONL logs)
- pywebview + PyInstaller for the native `.app`

## Key files

- `backend/api.py` — all FastAPI routes (~138 routes)
- `backend/config.py` — paths, constants, port definitions
- `backend/tmux_manager.py` — session spawn, correlation, gate logic
- `frontend/src/App.jsx` — session grid, layout, state
- `frontend/src/components/DetailPanel.jsx` — transcript, composer, approval UI
- `frontend/src/App.css` — all styles (dark control-surface theme)
- `docs/CHANGELOG.md` — completed work with root-cause notes
- `docs/ROADMAP.md` — forward-looking work, ordered by priority

## Conventions

- No TypeScript — plain JSX only
- No new dependencies without a strong reason
- Read every file before modifying it
- `git add <specific files>` only — never `git add .`
- Do not push to main; commit to a branch or leave for the user to push
- Verify the dev backend is alive before claiming anything works:
  `curl -s http://127.0.0.1:19419/api/sessions | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('sessions',[])), 'sessions')"`
