# Quarterdeck — Roadmap

Open work only. Completed items live in [CHANGELOG.md](CHANGELOG.md).
Ordering within a section is deliberate — top item first.

State of record: [HANDOVER-pty-api.md](HANDOVER-pty-api.md).
Design rationale: [ARCHITECTURE-pty-api.md](ARCHITECTURE-pty-api.md).

---

## 3. Security

- [ ] **Reconsider the loopback bypass.** Gate non-GUI endpoints behind the token
      even on loopback, with pywebview holding a token of its own. Required
      before `tailscale serve` can be safe. (Partially done 2026-08-15 — loopback
      now requires X-Local-Token; verify all edge cases are covered.)

---

## 7. Views and layout

### 7a. New views

- [ ] **Board by collection** — columns per collection.

### 7b. Structural work

- [ ] **Shared attention path.** Approval interrupts must be identical across
      views, not reimplemented per view.

### 7d. Agent-generated summaries

- [ ] Bound the input (tail + pane, not whole conversation).
- [ ] Show the cost and provide an off switch.
- [ ] Distinguish description from recommendation.

---

## 8. Carried over from handover

- [ ] Handoff for **iTerm2 and Ghostty** untested.
- [ ] `foreign` session status for agents without Deck's hooks — still on 10s
      freshness heuristic.
- [ ] `/api/projects` cache is in-process; first call after restart costs ~37s.
- [ ] `/api/upload` never implemented. Screenshot pickup may remove the need.
- [ ] Unused frontend leftovers: `handleRestore`, `showRecent`, `lastFocusTime`.
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

Sequencing: after sections 7b's data-layer extraction. Claude Code is the obvious second adapter.

---

## 10. Ideas from KiroCrew

### 10a. Time-limited safety override

- [ ] Per-session trust TTL with expiry re-enabling gating.
- [ ] Settings default for trust duration.
- [ ] Visual countdown on card/detail.
- [ ] Audit: trust-grant, trust-expire, trust-renew.

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

- [ ] Timeout awareness for stuck sub-agents.

### 10h. Team sharing — skills, agents, sessions

**Skills:**

- [ ] **Skill browser in Quarterdeck.** List installed skills with description,
      trigger, last-modified.
- [ ] **Shared skill repo convention.** Git repo per team, drift detection.
- [ ] **Install/update from shared.** Pull a skill from the team repo into local.
- [ ] **Publish local → shared.** Branch + PR flow.

**Agents:**

- [ ] **Agent config browser.** Show installed agents, hooks, model/effort defaults.
- [ ] **Exportable agent bundles.** Agent config + associated skills as a portable directory.
- [ ] **Team agent repo.** Same shape as skills.

**Sessions:**

- [ ] **Session export as markdown.** Readable transcript with metadata header.
      Shareable, not resumable.
- [ ] **Explore repo-local session storage.** Feasibility study: `--session-dir` flag.

---

## 12. kiro-cli V3 / ACP message format support

### 12g. Hooks

- [ ] V3's `pending_interaction` / `interaction_resolved` entries are the ACP
      equivalent of the `preToolUse` hook. Evaluate whether the existing
      file-based approval gate can be replaced or supplemented by reading these
      entries directly.

---

## 13. Folder scripts — mechanical procedures without an LLM

Shell commands bound to a folder, run in a tmux pane without involving kiro-cli.

- [ ] **Script store.** `GET/POST/DELETE /api/scripts?cwd=...`. Backed by `~/.osa-kiro/scripts/`.
- [ ] **Run endpoint.** `POST /api/scripts/{id}/run`. Streams output via `/api/scripts/{id}/output`.
- [ ] **Kill endpoint.** `DELETE /api/scripts/{id}/run`.
- [ ] **Script editor in Settings.** Add/edit/delete scripts per folder.
- [ ] **Script buttons in toolbar.** Chips for the selected session's cwd scripts.
- [ ] **Script output panel.** Reuse pane-view component. Exit status badge.
- [ ] **Import from Makefile / package.json.** Auto-detect and offer to import.
- [ ] **Phone support.** Always confirm before run on mobile.

---

## 14. Per-project secrets injection

Named secrets bound to a folder, injected as env vars into sessions. Agent-written
code can use them; values never appear in context or transcript.

Partially shipped: backend CRUD (`backend/secrets.py`), API endpoints, inject-at-spawn,
auto-deny patterns.

- [ ] **Settings UI** — per-project secrets panel: list (masked), add, delete.
      Accessible from detail panel and Settings.
- [ ] **Audit** — log which secret names (not values) were injected per session.
- [ ] **Rotation hint** — flag secrets older than 90 days in the UI.

---

## 15. kiro-cli 2.19.2 — improvements to adopt

### 15a. stream-json event source for session status

`--output-format stream-json` emits real events: tool-start, tool-end,
awaiting-approval, turn-complete. Candidate replacement for JSONL tail heuristics.

- [ ] **Prototype:** spawn one session with `--output-format stream-json`, map
      event types to Quarterdeck status values.
- [ ] **Adapter hook:** optional `stdout_events` path in `tmux_manager.py`.
- [ ] **Status accuracy check:** compare latency and correctness before/after.
- [ ] **Section 9 connection:** document as first step toward the adapter interface.

### 15b. Trust-all inheritance for subagents

- [ ] **Document in gating UI:** note that subagents inherit trust state.
- [ ] **Test:** gated parent spawns subagent; confirm Quarterdeck queue receives
      tool calls from both.

### 15c. Hook matcher warnings

- [ ] **Surface in Quarterdeck:** when pane output contains a hook matcher warning,
      show an amber banner in the detail panel.

### 15d. Interrupted tool call explanations

