# Task 7: Duration Measurement — Implementation Handover

**Source spec:** `PHASE2-PLAN.md` § TASK 7  
**Depends on:** stats view + audit trail (both exist — this extends them)  
**Blocks:** Task 9 stall thresholds (needs per-type p90 before thresholds can be data-driven)  
**Status:** Ready to build

---

## What this is

Every threshold in the Phase 2 plan (stall detection, budget ceilings) is currently a hand-written guess. Task 7 makes those guesses replaceable by data the system already generates for free.

Three outputs:
1. A per-session task record written at session end (new JSONL store)
2. A backend endpoint returning calibrated p50/p90 ranges by type tag
3. A UI annotation showing the estimate on session cards and detail panels

---

## Data model

### Task record (`~/.osa-kiro/turns/<session-id>.json`)

Written once, at session end (triggered by `stop` hook or `/api/sessions/{id}/dismiss`).

```json
{
  "session_id": "...",
  "recorded_at": "2026-08-15T05:30:00Z",

  "features": {
    "model": "claude-sonnet-4.6",
    "effort": "max",
    "has_task_string": true,
    "cwd": "/Users/a.vidanov/Documents/PROJECTS/PERSONAL/osa-kiro",
    "project": "osa-kiro",
    "gating_on": false,
    "stack_auto_advancing": false,
    "type_tag": "coding"
  },

  "outcome": {
    "wall_clock_min": 14.3,
    "tool_calls_total": 47,
    "tool_calls_distinct": 12,
    "oracle_attempts": 0,
    "final_verdict": "done",
    "messages_assistant": 8,
    "messages_user": 5
  }
}
```

**Field notes:**

`type_tag` — a small closed vocabulary: `"coding"`, `"research"`, `"writing"`, `"infra"`, `"review"`, `"unknown"`. Derived automatically from the first user message using keyword heuristics (see below). User-correctable via the UI after the fact.

`wall_clock_min` — derived from `created_at` / `updated_at` in the session `.json` metadata. Same source the stats endpoint already uses.

`tool_calls_total` / `tool_calls_distinct` — counted from the session JSONL: total `toolUse` blocks vs. the count of unique `(tool_name, input_hash)` pairs. Identical back-to-back calls without a mutation between them are the loop signal (Phase 2 1.3).

`oracle_attempts` — count of correction records (`~/.osa-kiro/corrections/*.jsonl`) referencing this session. Zero for most sessions.

`final_verdict` — `"done"`, `"abandoned"`, `"stalled"`, `"compacted"`. Derived from session end state.

---

## Type tag heuristics

Auto-classify from the first user message text. Simple keyword scan, not a model call.

| Tag | Trigger words/patterns |
|---|---|
| `coding` | implement, fix, add feature, refactor, bug, function, class, test |
| `research` | research, find out, look into, explain, what is, how does |
| `writing` | write, draft, article, post, document, readme, spec |
| `infra` | deploy, terraform, cdk, aws, stack, server, pipeline |
| `review` | review, check, audit, verify, analyse |
| `unknown` | (fallback) |

Match is case-insensitive, first-match wins. If no match: `"unknown"`. The UI lets the user correct the tag on a session card — one click, dropdown. Write the correction back to the record file.

---

## Estimation algorithm

`GET /api/stats/duration?type_tag=coding&project=osa-kiro`

```json
{
  "type_tag": "coding",
  "project": "osa-kiro",
  "n": 12,
  "p50_min": 11,
  "p90_min": 28,
  "display": "10–28 min (n=12)",
  "calibration": {
    "in_range_fraction": 0.72,
    "calibrated": true
  }
}
```

**Rules:**

- `n < 6` → return `{"display": null, "reason": "too few samples"}`. Do not show a number.
- Report p50 to p90 as the range, never a point estimate. The 6.5x repair-loop multiplier (Phase 2 §1.3) makes a mean meaningless.
- `calibrated: false` when `in_range_fraction < 0.90` for the p90 band. Show no number until calibrated.
- Filter to `project` first, then `type_tag`. If project has < 6 samples, do not cross projects.
- Round to nearest minute. `"10–28 min (n=12)"` is the canonical display string.

**Calibration tracking:** each task record includes a `predicted_range` field written at dispatch time (if an estimate existed). After session end, compare actual `wall_clock_min` against that range. The `in_range_fraction` is computed over the trailing 20 records with predictions.

---

## New files

```
~/.osa-kiro/turns/                   # per-session task records
    <session-id>.json

backend/duration.py                  # new module
    write_record(session_id)         # called at session end
    read_records(project, type_tag)  # returns list of outcome dicts
    estimate(project, type_tag)      # returns display dict or null
    classify_type_tag(text)          # keyword heuristics
```

