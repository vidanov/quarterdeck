# Quarterdeck — Roadmap

Forward-looking work. Completed items live in [CHANGELOG.md](CHANGELOG.md).
Ordering within a section is deliberate — top item first.

State of record: [HANDOVER-pty-api.md](HANDOVER-pty-api.md).
Design rationale: [ARCHITECTURE-pty-api.md](ARCHITECTURE-pty-api.md).

---

## 1. Release blocker

- [x] **Demo GIF: answering a permission prompt from a phone.** Record
      `docs/assets/quarterdeck-phone-approval.gif` against a gated session.
      Recorded 2026-08-13 and added to README Phone access section.

---

## 2. Settings — remaining items

- [x] **Concierge on/off.** Model selection is wired. Added: disable the
      feature and stop an existing concierge session.
- [x] **Screenshots folder.** Watched directory path for the pickup feature
      (section 8).
- [x] **Poll intervals.** 400ms busy / 1.2s idle pane poll, 2s session list.
      Configurable in Settings.
- [x] **Retention.** What `/api/cleanup` should consider stale. Configurable
      in Settings.
- [x] **Danger zone.** Cleanup scan and delete behind one clearly-labelled
      door in Settings.
- [x] **Starting folder.** Three modes: Auto (frontmost Finder window),
      Last (cwd of most recently modified session), Fixed (user-specified path
      with folder picker). Stored in backend settings, respected by both
      `/api/cwd-suggestion` and `/api/dispatch`. Added 2026-08-13.

### Remote access — remaining

- [x] **Report reachability honestly.** Whether the Mac is on battery (sleep
      assertion ignored on battery), and last successful remote request.

### Self-update

- [x] **Check for updates.** `GET /api/update/check` compares the running commit
      (`git rev-parse HEAD`) against the latest remote commit (`git ls-remote
      origin HEAD`). Returns current version, latest version, and whether the
      working tree is clean. No network call if the repo has no remote configured.
- [x] **Apply update from Settings.** `POST /api/update/apply` runs `git pull`,
      `pip install -r requirements.txt`, and the frontend build, streaming
      progress lines to a response. Requires a clean working tree; aborts with an
      explanation otherwise.
- [x] **Restart after update.** After a successful apply, the backend triggers a
      process restart (replace the running process via `os.execv`). pywebview
      reloads automatically on reconnect.
- [x] **Settings UI.** A "Check for updates" button in the Settings panel showing
      current commit, last-checked time, and an "Update now" action when one is
      available. No automatic background checks — pull only when asked.

---

## 3. Security — remaining items

Ordered by risk removed per unit of work:

- [x] **Per-device tokens, revocable.** Replace the single shared secret with
      named device tokens (`phone`, `laptop`), each revocable, each recorded on
      use. Makes a lost phone a two-click problem. Implemented in
      `backend/devices.py` with Settings UI.
- [ ] ~~**Local TLS over the MagicDNS name.**~~ **Parked** — HTTPS Certificates
      require Tailscale paid plan. Not worth pursuing for a personal tool.
      WireGuard encryption is sufficient for the threat model.
