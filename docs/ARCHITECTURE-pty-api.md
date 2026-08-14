# Deck `pty-api` — Architecture & Plan

Supersedes the earlier `web-api` plan, which assumed Warp coupling was limited to tab scanning and
dispatch. It is not — see [Warp coupling inventory](#appendix-warp-coupling-inventory-on-main). That
plan also produced a read-only-ish web API, which does not support the actual goal of driving
sessions from a phone.

Branch: `pty-api`, based on `main` (commit `00f78b9`).

## Objective

Own kiro-cli sessions from the backend so they can be created, read, answered, and killed over HTTP
from any device on the tailnet. No terminal emulator required on the client, no Warp, no osascript.

## Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Process backing | **tmux** — one detached tmux session per kiro session |
| 2 | Interaction | Backend owns the PTY; full interactivity including permission prompts |
| 3 | Output rendering | Structured chat parsed from `<id>.jsonl`; raw `capture-pane` tail only when a prompt is pending |
| 4 | Foreign sessions | Listed read-only; explicit **Take over** action kills the process and re-spawns under tmux with `--resume-id` |
| 5 | Auth | Loopback bypass + bearer token for non-loopback. Interim — see [Technical debt](#technical-debt) |

### Why tmux over raw `pty.openpty`

- Sessions survive `uvicorn --reload` and backend crashes. With in-process PTYs, every code edit
  kills every live agent session.
- `tmux attach -t kiro-<id>` from any terminal on the Mac is a real, zero-code replacement for the
  "Open in Terminal" endpoint the earlier plan wanted to build.
- Scrollback, resize, and process supervision come free.

Cost: tmux becomes a hard runtime dependency, and reading output means polling `capture-pane`
rather than watching a file descriptor. Acceptable because output of record comes from JSONL
(decision 3), not from the pane.

## Architecture

### Session states

A kiro session is in exactly one of three states:

- **managed** — a tmux session `kiro-<session_id>` exists and its pane process is alive.
  Full control: send input, answer prompts, kill, attach.
- **foreign** — `<id>.lock` exists with a live pid, but no tmux session of ours.
  Started by hand in some terminal. Read-only, plus a Take over action.
- **archived** — no live lock. History readable from `<id>.jsonl`. Can be resumed into a managed
  session.

The dashboard shows all three. Input controls are enabled only for **managed**.

### Spawning and the id-correlation problem

`kiro-cli` chooses its own session id and only reveals it by writing files under
`~/.kiro/sessions/cli/`. So spawn is two-phase:

1. `tmux new-session -d -s osa-pending-<nonce> -c <cwd> kiro-cli chat …`
2. Read the pane pid: `tmux list-panes -t <name> -F '#{pane_pid}'`.
3. Poll (250 ms, ~15 s cap) the `*.lock` files for one whose `pid` is a descendant of the pane pid.
4. On match: `tmux rename-session -t osa-pending-<nonce> kiro-<id>`, record the mapping.
5. On timeout: leave the tmux session alive, mark the record `unresolved`, surface it in the UI
   rather than silently orphaning a running agent.

**Correlate on process lineage, not cwd.** Two dead ends found by testing against real sessions:

- Matching on `cwd` cannot discriminate, because subagents inherit the parent's cwd.
- `session_created_reason` cannot either. It reads `"subagent"` for any session started with an
  initial task argument — which is exactly how dispatch starts them — so it does not separate a real
  subagent from the session we just launched. This is why 371 of 483 sessions on disk are tagged
  `subagent`: those are dispatched sessions, not subagents. Filtering that value out excludes the
  session you are looking for.

The `.lock` pid is exact. `kiro-cli` re-execs as `kiro-cli-chat`, so the lock pid is a child of the
pane pid rather than equal to it — walk the `ps -axo pid=,ppid=` tree. Genuine subagents are also
descendants of our pane, so ties break on the earliest lock `started_at`; the main session's lock is
written before it can spawn anything. Measured resolve time: ~2.7 s.

Mapping is persisted to `~/.osa-kiro/managed.json` — machine-local, deliberately outside the repo,
unlike the tracked `snapshots.json` / `favourites.json`. Writes go through a temp file and
`Path.replace()` so a crash mid-write cannot truncate state. On startup, reconcile:
`tmux list-sessions` ∩ `managed.json`; drop stale entries, adopt stray `kiro-*` sessions, retry
unresolved pendings with a zero timeout so startup never blocks. Only `kiro-`-prefixed tmux sessions
are ever touched, so unrelated tmux sessions on the machine are left alone.

### Input path

```
POST /api/sessions/{id}/input   { "text": "..." }
  → tmux send-keys -t kiro-<id> -l -- <text>
  → tmux send-keys -t kiro-<id> Enter
```

`-l` (literal) is required so text is not interpreted as tmux key names. Multi-line input is sent as
literal chunks; a trailing `Enter` submits.

For permission prompts:

```
POST /api/sessions/{id}/respond  { "choice": "y" | "n" | "t" }
  → tmux send-keys -t kiro-<id> <choice> Enter
```

Kept as a distinct endpoint from `/input` so the UI cannot accidentally submit prose into a
single-keystroke prompt.

### Prompt detection

Current `detect_status()` ([backend/api.py:106](../backend/api.py#L106)) infers
`awaiting-approval` from keyword matching on the JSONL tail — `"shall i"`, `"proceed?"`, trailing
`?`, and a 60-second staleness heuristic. That is guesswork.

For managed sessions we can do better: `tmux capture-pane -p -t kiro-<id> -S -20` returns what the
TUI is actually showing, so the real permission prompt is directly observable. Plan:

- managed → pane-based detection, authoritative; expose the pane tail to the UI so the user sees the
  exact question.
- foreign / archived → keep the existing heuristic. Note it as known-imprecise in the response
  (`"status_source": "heuristic"`) so the UI can render it with less confidence.

Keep `detect_status()` intact for the fallback path rather than rewriting it.

### Output path

`<id>.jsonl` is already parsed for status. Extend to a full history reader:

```
GET /api/sessions/{id}/messages?after=<seq>
  → [ { seq, role, kind, text, tool_uses[], ts } ]
```

Entry `kind` values seen on disk: `Prompt`, `AssistantMessage`, `ToolResults`, with content blocks
of `kind` `text` / `toolUse`. Render as a chat thread. `after=<seq>` lets the frontend poll
incrementally instead of refetching.

Polling, not WebSocket, for v1 — the existing frontend already polls, and JSONL has no push
mechanism. Revisit if latency is bad.

### Take over (foreign → managed)

Explicit, user-triggered, and destructive, so it must confirm in the UI before firing:

1. Read `<id>.lock` → pid.
2. `SIGTERM` the pid, wait up to 5 s, `SIGKILL` if needed.
3. Wait for `<id>.lock` to disappear.
4. `tmux new-session -d -s kiro-<id> -c <cwd> 'kiro-cli chat --resume-id <id>'`
5. Verify the lock reappears; on failure report it and leave the session archived.

The kill-first ordering is what avoids two writers on one session id. Never skip step 3.

### Trust model for tools

A managed session is spawned without `--trust-all-tools`, so tool calls surface as prompts the user
answers from the phone. That is the point of owning the PTY. `--trust-tools=<list>` is exposed as a
per-dispatch option for users who want fewer round-trips; `--trust-all-tools` is available but
off by default and labelled in the UI as what it is.

## API surface

### New

| Endpoint | Purpose |
|----------|---------|
| `POST /api/sessions/{id}/input` | Send text to a managed session |
| `POST /api/sessions/{id}/respond` | Answer a pending permission prompt |
| `GET /api/sessions/{id}/messages` | Chat history from JSONL, incremental via `after` |
| `GET /api/sessions/{id}/pane` | Raw `capture-pane` tail (managed only) |
| `POST /api/sessions/{id}/takeover` | Kill foreign process, re-spawn under tmux |
| `POST /api/sessions/{id}/resume` | Archived → managed via `--resume-id` |
| `POST /api/upload` | Multipart upload, returns absolute path |
| `GET /api/managed` | tmux/session reconciliation state, for debugging |

### Changed

| Endpoint | Change |
|----------|--------|
| `POST /api/dispatch` | tmux spawn + id correlation instead of osascript keystrokes |
| `GET /api/sessions` | Adds `managed`, `tmux_session`, `status_source`; drops `warp_name`, `warp_tabs` |
| `POST /api/sessions/{id}/kill` | `tmux kill-session` for managed; signal pid for foreign. No tab closing |
| `POST /api/sessions/{id}/send` | Replaced by `/input`; kept as a deprecated alias for one release |
| `POST /api/sessions/{id}/branch` | tmux spawn with `--resume` in the same cwd |

### Removed

`POST /api/scan-tabs`, `POST /api/sessions/{id}/assign-tab`, `POST /api/sessions/{id}/focus`,
`POST /api/open-accessibility`, and the helpers `warp_tab_name`, `get_warp_window_names`,
`cached_warp_names`, `match_warp_name`, `session_has_terminal`, plus the `_warp_names_cache`,
`_warp_names_ts`, `_scanned_tab_names`, `_tab_overrides` globals. `scan-helper.sh` goes too.

`POST /api/open-folder` and `POST /api/pick-folder` are macOS-local but not Warp-specific; they stay,
guarded to loopback-only requests since they are meaningless remotely.

## Auth

**Built, with two deviations from the sketch below** — see
[HANDOVER-pty-api.md](HANDOVER-pty-api.md#remote-access) for what shipped.
The token is read from `~/.osa-kiro/token` (0600) in preference to the env var,
which closes debt item 2; and a browser gets a `/login` form that stores the
token in an HttpOnly `SameSite=strict` cookie, so the existing frontend needed
no changes to authenticate. Bearer headers still work for scripts.

```python
API_TOKEN = os.environ.get("OSA_KIRO_TOKEN", "")

@app.middleware("http")
async def check_auth(request, call_next):
    if request.client.host in ("127.0.0.1", "::1"):
        return await call_next(request)
    if not API_TOKEN:
        return JSONResponse({"error": "remote access disabled"}, 403)
    if request.headers.get("Authorization") != f"Bearer {API_TOKEN}":
        return JSONResponse({"error": "unauthorized"}, 401)
    return await call_next(request)
```

Loopback bypass keeps the pywebview app and local dev working with zero frontend changes. Remote
access is off unless `OSA_KIRO_TOKEN` is set — fail closed, not open.

Bind explicitly to the Tailscale interface, never `0.0.0.0`:

```bash
OSA_KIRO_TOKEN=$(openssl rand -hex 32) uvicorn backend.api:app --host <tailscale-ip> --port 19418
```

### Threat model, stated plainly

This API can start processes and type into a shell-capable agent. The token is the only control
between any device that can reach port 19418 and code execution on this Mac. Tailscale narrows
*who can reach it*; it does not change what a request can do. This is inherent to the feature, not
a flaw to be designed away — it is worth being deliberate about, not worth pretending otherwise.

## Technical debt (accepted, deliberately)

Recorded here because these are shortcuts taken knowingly, not oversights:

1. **Static shared token, no expiry, no rotation, no per-device identity.** One leaked token = full
   access, and no way to revoke one device. Proper fix: per-device tokens in a store, revocable.
2. ~~**Token in plaintext env var**~~ — resolved. Read from `~/.osa-kiro/token` at `0600`; the env
   var remains only as a fallback. Still plaintext on disk rather than in the keychain.
3. **No TLS.** Traffic is encrypted by WireGuard only while inside Tailscale. Any non-Tailscale use
   is cleartext. Proper fix: local TLS cert, or refuse non-Tailscale source addresses outright.
4. **No rate limiting or audit log.** No record of which device dispatched what.
5. **Loopback bypass trusts every local process**, including any other app on the Mac. Acceptable
   for a single-user machine; not acceptable on a shared one.
6. **Upload path is not sandboxed per user** and files are never garbage-collected.
7. **`capture-pane` polling** rather than event-driven output; a fast-scrolling pane can drop
   intermediate frames. JSONL remains the record of truth, so this affects prompt latency only.
8. **Prompt detection for foreign sessions stays keyword-based** and will misfire.

Items 1–3 should be resolved before this is reachable from anything wider than a personal tailnet.

## Frontend

- Remove the Scan Tabs button and any `warp_name` / `warp_tabs` display.
- Session detail becomes a chat thread from `/messages`, with a composer wired to `/input`.
- Pending prompt renders the pane tail plus explicit `y` / `n` / `t` buttons hitting `/respond`.
- Foreign sessions: composer disabled, reason shown, **Take over** button with a confirm step that
  names the pid being killed.
- Managed sessions: show `tmux attach -t kiro-<id>` as copyable text.
- Responsive layout: single-column card list under ~700 px, detail as a full-screen view rather than
  a side panel, touch-sized controls.
- Upload: drag-drop on desktop, file picker on mobile; returns a path the user can insert into the
  composer.

## Implementation phases

1. **tmux session manager** — `backend/tmux_manager.py`: spawn, correlate, rename, list, reconcile,
   send-keys, capture-pane, kill. Unit-testable without FastAPI.
2. **De-Warp** — strip the osascript surface listed above; verify `/api/sessions` still populates
   with Warp not running.
3. **Dispatch + input + messages** — the core loop: create a session from the API, see its output,
   answer it.
4. **Prompt detection + respond** — pane-based detection for managed sessions.
5. **Takeover / resume** — with the kill-then-verify ordering.
6. **Auth + Tailscale bind** — done: `backend/auth.py` and `remote.sh`.
7. **Upload endpoint.**
8. **Mobile frontend** — responsive layout and the chat/composer/prompt UI.

Phases 1–3 are the point at which this is testable end to end; the rest is additive.

## Testing

```bash
# backend
source venv/bin/activate
uvicorn backend.api:app --port 19418 --reload

# frontend
cd frontend && npm run dev
```

Checks, in order:

1. Quit Warp entirely. `curl localhost:19418/api/sessions` still lists sessions.
2. `POST /api/dispatch` → `tmux ls` shows `kiro-<id>`, the session appears in the dashboard, and the
   id correlates within 15 s.
3. `POST /api/sessions/{id}/input` → text lands in the session; `/messages` shows it.
4. Trigger a tool call → status flips to `awaiting-approval` from the pane, `/respond y` unblocks it.
5. `tmux attach -t kiro-<id>` shows the same session, live.
6. Restart uvicorn → session survives, reconciliation re-adopts it.
7. Foreign session: start `kiro-cli chat` by hand in Terminal.app → appears read-only → takeover
   kills it and re-spawns managed.
8. From a phone on the tailnet, with the token: dispatch, read, answer a prompt.

## Success criteria

1. Warp is never launched, and nothing breaks with it uninstalled.
2. A session can be created, read, answered, and killed entirely over HTTP.
3. Permission prompts are answerable from a phone.
4. Sessions survive a backend restart.
5. Foreign sessions are visible, and takeover works without corrupting session state.
6. Layout is usable one-handed on a phone.

## Appendix: Warp coupling inventory on `main`

Everything below is osascript/Warp-dependent. The earlier plan accounted for only three of these.

| Location | What it does |
|----------|--------------|
| [api.py:215](../backend/api.py#L215) `warp_tab_name` | Derives an expected tab name from cwd |
| [api.py:222](../backend/api.py#L222) `get_warp_window_names` | osascript, front-window name |
| [api.py:245](../backend/api.py#L245) `cached_warp_names` | 2 s cache over the above |
| [api.py:257](../backend/api.py#L257) `match_warp_name` | Fuzzy match session → tab |
| [api.py:561](../backend/api.py#L561) `/assign-tab` | Manual session → tab override |
| [api.py:572](../backend/api.py#L572) `/branch` | New Warp tab with `--resume` |
| [api.py:629](../backend/api.py#L629) `/ack` | Tab cycling |
| [api.py:662](../backend/api.py#L662) `/focus` | Cycles up to 15 tabs matching names |
| [api.py:787](../backend/api.py#L787) `/scan-tabs` | Activates Warp, walks every tab |
| [api.py:1000](../backend/api.py#L1000) `/kill` | Kills pid *and* closes the tab |
| [api.py:1143](../backend/api.py#L1143) `/send` | Two-pass tab match, then keystrokes the message |
| [api.py:1219](../backend/api.py#L1219) `/dispatch` | Cmd-T, keystrokes `cd … && kiro-cli chat …` |
| `scan-helper.sh` | Warp scan helper |
