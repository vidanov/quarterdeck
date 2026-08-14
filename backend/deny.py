"""Denied command patterns for preToolUse auto-deny.

Patterns are stored in ~/.osa-kiro/deny-patterns.json as a list of objects:
  {"id": "uuid", "tool": "execute_bash", "pattern": "rm -rf /", "note": "..."}

`tool` defaults to "execute_bash" if omitted. The pattern is a Python regex
matched against a compact JSON representation of tool_input for that tool.
"""
import json
import re
import uuid
from pathlib import Path

from .config import STATE_DIR

DENY_FILE = STATE_DIR / "deny-patterns.json"

DEFAULT_PATTERNS = [
    {"id": "default-1", "tool": "execute_bash",
     "pattern": r"rm\s+-rf\s+/(?!\S)", "note": "rm -rf /"},
    {"id": "default-2", "tool": "execute_bash",
     "pattern": r"git\s+push\s+.*--force", "note": "force push"},
    {"id": "default-3", "tool": "execute_bash",
     "pattern": r"DROP\s+TABLE|TRUNCATE\s+TABLE", "note": "destructive SQL"},
    {"id": "default-4", "tool": "execute_bash",
     "pattern": r":(){ :\|:& };:", "note": "fork bomb"},
]


def _load() -> list[dict]:
    if not DENY_FILE.exists():
        return []
    try:
        data = json.loads(DENY_FILE.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(patterns: list[dict]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DENY_FILE.write_text(json.dumps(patterns, indent=2))


def list_patterns() -> list[dict]:
    return _load()


def add_pattern(tool: str, pattern: str, note: str = "") -> dict:
    patterns = _load()
    entry = {"id": str(uuid.uuid4())[:8], "tool": tool or "execute_bash",
             "pattern": pattern, "note": note}
    patterns.append(entry)
    _save(patterns)
    return entry


def remove_pattern(pattern_id: str) -> bool:
    patterns = _load()
    before = len(patterns)
    patterns = [p for p in patterns if p.get("id") != pattern_id]
    if len(patterns) == before:
        return False
    _save(patterns)
    return True


def matches(tool_name: str, tool_input: dict) -> tuple[bool, str]:
    """Return (matched, note) if any pattern matches this tool call."""
    patterns = _load()
    # Compact representation to match against
    input_str = json.dumps(tool_input, separators=(",", ":"))
    for p in patterns:
        if p.get("tool", "execute_bash") != tool_name:
            continue
        try:
            if re.search(p["pattern"], input_str, re.IGNORECASE):
                return True, p.get("note", p["pattern"])
        except re.error:
            continue
    return False, ""
