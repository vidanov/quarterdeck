# Quarterdeck — Changelog

Completed work, extracted from the roadmap on 2026-07-28. Organized by area.

---

## 2026-08-15

### Paste-as-document across all inputs

**Root cause fixed**: `api.py:send_input` collapsed every newline with `" ".join(text.split())`, destroying markdown, YAML, and code structure in any pasted document. The fix moves newline preservation upstream: large pastes are written to a file and delivered as a one-line file reference; the agent reads the file via `fs_read`. Gated sessions receive the content inline with newlines preserved via the existing bracketed-paste route in `tmux_manager.py`.

**backend/pastes.py** (new): `save`, `read`, `delete`, `sweep`, `storage_bytes`, `reference_line`, `should_collapse`. Files at `~/.osa-kiro/pastes/<session_id>/<YYYYMMDD-HHMMSS>-<slug>.md`. Traversal guard in `_resolve()`. 16 tests.

**backend/api.py**: `POST/GET/DELETE /api/pastes`; `_can_read_files()` checks `GATES_DIR`; `send_input` builds reference lines or inline content from `attachments[]`; `dispatch_task` prepends attachment references; `paste_store.sweep()` registered in cleanup; cleanup preview includes `paste_bytes`/`paste_size_display`.

**Frontend**: `usePasteAttachments.js` hook intercepts pastes above threshold, POSTs to `/api/pastes`, persists to `localStorage['paste-attachments:<sid>']`. `PasteAttachments.jsx` renders clipped tile with PASTED badge, expand modal, and ×. `DocCard.jsx` renders pasted documents in the transcript as collapsible `<details>` cards (lazy-fetch for ref markers, inline for heuristic long-text). Wired into: main composer, side chat, wall tile reply, card fast reply, quick-create, NewSessionLauncher. Cleanup settings panel shows paste storage size.

---

## 2026-08-14 (evening)

### ACP control surface — Tasks 4, 5, 6 (section 13)

**Task 4 — V3 status via ACP events** (`backend/api.py`, `backend/acp_observer.py`):
`detect_status()` now checks `acp_observer.detect_status()` first for sessions
with a live observer. Maps `session/update.sessionUpdate` → standard status
strings (thinking/running/idle/awaiting-approval/error). Falls through to V3
messages.jsonl and pane-scraping for unobserved sessions. More responsive
than file-polling — event arrives within milliseconds of state change.

**Task 5 — Slash command / prompt dispatch via ACP** (`backend/api.py`):
`send_input()` routes user prompts through `acp_observer.send_prompt()` for
observed sessions. `_sq_send_delayed()` routes slash commands through
`acp_observer.execute_command()`, skipping the 3.5s timing delay that tmux
send-keys requires. Both fall back to tmux on any error.

**Task 6 — Capability probe on spawn** (`backend/acp_observer.py`):
`attach()` registers a one-shot callback for `_kiro.dev/commands/available`.
Capabilities stored per entry and exposed via `get_capabilities()` and
`GET /api/sessions/{id}/acp-events` (now returns `{attached, events,
capabilities, status}`). `execute_command()` gates on the capability list
when known, preventing blind sends to unsupported commands.

### Bug fix — Collections: no way to add sessions from Projects or Archive

`frontend/src/components/CollectionsPanel.jsx`:
Added `AddToCollectionBtn` component — a 📁+ button that appears on every
session row in the Projects tab and Archive tab. On first click it fetches
all `manual`-source collections and shows an inline picker. Selecting a
collection calls `POST /api/collections/{id}/members`. Previously the only
way to add members was via the ★ (Favourites) button, which only wrote to
the Favourites collection, not user-created ones.

### Earlier fixes from 2026-08-14

- `fix: stale-build banner` (2128923) — real rebuild endpoint, dev-mode
  suppression, dismiss-by-hash, CSS for banner and progress modal.
- `fix: remote stop kills the app` (e9d4864) — set `_stop_proxy` flag on
  proxy thread before releasing reference; exclude own PID from lsof kill.
