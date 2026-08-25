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

## Open task: Paste as document

When a user pastes a large block of text into any composer input, it should
collapse into an attachment tile instead of landing in the textarea.

### Thresholds (already in `backend/config.py`)

```python
PASTE_MIN_CHARS = 1200
PASTE_MIN_LINES = 20
PASTES_DIR = STATE_DIR / "pastes"
PASTE_RETENTION_DAYS = 30
```

Trigger the tile when paste is ≥ PASTE_MIN_CHARS **or** ≥ PASTE_MIN_LINES.
Below both thresholds: fall through normally, no interception.

### Behaviour

1. Intercept the `paste` event — do NOT let the text land in the textarea.
2. Show an attachment tile above the composer input with:
   - 📄 icon
   - Auto-generated name: first 40 chars of first line, slugified (lowercase, spaces→dashes, strip punctuation)
   - Char count + line count: e.g. "1 847 chars · 34 lines"
   - A small ✕ button to remove the attachment before sending
3. On send, write the content to a file:
   `~/.osa-kiro/pastes/<session_id>/<name>.txt`
   via `POST /api/sessions/{id}/paste` (new endpoint, to be created in `backend/api.py`)
4. Prepend a reference line to the outgoing task text:
   `[attached: <name>.txt — <N> lines, <size>]`
5. Retention: `PASTE_RETENTION_DAYS` already defined; a sweep can be added later — skip it for now.

### Where to implement

- `frontend/src/components/PasteAttachments.jsx` — already exists, check its contents first
- `frontend/src/components/DetailPanel.jsx` — main composer is `.composer-input` / `.composer-row`
- `frontend/src/App.jsx` — card reply and wall sheet composers also need interception
- `backend/api.py` — add the paste endpoint following existing patterns (see `dispatch_task` ~line 5193)
- `frontend/src/App.css` — add tile styles; include a `@media (max-width: 700px)` block for mobile

### Done when

```
curl -s -X POST http://127.0.0.1:19419/api/sessions/test/paste \
  -H "Content-Type: application/json" \
  -d '{"name":"test.txt","content":"hello world"}' | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok' if d.get('ok') else d)"
```

And manually: paste 30+ lines into the detail panel composer → tile appears, send → `[attached: ...]` prepended to the message.
