# osa-kiro Knowledge Base

## Session Start (automatic)

At the start of every session in this folder, update the project knowledge base
so semantic search reflects the latest code and docs:

```
knowledge update path=<repo-root>/backend name=osa-kiro
knowledge update path=<repo-root>/frontend/src name=osa-kiro
knowledge update path=<repo-root>/docs name=osa-kiro
knowledge update path=<repo-root>/tests name=osa-kiro
knowledge update path=<repo-root>/.kiro/steering name=osa-kiro
```

Replace `<repo-root>` with the absolute path to your clone of this repository.

Do this silently — no need to mention it to the user.

## Knowledge Base Details

- **Name**: osa-kiro (five entries, searched together)
- **backend** (ID: 74bf8f41): `backend/` — Python source, 42 items
- **frontend/src** (ID: 30e0664c): `frontend/src/` — React/JSX source, 25 items
- **docs** (ID: e3164056): `docs/` — CHANGELOG, ROADMAP, SPEC, ARCHITECTURE, 26 items
- **steering** (ID: f43c4e25): `.kiro/steering/` — project steering files, 8 items
- **tests** (ID: 76612cba): `tests/` — pytest suite, 24 items
- **Storage**: `.knowledge/` (gitignored)

Note: full-project indexing fails due to node_modules/venv size.
Index each source directory separately instead.

## When to Search

Before answering questions about:
- How a specific feature works (API routes, hooks, components)
- Where a piece of code lives
- What was changed recently (check CHANGELOG.md directly)
- Architecture decisions or design rationale
- Test coverage for a module

Search query: use the user's words verbatim.

## When to Update

In addition to session start, re-index after:
- Significant code changes (new files, refactors)
- New backend modules or frontend components added
- CHANGELOG or docs updates

Use the same five `knowledge update` commands from the Session Start block above.