- `test: isolate suite from real state` (1127edb) — session-scoped fixture
  redirects all backend Path constants to tmp dir, stubs all three keychain
  functions. Guard test in `tests/test_isolation.py`.
- `fix: rebuild button honest, token verify-after-write, remote stop
  LaunchAgent` (006e8e8) — rebuild streams real output; write_token
  verifies readback; remote_stop persists intent before killing and
  unloads LaunchAgent; _remote_running no longer treats loaded LaunchAgent
  as a running listener.

---

## 2026-08-14

### Test isolation — stop suite from writing to real state (no production changes)

**Root cause:** `auth.write_token()` calls `_keychain_write()` first and only
falls back to `TOKEN_FILE` if the keychain is unavailable. Tests were patching
`auth.TOKEN_FILE` — which only redirected the branch that never ran. The
keychain write always succeeded, so every `write_token("c" * 64)` in
`test_api.py` was landing in the real `com.vidanov.quarterdeck / remote-token`
keychain item. One test run was enough to replace the live token with
`cccc…cccc`, which rotated it out of sync with phone sessions.

The same missing boundary left `audit-test-key` and `audit-body-probe` in
`~/.osa-kiro/settings.json`, a test-UUID stack file in `~/.osa-kiro/stacks/`,
and live exchange codes in `~/.osa-kiro/codes/`.

**Fix (tests only):**
- `tests/conftest.py`: session-scoped `isolate_state` autouse fixture redirects
  every Path constant in `backend.{config,auth,tmux_manager,audit,devices,api}`
  to a `tmp_path_factory` temp directory, and replaces `auth._keychain_read` /
  `auth._keychain_write` with in-memory dict stubs. No production module touched.
- `tests/test_isolation.py`: guard test that fails immediately if any backend
  Path still resolves under `~/.osa-kiro` while tests run, or if the keychain
  stubs are not in place. Acts as a tripwire for new constants.
- `tests/test_api.py`: four `TestQRLogin` tests now assert `auth.read_token()`
  returns the written value (via the stub), and `auth.TOKEN_FILE` does not exist
  (keychain path succeeded). Removed the now-misleading `TOKEN_FILE` patches.
- Live state cleaned: token rotated to a random hex value, `audit-test-key` and
  `audit-body-probe` removed from `settings.json`, test stack file deleted.

**Proof:** SHA-256 of `~/.osa-kiro/settings.json` and the keychain item are
identical before and after `pytest tests/ -q` (361 passed, 1 skipped).

---

## 2026-07-28 → 2026-08-14

### Features shipped

**Collections (section 6)**
One concept replacing snapshots, favourites, and project groups. A collection
is an ordered set of session ids with a name and a source (`snapshot`, `cwd`,
`manual`). Members carry `{session_id?, cwd, agent?, model?, prompt?}` — a live
session id when running, a recipe to spawn when not. `POST
/api/collections/{id}/start` spawns members that aren't running. Snapshots and
favourites migrated automatically on first start. Sessions can belong to
multiple collections.

**Branch at turn (section 4)**
`POST /api/sessions/{id}/branch-at` creates a truncated copy of a session's
JSONL up to `after_seq`, setting parent/branch metadata. kiro-cli loads the
truncated file without error via `--resume-id`. Lineage shown in the detail
panel header (parent name, cut point). Transcript turns are individually
addressable and carry a branch affordance.

**Focus, export, session summaries, deny patterns, stall detection, trust TTL**
- **Focus view** — one session full width, thin attention strip alongside.
- **Session export** — `GET /api/sessions/{id}/export` renders a Markdown
  transcript; written to `~/Downloads` and opened by macOS (WKWebView cannot
  download directly).
- **Waiting-session summaries** — when a session goes idle, a background
  concierge session reads its tail and writes a one-line annotation. Cached by
  JSONL line count; only regenerated on new content.
