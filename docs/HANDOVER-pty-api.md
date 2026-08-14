# Handover: Quarterdeck `pty-api`

State of the branch as of 2026-07-27. Design rationale lives in
[ARCHITECTURE-pty-api.md](ARCHITECTURE-pty-api.md); this is what exists, what is
verified, and what is not.

## What this branch changed

Quarterdeck no longer drives Warp by simulated keystrokes. The backend owns
kiro-cli sessions in detached tmux sessions, so they can be created, read,
answered, and ended over HTTP — with Warp uninstalled.

The product surface was renamed from Deck to Quarterdeck. Private compatibility
identifiers deliberately did not move: `~/.osa-kiro`, `DECK_*`,
`com.osa-kiro.remote`, and `deck-*` hook markers remain stable.

Every osascript/Warp code path is gone. The three AppleScript uses that remain
are scriptable and explicit, not keystroke simulation: reading the front Finder
window as a default directory, the native folder picker, and handing a session
to Terminal.app / iTerm2.

## Session control model

Each session is in one of four states, reported as `control` by the API:

| State | Meaning | What you can do |
|-------|---------|-----------------|
| `starting` | Process launched, session id not yet correlated | Wait — it becomes `managed` |
| | | (also reported for a real session mid-adoption, see below) |
| `managed` | We own its tmux session (`kiro-<id>`) | Send input, answer prompts, hand off, end |
| `foreign` | Alive, but started outside the app | Read only, or **take over** |
| `archived` | Not running | **Resume** it |

- **Take over** stops the foreign process, waits for its lock to clear, then
  restarts it under tmux with `--resume-id`. Kill-then-restart is what keeps two
  processes off one session id. Verified to preserve the conversation.
- **Hand off** does the reverse: quits the tmux session cleanly, then runs
  `cd <dir> && kiro-cli chat --resume-id <id>` in a real terminal. The session
  then shows as `foreign`.
- **End** sends `/quit` so kiro-cli flushes its conversation and the session
  stays resumable. Killing the tmux session outright is the fallback.

### One spawn, one card

Between the spawn and the moment correlation finishes, a session exists on disk
with a live `.lock` while its nonce is still `pending`. Listing both sources
naively showed the same agent twice — a `starting` placeholder *and* a `foreign`
session the app did not realise it owned. Creating several sessions quickly made
this obvious, because correlation takes longer under contention.

`tmux.pending_owners()` correlates each pending read-only and the listing uses it
to collapse the pair: the placeholder is dropped and the real session is reported
as `starting` rather than `foreign`. Reporting it as `foreign` was the worse
failure — it offered a **Take over** that would kill a process mid-adoption.
Promotion to `managed` still belongs to the resolver thread; a listing request
must not race it, which is why that helper does not write state.

Two pendings can never claim one session: each match is added to `claimed` as it
is found.

### Pendings whose owner died

A pending entry is resolved by a background thread. If the backend dies first —
a reload, a crash, a stopped dev server — the thread goes with it and the entry
survives with no tmux session and nobody polling it. Reaping only ran in
`reconcile()` at startup, so the result was a permanent `starting` card that no
UI action could remove: it has no session id, so every id-keyed endpoint was
useless against it.

Now `tmux.reap_pendings()` runs on every listing, and `POST
/api/pending/{nonce}/cancel` is the explicit escape hatch — it kills the pending
tmux session if one is still there and drops the entry. The card carries its
`nonce` so the UI has something to act on.

`~/.osa-kiro/managed.json` maps session ids to tmux sessions. It is machine-local
and deliberately outside the repo. On startup the backend reconciles it against
`tmux list-sessions`, so restarting the API re-adopts its own sessions. Only
`kiro-`-prefixed tmux sessions are ever touched.

## Status detection and hooks

Live working state is still read off the TUI. kiro-cli swaps its footer depending
on what it is doing, and those strings are the signal:

```
idle      " ask a question or describe a task ↵"
working   " Kiro is working · Type to steer · Ctrl+S to queue"
prompt    " esc to close · ↑↓ to navigate · ↵ to select · Tab to edit"
```

A permission prompt looks like this — an **arrow-key menu**, not a y/n/t
question, which is why answering it takes navigation keys:

```
 shell requires approval
 ❯ Yes, single permission
   Trust, always allow in this session
   No (Tab to edit)
```

`/respond` takes `allow` / `trust` / `deny` / `dismiss` and expands each to the
right key sequence (`allow` = Enter, `trust` = Down+Enter, `deny` = Down+Down+
Enter). Raw `keys[]` is accepted for anything else.

Four optional hooks replace or check the guesses where kiro-cli exposes a real
event:

