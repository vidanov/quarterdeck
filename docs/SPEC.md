# Deck — Agent Control Surface

> A visual dashboard that monitors and dispatches Kiro CLI agents. The software equivalent of OpenAI's Codex Micro hardware: status at a glance, one-click dispatch, saved task templates, and an approval queue.

## Doctrine

1. **Read-only on session files.** Never modify `~/.kiro/sessions/cli/`. Watch and read only.
2. **Local-first.** No network required. All data comes from the filesystem.
3. **Real-time.** Status updates within 2 seconds of file changes.
4. **No-config start.** Works out of the box by watching the standard Kiro sessions directory.

## Data Source

Kiro CLI stores sessions at `~/.kiro/sessions/cli/`:
- `{uuid}.lock` — exists while session is active (JSON: `{pid, started_at}`)
- `{uuid}.json` — session metadata (title, cwd, created_at, updated_at, session_state)
- `{uuid}.jsonl` — conversation log (NDJSON: user prompts, assistant responses, tool calls)
- `{uuid}.history` — terminal prompt history

## Detection Logic

- **Active session:** `.lock` file exists AND process (from `pid` in lock) is still running
- **Idle/done session:** `.json` exists, no `.lock` file, or `.lock` pid is dead
- **Session status derivation from .jsonl tail:**
  - Last entry is assistant text → `done` or `idle`
  - Last entry is tool_use → `running` (agent is executing)
  - Last entry contains "confirm" / "proceed" / "permission" patterns → `awaiting-approval`
  - Process crashed → `error`

## UI Panels

### 1. Agent Grid (main view)
Color-coded cards showing all active + recent sessions:
- 🟢 Green = running (agent actively working)
- 🟡 Yellow = awaiting approval (agent asked a question)
- ⚪ Gray = idle/done
- 🔴 Red = error/crashed

Each card shows: title (truncated), project folder, elapsed time, last activity snippet.

### 2. Dispatch Box
- Text input for new tasks
- Project/folder picker (recent working directories)
- "Launch" button → spawns `kiro-cli chat` in a new terminal tab

### 3. Quick Actions
- Saved command templates (configurable)
- Maps to common operations per project

### 4. Session Detail (click a card)
- Full title
- Working directory
- Last N messages from the conversation
- Tail of the .jsonl output

## Tech Stack

- Python 3.14, FastAPI, uvicorn (backend, port 8000)
- React + Vite (frontend, port 5173 in dev)
- `watchdog` for filesystem monitoring (or polling fallback)
- pywebview + PyInstaller for native macOS app
- No database — all state derived from filesystem in real-time
