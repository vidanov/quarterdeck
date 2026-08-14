"""Steering delivery measurement for Quarterdeck.

Key finding from delivery-audit.md (2026-08-08): steering content is NOT stored
in session JSONL files. The CLI injects it at model-call time ephemerally. There
is no post-hoc way to determine what steering the model received from the session
file alone.

This module therefore uses two complementary methods:

1. STATIC INFERENCE — what should have been delivered, based on agent config.
   Reads the agent field from the session's managed.json record or lock file,
   then checks that agent's resources array for steering globs. This is the
   lower-fidelity but always-available signal.

2. PROBE OBSERVATION — what was actually observed via the echo test.
   Written explicitly by the caller (POST /api/sessions/{id}/delivery/probe)
   when a human runs the echo test and records a result.

Records are written to ~/.osa-kiro/delivery/YYYY-MM-DD.jsonl, one line per
observation. They are append-only; never rewritten.
"""

from __future__ import annotations

import fnmatch
import json
import time
from datetime import date
from pathlib import Path
from typing import Optional

_delivery_dir: Optional[Path] = None
_agents_dir: Optional[Path] = None


def init(delivery_dir: Path, agents_dir: Path) -> None:
    global _delivery_dir, _agents_dir
    _delivery_dir = delivery_dir
    _agents_dir = agents_dir
    delivery_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Agent steering resolution
# ---------------------------------------------------------------------------