- `agentSpawn` reports `KIRO_SESSION_ID` and checks lineage correlation.
- `stop` records an exact end-of-turn mark.
- `preToolUse` can hold a structured tool call for a per-session decision.
- `postToolUse` records the outcome in the audit trail.

Hooks live in file-backed agent configurations and are installed from Settings.
Built-in agents cannot carry them. A foreign session without the stop hook still
falls back to JSONL freshness, and a non-gated TUI permission menu still uses the
arrow-key `/respond` path above.

## API surface

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/sessions` | Adds `control`, `name`, `folder`, `attach`, `status_source`; includes `starting` placeholders |
| GET | `/api/sessions/{id}` | Adds `control`, `attach`, `awaiting_prompt`, `dead_pane`, `prompt` |
| GET | `/api/sessions/{id}/messages` | Line-addressed transcript entries with stable `seq` values |
| POST | `/api/dispatch` | tmux spawn. Accepts `task`, `cwd`, `model`, `effort`, `trust_tools`, `trust_all`, `pre_command`. Returns in ~0.1s |
| POST | `/api/sessions/{id}/input` | Send text (newlines flattened — each would submit separately) |
| POST | `/api/sessions/{id}/respond` | Answer the permission menu |
| GET | `/api/sessions/{id}/pane` | Raw `capture-pane` tail + `awaiting_prompt` |
| POST | `/api/sessions/{id}/takeover` | Foreign → managed |
| POST | `/api/sessions/{id}/resume` | Archived → managed |
| POST | `/api/sessions/{id}/handoff` | Managed → a real terminal |
| POST | `/api/sessions/{id}/kill` | Graceful `/quit`; `?force=true` skips it |
| POST | `/api/sessions/{id}/rename` | Persists to `~/.osa-kiro/names.json` — survives kiro-cli replacing the session JSON on every turn |
| POST | `/api/pending/{nonce}/cancel` | Abandon a spawn that never correlated — the only handle on a session-id-less pending |
| GET | `/api/managed` | tmux reconciliation state, for debugging |
| GET | `/api/options` | Models, efforts, quick commands, terminals |
| GET | `/api/cwd-suggestion` | Front Finder window, else `$HOME` |
| GET | `/api/projects?refresh=true` | Cached 60s — the uncached scan takes ~37s |
| POST | `/api/sessions/{id}/send` | Deprecated alias for `/input` |
| GET/POST | `/api/sessions/{id}/gate` | Read or change per-session structural approval gating |
| GET/POST/PATCH/DELETE | `/api/sessions/{id}/stack...` | Manage queued work and auto-advance |
| GET | `/api/approvals` | Structured held `preToolUse` requests |
| GET | `/api/audit` | Recent request, decision, and tool records |
| GET/POST | `/api/remote/...` | Remote status, serving, token, QR, and LaunchAgent controls |

Removed: `/api/scan-tabs`, `/api/sessions/{id}/assign-tab`,
`/api/sessions/{id}/focus`, `/api/open-accessibility`.

**3. kiro-cli replaces the session JSON atomically on every agent turn.** It does
not merge — it writes a brand new file with a new inode. Any field written into
`~/.kiro/sessions/cli/{id}.json` (including a custom `_deck_name` or patched
`title`) is silently lost the moment the user sends a message. The only safe
place to store Quarterdeck-owned metadata is `~/.osa-kiro/` which kiro-cli never
touches. Session renames go to `~/.osa-kiro/names.json`; `meta_title(meta)` in
`api.py` checks that sidecar first before falling back to `meta["title"]`.

## Two findings worth not rediscovering

**1. Session id correlation must use process lineage.** kiro-cli only reveals its
id by writing files, so spawning is two-phase: start tmux under a placeholder
name, then find the `.lock` whose pid is a descendant of the pane pid, then
rename. Two simpler approaches fail:

- `cwd` cannot discriminate — subagents inherit the parent's directory.
- `session_created_reason` is **not** a subagent marker. It reads `"subagent"`
  for any session started with an initial task argument, which is exactly how
  dispatch starts them. Filtering it out excludes the session you are looking
  for. This is why 371 of 483 sessions on disk carry that value.

Real subagents are pane descendants too, so ties break on the earliest lock
`started_at` — the main session locks before it can spawn anything.

**2. `window.confirm()` is a no-op in the embedded webview.** It returns `false`
without rendering, so every confirm-gated action silently did nothing. The app
uses its own dialog and toasts. That dialog ignores the click that opened it
(React flushes state during the click, so the backdrop would dismiss itself),
and has no Enter-to-confirm or autofocus — both approved destructive actions
without a deliberate click.

## UI

- Cards lead with the task text; the directory sits underneath. A single click
  opens the side panel; a **double-click opens it maximised**, so reaching a
  session full screen is one gesture rather than two clicks.
- The side panel's left edge is a drag handle: 360px floor, and a ceiling of
  `innerWidth - 340` so the card grid always keeps a column. Width is stored in
  `localStorage` under `detail-width`, written on pointer release rather than
  per move, and re-clamped when the window shrinks. Double-click the handle to
  reset to 480px.
- **Click the panel title to rename it.** The pencil button is gone — next to
  close and maximise it read as one more icon to decode, and the title is its
  own affordance. Enter saves, Escape cancels. Keyboard-reachable via
  `role="button"` and `tabIndex`.
- **Click the session name directly** to rename inline — works in card view,
  list view, and wall view. `stopPropagation` prevents the click from also
  selecting the card or opening the detail panel.
- **`F` maximises the open session** and restores it. Bare letter, no modifier:
  text fields, selects and contenteditable are excluded, and it is inert while
  a confirm dialog is up. `expanded` lives in `App` rather than `DetailPanel` so
  a card's double-click can open straight into it; closing the panel clears it.

Two CSS traps worth not rediscovering:

- **A flex item with a non-visible `overflow` loses its automatic minimum
  size.** The `tmux attach …` line sets `overflow-x: auto`, and the panel is a
  flex column, so it was squashed to a sliver of its own text — cropped top and
  bottom. Fixed with `flex: 0 0 auto`, not with a height.
- **`body.resizing { user-select: none }` is a footgun.** A drag released
  outside the window never delivered `pointerup`, so the class stuck and text
  selection was dead app-wide until a reload. The handle now takes pointer
  capture, `pointercancel` and `blur` also clear it, and the panel clears any
  stale class on mount.
- Detail panel holds the composer next to the output: quick-command chips
  (`/goal`, `/compact`, `/context`, `/plan`, `/tools`, `/usage`, `/clear`),
  per-session model and effort switching, and `esc` / `ctrl-c` / `↵` keys.
- Three panes: **Live** (tmux pane, polled at 400ms while busy, 1.2s idle),
  **Activity** (jsonl, markdown-rendered), **Last Output**. Light/dark toggle
  applies to all three. Markdown is rendered as React elements — no agent output
  is ever injected as HTML.
- Header badges filter; there is a managed/foreign filter, a one-line
  quick-create bar plus the `+` launcher, and an attention strip so another
  session needing approval can interrupt a focused view.

## Remote access

Settings drives start, stop, token display and rotation, one-time QR login, and
installation of `com.osa-kiro.remote` as a LaunchAgent. `./remote.sh` remains the
manual path. Both bind to the detected Tailscale IPv4 address rather than
`0.0.0.0`. A sleeping Mac drops off the tailnet; on battery the system-sleep
assertion is ignored, so keep a laptop on power to stay reachable with the lid
shut.

The phone opens `http://<tailscale-ip>:19418/app/` and is redirected to
`/login`. A QR carries a random, single-use, two-minute exchange code; the
long-lived token is never put in the URL. Redemption sets an HttpOnly,
`SameSite=strict` cookie for 30 days. Scripts can use
`Authorization: Bearer <token>`.