- **Deny patterns** — configurable blocklist for `preToolUse execute_bash`.
  Default entries cover `rm -rf /`, force-push, `DROP TABLE`. Settings UI for
  view / add / disable per pattern.
- **Stall detection** — if a session hasn't written to its JSONL in N minutes
  while showing as active, the card annotates it. Threshold in Settings.
- **Trust TTL** — per-session "trust for N minutes" shortcut. Countdown shown
  on card and in detail panel. Auto-expires back to gated.

**Side chat (section 10g)**
`/side` command opens a lightweight kiro-cli session against a frozen snapshot
of the parent's tail. Messages never enter the parent's log. Tools available via
`fs_read` so it can answer questions about files. Tab in detail panel.

**Self-update (section 2)**
`GET /api/update/check` compares running commit against latest remote. `POST
/api/update/apply` runs `git pull`, `pip install -r requirements.txt`, and
the frontend build, streaming progress. Restart via `os.execv`. Settings UI
with current commit, check button, and "Update now" action.

**Per-device tokens (section 3)**
Named device tokens (`phone`, `laptop`), each revocable, each recorded on use.
Lost-phone revocation without rotating the Mac token. Settings UI in
Remote access tab.

**Concierge (⌘K)**
Natural-language command bar. Type a question or request; it queries the API,
synthesizes a structured JSON response, and the UI renders results, reports, or
action buttons. Sessions with `name + messages` render as horizontal bar charts.
Can dispatch new sessions, resume archived ones, search, and generate weekly
reports.

**Wall view**
Read-only ambient display — full-screen status cards, no controls. For a second
monitor. All sessions on one surface, color-coded by status.

**Per-session slash command queue**
Text or slash commands queued per session, drained one per turn after the
current turn ends. No opt-in: any managed idle session drains its queue on stop.
Slash commands run before the task stack so `/compact` finishes before the next
task fires. Queue input and cancel list in detail panel. `⏎ N queued` badge on
the card. `~/.osa-kiro/slash-queues/`.

**Starting folder setting**
Three modes for the default cwd of new sessions:
- **Auto** — frontmost Finder window (previous behaviour)
- **Last** — cwd of the most recently modified session with a real directory
- **Fixed** — user-specified path with folder picker

Stored in backend settings. Respected by `/api/cwd-suggestion` and
`/api/dispatch`. Settings → General → Starting folder.

**Verification infrastructure (Phase 2, tasks 2–6)**
- `build-app.sh` writes a stamp (`git sha`, source hashes, build time) to
  `~/.osa-kiro/build-stamp.json` and `backend/build-stamp.json`.
- `GET /api/health/build` recomputes source hashes from disk and compares
  against the stamp. Returns `stale`, `stale_reason`, `changed_files`,
  `uptime_s`.
- Amber **STALE BUILD** banner in the app header fires within 30 s of a source
  edit. Hard stop — do not present results from a stale UI.
- `scripts/verify-claim.sh` — stop hook that reads the JSONL tail, detects
  claim keywords without an observation tool call, and POSTs an unverified-claim
  record + fires a macOS notification.
- Corrections panel gains a `kind` field (`correction` | `unverified_claim`).
  Unverified claims render with an amber ⚠ auto-detected badge and a "False
  positive" button.
- `profile_verified` field on sessions: hollow ○ dot when the active profile
  changed after session spawn, solid ◉ when the session started under the
  currently active profile.

**Model/effort selectors show live values**
Model and effort dropdowns in the detail panel now initialise to the session's
current active values (read from `session_state.rts_model_state` in the JSONL),
not placeholders.

**Helpers menu persistence**
The esc/ctrl-c/ctrl-x/del/model/effort chips strip state survives WKWebView
restarts. Backed by `POST /api/settings` (`detail-chips-open`), restored on
mount. The `localStorage` copy handles the initial render frame; the backend is
the authoritative store.

---

### Bugs fixed (2026-07-28 → 2026-08-14)

