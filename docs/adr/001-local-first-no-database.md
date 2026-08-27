# ADR-001: Local-first state with no database

## Status
Accepted

## Context
Quarterdeck monitors Kiro CLI sessions and needs to store settings, managed
session metadata, approval gates, and audit logs. The options were SQLite,
an embedded key-value store, or plain JSON/JSONL files on disk.

## Decision
All state lives under `~/.osa-kiro/` as JSON files (settings, managed sessions,
collections) and JSONL files (audit logs). No database.

## Rationale
- The data volume is small (hundreds of sessions, not millions of rows)
- Atomic writes via tmp-and-rename are sufficient for single-user access
- No migration tooling needed; files are human-readable and git-diffable
- Removes a build dependency and a crash surface (SQLite locking, WAL, schema)

## Consequences
- No query language; lookup is scan-and-filter in Python
- Concurrent writes from multiple processes need file-level coordination
- If data volume grows past ~10k sessions, search will need an index (FTS5 added for this)

## Verification
```bash
# State dir exists and has expected structure
ls ~/.osa-kiro/managed.json ~/.osa-kiro/settings.json
# No database files present
! ls ~/.osa-kiro/*.db ~/.osa-kiro/*.sqlite 2>/dev/null
```