- [ ] **Reconsider the loopback bypass.** Gate non-GUI endpoints behind the token
      even on loopback, with pywebview holding a token of its own. Required
      before `tailscale serve` can be safe.
      **Done 2026-08-15:** All loopback requests now require X-Local-Token.
      Exemptions: OPTIONS preflight, /app/* static assets (webview bootstrap),
      /login, /favicon.ico, proxied device tokens, dev/startup fail-open.
      inject_local_token in app.py extended to all methods (was mutating only).
      wait_for_backend() fixed to poll /app/ instead of /api/sessions.
- [x] **Move token to macOS keychain.** Remote and local tokens stored under
      `com.vidanov.quarterdeck` in the login keychain via `security` CLI.
      Auto-migrates from `~/.osa-kiro/token` and `local-token` on first read.
      Falls back to file in CI/headless environments. Implemented 2026-08-14.

---

## 4. Branch a session from a specific turn

The single biggest improvement available to the kiro-cli experience. `POST
/api/sessions/{id}/branch` already copies the full JSONL; branching at a turn
is a truncating copy.

- [x] **Verify kiro-cli tolerates a truncated JSONL on `--resume-id`.** Tested:
      kiro-cli loads a truncated JSONL without error. Missing trailing entries
      are simply absent from context. No dangling-tool-call issue.
- [x] **Turn-addressable transcript in the panel.** Each user turn as a discrete
      element with `seq` and a branch affordance.
- [x] **Truncating branch:** `POST /api/sessions/{id}/branch-at {"after_seq": N}`.
      Implemented with lineage tracking (parent_id, branch_point in metadata).
- [x] **Make lineage visible.** Record parent id and cut point in branch metadata.
      (Metadata is stored; UI is not yet wired.)

---

## 5. Task stack — remaining items

- [x] **Decided: kiro-cli's `Ctrl+S` queue.** Establish what it does before
      building a parallel mechanism.
- [x] **Cross-session stack view.** Dashboard answers "what is waiting" across all
      sessions.

---

## 6. Collections (unifying snapshots, projects, favourites)

One concept replacing three overlapping storage shapes. A collection is an
ordered set of session ids with a name and a `source` (snapshot, cwd, manual).

Members are `{session_id?, cwd, agent?, model?, prompt?}` — a session id when
running, a recipe when not. Opening a collection can spawn what isn't running.

- [x] Define the collection shape and storage (`~/.osa-kiro/`).
- [x] One endpoint set: list, create, rename, delete, add/remove members,
      reorder.
- [x] **`POST /api/collections/{id}/start`** — spawn members that aren't running.
- [x] **Migrate** `snapshots.json` and `favourites.json` into collections.
- [x] Handle missing members (show as unavailable, offer resume).
- [x] Decide whether a session can be in several collections (probably yes).

---

## 7. Views and layout — remaining items

### 7a. New views

- [x] **Focus** — one session full width, everything else as a thin attention
      strip.
- [x] **List / triage** — dense one-line rows, sortable, keyboard-navigable.
- [ ] **Board by collection** — columns per collection.
- [x] **Wall / ambient** — read-only, oversized status, no controls. For a
      second monitor.

### 7b. Structural work

- [x] **Extract the data layer.** `App.jsx` was 3957 lines with fetching, state,
      and layout interleaved. First extraction: SettingsPanel and all 10
      sub-components (1198 lines) into `components/SettingsPanel.jsx`. App.jsx
      reduced to 2783 lines. Further extractions (DetailPanel, SessionCard)
      remain for the next pass.
- [ ] **Shared attention path.** Approval interrupts must be identical across
      views, not reimplemented per view.

### 7c. Mobile UX

- [ ] **Bigger fonts and reduced element density on small screens.** The phone
      view inherits the desktop density — too small to tap reliably. Responsive
      pass: increase base font size, reduce visible card fields to the
      essentials (status, title, one action), and widen tap targets. Detail
      panel and approval gate are the critical paths.

### 7d. Grid refinements

- [x] **Quick-select folders editable.** Pin, remove, reorder. Wants 6a
      (collections) or at least a deliberate decision not to add `folders.json`.
- [x] **Per-session input history.** ↑/↓ walks history. Shared across card reply
      and detail composer for the same session.
- [x] **Snapshot button → "new collection".** Disappears into Collections view.

### 7d. Agent-generated summaries

Let an agent annotate waiting sessions with one line of context ("asked you to
confirm the DB name before it migrates"). Dangerous: never replace the
deterministic action, only annotate it.

- [x] Trigger on `stop` hook events, not a timer.
- [x] Cache by last entry id.
- [x] Only for sessions that are waiting.
- [ ] Bound the input (tail + pane, not whole conversation).
- [ ] Show the cost and provide an off switch.
- [ ] Distinguish description from recommendation.

---

## 8. Carried over from the handover

- [ ] Handoff for **iTerm2 and Ghostty** untested.
- [ ] `foreign` session status for agents without Deck's hooks — still on 10s
      freshness heuristic.
- [ ] `/api/projects` cache is in-process; first call after restart costs ~37s.
      Lands in collections (section 6).
- [ ] `/api/upload` never implemented. Screenshot pickup may remove the need.
- [ ] Unused frontend leftovers: `handleBranchSession` (lives again under
      section 4), `handleRestore`, `showRecent`, `lastFocusTime`.
- [x] Rename from the cards — click the session name directly in card, list, or
      wall view. `stopPropagation` prevents the click from opening the panel.
- [ ] **Screenshot pickup:** watched folder offering new files as paths to
      insert into the composer.

---

## 9. Beyond kiro-cli — multi-agent adapters

Stay Kiro-first. The adapter interface keeps depth portable later.

What an adapter abstracts:

- [ ] Session discovery (directory and history format).
- [ ] Spawn and resume (argv, id-correlation strategy).
- [ ] Status detection (per-tool TUI signatures).
- [ ] Prompt answering (key sequences for allow/deny).
- [ ] Capabilities (what a given adapter cannot do).

Sequencing: after sections 1–4 and 7b's data-layer extraction. Claude Code is
the obvious second adapter.

---

## 10. Ideas from KiroCrew worth adopting

### 10a. Time-limited safety override

- [ ] Per-session trust TTL with expiry re-enabling gating.
- [ ] Settings default for trust duration.
- [ ] Visual countdown on card/detail.
- [ ] Audit: trust-grant, trust-expire, trust-renew.

### 10b. Denied command patterns

- [ ] Deny list in the `preToolUse` hook for `execute_bash`.
- [ ] Default list (rm -rf /, git push --force, DROP TABLE, etc.).
- [ ] Settings UI: view, disable, add patterns.
- [ ] Bypass for gated sessions (already human-reviewed).

### 10c. Session resource protection

- [ ] Stall detection (no jsonl write for N minutes while "working").
- [ ] Auto-advance loop guard (N items without human interaction → pause).
- [ ] Turn limit per auto-advanced session.
- [ ] Resource monitoring (RSS, CPU in stats view).

### 10d. Scheduled dispatch (cron)

- [ ] Cron store (`~/.osa-kiro/cron.json`).
- [ ] Background evaluation and dispatch on schedule.
- [ ] Guard rails: timeout, no concurrent same-job.
- [ ] Settings UI.
- [ ] Phone notification on failure.

### 10e. Persistent memory across sessions

- [ ] Session summaries on archive (concierge-generated, one paragraph).
- [ ] Project context injection (last N summaries from same cwd).
- [ ] Learned preferences file, injected as context.
- [ ] Searchable history via FTS5.

### 10f. Sub-agent visibility

- [x] Detect sub-agents from process tree.
- [x] Read sub-agent status from their `.jsonl`.
- [x] Card annotation ("Thinking — 2 sub-agents active").
- [ ] Timeout awareness for stuck sub-agents.

### 10g. Side chat (non-blocking clarification against frozen context)

**What it is.** A `/side` command opens a multi-turn side conversation against a
frozen snapshot of the parent session's context. It lives in a separate tab (the
Activity panel or a dedicated pane), is fully isolated — messages never enter the
main conversation log — and tools are hard-rejected. You ask "wait, what does
this function do?" or "explain the trade-off you just proposed" without polluting
the primary agent's state or interrupting its current turn.

**Why it matters for Quarterdeck.** Today the choice when a session finishes with
something you don't understand is: type a follow-up (which starts a new turn,
costs context, and may steer the agent away from what it was doing), or go read
the code yourself. Side chat is the third option: ask the agent about its own
output without that question becoming part of the conversation. Especially
valuable when reviewing auto-advanced stack items — the session has moved on, but
you have a question about turn 4.

**Why it fits our architecture.** kiro-cli supports `--resume-id` and the `.jsonl`
is append-only. A side chat is a branch (section 4) with three constraints:
results are never written back, tools are disabled, and the branch point is always
"now." It can be a lightweight kiro-cli session with `--no-tools` (if that flag
exists) or a concierge-style query with the parent's tail injected as context.

Work:

- [ ] **Verify kiro-cli can run read-only.** Does `--trust none` or equivalent
      suppress all tool use while keeping the conversation context? If not, is
      there a flag for it? If neither, the concierge path is the fallback.
- [ ] **Snapshot the context.** On `/side`, capture the parent session's last N
      messages (tail of `.jsonl`) as a frozen context blob. This is the input to
      the side session and never changes regardless of what the parent does next.
- [ ] **Isolation guarantees.** The side session's `.jsonl` lives under a separate
      prefix (e.g. `~/.osa-kiro/side/<parent_id>-<timestamp>.jsonl`). It is never
      read by the parent, never summarized into memory (10e), never counted in
      stats.
- [ ] **UI.** A tab in the detail panel (alongside Activity, Queue, etc.) that
      shows the side conversation. Multiple side chats per session allowed —
      they're cheap. A "close" discards; there is no resume because the context
      was frozen at open time and grows stale.
- [ ] **Phone.** The side tab is reachable from the detail view on mobile. Keep
      it a simple text exchange — no tool output, no artifacts, no streaming
      indicators beyond a spinner.
- [ ] **No token bleed.** Side chats use the concierge session's token budget, not
      the parent's. The parent session is untouched — it does not know a side chat
      exists.

This is low-cost, high-value. It changes how you review agent output without
changing how the agent works.

### 10h. Team sharing — skills, agents, sessions

Three layers of shareability, in order of how tractable they are.

**Skills: local and shared.**

Skills are already directory-based (`SKILL.md` + supporting files). Two tiers:

- **Local skills** (`~/.kiro/skills/`). Personal, unversioned, the current
  default. Quarterdeck shows what's installed and lets you browse/edit.
- **Shared skills** — a git repo the team clones. Versioned, reviewable, one
  source of truth. Each team member pulls; Quarterdeck detects which local skills
  diverge from the shared repo and offers sync/update.

Work:

- [ ] **Skill browser in Quarterdeck.** List installed skills with description,
      trigger, last-modified. Already parseable from SKILL.md frontmatter.
- [ ] **Shared skill repo convention.** A git repo with one directory per skill.
      Quarterdeck points at it (settings), compares local vs remote, shows
      drift.
- [ ] **Install/update from shared.** Pull a skill from the team repo into
      local. Track origin so updates are offered when remote changes.
- [ ] **Publish local → shared.** Push a personal skill to the team repo
      (branch + PR, not direct push).

**Agents: shareable configurations.**

Agent configs (JSON with hooks, model, system prompt) are portable today but
have no distribution story. Same pattern as skills:

- [ ] **Agent config browser.** Show installed agents, which carry hooks, which
      model/effort they default to.
- [ ] **Exportable agent bundles.** An agent config + its associated skills as a
      portable directory. "Here's our review agent — install it."
- [ ] **Team agent repo.** Same shape as skills: git-versioned, pulled locally,
      drift detection.

**Sessions: idea stage, fragile.**

Sessions are folder-based (`.jsonl`, `.json`, `.lock`, `.history`) with absolute
`cwd` paths. Sharing is hard because:

- Paths are machine-specific.
- The `.jsonl` references tool outputs with local file contents.
- `--resume-id` expects the working directory to exist.

Possible direction: sessions live inside the repository they work on (a
`.quarterdeck/sessions/` directory). Clone the repo, get the sessions. Paths
become relative. But:

- kiro-cli writes to `~/.kiro/sessions/cli/`, not into the repo.
- Changing that is either a kiro-cli feature request or a symlink hack.
- Tool outputs reference files that may have changed since the session ran.
- A resumed session in a different checkout may hallucinate about code that
  moved.

This is worth exploring but not worth building yet. The simpler version —
**export a session transcript as a readable artifact** (markdown, not resumable)
— covers "show the team what happened" without the path fragility.

- [ ] **Session export as markdown.** Render `.jsonl` as a readable transcript
      with metadata header. Shareable, not resumable. Useful for reviews,
      postmortems, onboarding.
- [ ] **Explore repo-local session storage.** Feasibility study: can
      `--resume-id` work with a non-default session directory? If yes, what
      breaks? Park until kiro-cli offers a `--session-dir` flag or equivalent.

---

## 11. Monetization (if ever)

**Do not fund a rebuild on a guess.** Gate it on a cheap test: find people
running concurrent sessions, get 2–3 using it weekly, then ask for money.

Shapes in order of what has to change:
1. Open source, no money (default)
2. Sponsorship / bounties
3. Paid support and deployment
4. Open core (free local + paid team layer)
5. Managed relay (biggest setup-barrier removal, biggest responsibility)
6. Team subscription (SSO, retained audit, fleet health)

Target budget line: **team tooling** (one manager signs, no procurement).
The security/compliance line is the trap — longest cycle, hardest requirements,
buyer most likely to object to the premise.

---

## 12. kiro-cli V3 / ACP message format support

kiro-cli 2.16.0 introduced a `--v3` / `--agent-engine v3` flag with a new
session store and message format (ACP-style). V3 is not the default yet, but
migration tooling is coming and it will be. Quarterdeck must support both
formats simultaneously: new spawns can use V3, all ~1,500 existing V1 sessions
stay fully visible.

### What changed in V3 (verified from disk)

**Storage layout:**

| | V1 (current) | V3 |
|---|---|---|
| Root | `~/.kiro/sessions/cli/` (flat) | `~/.kiro/sessions/<workspace-hash>/` |
| Session files | `{uuid}.json`, `{uuid}.jsonl`, `{uuid}.lock` | `sess_{uuid}/session.json`, `sess_{uuid}/messages.jsonl` |
| Session ID prefix | plain UUID | `sess_` + UUID |
| Process lock file | `{uuid}.lock` with pid | absent |

**Message format** — entries changed from `{version, kind, data}` to
`{id, timestamp, payload: {type, ...}}`:

| V1 kind | V3 payload type |
|---|---|
| `Prompt` | `user` |
| `AssistantMessage` | `assistant` (content is a plain string, not a block list) |
| `ToolResults` | `tool_result` (separate line per result) |
| — | `tool_call` (separate line, precedes the result) |
| — | `turn_start` / `turn_end` (explicit turn boundaries) |
| — | `pending_interaction` (structural approval request — type `tool_approval`) |
| — | `interaction_resolved` (outcome: `accept`, `accept-all`, `deny`) |
| — | `session_start`, `session_metadata`, `session_event`, `usage_summary` |

**V3 session metadata** (`session.json`) gains `status`, `description`,
`agentMode`, `workspacePaths` (list), `modelId`.

### Work

**12a. Session discovery**

- [x] Scan `~/.kiro/sessions/` for workspace-hash dirs (8–16 hex chars) in
      addition to `cli/`. Add a `V3_SESSIONS_BASE` constant and a discovery
      function that returns `(store, path)` tuples for all sessions.
- [x] Detect session format from file layout: presence of `sess_*/session.json`
      = V3, `{uuid}.json` in `cli/` = V1. Tag each session with
      `format: "v1" | "v3"` in the listing response.
- [x] V3 has no `.lock` file. Process-alive check must use `session.json`
      `status` field and tmux pane presence instead.
- [x] `SESSIONS_DIR` constant is used in ~20 places — replace with a
      `session_paths(session_id)` helper that resolves the right paths for
      either format.

**12b. Status detection**

- [x] `tail_jsonl()` / `detect_status()`: add a V3 path that reads
      `payload.type` instead of `kind`. The `last_kind in ("ToolResults",
      "Prompt")` heuristic maps to `payload.type in ("tool_result", "user")`.
- [x] V3 `pending_interaction` entries provide structural approval detection
      without TUI scraping — more reliable than the current pane heuristic.
      Prefer it when present.
- [x] V3 `turn_end` entries provide an exact idle signal, replacing the
      `stop` hook's turn-mark files for V3 sessions.

**12c. Transcript reading**

- [x] `_ROLE_OF_KIND` map, `_block_text()`, `_block_tools()`,
      `_result_count()`, `_transcript_entry()`: add V3 variants that handle
      the flat `payload.content` string, separate `tool_call`/`tool_result`
      lines, and the `pending_interaction` / `interaction_resolved` pair.
- [x] `get_last_output()` and `last_message()`: V3 assistant content is
      `payload.content` (string), not nested blocks.
- [x] `read_transcript()`: detect format per-file and dispatch to the right
      parser. Keep `seq` continuous across both formats.

**12d. Session metadata**

- [x] `read_metadata()`: for V3 read `sess_{id}/session.json`. Map
      `workspacePaths[0]` → `cwd`, `lastModifiedAt` → `updated_at`,
      `modelId` → `model`, `status` → pass through.
- [x] `clean_title()` / `get_full_prompt()`: for V3 the first user message is
      the `user` payload entry, not a `Prompt` kind block.

**12e. Dispatch**

- [x] `tmux_manager.py` spawn: add `engine` parameter (`"v1"` | `"v3"`).
      Append `--agent-engine v3` to the `kiro-cli chat` command when `engine == "v3"`.
- [x] Store `engine` in the managed session record so resume and handoff use
      the same flag.
- [x] Correlation: V3 writes into a workspace-hash subdir with `sess_`-prefixed
      IDs. Update the `.lock`-based walk to also check the new layout; use
      `session.json` `id` field instead of lock file for session ID discovery.
- [x] `/api/dispatch` and `/api/options`: expose `engine` as a selectable
      parameter alongside `model` and `effort`. Settings UI: default engine
      (V1 / V3).

**12f. Archive and cleanup**

- [x] `GET /api/archive`: include V3 sessions (workspace-hash dirs). Return
      `format` field so the UI can badge V3 sessions.
- [x] `GET /api/cleanup/preview` / `POST /api/cleanup/apply`: handle V3 paths
      (delete `sess_{id}/` directory, not three flat files).

**12g. Hooks**

- [ ] V3's `pending_interaction` / `interaction_resolved` entries in the
      JSONL are the ACP equivalent of the `preToolUse` hook. Evaluate whether
      the existing file-based approval gate (`APPROVALS_DIR`) can be replaced
      or supplemented by reading these entries directly.
- [x] `agentSpawn` / `stop` hooks: verify they fire for V3 sessions and that
      the `KIRO_SESSION_ID` env var carries the `sess_`-prefixed form.

### What stays the same

- tmux as session transport. V3 changes the file format, not how the process
  is owned. `tmux attach` still works.
- TUI scraping for running sessions (`pane_status`, `pane_awaiting_approval`).
  The V3 TUI footer strings are identical to V1.
- Auth, remote access, audit trail, approval gate UI — all format-agnostic.

---

## 13. ACP control surface (V3 programmatic driver)

Source: empirical probe document dated 2026-07-31.

`kiro-cli acp` speaks JSON-RPC 2.0 over stdio. The interactive TUI is itself
an ACP client — there is no privileged channel. This means Quarterdeck can
drive V3 sessions entirely over protocol instead of via tmux key injection,
giving it structured events rather than screen-scraped TUI strings.

### What is verified on the wire (from the probe)

- `session/new`, `session/load`, `session/prompt`, `session/cancel`,
  `session/set_mode`, `session/set_model` are the core methods.
- `_kiro.dev/commands/execute` dispatches slash commands over the protocol.
  Slash commands are reachable programmatically — no TUI input needed.
- `_kiro.dev/commands/available` is a notification emitted after session
  creation carrying the full command list. This is the authoritative
  capability probe: the binary enumerating itself, superseding `/help`,
  documentation, and any model's claim.
- `ToolCall` and `ToolCallUpdate` notifications provide live streaming of
  tool use without parsing JSONL.
- `sessionCapabilities: {}` — no fork, list, or resume advertised at
  protocol level. `loadSession: true`. Forking exists only as a slash
  command (`/rewind`).
- `mcpCapabilities`: http false, sse false. MCP over stdio only.
- Client capabilities are not used. Kiro performs its own file I/O.
  The driver cannot intercept writes at protocol level.

### Architectural implication for Quarterdeck

The current stack is: tmux pane → screen-scrape footer → infer status.
ACP offers: JSON-RPC session → structured `ToolCall`/`ToolCallUpdate`
notifications → exact status without inference.

This does not replace tmux for process ownership (tmux attach still works,
the session still survives backend restarts). It replaces the pane-scraping
path for V3 sessions.

**Open before building:**

- Whether `kiro-cli acp` runs the V2 or V3 engine, and whether it accepts
  `--v3`. ACP docs were updated 27 May 2026, V3 docs 17 June 2026.
  Test: `kiro-cli acp --v3`, call `initialize`, read `agentInfo.version`.
- Whether `PreTaskExec` fires when a spec is driven over ACP rather than
  the TUI. If it does not, the step-boundary gate is TUI-only.

**Probe results (2026-08-12, kiro-cli 2.16.2):**

- `kiro-cli acp --agent-engine v3` **works**. Flag is `--agent-engine v3`
  (not `--v3`). Default engine is `v2`.
- `initialize` response confirms: `protocolVersion: 1`, `sessionCapabilities:
  {list, fork}` — **fork is now available** (was absent in previous probe).
  `mcpCapabilities: {http: true, sse: true}` — changed from prev probe.
  `checkpoints: true`. No `_kiro/commands/available` in extensionMethods.
- V3 sessions write to `~/.kiro/sessions/<workspace-hash>/sess_<uuid>/`
  with `session.json` + `messages.jsonl`. Workspace hash is 16 hex chars.
- Auth: ACP uses host-mediated token refresh via `_kiro/auth/getAccessToken`
  server→client request. Quarterdeck must respond to this with a valid token.
- `session/new` requires `{cwd, mcpServers: []}` at minimum.
- `session/update` notifications fire on session state changes.
- `_kiro/tools/didChange` fires on tool list changes.
- `PreTaskExec` **not observed** as an ACP notification — the prompt failed
  due to auth token refresh returning empty. Cannot confirm or deny yet.
  The step-boundary gate question remains open pending a full auth flow.

**Probe results (2026-08-14, kiro-cli 2.16.2 — session/load conflict test):**

`session/load` against a tmux-owned V3 session **hangs without response**. Tested
three times with throwaway sessions. The ACP process initializes cleanly
(`--agent-engine v3`, no `--trust-all-tools` which is incompatible with v3),
`initialize` returns normally, but `session/load` with the live session id
produces no reply within 20 seconds. The tmux session survives and the transcript
stays parseable. The messages.jsonl line growth observed was from the tmux session
completing its own turn, not from the ACP process writing.

| Question | Result |
|---|---|
| `session/load` against tmux-owned session | **hangs — no response** |
| ACP process writes to messages.jsonl | no |
| tmux session survives the probe | yes |
| Events arrive for tmux-driven turns | not reached (load never returned) |
| Transcript parseable after probe | yes |

**Conclusion:** ACP is not a viable side-channel for sessions it did not spawn.
The wrapper (Task 2) uses `session/new` only and owns its sessions from spawn.
**12g falls back to `tmux send-keys`** for V3 sessions Quarterdeck manages via tmux.
Tasks 3–7 follow the fallback branch of the flowchart.

Prerequisite for section 14 (constraint loop): ACP can drive sessions it spawns
itself. The `session/load` path for externally-owned sessions is closed.

### Work

- [x] **Probe ACP engine flag.** Run the two open questions above and
      record results in this section.
- [x] **ACP session wrapper.** `backend/acp_session.py` — ACPSession class,
      JSON-RPC 2.0 over stdio, notification callbacks, `collect_response()`.
      `backend/acp_query.py` refactored as thin wrapper. 15 unit tests,
      fake-subprocess pattern, 0.68s. Committed 2026-08-14 (940efd9).
- [x] **ACP observer side-channel.** `backend/acp_observer.py` — registry
      of ACPSession per V3 dispatch. Auth via SQLite `kirocli:odic:token`.
      `attach()` called in `dispatch_task` for `engine=v3`; `detach()` on
      kill; `detach_all()` on shutdown. `GET /api/sessions/{id}/acp-events`
      exposes events + capabilities + live status. Committed 2026-08-14
      (4ea8ec2).
- [x] **Replace pane-scraping for V3.** `detect_status()` in `api.py` now
      checks `acp_observer.detect_status()` first for observed sessions.
      Maps `session/update.sessionUpdate` values to standard status strings.
      Falls through to V3 messages.jsonl path and pane-scraping for
      unobserved sessions. Committed 2026-08-14.
- [x] **Slash command dispatch via ACP.** `_sq_send_delayed()` routes slash
      commands through `acp_observer.execute_command()` for observed sessions.
      `send_input()` routes prompts through `acp_observer.send_prompt()`.
      Both fall back to tmux on any error. Committed 2026-08-14.
- [x] **Capability probe on spawn.** `attach()` registers a one-shot callback
      for `_kiro.dev/commands/available`. Capabilities stored in the registry
      entry; exposed via `get_capabilities()` and the acp-events endpoint.
      `execute_command()` gates on capability list when known. Committed
      2026-08-14.

---

## 14. Constraint-accumulating agent loop

Source: design document dated 2026-07-31. Author's note: "interesting concept
to implement or to try out maybe in the quarterdeck or alone."

A long-running agentic loop where a doer executes steps, a deterministic
observer checks each step against anchors, and failures are returned as
compiled gate predicates (not prose) that block the same mistake on retry.
The loop accumulates constraints in `.kiro/hooks/` so learning survives
the episode.

This is a feature distinct from Quarterdeck's core session-management mission.
It is listed here because (a) Quarterdeck's approval gate and hook management
are the natural host for the gate registry, and (b) section 13's ACP driver
is the control surface the loop needs to drive the doer.

### Core design (from the probe document)

**The four claims the design rests on:**

1. Learning compiled into a repo file outlives the session. Retries are
   the input to the constraint accumulator, not the product.
2. Rewind only earns its cost when the failure and its cause are at
   different steps (attribution distance > 0). If distance is zero, a
   simple oracle-feedback loop is cheaper.
3. The observer must be starved of the doer's reasoning. Same-family model
   reading the doer's narrative reproduces its blind spot. Decorrelation
   by information starvation, not model vendor switching.
4. Constraints must be predicates over artifacts (scripts with exit codes),
   not prose reminders. Prose gets violated under context pressure. A gate
   that returns exit 2 cannot be forgotten.

**Oracle classes** — the real generalization axis:

| Class | Anchor | Loop authority |
|---|---|---|
| Native | Tests, type check, CDK diff, schema validator | Auto-rewind permitted |
| Proxy | Linter, latency budget, cost delta | Advisory only, never auto-rewind |
| Manufactured | Row counts, checksums, referential integrity | Auto-rewind after anchor validation |
| Internalized | Model judgment only | Auto-rewind disabled, route to human |

**Gate templates** (observer fills parameters, never writes free-form scripts):

1. Forbidden pattern in scope (regex + path glob).
2. Required structure (JSON Schema or required-field assertion).
3. Invariant (property assertion from a parameterized harness).
4. Budget (numeric ceiling on tokens, time, or calls).

**Roles:**

- Doer: strongest coding model, full tool access, one spec session.
  Authors code only. Does not author its verdict or the rewind decision.
- Observer tier 0: deterministic. `PostTaskExec` runs the anchor, appends
  `{branch, task, verdict, exit_code, stderr_tail, cost}` to trace JSONL.
- Observer tier 1: small model, separate ACP session, input is anchor output
  plus artifact diff, never the doer's reasoning. Output is a constraint
  proposal in constrained JSON.

**Loop invariants:**

- No rewind without at least one new gate that would have blocked the failed
  branch. Otherwise the loop re-rolls dice with a clean context.
- Same constraint proposed twice = oscillation. Stop and escalate.
- Every gate carries a path scope and domain tag. Gates without scope
  accumulate globally and eventually block everything.
- Every gate records the trace event and branch that produced it, plus a
  review-by date. Gates that have not fired in 90 days are retired.

### Relationship to Quarterdeck's existing primitives

| This design needs | Quarterdeck already has |
|---|---|
| Gate registry in `.kiro/hooks/` | Hook installer (Settings → Kiro hooks) |
| Per-session approval gating | `preToolUse` gate (`GATES_DIR`, `APPROVALS_DIR`) |
| Step-boundary events | `PreTaskExec` hook (V3 only, needs section 13 probe) |
| Slash command dispatch | ACP driver (section 13) |
| Trace JSONL per session | `~/.kiro/sessions/` JSONL (V1) + V3 `messages.jsonl` |
| Branch table with parent pointers | Not yet — needed if attribution distance > 0 |
| Observer tier 1 session | Concierge pattern (separate ACP session) |

### Build order (from the design doc, adapted)

**Stage 0 (prerequisite, ~1 day).** Run the open questions from section 13:
does `kiro-cli acp` run V3, and does `PreTaskExec` fire over ACP? Without
this, the step-boundary gate is speculative.

**Stage 1 (no branching).** Single session per attempt. Anchor + constraint
compiler + gate registry. Gates accumulate in `.kiro/hooks/`. Worked example:
tasks 1–3 of an SQS idempotency spec, unit-test oracle, worktree rollback.
*Kill criterion:* if gate yield is near zero, the loop was never the problem.

**Stage 2.** Measure attribution distance on real trajectories. If the mass
sits at zero, ship stage 1 without rewind.

**Stage 3.** Compensations for deploying steps. Add a task that calls an
external API and declare its rollback.

**Stage 4.** Branch table, only if stage 2 justified it. Options: `/rewind`
via ACP plus git worktree with own `branch_id`, or LangGraph as orchestrator
with Kiro over ACP as the doer node.

### Open questions before building

1. Does `kiro-cli acp` run the V3 engine? (See section 13.)
2. Does `PreTaskExec` fire when a spec is driven over ACP? (See section 13.)
3. Does `/rewind` mint a new session id or rewrite in place? Test:
   `/session-id`, `/rewind`, `/session-id`. Determines branch table key.
4. Is `/checkpoint` removed or flag-gated? (See section 13 capability probe.)

---

---

## Phase 2 — Measurement and memory

Full specification: [PHASE2-PLAN.md](PHASE2-PLAN.md).

**The gap.** Quarterdeck is an authorization and observation plane with no memory.
Nothing accumulates across sessions. That is the only gap worth building.

**Measured basis.** 23 sessions labelled by hand: 17 rule violations, 15 of 17
caused by a rule that never reached the context. One repair loop costs ~20,000
tokens; one rule injection costs ~175. Break-even is roughly one injection in fifty.

**Three additions**, ordered by independence:

1. **Delivery recording** — which steering rules reached the context per turn.
   The deliverable that stands alone regardless of everything else.
2. **Correction capture** — one keystroke writes a timestamped record with which
   rules were live, what commit the steering tree was on, and which turn it happened at.
3. **Per-rule gates** — `preToolUse` blocks that fire on specific measured
   patterns, start in `warn` mode, and require both a positive and a negative fixture.

**Task readiness**

| # | Task | Status | Blocker |
|---|---|---|---|
| 1 | Ownership adapter and grid fix | **Blocked** | Q1: does `~/.kiro/crew` expose parent-child relationships? |
| 2 | Delivery recording | **Shipped 2026-08-13** — `GET /api/health/build`, build stamp, stale banner | — |
| 3 | Correction button | **Shipped 2026-08-13** — corrections panel with kind field, `record_unverified_claim()` | — |
| 4 | Claim detector stop hook | **Shipped 2026-08-13** — `scripts/verify-claim.sh`, `STOP_HOOK_COMMAND` in tmux_manager | — |
| 5 | Unverified claims UI + profile badge | **Shipped 2026-08-13** — amber ⚠ badge, hollow ○ profile dot, `profile_verified` field | — |
| 6 | Model/effort selectors show live values | **Shipped 2026-08-13** — reads `rts_model_state` from session JSONL | — |
| 7 | Duration measurement | Ready, lower priority | Extends existing stats and audit trail |
| 8 | Secrets handling | Ready, lower priority | Extends existing audit redaction (§2.1) |
| 9 | Liveness states + spawned process registry | After Task 7 | Extends 10c; thresholds need Task 7 data |
| 10 | Board layering | After Task 9 | Extends existing grid; context % part already shipped |
| 11 | Compaction management | Q8 must be resolved first | 11.3 conflicts with the cancelled double-buffered handoff (Part 6) |

**Immediate action plan**

Start Task 2. While it runs:
- Probe Q1 (`~/.kiro/crew` schema) to unblock Task 1
- Read section 12 (V3 format) and section 13 (ACP driver) before building Task 9

**Cross-links to existing roadmap sections**

| Phase 2 task | Extends |
|---|---|
| Task 1 (ownership) | [10f sub-agent visibility](#10f-sub-agent-visibility) |
| Task 2 (delivery recording) | Settings panel, hook coverage display |
| Task 3 (correction button) | Card and detail panel controls |
| Task 4 (per-rule gates) | [10b denied command patterns](#10b-denied-command-patterns), existing `preToolUse` hook |
| Task 5 (recurrence) | [10e persistent memory](#10e-persistent-memory-across-sessions) |
| Task 6 (findings) | [10e persistent memory](#10e-persistent-memory-across-sessions), [10g side chat](#10g-side-chat-non-blocking-clarification-against-frozen-context) |
| Task 7 (duration) | Stats view, audit trail (§2.1 of PHASE2-PLAN.md) |
| Task 8 (secrets) | Existing audit redaction, Settings panel |
| Task 9 (liveness) | [10c session resource protection](#10c-session-resource-protection) |
| Task 10 (board) | [7a wall view](#7a-new-views), existing grid |
| Task 11 (compaction) | [12 V3 format support](#12-kiro-cli-v3--acp-message-format-support), [13 ACP driver](#13-acp-control-surface-v3-programmatic-driver) |

**Open questions that gate build decisions**

| # | Question | Gates |
|---|---|---|
| Q1 | Does `~/.kiro/crew` expose parent-child session relationships? | Task 1 |
| Q2 | Does a steering rule reach the model at decision time, or only at session start? | Task 2 deliverable |
| Q3 | Does KiroCrew's lesson mechanism actually deliver? | Task 2 deliverable |
| Q4 | Is recurrence real once a denominator exists? | If no: stop at Task 5, revert Task 4 |
| Q8 | Is Task 11.3's compaction overlap the same mechanism as the cancelled double-buffered handoff? | Task 11.3 |
| Q9 | Does the "captain" role (6B.2) conflict with the cancelled driver/orchestrator? | 6B.2 becoming a task |

---

## 19. UX patterns from Omarchy evaluation (2026-08-23)

Source: Omarchy v4 "Quattro" by DHH (basecamp/omarchy). A Linux desktop environment, not a
web framework — but three design decisions map directly onto Quarterdeck problems.

### 19a. Merge Concierge and dispatch into one ⌘K surface

Omarchy finding: once `Super + Space` could search both apps and commands, there was no
reason to keep two separate palettes. They merged the launcher and the command menu into
one filterable box.

Quarterdeck currently has two overlapping entry points: the Concierge bar (⌘K, natural
language, session search) and the dispatch box (quick-create input, raw task text). They
serve different users of the same intent: "start or find something."

- [ ] **Unified ⌘K surface.** One box that: finds active sessions by title/cwd, searches
      the archive (Phase 1 of section 17a), dispatches new sessions, runs Concierge queries,
      and opens Library folders. The quick-create strip below the header becomes a shortcut
      to ⌘K, not a separate input.
- [ ] **Intent detection.** If the query matches a session title → show it. If it matches
      nothing → offer to dispatch it as a new task. No mode switching. The box decides.
- [ ] **Mobile ⌘K.** On small screens ⌘K is the primary nav. A single tap-target in the
      header opens it. Session cards are the result list. Approval actions stay on cards.

Relationship to section 17a (content-aware search): the unified ⌘K is the front-end for
the FTS5 index. Build 17a first, then wire it into a merged ⌘K surface.

### 19b. Data-file pattern for display widgets

Omarchy's agents bar widget was rewritten: it discovers JSON records that separate
collector scripts write, watches them for changes, and draws whatever appears. Adding an
agent means shipping a collector — never touching the display code.

The display is dumb. The logic is replaceable.

This pattern already appears implicitly in Quarterdeck's roadmap (16a gate types, 16d
knowledge MCP). Worth naming it explicitly as a convention:

- [ ] **Collector/display split convention.** Any Quarterdeck widget that shows live data
      (session status, agent usage, deny pattern stats, knowledge index freshness) reads
      from a JSON file in `~/.osa-kiro/state/`. A background collector writes that file.
      The API endpoint serves the file; the frontend renders it. Neither knows about the
      other's internals.
- [ ] **File-watching for live updates.** Instead of polling `/api/sessions` for every
      status widget, have the backend watch its own state files and push updates via the
      existing SSE or WebSocket path. The frontend subscribes once. Omarchy's conclusion:
      "an idle desktop stops burning CPU" — same applies to Quarterdeck on a phone with
      all sessions idle.

Relationship to section 10c (session resource protection) and the adaptive polling work
done in SessionsContext: this is the next step past backoff polling.

### 19c. Direct manipulation over settings dialogs

Omarchy principle: "There's no settings panel for the bar — you just grab it." Direct
interaction with the thing itself rather than configuring it in a modal.

Quarterdeck's settings panel is now 6 tabs and growing. Some of what lives there should
move to where the thing actually is.

- [ ] **Drag-to-assign folders in Library.** Session cards in the Library view (section 18)
      should be draggable onto folder targets. No "Add to folder" dialog.
- [ ] **Inline session rename.** Already implemented for session cards. Extend to the
      detail panel header — click the title, type, done. No modal.
- [ ] **Drag-to-reorder chips.** Composer chips (the starter chips row) already have up/down
      buttons in the chips editor. Replace with drag handles for direct reordering, both
      in the editor and in the composer strip itself.
- [ ] **Settings panel audit.** After Library and 19a are built, review Settings for items
      that belong closer to the thing they configure. Candidates: deny patterns (belong in
      a session's security context, not a global tab), chips (belong on the composer),
      per-project secrets (belong in the session detail panel, not Settings).

---

Status: design discussed, not started. Replaces and extends current Collections tab.

### Problem with current state

- Collections are flat — no nesting, no subfolders
- Snapshots are pointer lists (session IDs), not reusable spawn recipes
- Projects tab is auto-generated by cwd, not user-organised
- No way to duplicate a session with the same premise
- Archive, Projects, Snapshots, Collections are four separate sub-tabs that conceptually belong together

### Core concept

A two-pane **Library** tab replaces the Collections tab. Left pane: folder tree (user-managed, infinitely nestable). Right pane: sessions and templates in the selected folder.

Sessions can live in multiple folders (folders are labels, not containers — the session still lives in `~/.kiro/sessions/`). Templates and sessions coexist in the same tree, distinguished by icon.

```
┌────────────────┬───────────────────────────────────────────┐
│ FOLDERS        │  Quarterdeck                               │
│                │                                            │
│ 📁 AWS         │  ◉ osa-kiro refactor        active  2h ago│
│  📁 Quarterdeck│  ○ auth bug fix              done   3d ago │
│  📁 VPtB       │  📋 Feature start template  template      │
│ 📁 Porsche     │                                            │
│ 📁 Personal    │  [+ Add]  [Duplicate]  [Spawn template]   │
│                │                                            │
│ [+ New folder] │  ── Archive ─────────────────────────────│
│                │  [search archived sessions inline]         │
└────────────────┴───────────────────────────────────────────┘
```

### Data model

`~/.osa-kiro/library.json`:

```json
{
  "folders": [
    {"id": "f1", "name": "AWS", "parent_id": null},
    {"id": "f2", "name": "Quarterdeck", "parent_id": "f1"}
  ],
  "entries": [
    {"id": "e1", "folder_id": "f2", "session_id": "abc123", "added_at": "..."},
    {"id": "e2", "folder_id": "f2", "template_id": "t1"}
  ]
}
```

### Templates (replaces Snapshots)

Current snapshots = list of session IDs captured at a point in time.
Proposed templates = **session spawn recipe**: cwd, task text, agent, model, effort, chips config, secret names (not values).

Captured from any live or archived session via "Save as template". Click template → pre-fills the launcher. "Quick spawn" fires immediately with no edits.

Template shape:
```json
{
  "id": "t1",
  "name": "Quarterdeck feature start",
  "folder_id": "f2",
  "recipe": {
    "cwd": "/path/to/project",
    "task": "Implement: [describe feature]",
    "agent": "kiro_default",
    "model": "claude-sonnet-4-5",
    "effort": "normal",
    "chips": [...],
    "env_vars": ["DATABASE_URL"]
  },
  "source_session_id": "abc123",
  "use_count": 4
}
```

### Duplicate session

Two variants:

- **Duplicate as new session** — spawn a fresh session in the same cwd with the same first user turn as task. Available from card overflow, detail panel, Library view. Uses `POST /api/dispatch`.
- **Branch** (already exists as `branch-at`) — fork from a specific conversation turn. Keep as-is, surface more visibly.

Distinction: Duplicate = fresh start with same premise. Branch = fork from a point in the conversation.

### Migration from current state

- Collections → Library folders (a collection becomes a folder, migration on first Library open)
- Snapshots → Templates (legacy snapshot = template without recipe, displayed as "legacy snapshot" until manually upgraded)
- Archive → stays as search-oriented filter inside Library ("Show archived" toggle)
- Projects → auto-folders in Library (grey, system-managed, seeded from cwd grouping; user folders appear above)

### Build order (when work starts)

1. **Library tab skeleton** — two-pane layout, folders in localStorage, sessions from existing API
2. **Folder persistence backend** — `GET/POST/PATCH/DELETE /api/library/folders`, `~/.osa-kiro/library.json`
3. **Templates** — `GET/POST/DELETE /api/templates`, capture from session, spawn via launcher
4. **Duplicate** — `POST /api/sessions/{id}/duplicate`, surface in card overflow and detail panel
5. **Migration** — convert existing collections/snapshots on Library tab first open

### Open questions before build

- Should sessions be movable between folders (change primary folder) or only addable to multiple folders?
- Auto-add to folder when session starts in a cwd that matches a folder's pinned path?
- Template versioning: can a template be updated, or is each capture a new template?
- Should the folder tree replace the Projects tab entirely, or coexist?

---

Source: handover document "Semantic Checkout for Captain and Quarterdeck" (2026-08-23).
The core design decision: **late binding**. Do not compress session content at write time
against a guess about future questions. Extract at read time, against the actual question.

Two independent phases. Phase 1 unlocks Concierge content search with minimal infra.
Phase 2 is the full semantic checkout — restoring a specific reasoning thread verbatim
into a new session.

### 17a. Content-aware archive search (Phase 1)

Current `GET /api/archive?q=` does title + cwd substring match only. "Find the session
about the auth bug" fails when the session is titled "osa-kiro refactor."

- [ ] **Session content index.** `backend/session_index.py` — SQLite FTS5 index over
      the first 5 user turns of every session. Rebuild incrementally on access
      (mtime check against `~/.osa-kiro/session-index.db`). No embeddings for Phase 1 —
      BM25 over raw text is sufficient for same-project queries.
- [ ] **Extend archive search.** `GET /api/archive?q=...&content=true` falls through to
      the FTS5 index when title+cwd match returns zero results. Return ranked results with
      a `match_source: "content"` field so Concierge can distinguish.
- [ ] **Concierge intent-aware dispatch.** When a Concierge query resolves zero title
      matches, automatically retry against content index. Present top 3 with matched
      excerpt visible. Already noted in section 15b.
- [ ] **V1/V2/V3 reader parity.** The index must cover all three session formats
      (V1 SQLite, V2 SQLite, V3 JSONL). Quarterdeck already reads all three for status
      detection — reuse that path for content extraction.

### 17b. Chapter segmentation and semantic checkout (Phase 2)

A long session contains several unrelated threads. Restore the specific thread, not
a summary of the whole session.

Design principle from the handover: **chapter title = originating user question verbatim**.
Not a generated summary. You recognise your own question faster than any abstraction of it.

- [ ] **Chapter segmenter.** Segment sessions into chapters on user-turn topic shift
      (heuristic: embedding distance between consecutive user turns > threshold).
      Fall back to the SeCom segmenter (arXiv 2502.05589) if boundaries are poor.
      Chapter index stored in `~/.osa-kiro/chapter-index.db`.
- [ ] **Chapter index is a read model.** The index is rebuildable from the JSONL by
      replay. It never mutates session files. Re-segmentation produces a new projection
      version; old projections stay addressable.
- [ ] **Hybrid retrieval.** BM25 + vector over chapter titles and first user turn.
      Rerank. Return the chapter map — do not auto-select. Selection is the user's.
- [ ] **Restore action.** `POST /api/sessions/{id}/restore-chapter` assembles:
      compacted prior context, verbatim chapter, current date, provenance envelope
      (`source_session_id`, `turn_range`, `checkpoint_date`, `ledger_entry_hash`,
      `segmenter_version`). Dispatches as a new session via `load-via-script` hook.
- [ ] **Provenance envelope in audit trail.** Restored context enters the new session
      as an unverified claim. The audit trail records the envelope so derived outputs
      are traceable to an unattested input.
- [ ] **Chapter browser in detail panel.** A "Chapters" tab alongside Transcript and
      Activity. Lists chapter titles (verbatim user questions), date, turn range. Click
      to restore. On mobile: accessible from the session detail view.

### Mode split (from arXiv 2604.27003 evidence)

| Mode | Distance | Representation | v1 scope |
|---|---|---|---|
| Resume | same project, same problem | verbatim chapter | ✅ build |
| Transfer | different project or task | abstracted decision, verbatim on demand | deferred |

Cross-project transfer is deliberately deferred. Within-project verbatim beats abstraction
(evidence: -9.5 FWT negative transfer for raw cross-task vs +6.5 for abstracted insights).
The ordering inverts for within-task reuse. Build resume first, measure, then transfer.

### What not to build (explicitly out of scope for v1)

- Automatic chapter selection (selection is the user's)
- Delta / invalidation sets (git gives chronology, not semantic supersession)
- Cross-project reasoning transfer
- Abstract decision extraction

### Sequencing

Phase 1 (17a) before Phase 2 (17b). Phase 1 stands alone and unblocks Concierge.
Phase 2 depends on Phase 1's reader infrastructure.

---

Source: `/Users/a.vidanov/Documents/PROJECTS/PORSCHE/ACTUAL/turbo-spec-main` — a spec-driven
multi-agent CI/CD engine (Porsche internal). Five patterns worth adopting, in priority order.

### 16a. Named quality gates with retry/reroute logic

turbo-spec's evaluators are discrete, named, pluggable verdicts (`build_pass`, `review_approved`,
`spec_completeness`). Quarterdeck's approval gate is binary: allow/deny.

Pattern: named gate types with configurable outcome routing instead of a single hold/release.

- [ ] **Gate types per session rule file.** Each rule entry carries an `action` field: `auto_allow`,
      `auto_deny`, `human_required`, `timed_out`. Pairs with 15a (per-session rule files) and 10a
      (trust TTL).
- [ ] **Retry/reroute on gate outcome.** When a gate fires `human_required` and the human denies,
      the agent can be rerouted to a fallback task rather than hard-blocked. Configurable per rule.
- [ ] **Gate verdict in audit trail.** Every auto-allow and auto-deny records the rule name that
      matched, not just the tool name. Already partially addressed by 15a.

Reference: `src/agentic_evaluator/assessor.py`, `script_gate_evaluator.py` in turbo-spec.

### 16b. Blueprint YAML for multi-stage dispatch

turbo-spec describes multi-agent pipelines declaratively. Quarterdeck dispatches single sessions
with a task string.

- [ ] **Blueprint format for chained dispatch.** A lightweight YAML definition:
      `stages: [{task, cwd, depends_on, gate}]`. Define "plan then implement" chains from the UI
      without needing ACP. Stored in `~/.osa-kiro/blueprints/`.
- [ ] **Blueprint picker in launcher.** When dispatching, offer to pick a blueprint instead of
      typing a raw task. Blueprint fills the task field and sets up the dependency chain.
- [ ] **Stage handoff.** When a stage completes successfully, auto-dispatch the next stage with the
      previous stage's output injected as context.

Relationship to ROADMAP: this is a lighter-weight version of section 14 (constraint loop) that
does not require ACP. Sequence: build 16b before 14.

Reference: `docs/workflow-blueprint.md`, `src/workflow_skeleton/schemas/` in turbo-spec.

### 16c. Evaluator pattern for deny chains (waterfall)

turbo-spec's `aop_apply_critic`, `script_gate_evaluator`, `scripted_judge_gates` are all
variations of "run a check, get a verdict, route on outcome." Python, no external framework.

- [ ] **Waterfall deny chain.** Replace the flat pattern list in `deny.py` with a chain of policy
      handlers that each call `next()`. Each handler can short-circuit (deny) or pass through.
      Cordis-style semantics, ~20 lines of Python.
- [ ] **Script gates.** Allow a deny pattern entry to specify a shell script instead of a regex.
      Script receives the tool input on stdin, exits 0 (allow) or non-zero (deny). Enables complex
      checks (file content inspection, git diff analysis) that regex cannot express.

Reference: `src/agentic_evaluator/assessor.py` in turbo-spec.

### 16d. knowledge-mcp as queryable context injection

turbo-spec runs a local MCP server that agents query for project-specific reference material
instead of injecting entire steering files. Agents ask questions, get relevant chunks.

- [ ] **Local knowledge MCP server.** Small SQLite-backed MCP server (`backend/knowledge_mcp.py`)
      built at session dispatch from a watched directory (`~/.kiro/steering/`, project docs).
      Exposes a `search_knowledge(query)` tool agents can call during a session.
- [ ] **Per-session knowledge scope.** The knowledge server is scoped to the session's cwd —
      project-specific docs override global steering for that session.
- [ ] **Settings UI.** Configure watched directories per project. Toggle knowledge MCP on/off per
      session from the detail panel.

Relationship to ROADMAP: this is the implementation path for section 10e (persistent memory across
sessions). Sequence: build 16d before 10e's full memory feature.

Reference: `knowledge-mcp/server/` in turbo-spec.

### 16e. OpenSpec governance for Quarterdeck's own development

turbo-spec enforces: every PR that changes behaviour must carry a versioned spec document, archived
into the repo on merge. CI blocks merge until archived.

- [ ] **Lightweight spec convention.** For non-trivial features (anything touching auth, approval
      gates, session storage), require a spec entry in `docs/specs/` before implementation.
      Not CI-enforced (solo tool), but tracked in ROADMAP and CHANGELOG.
- [ ] **ADR discipline.** turbo-spec's `docs/adr/` pattern — one file per architectural decision
      with status, context, decision, consequences — applied to Quarterdeck's open decisions
      (V3 vs V1 session format, ACP transport, loopback bypass, etc.).

This is a process change, not a feature. Low urgency, worth adopting if the project gets
contributors.

---

Source: https://composio.dev — tool execution infrastructure for AI agents.
Composio's product is orthogonal to Quarterdeck (tool layer vs. session layer),
but several of its design patterns map directly onto open problems here.

### 15a. Approval gate rule sets (auto-allow by pattern)

**The steal.** Composio offers action-level scoping: allow READ but block
DELETE per agent role. Quarterdeck's gate is binary (allow / deny per call).

- [ ] **Per-session rule file.** A JSON or YAML file per session (or per
      project cwd) listing tool patterns that are auto-allowed, auto-denied,
      or always gated. Format: `{pattern: "read_file", action: "allow"}`.
- [ ] **Default deny list.** Seed from the 10b list (`rm -rf /`, force
      pushes, DROP TABLE) plus safe-reads auto-allow category.
- [ ] **Settings UI.** View and edit the active rule set for a session.
      Toggle individual patterns. Inherits from project rule if no session
      rule exists.
- [ ] **Gate bypass indicator.** When a tool call is auto-allowed by rule,
      show it in the audit trail with the rule that matched, not silently.

This directly reduces approval fatigue for power users running many sessions
while keeping explicit gates on high-risk operations. Pairs with 10a (TTL)
and 10b (deny patterns) — those two become the starter rule set.

### 15b. Intent-based session search in Concierge

**The steal.** Composio resolves tools by semantic intent ("send email" →
GMAIL_SEND_EMAIL), not keyword match. The Concierge command bar queries by
title string today.

- [ ] **Semantic session search.** Index session transcripts by content (not
      just title/cwd). When a Concierge query returns zero title matches,
      fall through to a content search across the last N turns of each
      session. "find the session about the auth bug" should match a session
      titled "osa-kiro refactor" if the transcript discusses auth.
- [ ] **Intent-aware dispatch.** "Start fixing the CSS" → resolve cwd from
      project name, pre-fill task field. Already partially done; the missing
      piece is resolving ambiguous project names from transcript context.

### 15c. Large tool output offload (context efficiency)

**The steal.** Composio stores large tool responses on a remote filesystem
the agent can browse rather than dumping them into context.

- [ ] **Auto-offload oversized tool outputs.** When a tool result exceeds a
      configurable threshold (default: 8 KB), write it to
      `~/.osa-kiro/artifacts/{session_id}/{seq}.txt` and inject a compact
      reference (`[artifact:seq]`) into the transcript instead.
- [ ] **Artifact browser in detail panel.** List session artifacts alongside
      the transcript. Click to view or download.
- [ ] **Pair with "paste as document".** The existing feature collapses
      large pastes into tiles. This extends the same idea to *outbound*
      tool results, not just inbound pastes.

### 15d. Just-in-time approval (lazy auth pattern)

**The steal.** Composio triggers auth inline when the agent actually needs
it, not at pre-configuration time.

- [ ] **Deferred gate enrollment.** Today, approval gating must be enabled
      before a session starts. Allow enabling the gate mid-session: the next
      tool call after enrollment is the first gated one. No session restart
      required.
- [ ] **Approval prompt surfaced at use time.** When an ungated session
      encounters a high-risk tool pattern (matching 15a's deny category),
      surface a one-time "gate this?" prompt in the card rather than silently
      allowing or silently blocking.

---



**The name is settled: Quarterdeck.** The deck an officer of the watch stands on
and gives orders from.

Internal identifiers stay unchanged (`~/.osa-kiro/`, `DECK_NONCE`, `DECK_PORT`,
`com.osa-kiro.remote`, `deck-*` markers) to avoid orphaning installed hooks.

### Things explicitly NOT adopted from KiroCrew

- ACP as session **transport** (kills `tmux attach`). The V3 ACP-style file
  format is adopted (section 12); the transport layer is not.
- Multi-surface messaging (Slack, Discord, Telegram).
- Electron wrapper.
- Token auth ceremony for localhost.
- Governance profiles and multi-channel policies.
- MCP server management UI.

## Pending UX requests (2026-08-02)

- ~~**Ctrl+X shortcut**~~ — done: chip in composer bar + `Ctrl+X` key in textarea
- ~~**Quick new session from focused/fullscreen mode**~~ — done: ＋ button in detail header, opens launcher without collapsing panel
- ~~**Persist helpers menu (chips strip) state across restarts**~~ — done 2026-08-13: `chipsOpen` saved to backend settings (`dispatch-cwd-mode`), survives WKWebView localStorage clear
- ~~**Copy button on transcript messages**~~ — done 2026-08-15: `⎘` button on every assistant block and user bubble, hidden until row hover, copied state shows green `✓` for 1.5 s.

## Pending UX requests (2026-08-20)

- [x] **macOS dock badge — attention count.** When one or more sessions need
      attention (status `awaiting-approval`, or `error`), show a red badge on
      the Quarterdeck dock icon with the count. Clears when no sessions need
      attention. Background thread polls `/api/sessions` every 5 s and calls
      `NSApp.dockTile().setBadgeLabel_()` via pyobjc. Implemented in `app.py`
      2026-08-20.
- [ ] **Project automation — mechanical procedures outside LLM chat.** Repetitive
      project-level operations (git push, build, deploy, run tests, npm install,
      pip sync) should be executable from Quarterdeck without opening a chat session
      or typing a prompt. A per-project "Procedures" panel lists named shell commands
      defined in `.quarterdeck/procedures.json` (or a global fallback list). Each
      procedure is a label + shell command, optionally with a confirm step for
      destructive ones.

      - `GET /api/projects/{cwd}/procedures` — list procedures for a cwd.
      - `POST /api/projects/{cwd}/procedures/run` — `{"name": "..."}`. Runs the
        command in the project cwd in a short-lived tmux pane; streams output back.
      - UI: a "⚡ Run" button on project cards and the detail header that opens a
        procedure picker. One click to git push, build, or deploy without involving
        an LLM. Output shown inline (small terminal or toast log).
      - File format: `[{"name": "git push", "cmd": "git push origin main",
        "confirm": true}, {"name": "build", "cmd": "./build-app.sh --install"}]`.
        Global defaults (git status, git push, npm run build) when no project file.
      - Commands matching destructive patterns (push, rm, deploy) require confirm.
        Safe commands (build, test, status) run immediately with output inline.

## Pending UX requests (2026-08-15)

- [ ] **Restore + Queue button in mobile Chat view.** `App.css` hides `.queue-btn`
      on ≤700 px with `display: none` (line ~3636). The original rationale was
      "use the stack chip instead", but the chip is not always visible and the
      button is the primary queueing affordance in the Chat tab. Either restore
      the button with a compact style (icon-only, min 44 px tap target) or keep
      the chip but make it visually prominent enough to replace the button.

- [ ] **Restore Managed filter on mobile.** `.control-filter-btn` is hidden
      entirely on ≤700 px (line ~3624). The "Managed" filter is the most
      useful one on a phone — it narrows to sessions Quarterdeck owns, where
      approvals and dispatch are relevant. Restore at minimum the Managed
      button; keep All/Crew/Idle hidden if space is tight. A compact two-button
      strip (All | Managed) fits in one row at 44 px minimum height.

- [ ] **Obsidian file browser and basic editor.** A new top-level view (tab in
      the header alongside Sessions, Stats, etc.) that serves a user-configured
      vault path (Settings → General → Obsidian vault) over a new backend endpoint set:
      - `GET /api/obsidian/tree` — directory tree (depth-limited, excludes
        `.obsidian/`, `node_modules/`).
      - `GET /api/obsidian/file?path=...` — read a single markdown file.
      - `PUT /api/obsidian/file?path=...` — write back (body: new content).
      The UI is a two-pane layout: file tree on the left (collapsible on
      mobile), markdown rendered on the right with an Edit toggle that swaps
      in a plain textarea. Save writes back via PUT. No rich editor needed —
      the goal is read access and light edits from the phone without leaving
      Quarterdeck. Path is validated against the vault root (no directory
      traversal). Write endpoint is POST-gated behind the existing auth token.

---

## 13. Folder scripts — mechanical procedures without an LLM

Some project operations are deterministic and need no agent: `git push`, `npm run build`, `pytest`, `docker compose up`, deploy scripts. Running these through kiro-cli wastes context and adds latency. Quarterdeck should let you define and run them directly, one click from the project's card or detail panel.

**Core idea.** A script is a named shell command bound to a folder (or collection). It runs in a tmux pane (visible output, killable), not through kiro-cli. The LLM is not involved.

**Design constraints:**
- Scripts are per-folder, stored in `~/.osa-kiro/scripts/<cwd-hash>.json`. The cwd hash avoids filesystem path issues.
- Each script has: `name`, `command`, `cwd`, optional `description`, optional `confirm` flag (ask before running).
- Runs in a dedicated short-lived tmux session named `deck-script-<uuid>`. Output is capturable by the backend.
- A script's exit code is reported back to the UI (green/red badge).
- Scripts are not sessions — they do not appear in the session grid or archive.

**Work:**

- [ ] **Script store.** `GET/POST/DELETE /api/scripts?cwd=...`. Returns scripts bound to a cwd or all scripts. Backed by `~/.osa-kiro/scripts/`.
- [ ] **Run endpoint.** `POST /api/scripts/{id}/run`. Spawns a tmux session, streams output via `/api/scripts/{id}/output` (tail). Returns `{ok, run_id, tmux_session}`.
- [ ] **Kill endpoint.** `DELETE /api/scripts/{id}/run` to cancel a running script.
- [ ] **Script editor in Settings.** Add/edit/delete scripts per folder. Name, command, confirm-before-run toggle.
- [ ] **Script buttons in the toolbar.** When `view === 'active'` and the selected session's cwd has scripts, show them as chips in the filter/action bar. One click runs; confirm flag shows a prompt first.
- [ ] **Script output panel.** A lightweight pane (reuse the pane-view component) showing the last N lines of a running or finished script. Exit status badge.
- [ ] **Import from Makefile / package.json.** Auto-detect common script sources in the project folder and offer to import them as Quarterdeck scripts. `make` targets from `Makefile`, `scripts` block from `package.json`.
- [ ] **Phone support.** Script chips visible on mobile detail view. Confirm prompt before run (always, on mobile — fat-finger guard).

**What this is not.** This is not a task queue or a build system. It is a one-click shortcut for things you run manually anyway. No scheduling, no dependencies, no retry logic. Those belong in the agent or in a real CI tool.


---

## 14. Per-project secrets injection

Named secrets bound to a folder. Injected as environment variables into sessions
started in that folder. Agent-written code can use them; the agent never sees the
values in context.

**Protection model:**
- Agent writes `os.environ.get("DATABASE_URL")` → works, value never in context
- Agent tries `echo $DATABASE_URL` → deny pattern auto-blocks it
- Agent tries to read `~/.osa-kiro/secrets/` → fs_read deny pattern blocks it
- Value never typed into the composer, never appears in the JSONL transcript

**Storage:** `~/.osa-kiro/secrets/<cwd-hash>.json`, values encrypted with a
key stored in the macOS keychain under `com.vidanov.quarterdeck.secrets`.
Falls back to file-based key in `~/.osa-kiro/secrets.key` in headless/CI.

**Work:**

- [x] `backend/secrets.py` — load, save, encrypt/decrypt per cwd. CRUD: list (names only), add, remove, get-for-injection (decrypted, internal only).
- [x] `/api/secrets` CRUD endpoints — GET (names + masked values), POST (add), DELETE (remove by name). Value never returned after creation.
- [x] Inject at spawn — `tmux.spawn()` calls `secrets.get_env(cwd)` and passes each secret as `-e KEY=VALUE` to `tmux new-session`.
- [x] Auto-deny on spawn — when secrets exist for a cwd, ensure deny patterns block `fs_read` on `~/.osa-kiro/secrets/` and `execute_bash` patterns that would print secret names (`echo \$KEY`, `printenv KEY`, `env | grep KEY`).
- [ ] Settings UI — per-project secrets panel: list secrets by name (masked), add new (name + value, value cleared after save), delete. Accessible from the session detail panel and from Settings when a project is selected.
- [ ] Audit — log which secret names were injected per session (names only, never values). Visible in the audit trail.
- [ ] Rotation hint — flag secrets older than 90 days in the UI.