**Remote access in packaged app**
- Tailscale not detected: PATH in a PyInstaller bundle excluded `/usr/local/bin`.
  Fixed by explicit path probe.
- Remote serving failed: `uvicorn` CLI unavailable inside the bundle. Switched
  to `uvicorn.run()` in a thread.
- Remote serving persistence: LaunchAgent restart path assumed dev layout. Now
  resolves correctly in both dev and packaged environments.

**Profile detection**
Profiles were matched by token fingerprint, not ARN — two profiles sharing a
token prefix collided. Matching is now by full ARN. Model list refresh happens
synchronously (~1.4s) on switch to avoid the race where the frontend receives a
stale list for the new profile.

**Context percentage**
`context_pct` regex matched the wrong Unicode character (`◔` U+25D4) instead
of the actual kiro-cli character `◉` (U+25C9). Now matches `◑◐◕` family.

**Draft and history persistence**
- Large input (> 1 KB) used `send-keys` and got silently truncated. Now uses
  `load-buffer` + `paste-buffer` with bracketed paste.
- History was stored in WKWebView `localStorage` only — lost on window close.
  Now backed by backend file `~/.osa-kiro/client-prefs.json`.
- Draft content survived arrow-key recall but not app restart. Backend restore
  now runs before any null write, preventing the null from overwriting the
  restored value.

**Session export path**
FastAPI cannot route paths with dots. `/export.md` → `/export` with
`Content-Disposition: attachment; filename=...` header.

**Stale build banner after commit-during-build**
`build-app.sh` wrote the stamp before PyInstaller ran, then a commit bumped the
SHA while packaging was in progress. The stamp now writes a second time at the
very end of `--install` (after `ditto`), capturing the actual HEAD at
completion time. Banner no longer appears immediately after a clean install.

**Startup**
- Summary threads fired for all 35 existing sessions at boot, causing a ~35-way
  concierge spawn. Pre-populated `last_seen` with existing turn files so only
  new stop events trigger summary generation.
- Stalled spawn showed no useful message. Now names the process holding the port
  and offers to kill it.
- `kiro-cli login` had no shell to run in. Settings now provides a login shell
  pane (tmux session) for auth flows.

**WKWebView cache**
Build/install now sends `Cache-Control: no-store` for frontend assets and
clears the WKWebView JS cache on `--install`. Stale JS no longer survives a
rebuild.

---

### Doc updates

- README: Tailscale listed as a requirement in Quick start for phone/remote use.
- README: Phone access section GIF recorded and added (2026-08-13).
- README: Contributing section — demo GIF entry removed (done).
- ROADMAP: Phase 2 section added with task readiness table, action plan,
  cross-links, and open questions. Tasks 2–6 marked shipped.
- ROADMAP: Starting folder setting, helpers persistence, and ACP probe results
  updated.

---

## Bugs fixed

### Snapshots and favourites were stored inside the app bundle (2026-07-28)

The same fault as the settings one below, unfixed for these two long after
`settings.json` was moved. `SNAPSHOTS_FILE` and `FAVOURITES_FILE` were built from
`Path(__file__).parent.parent` — the repo root in a checkout, and
`Quarterdeck.app/Contents/Frameworks` once packaged. So they were user data kept
inside the application: replacing the bundle deleted them, and a signed or
read-only bundle would refuse the write outright.

Found from the other side. After an install, the only file differing between the
installed bundle and a fresh build was a leftover `Contents/Frameworks/
snapshots.json` from an older layout — a fossil of exactly this bug, and proof
that a reinstall both destroys the current file and resurrects nothing.

Both now live in `~/.osa-kiro/`, and `migrate_settings()` carries all three
files across on first start, reporting which moved rather than a bare boolean. It
copies rather than moves, so a wrong call leaves the original in place. Verified
on real data: two favourites and a snapshot carried over, second run a no-op.
Tests pin the location for all three, assert `api` does not rebuild the path from
`__file__`, and cover the migration of the two files that had real data in them.