[backend/auth.py](../backend/auth.py):

- **Loopback bypasses everything.** The pywebview app and `npm run dev` need no
  token and no frontend changes.
- **Non-loopback fails closed.** With no token configured the answer is 403,
  not an open door.
- **Non-tailnet socket peers are refused before auth.** Accepted ranges are
  Tailscale IPv4 `100.64.0.0/10` and IPv6 `fd7a:115c:a1e0::/48`; forwarding
  headers are ignored.
- Token is read from `~/.osa-kiro/token` first, `$OSA_KIRO_TOKEN` second. The
  file is preferred: an env var shows up in `ps` for every local user.
- Authenticated remote dispatch is limited to 10 requests/minute; input and its
  deprecated `/send` alias share a 60 requests/minute bucket.
- `/api/open-folder` and `/api/pick-folder` are loopback-only regardless of
  token — they drive this Mac's GUI and mean nothing remotely.
- CORS no longer allows `*`. Remote clients are same-origin (served from
  `/app`), so the only cross-origin caller is the Vite dev server on loopback.

Rotation is exposed in Settings; existing cookies stop matching immediately.

The debt in [ARCHITECTURE-pty-api.md](ARCHITECTURE-pty-api.md#technical-debt)
still stands where applicable: one shared token, no per-device identity or
revocation, loopback bypass, and no application TLS outside WireGuard. The audit
trail is built and defaults on, with 90-day retention.

## Running it

```bash
source venv/bin/activate
uvicorn backend.api:app --port 19418 --reload
```

```bash
cd frontend && npm run dev
```

Native app:

```bash
./build-app.sh
```

Requires `tmux` (`brew install tmux`) and `kiro-cli` on PATH. A Finder-launched
bundle does not inherit the shell PATH, so `ensure_tool_path()` adds
`/opt/homebrew/bin`, `/usr/local/bin`, and `~/.local/bin` back; without it every
session action fails with "not installed".

## Tests

Run `python -m pytest tests/ -q` from the repository root; CI holds the current
count rather than this document. `tests/test_tmux_manager.py` covers
the process-tree walk, correlation (including the earliest-lock tie-break), and
atomic state persistence without touching tmux. `tests/test_api.py` covers the
control surface and uses the real captured permission prompt as a fixture.
`tests/test_auth.py` covers the loopback bypass, fail-closed remote, bearer and
cookie paths, tailnet source boundary, rate limits, and local-only endpoints.
`tests/test_audit.py` covers request, decision, and hook-written tool records.

`TestClient` reports a client host of `testclient`, which the middleware treats
as remote — hence `TestClient(app, client=("127.0.0.1", 45678))` in
`test_api.py`. Without it every existing test 403s.

## Verified against real sessions

- Dispatch resolves and becomes `managed`; `/input` reaches the agent.
- Takeover of a foreign session preserves the conversation (recalled a secret
  word set before the kill).
- Resume of a cleanly exited session appends to its jsonl rather than
  overwriting it.
- Handoff to **Terminal.app**: tmux quits, the command runs, session goes
  `archived` then `foreign`.
- `pre_command` (`cd nested`) takes effect — kiro records the nested directory.
- Pane-based `awaiting-approval` fires on a genuine prompt and no longer fires
  on an ordinary question.
- In-app confirm dialog: opens, survives the opening click, and only proceeds on
  an explicit Confirm.
- A pending entry left behind by a killed backend is reaped by the next listing,
  and the phantom `starting` card it produced is gone.
- Renaming from the panel title: click opens the input pre-filled with the
  current title; Escape cancels without saving.
- Text selection works and `body` carries no stale `resizing` class after a
  drag.
- Panel resize and `F`: drag sets and persists the width across a reload,
  clamps at both ends with no horizontal overflow, double-click resets, `F`
  toggles maximise, and typing `off` in the composer does not toggle it.
- Phone layout: active cards, approval controls, detail panes and composers
  reflow at the mobile breakpoint.
- Settings: remote serving, LaunchAgent, hook installation, dispatch defaults,
  pane theme, concierge model and audit state round-trip to their real runtime
  behavior.
- Gated `preToolUse`: a held request can be allowed or denied from the app;
  switching the gate off releases an existing hold.
- Audit: mutating requests, approval decisions and hook-written tool outcomes
  appear in the settings view with bounded, redacted records.
- Auth over the real tailnet address: no credentials 401, wrong token 401,
  bearer token 200, browser navigation 303 to `/login`, form login sets the
  cookie and unlocks both `/api` and `/app`, `pick-folder` refuses a
  token-bearing remote caller, and loopback still needs nothing.

## Not done / known gaps

1. **File upload** (`/api/upload`) not implemented.
2. Handoff verified only for Terminal.app. **iTerm2 and Ghostty are untested.**
   Warp cannot be told to run a command at all, so it opens a tab in the
   directory and puts the command on the clipboard.
3. Thinking-vs-idle for a `foreign` session whose agent has no stop hook still
   uses the 10s freshness heuristic, which lags.
4. `/api/projects` is cached in-process, so the cache dies with the backend and
   the first call after a restart still costs ~37s.
5. Unused frontend leftovers: `handleBranchSession`, `handleRestore`,
   `showRecent`, `lastFocusTime`.
6. Reachability does not report battery state or the last successful remote
   request, so a sleeping Mac and an idle remote listener still look too alike.
7. Authentication is one shared token with loopback bypass; there is no
   per-device identity, individual revocation, or application-level TLS.

## Requested, not yet built

Still intentionally outside the current release tranche:

1. **Toolbar is overloaded** — wants to be toggleable and grouped rather than
   one flat row of chips.
2. **Screenshot pickup.** A configurable screenshots folder in settings, watched
   while a session is open, offering any new file as a path to insert into the
   composer. Needs a settings key, a poll or watcher on that directory, and a
   dismissable suggestion in the composer — reference the path only, no upload.