def _agent_steering_resources(agent_name: str) -> list[str]:
    """Return the resources entries for an agent that look like steering globs."""
    if not _agents_dir or not agent_name:
        return []
    agent_file = _agents_dir / f"{agent_name}.json"
    try:
        data = json.loads(agent_file.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    resources = data.get("resources") or []
    # Keep only entries that reference .kiro/steering or ~/.kiro/steering
    return [r for r in resources if "steering" in r and r.startswith("file://")]


def _steering_files_for_agent(agent_name: str, workspace_steering_dir: Path,
                               global_steering_dir: Path) -> dict:
    """Determine which steering files should arrive for a given agent.

    Returns a dict with:
        expected: list of {file, mode, scope} for files that should be delivered
        notes: list of human-readable notes
    """
    resources = _agent_steering_resources(agent_name)
    is_builtin = not resources and not _agents_dir or not (_agents_dir / f"{agent_name}.json").exists()

    expected = []
    notes = []

    # Built-in agents get CLI-injected steering from both workspace and global.
    # Custom agents only get what their resources array includes.
    if is_builtin or agent_name in ("kiro_default", "kiro_planner", "kiro_guide", ""):
        # Workspace always-mode files
        for f in _collect_always_files(workspace_steering_dir):
            expected.append({"file": f.name, "mode": "always", "scope": "workspace"})
        # Global always-mode files
        for f in _collect_always_files(global_steering_dir):
            expected.append({"file": f.name, "mode": "always", "scope": "global"})
        notes.append("built-in agent: CLI injects workspace + global always-mode steering")
    else:
        # Custom agent — only what resources says
        if not resources:
            notes.append(f"custom agent '{agent_name}' has no steering resources — zero files delivered")
        for resource_glob in resources:
            # resource is like "file://.kiro/steering/**/*.md" or "file://~/.kiro/steering/**/*.md"
            path_part = resource_glob[len("file://"):]
            path_part = path_part.replace("~", str(Path.home()))
            glob_base = path_part.replace("/**/*.md", "").replace("**/*.md", "")
            steering_dir = Path(glob_base)
            scope = "workspace" if ".kiro/steering" in str(steering_dir) and "~" not in resource_glob else "global"
            if steering_dir.exists():
                for f in _collect_always_files(steering_dir):
                    expected.append({"file": f.name, "mode": "always", "scope": scope})
            notes.append(f"resource '{resource_glob}' -> {len(expected)} files")

    return {"expected": expected, "notes": notes}


def _collect_always_files(steering_dir: Path) -> list[Path]:
    """Collect steering files that use always mode (no frontmatter, or inclusion:always)."""
    result = []
    if not steering_dir.exists():
        return result
    for f in sorted(steering_dir.rglob("*.md")):
        try:
            first_line = f.read_text(errors="replace").lstrip()
        except OSError:
            continue
        # If file starts with --- it has frontmatter; check inclusion mode
        if first_line.startswith("---"):
            lines = first_line.split("\n")
            mode = None
            for line in lines[1:]:
                if line.strip() == "---":
                    break
                if line.startswith("inclusion:"):
                    mode = line.split(":", 1)[1].strip().strip('"\'')
            if mode and mode != "always":
                continue  # fileMatch, auto, manual — skip for static analysis
        result.append(f)
    return result


# ---------------------------------------------------------------------------
# Record writing
# ---------------------------------------------------------------------------

def _today_file() -> Path:
    assert _delivery_dir is not None
    return _delivery_dir / f"{date.today().isoformat()}.jsonl"


def _append(record: dict) -> None:
    record.setdefault("ts", time.time())
    with _today_file().open("a") as f:
        f.write(json.dumps(record) + "\n")


def record_session_delivery(session_id: str, agent_name: str,
                             workspace_dir: str) -> dict:
    """Compute and record what steering should have been delivered for a session turn.

    Call this once per session poll (not per turn — it's stable until agent changes).
    Returns the delivery record.
    """
    if not _delivery_dir:
        return {}

    workspace_steering = Path(workspace_dir) / ".kiro" / "steering" if workspace_dir else Path()
    global_steering = Path.home() / ".kiro" / "steering"

    info = _steering_files_for_agent(agent_name, workspace_steering, global_steering)
    record = {
        "session_id": session_id,
        "agent": agent_name,
        "workspace": workspace_dir,
        "method": "static_inference",
        "expected_count": len(info["expected"]),
        "expected_files": [e["file"] for e in info["expected"]],
        "notes": info["notes"],
        "probe_observations": [],  # filled by record_probe_observation()
    }
    _append(record)
    return record


def record_probe_observation(session_id: str, mode: str, token: str,
                              delivered: bool, agent_name: str = "",
                              scope: str = "workspace") -> dict:
    """Record the result of a manual probe echo test for one mode/token pair.

    delivered=True means the model echoed back the token.
    """
    if not _delivery_dir:
        return {}
    record = {
        "session_id": session_id,
        "agent": agent_name,
        "method": "probe_echo",
        "mode": mode,
        "token": token,
        "scope": scope,
        "delivered": delivered,
    }
    _append(record)
    return record


# ---------------------------------------------------------------------------
# Reading delivery records for a session
# ---------------------------------------------------------------------------

def get_session_delivery(session_id: str) -> dict:
    """Return all delivery records for a session, merged across all date files.

    Returns:
        latest_static: the most recent static_inference record
        probe_results: all probe_echo records
        summary: {agent, expected_count, expected_files, notes, probes_run}
    """
    if not _delivery_dir:
        return {}

    static_records = []
    probe_records = []

    for day_file in sorted(_delivery_dir.glob("*.jsonl"), reverse=True):
        try:
            for line in day_file.read_text().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("session_id") != session_id:
                    continue
                if rec.get("method") == "static_inference":
                    static_records.append(rec)
                elif rec.get("method") == "probe_echo":
                    probe_records.append(rec)
        except (OSError, json.JSONDecodeError):
            continue

    latest = static_records[0] if static_records else {}
    return {
        "session_id": session_id,
        "agent": latest.get("agent", ""),
        "expected_count": latest.get("expected_count", 0),
        "expected_files": latest.get("expected_files", []),
        "notes": latest.get("notes", []),
        "probe_results": probe_records,
        "probes_run": len(probe_records),
        "last_recorded": latest.get("ts"),
    }