### Settings did not survive an app restart (2026-07-27)

Two faults wearing one symptom. `SETTINGS_FILE` pointed inside the app bundle,
so reinstalling overwrote it. It now lives at `~/.osa-kiro/settings.json` with
migration on first start. Separately, `private_mode=False` was set in source but
not in the installed app — WKWebView discarded `localStorage` on quit.

### App "did not start" after a rebuild

macOS relaunched a second copy, `claim_port` found 19418 taken, and it exited
silently. A clash now brings the running instance to the front and exits 0.
Only a non-Quarterdeck process on the port is treated as an error, shown via
`NSAlert`.

### Finished session stayed "thinking" forever

`pane_status` matched "Kiro is working" in the tip text above the composer.
Status is now read from the last dozen lines only, matched at start of line,
with the idle prompt winning when both appear.

### Queue items were not editable or reorderable

Drag started text selection in WebKit. Only the ⠿ handle is draggable now.
Added ↑ ↓ and ✎ buttons for phone use. Both gestures round-trip to the server
and survive a reload.

### Answering an approval looked like it had not worked

Two Allow buttons, two faults. The banner's Allow: the next poll put the row
back because the hook hadn't consumed the answer yet. Deck now retires the
request file immediately. The detail panel's Allow once: no optimism, 1200ms
pane poll. It now hides on click and re-captures after kiro-cli redraws.

---

## Release blockers cleared

### Correctness

- **`POST /api/projects/delete` raised on every call.** `list_sessions()`
  returned `{"sessions": [...]}`, iterating the dict yielded the string
  `"sessions"`. Fixed with contract test. Also added `delete-preview` endpoint
  that reports the true set including subdirectories, and refuses the `$HOME`
  project.

- **`POST /api/sessions/{id}/delete` deleted running sessions.** Backend guard
  and frontend `deleteOneSession` helper added. Batch delete leaves refused ids
  selected.

- **"Open in Finder" failed for `~/Documents/PROJECTS`.** `shorten_path`
  produced `…` prefixes that `open_folder` couldn't reverse. Paths now cross
  the API as `cwd` (real) plus `cwd_display` (abbreviated) at all 14 emission
  sites.

- **State writes neither atomic nor serialised.** Settings, favourites, and
  snapshots now use temp-file + `Path.replace()` with serialised
  read-modify-write.

### Packaging

- **`LICENSE` added.** MIT.
- **`README.md` rewritten.** Hooks, structural approvals, audit trail, remote
  access, LaunchAgent, QR exchange, tailnet boundary, and rate limits described
  as shipped.
- **Runtime state untracked.** `settings.json`, `snapshots.json`,
  `favourites.json` removed from git and gitignored.
- **`scan-helper.sh` deleted.** Dead since de-Warping.
- **Renamed to Quarterdeck.** Window title, login page, README, package
  metadata, spec file, bundle name, FastAPI title and icon. Bundle id:
  `com.vidanov.quarterdeck`. Internal identifiers (`~/.osa-kiro/`, `DECK_*`,
  `com.osa-kiro.remote`, `deck-*` markers) deliberately unchanged.

---

## Settings and remote access

### Settings page

- `SettingsPanel` in `App.jsx` with real controls.
- **Remote access** — start/stop, token display, rotate, QR, LaunchAgent.
- **Dispatch defaults** — default agent, model, effort for new sessions.
- **Hooks** — install/remove across agent configs with live coverage count.
- **Shell** — login shell in its own tmux session for `kiro-cli login` etc.
- **Appearance** — pane theme lifted to settings, view/filters persist to
  `localStorage`.

### Remote access

- Launch remote serving from the app via `/api/remote/start` and `/stop`.
- Token shown with copy button and QR code (single-use exchange code, 2-min
  expiry, not the token itself).
- Rotate token from UI.
- LaunchAgent install/uninstall for surviving reboots.

---

## Security

- **QR login no longer puts the token in a query string.** Uses a single-use
  `?c=<code>` with 2-min expiry. `?t=` rejected entirely.
