"""V3 session format support for Quarterdeck.

kiro-cli 2.16+ writes sessions to:
  ~/.kiro/sessions/<workspace-hash>/sess_<uuid>/
    session.json    — metadata
    messages.jsonl  — messages with {id, timestamp, payload: {type, ...}}

This module provides format detection, path resolution, metadata reading,
status detection, and transcript parsing for V3 sessions, with the same
return shapes as the V1 equivalents in api.py so callers are transparent.
"""
import json
import re
from pathlib import Path

KIRO_SESSIONS_ROOT = Path.home() / ".kiro" / "sessions"

# Workspace-hash dirs: 16 hex characters (not 'cli' or 'cli-archived-zombies')
_WORKSPACE_HASH_RE = re.compile(r"^[0-9a-f]{16}$")

# V3 payload types → role mapping
_V3_ROLE = {
    "user": "user",
    "assistant": "assistant",
    "tool_call": "tool",
    "tool_result": "tool",
    "pending_interaction": "system",
    "interaction_resolved": "system",
}


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def workspace_dirs() -> list[Path]:
    """All workspace-hash directories under ~/.kiro/sessions/."""
    if not KIRO_SESSIONS_ROOT.exists():
        return []
    return [
        d for d in KIRO_SESSIONS_ROOT.iterdir()
        if d.is_dir() and _WORKSPACE_HASH_RE.match(d.name)
    ]


def is_v3_session(session_id: str) -> bool:
    """True if session_id follows the V3 'sess_<uuid>' prefix."""
    return session_id.startswith("sess_")


def session_dir(session_id: str) -> Path | None:
    """Return the directory containing a V3 session, or None if not found."""
    if not is_v3_session(session_id):
        return None
    for ws in workspace_dirs():
        d = ws / session_id
        if (d / "session.json").exists():
            return d
    return None


def messages_jsonl(session_id: str) -> Path | None:
    d = session_dir(session_id)
    return (d / "messages.jsonl") if d else None


