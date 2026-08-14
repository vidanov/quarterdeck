# Crew Adapter — Q1 Probe Results

Probed: 2026-08-13. KiroCrew version: running at `~/.kiro/crew/`.

## What `~/.kiro/crew/` exposes

```
~/.kiro/crew/
  session_map.json        — active/recent crew sessions (see schema below)
  sessions/               — per-conversation JSONL files
  members/default/        — activity.jsonl per member (default agent)
  members/macos/          — activity.jsonl per member (macos agent)
  subagents/              — (empty in probe)
  skills/                 — crew skill registry
  config.json             — crew configuration
  audit.log               — security/audit log
  memory.db               — SQLite memory store
```

## `session_map.json` schema

```json
{
  "dashboard:chat-10-1786390169": {
    "sid": "a6e19fec-...",        // kiro-cli session UUID — cross-references ~/.kiro/sessions/cli/
    "slack_thread_ts": null,
    "slack_channel_id": null,
    "provider": "acp",
    "cwd": "/Users/<username>/.kiro/crew/workspace"
  },
  "meetings-note-taker-adhoc-2026-08-10T14-58-10-344Z": {
    "sid": "cb5f08e9-...",
    "provider": "acp",
    "cwd": "/Users/.../kirocrew-workspace/meetings-note-taker-adhoc-..."
  }
}
```

**Key format:** `<workspace_prefix>:<session_type>-<seq>-<unix_ts>` or `<agent_name>-adhoc-<ISO8601>`

## Q1 answer: parent-child relationship

**NOT directly stored.** `session_map.json` has no `parent_id` or `group_id` field.

Parent-child can be **inferred** from the key naming pattern:
- `dashboard:chat-1-...`, `dashboard:chat-2-...` etc. share the `dashboard` workspace prefix → same group
- `meetings-note-taker-adhoc-...`, `meetings-sketch-artist-adhoc-...`, `meetings-task-extractor-adhoc-...` with identical ISO8601 timestamps → spawned together as siblings

The colon-separated prefix (`dashboard`, `meetings`) is the workspace/parent context. Sessions sharing a prefix and overlapping in time are siblings.

## Crew session JSONL format

Different from kiro-cli JSONL. Located at `~/.kiro/crew/sessions/<key>.jsonl`.

```
Line 0: {_type: "metadata", created_at, closed, agent, model, title, ...}
Line 1+: {role: "user"|"assistant"|"tool", content, ts, source_thread, source_user, meta}
```

No `version`/`kind`/`data` envelope. No `Prompt`/`AssistantMessage`/`ToolResults` kinds. Not parseable by Quarterdeck's JSONL reader without an adapter.

## Implications for Task 1 (ownership adapter)

**Grouping is possible** via prefix parsing: split `crew_session_key` on `:` to get workspace prefix. Sessions with the same prefix spawned within a short window belong to one group.

**Grouping is not authoritative.** The `dashboard` workspace can have many unrelated chat sessions. Time proximity + matching prefix is the best heuristic available.

**Recommended approach:** for Crew sessions, infer group from `(workspace_prefix, spawned_within_60s)`. Show group cards for clusters of ≥2. Single-session groups show normally as `crew` cards.

**No `parent_id` in kiro-cli session files either.** Crew-spawned sessions appear in `~/.kiro/sessions/cli/` as normal sessions; only the `session_map.json` cross-reference ties them to Crew.

## Cross-reference path

To link a Quarterdeck session to its Crew session:
1. Read `session_map.json`
2. Find the entry where `sid == quarterdeck_session_id`
3. The key gives the crew conversation name and workspace context
4. Crew JSONL at `~/.kiro/crew/sessions/<key>.jsonl` has the full conversation

This is read-only. Quarterdeck never writes to `~/.kiro/crew/`.