- **Non-Tailscale source addresses refused.** Auth rejects non-loopback peers
  outside `100.64.0.0/10` (IPv4) and `fd7a:115c:a1e0::/48` (IPv6). Forwarding
  headers ignored.
- **Audit log built.** `backend/audit.py`, `~/.osa-kiro/audit/<date>.jsonl`,
  90-day retention. Records requests (in middleware, including refused ones),
  approval decisions, and tool outcomes. Redacts sensitive keys at write time.
- **Rate limits on `/dispatch` and `/input`.** 10 dispatches and 60 inputs per
  minute per remote device. Loopback unlimited. HTTP 429 with `Retry-After`.
- **Security warning in README.** States protections and limitations plainly.

---

## Kiro CLI hooks

- **`agentSpawn` correlation.** `DECK_NONCE` injected via tmux env, hook writes
  `KIRO_SESSION_ID` to `~/.osa-kiro/spawns/$DECK_NONCE`. Acts as a check on the
  process-lineage guess. `correlated_via` records which route was used.
- **`preToolUse` approval gating.** Per-session opt-in. Hook is a no-op unless
  `~/.osa-kiro/gates/<session id>` exists. Supports nonce-keyed pre-correlation
  gates. Timeout, default-deny, UI toggle with "no hook" warning.
- **`stop` signal.** Real end-of-turn, replacing 10s jsonl freshness heuristic.
  Not nonce-guarded so foreign sessions with the hook also benefit.
- **`postToolUse` audit.** Fourth hook, feeds the audit trail.
- **Auto-advance trigger.** `stop` fires the task stack's next item. One stop =
  one send, refuses to send into thinking/awaiting-approval.
- **Hook installer.** Merges marker-scoped entries into agent configs,
  preserves existing hooks, keeps backup. Reports stale vs missing vs current.

---

## Task stack (section 6)

- Per-session JSON storage under `~/.osa-kiro/stacks/`.
- Endpoints: list, add, reorder, edit, delete, send-next, auto-advance flag.
- Composer UI: "+ Queue" button, ↑ ↓ ✎ buttons, ⠿ drag handle.
- Auto-advance on turn end (off by default), driven by `stop` hook.
- Refuses to auto-send into a prompt (thinking or awaiting-approval blocked).

---

## Views and layout (section 7)

- **Attention-first grid.** "Needs you" as cards (action-first labels), "Working"
  as collapsed lines. Ordering by how stuck the session is.
- **Card reply.** Right control on the card when something is waiting (held tool
  call, permission menu, text input). No need to open detail panel.
- **Card excerpts.** Last assistant message shown in card (3-line clamp), full on
  hover. Bounded JSONL tail read, cached by mtime+size.
- **Transcript endpoint.** `GET /api/sessions/{id}/messages?after=<seq>` —
  line-addressed, paginated, with stable `seq` per entry.
- **QuickCreate and launcher merged.** Opening `+` replaces the quick line with
  detailed options.
- **Six view tabs collapsed to four.** Active, Collections, Stats, Settings.
- **Control filter deduplicated.** Managed badge removed from header.
- **Status-filter clear link removed.** Badge click toggles filter.
- **Pane theme moved to settings.** Appearance preference, not per-session.
- **View and filters persisted.** `localStorage` per device.
- **Phone treated as a breakpoint.** Media-query layout for Grid and detail.

---

## Messages API

`GET /api/sessions/{id}/messages?after=<seq>` — bounded, line-addressed
transcript. Each entry carries stable JSONL `seq`, spoken text, role, message
id, and lightweight tool metadata. Oversized tool-result lines become
addressable placeholders.

---

## Doc hygiene

Handover reconciled: no longer claims fixed test count, missing mobile layout,
missing pane resize, missing audit/hooks, or missing LaunchAgent. Rules set for
going forward (no test counts in prose, update handover with behavior changes,
checked items describe current behavior).