def all_v3_sessions() -> list[tuple[str, Path]]:
    """Return (session_id, session_dir) for every V3 session on disk."""
    result = []
    for ws in workspace_dirs():
        for d in ws.iterdir():
            if d.is_dir() and d.name.startswith("sess_") and (d / "session.json").exists():
                result.append((d.name, d))
    return result


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def read_metadata(session_id: str) -> dict | None:
    """Read V3 session.json and return a normalised metadata dict."""
    d = session_dir(session_id)
    if not d:
        return None
    try:
        raw = json.loads((d / "session.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None

    cwd = ""
    paths = raw.get("workspacePaths") or []
    if paths:
        cwd = paths[0]

    return {
        "session_id": session_id,
        "title": raw.get("title") or "",
        "cwd": cwd,
        "model": raw.get("modelId") or "",
        "status": raw.get("status") or "",
        "created_at": raw.get("createdAt") or "",
        "updated_at": raw.get("lastModifiedAt") or "",
        "agent_mode": raw.get("agentMode") or "",
        "format": "v3",
        # Keep raw for callers that need it
        "_raw": raw,
    }


# ---------------------------------------------------------------------------
# Status detection
# ---------------------------------------------------------------------------

def detect_status(session_id: str) -> str:
    """Infer session status from V3 messages.jsonl tail."""
    path = messages_jsonl(session_id)
    if not path or not path.exists():
        # Fall back to session.json status field
        meta = read_metadata(session_id)
        s = (meta or {}).get("status", "")
        return _map_v3_status(s)

    # Read last 20 lines
    try:
        lines = path.read_text().splitlines()[-20:]
    except OSError:
        return "unknown"

    # Walk backwards looking for meaningful payload types
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        ptype = (entry.get("payload") or {}).get("type", "")
        if ptype == "turn_end":
            return "idle"
        if ptype == "turn_start":
            return "thinking"
        if ptype == "tool_call":
            return "running"
        if ptype == "pending_interaction":
            return "awaiting-approval"
        if ptype == "user":
            return "thinking"  # user message with no turn_end yet
        if ptype in ("usage_summary", "session_event", "session_pause"):
            return "idle"

    return "idle"


def _map_v3_status(s: str) -> str:
    return {
        "running": "thinking",
        "idle": "idle",
        "paused": "idle",
        "failed": "error",
        "completed": "done",
    }.get(s, "idle")


# ---------------------------------------------------------------------------
# Last output / last message
# ---------------------------------------------------------------------------

def get_last_output(session_id: str) -> str:
    """Return the last assistant text from a V3 session (up to 1500 chars)."""
    path = messages_jsonl(session_id)
    if not path or not path.exists():
        return ""
    try:
        lines = path.read_text().splitlines()[-60:]
    except OSError:
        return ""

    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        p = entry.get("payload") or {}
        if p.get("type") == "assistant" and p.get("content"):
            return str(p["content"])[:1500]
    return ""


def last_message(session_id: str) -> str:
    """Return last user or assistant message text for card preview."""
    path = messages_jsonl(session_id)
    if not path or not path.exists():
        return ""
    try:
        lines = path.read_text().splitlines()[-40:]
    except OSError:
        return ""

    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        p = entry.get("payload") or {}
        ptype = p.get("type", "")
        if ptype in ("assistant", "user") and p.get("content"):
            return str(p["content"])[:500]
    return ""


def context_pct(session_id: str) -> str:
    """Return context usage percentage from V3 session_metadata entries."""
    path = messages_jsonl(session_id)
    if not path or not path.exists():
        return ""
    try:
        lines = path.read_text().splitlines()[-30:]
    except OSError:
        return ""

    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        p = entry.get("payload") or {}
        if p.get("type") == "session_metadata" and p.get("key") == "contextUsage":
            pct = (p.get("value") or {}).get("usagePercentage")
            if pct is not None:
                return f"{round(float(pct))}%"
    return ""


# ---------------------------------------------------------------------------
# Transcript reading
# ---------------------------------------------------------------------------

def read_transcript(session_id: str, after: int = -1, limit: int = 200) -> dict:
    """Return transcript messages in the same shape as V1 read_transcript."""
    path = messages_jsonl(session_id)
    if not path or not path.exists():
        return {"messages": [], "has_more": False}

    try:
        raw_lines = path.read_text().splitlines()
    except OSError:
        return {"messages": [], "has_more": False}

    messages = []
    seq = 0
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue

        msg = _transcript_entry(seq, entry)
        if msg:
            if msg["seq"] > after:
                messages.append(msg)
            seq += 1

    # Apply limit from the end (most recent messages)
    if len(messages) > limit:
        return {"messages": messages[-limit:], "has_more": True}
    return {"messages": messages, "has_more": False}


def _transcript_entry(seq: int, entry: dict) -> dict | None:
    """Convert a V3 messages.jsonl entry to the standard transcript shape."""
    p = entry.get("payload") or {}
    ptype = p.get("type", "")

    if ptype == "user":
        return {
            "seq": seq,
            "role": "user",
            "text": str(p.get("content") or ""),
            "ts": entry.get("timestamp", ""),
        }

    if ptype == "assistant":
        return {
            "seq": seq,
            "role": "assistant",
            "text": str(p.get("content") or ""),
            "ts": entry.get("timestamp", ""),
        }

    if ptype == "tool_call":
        return {
            "seq": seq,
            "role": "tool",
            "type": "tool_call",
            "text": f"Tool: {p.get('toolName', '?')}",
            "tool_name": p.get("toolName", ""),
            "tool_input": p.get("args") or {},
            "ts": entry.get("timestamp", ""),
        }

    if ptype == "tool_result":
        content = p.get("content") or ""
        # content may be JSON string
        try:
            parsed = json.loads(content) if isinstance(content, str) else content
            if isinstance(parsed, list):
                text = " ".join(
                    item.get("text", "") for item in parsed
                    if isinstance(item, dict)
                )[:500]
            else:
                text = str(parsed)[:500]
        except (json.JSONDecodeError, TypeError):
            text = str(content)[:500]
        return {
            "seq": seq,
            "role": "tool",
            "type": "tool_result",
            "text": text,
            "ts": entry.get("timestamp", ""),
        }

    if ptype == "pending_interaction":
        return {
            "seq": seq,
            "role": "system",
            "type": "approval_request",
            "text": f"[Approval requested: {p.get('interactionType', 'tool')}]",
            "ts": entry.get("timestamp", ""),
        }

    # Skip turn_start, turn_end, session_metadata, usage_summary, session_event
    return None
