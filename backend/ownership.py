"""Session ownership sidecars and Crew adapter.

Ownership records live under ~/.osa-kiro/owners/<session-id>.json.
They are the only source Quarterdeck writes; session files are never touched.

Safe defaults (per plan §3.2):
  owner: "human", role: "primary", handoverable: True, visible: True

An unknown session is never hidden. "Visible and refused" is always better
than "hidden and silently unavailable."

Crew adapter (plan §3.4):
  Reads ~/.kiro/crew/subagents/*/state.json (read-only).
  Derives owner, group_id, and handoverable from the parent_session field.
  Result is cached with a 5-second TTL — cheap enough for the polling interval.
"""

import json
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_CREW_DIR = Path.home() / ".kiro" / "crew"
_CREW_SUBAGENTS_DIR = _CREW_DIR / "subagents"
_CREW_SESSION_MAP = _CREW_DIR / "session_map.json"

# Populated by init() called from api.py after config is loaded.
_owners_dir: Path | None = None


def init(owners_dir: Path) -> None:
    global _owners_dir
    _owners_dir = owners_dir
    _owners_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Safe defaults
# ---------------------------------------------------------------------------

DEFAULT_OWNERSHIP: dict[str, Any] = {
    "owner": "human",
    "role": "primary",
    "group_id": None,
    "handoverable": True,
    "visible": True,
}


def _safe_defaults(session_id: str) -> dict[str, Any]:
    return {**DEFAULT_OWNERSHIP, "session_id": session_id}


# ---------------------------------------------------------------------------
# Sidecar read / write
# ---------------------------------------------------------------------------

def read_sidecar(session_id: str) -> dict[str, Any] | None:
    """Return the sidecar for session_id, or None if it does not exist."""
    if _owners_dir is None:
        return None
    path = _owners_dir / f"{session_id}.json"
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_sidecar(session_id: str, data: dict[str, Any]) -> None:
    """Write or update the sidecar for session_id."""
    if _owners_dir is None:
        raise RuntimeError("ownership.init() not called")
    path = _owners_dir / f"{session_id}.json"
    existing = read_sidecar(session_id) or {}
    existing.update(data)
    existing["session_id"] = session_id
    path.write_text(json.dumps(existing, indent=2))


def release_sidecar(session_id: str) -> bool:
    """Set released=True on the sidecar, restoring handoverability.

    Returns True if the sidecar existed and was updated.
    """
    sidecar = read_sidecar(session_id)
    if sidecar is None:
        return False
    sidecar["released"] = True
    sidecar["handoverable"] = True
    write_sidecar(session_id, sidecar)
    return True


# ---------------------------------------------------------------------------
# Crew adapter
# ---------------------------------------------------------------------------

# Cache: (timestamp, {session_id: ownership_dict})
_crew_cache: tuple[float, dict[str, dict]] = (0.0, {})
_CREW_CACHE_TTL = 5.0  # seconds


def _load_crew_ownership() -> dict[str, dict]:
    """Read ~/.kiro/crew/subagents/*/state.json and build a session_id → ownership map.

    Schema observed (2026-08-08, KiroCrew):
      state.json: {id, task, agent, parent_session, session_id, status, pid, ...}
      parent_session: "dashboard:chat-34-1785684035"
      session_id: "16a53cc9-61f0-421a-ac31-bd8db09197c8"  ← matches ~/.kiro/sessions/cli/

    parent_session is the Crew dashboard session key (not a kiro-cli UUID).
    group_id is derived from it: a stable string identifying the parent job.
    """
    if not _CREW_SUBAGENTS_DIR.exists():
        return {}

    result: dict[str, dict] = {}

    for subagent_dir in _CREW_SUBAGENTS_DIR.iterdir():
        if not subagent_dir.is_dir():
            continue
        state_path = subagent_dir / "state.json"
        try:
            state = json.loads(state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            continue

        kiro_session_id = state.get("session_id")
        if not kiro_session_id:
            continue

        parent = state.get("parent_session", "")
        # group_id: use the parent session key as a stable group identifier.
        # Subagents sharing the same parent are in the same group.
        group_id = f"crew:{parent}" if parent else f"crew:{subagent_dir.name}"

        # A released sidecar takes precedence — don't override user intent.
        result[kiro_session_id] = {
            "session_id": kiro_session_id,
            "owner": "kirocrew",
            "role": "worker",
            "group_id": group_id,
            "handoverable": False,
            "visible": False,
            "crew_subagent_id": subagent_dir.name,
            "crew_parent_session": parent,
            "crew_status": state.get("status", ""),
        }

    return result


def _crew_ownership() -> dict[str, dict]:
    global _crew_cache
    ts, cache = _crew_cache
    if time.monotonic() - ts < _CREW_CACHE_TTL:
        return cache
    fresh = _load_crew_ownership()
    _crew_cache = (time.monotonic(), fresh)
    return fresh


# ---------------------------------------------------------------------------
# Public API: resolve ownership for a session
# ---------------------------------------------------------------------------

def get_ownership(session_id: str) -> dict[str, Any]:
    """Return the effective ownership record for session_id.

    Priority:
      1. Sidecar (explicit, written by Quarterdeck or by an orchestrator)
      2. Crew adapter (derived, read-only)
      3. Safe defaults (human-owned, handoverable, visible)

    A sidecar with released=True always overrides the Crew adapter's
    handoverable=False, so humans can reclaim a session.
    """
    sidecar = read_sidecar(session_id)
    if sidecar is not None:
        # Merge defaults for any missing fields (forward-compat).
        merged = {**DEFAULT_OWNERSHIP, **sidecar, "session_id": session_id}
        # released=True always restores handoverability regardless of owner.
        if merged.get("released"):
            merged["handoverable"] = True
        return merged

    crew = _crew_ownership().get(session_id)
    if crew is not None:
        return {**DEFAULT_OWNERSHIP, **crew, "session_id": session_id}

    return _safe_defaults(session_id)


def is_handoverable(session_id: str) -> tuple[bool, str]:
    """Return (True, "") or (False, reason) for the takeover gate."""
    o = get_ownership(session_id)
    if o.get("handoverable", True):
        return True, ""
    owner = o.get("owner", "unknown")
    return False, f"Session is owned by '{owner}' — set released=true to hand over"


# ---------------------------------------------------------------------------
# Group resolution
# ---------------------------------------------------------------------------

def resolve_groups(session_ids: list[str]) -> dict[str, list[str]]:
    """Return {group_id: [session_id, ...]} for all sessions that have a group.

    Sessions without a group_id are not included.
    """
    groups: dict[str, list[str]] = {}
    for sid in session_ids:
        o = get_ownership(sid)
        gid = o.get("group_id")
        if gid:
            groups.setdefault(gid, []).append(sid)
    return groups