- [ ] **Verify DetailPanel renders it.** If blank: add "interrupted" fallback label
      in the tool cluster when result content is empty.

---

## 16. Patterns from turbo-spec

### 16a. Named quality gates with retry/reroute logic

- [ ] **Gate types per session rule file.** Each rule entry carries an `action`
      field: `auto_allow`, `auto_deny`, `human_required`, `timed_out`.
- [ ] **Retry/reroute on gate outcome.** Deny → reroute to fallback task.
- [ ] **Gate verdict in audit trail.** Record rule name, not just tool name.

### 16b. Blueprint YAML for multi-stage dispatch

- [ ] **Blueprint format.** YAML: `stages: [{task, cwd, depends_on, gate}]`.
      Stored in `~/.osa-kiro/blueprints/`.
- [ ] **Blueprint picker in launcher.**
- [ ] **Stage handoff.** Auto-dispatch next stage with previous output as context.

### 16c. Waterfall deny chain

- [ ] **Waterfall deny chain.** Replace flat pattern list in `deny.py` with a
      chain of policy handlers (~20 lines). Cordis-style semantics.
- [ ] **Script gates.** Allow a deny pattern entry to specify a shell script
      instead of a regex. Receives tool input on stdin, exits 0 (allow) / non-zero (deny).

### 16d. Local knowledge MCP server

- [ ] **Local knowledge MCP server.** SQLite-backed MCP server built at dispatch
      from `~/.kiro/steering/` and project docs.
- [ ] **Per-session knowledge scope.** Project docs override global steering.
- [ ] **Settings UI.** Configure watched directories per project.

### 16e. ADR discipline

- [ ] **Lightweight spec convention.** For auth/approval/storage changes: a spec
      entry in `docs/specs/` before implementation.
- [ ] **ADR discipline.** One file per architectural decision in `docs/adr/`.

---

## 17. Semantic checkout

### 17a. Content-aware archive search (Phase 1)

- [ ] **Session content index.** `backend/session_index.py` — SQLite FTS5 over
      first 5 user turns. Incremental rebuild on mtime check.
- [ ] **Extend archive search.** `GET /api/archive?q=...&content=true` falls
      through to FTS5 when title+cwd returns zero results.
- [ ] **Concierge intent-aware dispatch.** Zero title matches → auto-retry
      against content index, show top 3 with excerpt.
- [ ] **V1/V2/V3 reader parity.** Index must cover all three session formats.

### 17b. Chapter segmentation and semantic checkout (Phase 2)

Depends on 17a.

- [ ] **Chapter segmenter.** Segment sessions into chapters on user-turn topic
      shift. Index in `~/.osa-kiro/chapter-index.db`.
- [ ] **Chapter index is a read model.** Rebuildable from JSONL by replay.
- [ ] **Hybrid retrieval.** BM25 + vector over chapter titles and first user turn.
- [ ] **Restore action.** `POST /api/sessions/{id}/restore-chapter` — assembled
      context dispatched as a new session.
- [ ] **Provenance envelope in audit trail.**
- [ ] **Chapter browser in detail panel.** Lists chapter titles (verbatim user
      questions), date, turn range. Click to restore.

---

## 18. Library tab (replaces Collections)

Two-pane view: folder tree (left), sessions + templates (right). Replaces the
Collections tab. Design is settled; build not started.

- [ ] **Library tab skeleton** — two-pane layout, folders in localStorage.
- [ ] **Folder persistence backend** — `GET/POST/PATCH/DELETE /api/library/folders`.
- [ ] **Templates** — `GET/POST/DELETE /api/templates`, capture from session,
      spawn via launcher.
- [ ] **Duplicate** — `POST /api/sessions/{id}/duplicate`, surface in card and
      detail panel.
- [ ] **Migration** — convert collections/snapshots on first Library open.

Open questions before build:
- Sessions movable between folders, or only addable to multiple?
- Auto-add to folder when session starts in a pinned cwd?
- Template versioning: update in place or new capture each time?

---

## 19. UX patterns from Omarchy

### 19a. Unified ⌘K surface

- [ ] **Unified ⌘K surface.** One box: find sessions, search archive, dispatch,
      run Concierge queries, open Library folders.
- [ ] **Intent detection.** Query matches session → show it. No match → offer
      dispatch. No mode switching.
- [ ] **Mobile ⌘K.** Single tap-target in header; session cards as result list.

### 19b. Collector/display split convention

- [ ] **Collector/display split convention.** Any live-data widget reads from
      `~/.osa-kiro/state/`. A background collector writes that file.
- [ ] **File-watching for live updates.** Backend watches own state files, pushes
      via SSE/WebSocket. Frontend subscribes once.

### 19c. Direct manipulation

- [ ] **Drag-to-assign folders in Library.**
- [ ] **Drag-to-reorder chips** in composer strip and chips editor.
- [ ] **Settings panel audit.** After Library and 19a: move deny patterns, chips,
      and per-project secrets closer to where they're used.

---

## Pending UX

- [ ] **Project automation — Procedures panel.** Per-project named shell commands
      runnable without a chat session. `.quarterdeck/procedures.json`. Confirm
      step for destructive commands. `GET/POST /api/projects/{cwd}/procedures/run`.

- [ ] **Restore + Queue button on mobile.** `.queue-btn` hidden on ≤700 px
      (App.css ~line 3636). Restore with icon-only, 44 px tap target.

- [ ] **Managed filter on mobile.** `.control-filter-btn` hidden on ≤700 px
      (~line 3624). Restore at minimum a compact All | Managed strip.

- [ ] **Obsidian file browser.** New tab: tree + read/edit markdown.
      `GET /api/obsidian/tree`, `GET/PUT /api/obsidian/file`. Two-pane layout,
      Edit toggle swaps in a textarea. Path validated against vault root.