---

## Backend changes

### `backend/duration.py` (new file)

Four public functions:

```python
def write_record(session_id: str) -> bool:
    """Build and persist the task record for a completed session.
    Reads from: session .json (metadata), session .jsonl (tool calls),
    corrections store (oracle_attempts).
    Writes to: ~/.osa-kiro/turns/<session-id>.json
    Silent on failure — mirrors audit.py's never-raise contract.
    """

def read_records(project: str = "", type_tag: str = "") -> list[dict]:
    """Return all records, optionally filtered. Reads ~/.osa-kiro/turns/*.json."""

def estimate(project: str, type_tag: str) -> dict:
    """Return {display, p50_min, p90_min, n, calibrated} or {display: None, reason}.
    Applies the n >= 6 and calibration checks.
    """

def classify_type_tag(text: str) -> str:
    """Classify a task string into the closed type_tag vocabulary."""
```

### `backend/api.py` additions

```python
@app.get("/api/stats/duration")
def get_duration_stats(type_tag: str = "", project: str = ""):
    """Calibrated duration estimate for a type+project combination."""
    from . import duration
    return duration.estimate(project, type_tag)

@app.get("/api/sessions/{id}/duration")
def get_session_duration(id: str):
    """Return the task record for a session, or null if not yet recorded."""
    from . import duration
    rec = duration.read_records()  # filter by session_id
    ...

@app.post("/api/sessions/{id}/duration/type-tag")
def set_type_tag(id: str, payload: dict):
    """Let the user correct the auto-classified type tag."""
    tag = payload.get("tag", "").strip()
    if tag not in {"coding", "research", "writing", "infra", "review", "unknown"}:
        return {"error": "invalid tag"}
    ...
```

### Hook: write record at session end

`write_record()` is called in two places:

1. Inside `POST /api/sessions/{id}/dismiss` (already exists — fires when a session transitions to done/idle)
2. Inside the `stop` hook handler path in `api.py` (search for `stop` hook event processing)

This is additive — no existing behaviour changes.

---

## Frontend changes

### Session card annotation

Below the session title, when an estimate exists for the session's type+project:

```
est 10–28 min (n=12)
```

Grey, small text, same line as the path. Only rendered when `display` is non-null. Fetched alongside `/api/sessions` via a parallel `/api/stats/duration?type_tag=X&project=Y` call — one call per active type+project combination, cached for the session list lifetime.

### Type tag chip on cards

A small chip showing the auto-classified type (e.g. `coding`). Click opens a dropdown with the six values. Selection posts to `/api/sessions/{id}/duration/type-tag`. The chip is always visible even when no estimate exists — it's the input, not the output.

### Duration in detail panel

In the session detail panel's metadata row (where model, effort, cwd are shown), add:

```
14 min  •  47 calls (12 distinct)
```

Derived from the task record if it exists, or from live `created_at` vs. now for running sessions.

---

## What NOT to build

- No regression model. Median and p90 of historical data is the spec.
- No cross-project estimates. Project-scoped until transfer is demonstrated (Q7).
- No automatic type-tag inference from model calls. Keyword heuristics only.
- No UI for browsing the full `turns/` store. The stats view extension in Task 10 covers that later.

---

## Done when

1. At least one type+project combination has `n >= 6` records.
2. The estimate endpoint returns a range (not a point) with the display string.
3. The calibration report exists (even if only one session has a `predicted_range` to compare against — the infrastructure must be in place, not the full dataset).
4. A session card shows the tag chip and, when n >= 6, the estimate string.
5. User can correct a type tag from the card and the record is updated.

---

## Implementation order

1. `backend/duration.py` — write_record, read_records, classify_type_tag, estimate
2. Wire write_record into the dismiss/stop paths in api.py
3. `GET /api/stats/duration` and `GET /api/sessions/{id}/duration`
4. `POST /api/sessions/{id}/duration/type-tag`
5. Frontend: type tag chip + estimate string on cards
6. Frontend: duration in detail panel metadata row
7. Run tests, verify build fresh

---

## Key invariants for the implementer

- **Silent on failure.** `write_record()` must never raise. Same contract as `audit.append()`.
- **Never a point estimate.** Always p50–p90 or nothing.
- **n < 6 = no display.** An estimate from two observations is worse than none.
- **Project-scoped only.** Do not merge `osa-kiro` and `vptb` data.
- **Reads are additive.** `write_record()` writes under `~/.osa-kiro/turns/`. It does not touch session files.
- **Correction at one click.** The type tag dropdown must be reachable from the card without opening the detail panel.
