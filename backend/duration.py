"""Per-session task records and duration estimation for Quarterdeck.

Each completed session writes one JSON record to ~/.osa-kiro/turns/<session-id>.json.
The estimate() function aggregates these into p50/p90 ranges by project+type_tag.

Design constraints (from TASK7-DURATION.md):
- Never raise: write_record() swallows all failures, mirrors audit.py's contract.
- Never a point estimate: always p50–p90 or nothing.
- n < 6 = no display; an estimate from 2 observations is worse than none.
- Project-scoped only: do not merge data across projects.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import SESSIONS_DIR, CORRECTIONS_DIR, TURNS_DIR, STATE_DIR

# Closed vocabulary for type tags.
VALID_TAGS = {"coding", "research", "writing", "infra", "review", "unknown"}

# Keyword table for classify_type_tag — first match wins.
_TAG_KEYWORDS: list[tuple[str, list[str]]] = [
    ("coding",   ["implement", "fix", "add feature", "refactor", "bug", "function",
                  "class", "test"]),
    ("research", ["research", "find out", "look into", "explain", "what is",
                  "how does"]),
    ("writing",  ["write", "draft", "article", "post", "document", "readme", "spec"]),
    ("infra",    ["deploy", "terraform", "cdk", "aws", "stack", "server",
                  "pipeline"]),
    ("review",   ["review", "check", "audit", "verify", "analyse", "analyze"]),
]

MIN_SAMPLES = 6   # fewer than this → no estimate displayed


# ---------------------------------------------------------------------------
# classify_type_tag
# ---------------------------------------------------------------------------

def classify_type_tag(text: str) -> str:
    """Classify a task string into the closed type_tag vocabulary.

    Case-insensitive, first-match wins. Returns 'unknown' if nothing matches.
    """
    if not text:
        return "unknown"
    lower = text.lower()
    for tag, keywords in _TAG_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return tag
    return "unknown"


# ---------------------------------------------------------------------------
# write_record
# ---------------------------------------------------------------------------

def write_record(session_id: str) -> bool:
    """Build and persist the task record for a completed session.

    Reads from: session .json (metadata), session .jsonl (tool calls),
    corrections store (oracle_attempts).
    Writes to: ~/.osa-kiro/turns/<session-id>.json

    Returns True when the record was written, False on any failure.
    Silent on failure — mirrors audit.py's never-raise contract.
    """
    try:
        return _write_record_inner(session_id)
    except Exception:
        return False


def _write_record_inner(session_id: str) -> bool:
    # ---- read session metadata (.json) ------------------------------------
    json_path = SESSIONS_DIR / f"{session_id}.json"
    meta: dict = {}
    if json_path.exists():
        try:
            meta = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    created_at_str: str = meta.get("created_at", "") or ""
    updated_at_str: str = meta.get("updated_at", "") or ""
    wall_clock_min = _wall_clock(created_at_str, updated_at_str)

    # ---- derive project name from cwd ------------------------------------
    cwd: str = meta.get("cwd", "") or ""
    project = _project_from_cwd(cwd)

    # ---- read first user message for type tag classification -------------
    first_prompt = _first_user_message(session_id)
    type_tag = classify_type_tag(first_prompt)

    # ---- model / effort --------------------------------------------------
    model = ""
    effort = ""
    try:
        rts = meta.get("session_state", {}).get("rts_model_state", {})
        model = rts.get("model_info", {}).get("model_id", "") or ""
        effort = (
            rts.get("additional_fields", {})
               .get("overrides", {})
               .get("output_config", {})
               .get("effort", "")
        ) or ""
    except Exception:
        pass

    # ---- count tool calls in .jsonl -------------------------------------
    tool_calls_total, tool_calls_distinct, messages_assistant, messages_user = (
        _count_jsonl(session_id)
    )

    # ---- oracle_attempts: confirmed corrections for this session ---------
    oracle_attempts = _count_confirmed_corrections(session_id)

    # ---- has_task_string ------------------------------------------------
    has_task_string = bool(first_prompt)

    # ---- gating_on ------------------------------------------------------
    from .config import GATES_DIR
    gating_on = (GATES_DIR / session_id).exists()

    # ---- stack_auto_advancing -------------------------------------------
    stack_auto_advancing = False
    try:
        from .config import SETTINGS_FILE
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        stack_auto_advancing = bool(settings.get(f"stack-auto:{session_id}"))
    except Exception:
        pass

    # ---- final_verdict --------------------------------------------------
    final_verdict = _derive_verdict(session_id, meta)

    # ---- build record ---------------------------------------------------
    record: dict = {
        "session_id": session_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "features": {
            "model": model,
            "effort": effort,
            "has_task_string": has_task_string,
            "cwd": cwd,
            "project": project,
            "gating_on": gating_on,
            "stack_auto_advancing": stack_auto_advancing,
            "type_tag": type_tag,
        },
        "outcome": {
            "wall_clock_min": round(wall_clock_min, 1) if wall_clock_min is not None else None,
            "tool_calls_total": tool_calls_total,
            "tool_calls_distinct": tool_calls_distinct,
            "oracle_attempts": oracle_attempts,
            "final_verdict": final_verdict,
            "messages_assistant": messages_assistant,
            "messages_user": messages_user,
        },
    }

    # ---- write ----------------------------------------------------------
    try:
        TURNS_DIR.mkdir(parents=True, exist_ok=True)
        dest = TURNS_DIR / f"{session_id}.json"
        # If a record already exists, preserve a user-corrected type_tag.
        if dest.exists():
            try:
                existing = json.loads(dest.read_text(encoding="utf-8"))
                existing_tag = (existing.get("features") or {}).get("type_tag", "")
                if existing_tag and existing_tag in VALID_TAGS and existing_tag != "unknown":
                    record["features"]["type_tag"] = existing_tag
            except Exception:
                pass
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, dest)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# read_records
# ---------------------------------------------------------------------------

def read_records(project: str = "", type_tag: str = "") -> list[dict]:
    """Return all task records, optionally filtered by project and/or type_tag.

    Reads ~/.osa-kiro/turns/*.json. Skips files that look like bare turn marks
    (not valid JSON objects with 'session_id').
    """
    if not TURNS_DIR.is_dir():
        return []
    results: list[dict] = []
    try:
        paths = sorted(TURNS_DIR.glob("*.json"))
    except OSError:
        return []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            continue
        if not isinstance(data, dict) or "session_id" not in data:
            continue
        features = data.get("features") or {}
        if project and features.get("project", "") != project:
            continue
        if type_tag and features.get("type_tag", "") != type_tag:
            continue
        results.append(data)
    return results


# ---------------------------------------------------------------------------
# estimate
# ---------------------------------------------------------------------------

def estimate(project: str, type_tag: str) -> dict:
    """Return calibrated duration estimate or a 'no data' sentinel.

    Returns:
        {display, p50_min, p90_min, n, calibrated} when n >= MIN_SAMPLES
        {display: None, reason: str}               when not enough data
    """
    records = read_records(project=project, type_tag=type_tag)
    # Only use records that have a valid wall_clock_min
    durations: list[float] = []
    for r in records:
        outcome = r.get("outcome") or {}
        wc = outcome.get("wall_clock_min")
        if isinstance(wc, (int, float)) and wc > 0:
            durations.append(float(wc))

    n = len(durations)
    if n < MIN_SAMPLES:
        return {"display": None, "reason": "too few samples", "n": n}

    durations_sorted = sorted(durations)
    p50_min = round(_percentile(durations_sorted, 50))
    p90_min = round(_percentile(durations_sorted, 90))

    # Calibration: look at records that carry a predicted_range (written at dispatch)
    # and compare against actual wall_clock_min.
    in_range_count = 0
    predicted_count = 0
    # Use trailing 20 records with predictions
    trailing = [r for r in records if (r.get("outcome") or {}).get("wall_clock_min") and
                r.get("predicted_range")][-20:]
    for r in trailing:
        pr = r.get("predicted_range") or {}
        lo = pr.get("p50_min")
        hi = pr.get("p90_min")
        actual = (r.get("outcome") or {}).get("wall_clock_min")
        if lo is not None and hi is not None and actual is not None:
            predicted_count += 1
            if lo <= actual <= hi:
                in_range_count += 1

    if predicted_count >= 1:
        in_range_fraction = in_range_count / predicted_count
        calibrated = in_range_fraction >= 0.90
    else:
        # No predictions yet — infrastructure is in place but not calibrated
        in_range_fraction = 0.0
        calibrated = False

    display = f"{p50_min}–{p90_min} min (n={n})" if calibrated else None
    if not calibrated and predicted_count == 0:
        # No predicted_range records yet — show data but mark uncalibrated
        display = f"{p50_min}–{p90_min} min (n={n})"

    return {
        "type_tag": type_tag,
        "project": project,
        "n": n,
        "p50_min": p50_min,
        "p90_min": p90_min,
        "display": display,
        "calibration": {
            "in_range_fraction": round(in_range_fraction, 3),
            "calibrated": calibrated,
            "predicted_count": predicted_count,
        },
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wall_clock(created_at: str, updated_at: str) -> Optional[float]:
    """Derive wall_clock_min from ISO timestamp strings."""
    try:
        t0 = datetime.fromisoformat(created_at.rstrip("Z").replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(updated_at.rstrip("Z").replace("Z", "+00:00"))
        return (t1 - t0).total_seconds() / 60.0
    except (ValueError, AttributeError):
        return None


def _project_from_cwd(cwd: str) -> str:
    """Extract a short project name from the cwd path."""
    if not cwd:
        return ""
    return Path(cwd).name


def _first_user_message(session_id: str) -> str:
    """Read the first user prompt from the session JSONL."""
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
    if not jsonl_path.exists():
        return ""
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    entry = json.loads(raw_line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(entry, dict):
                    continue
                # V1: {"type": "user", "message": {...}}
                if entry.get("type") == "user":
                    msg = entry.get("message") or {}
                    content = msg.get("content") or ""
                    if isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text = part.get("text", "")
                                if text:
                                    return text
                    elif isinstance(content, str) and content:
                        return content
    except OSError:
        pass
    return ""


def _count_jsonl(session_id: str) -> tuple[int, int, int, int]:
    """Count tool_calls_total, tool_calls_distinct, messages_assistant, messages_user."""
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
    tool_calls_total = 0
    tool_calls_distinct: set[str] = set()
    messages_assistant = 0
    messages_user = 0

    if not jsonl_path.exists():
        return 0, 0, 0, 0

    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    entry = json.loads(raw_line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(entry, dict):
                    continue
                etype = entry.get("type", "")
                if etype == "user":
                    messages_user += 1
                elif etype == "assistant":
                    messages_assistant += 1
                    # Count tool_use blocks within assistant messages
                    msg = entry.get("message") or {}
                    content = msg.get("content") or []
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                tool_calls_total += 1
                                tool_name = block.get("name", "")
                                # Deduplicate by (name, input hash) — use str of input
                                inp = block.get("input") or {}
                                key = f"{tool_name}:{json.dumps(inp, sort_keys=True)}"
                                tool_calls_distinct.add(key)
    except OSError:
        pass

    return tool_calls_total, len(tool_calls_distinct), messages_assistant, messages_user


def _count_confirmed_corrections(session_id: str) -> int:
    """Count confirmed corrections for a session from the corrections store."""
    if not CORRECTIONS_DIR.is_dir():
        return 0
    by_id: dict[str, dict] = {}
    try:
        for day_file in CORRECTIONS_DIR.glob("*.jsonl"):
            try:
                lines = day_file.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if rec.get("session_id") != session_id:
                    continue
                by_id[rec.get("id", "")] = rec
    except Exception:
        pass
    return sum(1 for r in by_id.values() if r.get("status") == "confirmed")


def _derive_verdict(session_id: str, meta: dict) -> str:
    """Derive final_verdict from session end state."""
    from .config import SESSIONS_DIR as SD
    lock_path = SD / f"{session_id}.lock"
    # No lock file → session ended (done or abandoned)
    if not lock_path.exists():
        return "done"
    # If lock exists but process is gone, also done
    try:
        import json as _json
        lock = _json.loads(lock_path.read_text(encoding="utf-8"))
        pid = lock.get("pid")
        if pid:
            import os as _os
            try:
                _os.kill(pid, 0)
                return "done"  # process alive at record time
            except ProcessLookupError:
                return "done"
            except PermissionError:
                return "done"
    except Exception:
        pass
    return "done"


def _percentile(sorted_vals: list[float], p: int) -> float:
    """Nearest-rank percentile from a sorted list."""
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    # Nearest-rank method
    rank = max(1, int((p / 100.0) * n + 0.5)) - 1
    rank = min(rank, n - 1)
    return sorted_vals[rank]


def update_type_tag(session_id: str, tag: str) -> bool:
    """Update the type_tag in an existing task record. Returns True on success."""
    if tag not in VALID_TAGS:
        return False
    dest = TURNS_DIR / f"{session_id}.json"
    if not dest.exists():
        return False
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
        if "features" not in data:
            data["features"] = {}
        data["features"]["type_tag"] = tag
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, dest)
        return True
    except (json.JSONDecodeError, OSError):
        return False


def get_record(session_id: str) -> Optional[dict]:
    """Return the task record for a session, or None if not yet recorded."""
    dest = TURNS_DIR / f"{session_id}.json"
    if not dest.exists():
        return None
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "session_id" in data:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None
