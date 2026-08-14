"""Correction capture for Quarterdeck.

A correction is a human judgment that the agent did something wrong. It is the
ground truth in the system. One keypress writes it; confirm or withdraw later.
Only confirmed corrections count toward any rate.

An unverified_claim is machine-detected: the stop hook found a completion claim
(done, fixed, works now, verified) in the turn with no observation command after
the last user message. False-positive rate is tolerable — one glance to dismiss;
missing a false claim costs a debugging conversation.

Records are appended to ~/.osa-kiro/corrections/YYYY-MM-DD.jsonl, never rewritten.

Record format:
{
    "id":              "<random hex>",
    "kind":            "correction" | "unverified_claim",   # default "correction" for old records
    "session_id":      "<session id>",
    "group_id":        "<group id or null>",
    "owner":           "<owner name or null>",
    "ts":              <unix float>,
    "last_message_seq": <int or null>,     # seq from the messages API at press time
    "steering_commit": "<git hash or null>",
    "rules_in_context": ["file.md", ...],  # from delivery module
    "status":          "open" | "confirmed" | "withdrawn",
    "note":            "",
    "claim_text":      "<the sentence that triggered detection or null>",
    "observed_tools":  ["tool1", ...] | null   # tools run in the turn (empty = no observation)
}
"""

from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Optional

_corrections_dir: Optional[Path] = None


def init(corrections_dir: Path) -> None:
    global _corrections_dir
    _corrections_dir = corrections_dir
    corrections_dir.mkdir(parents=True, exist_ok=True)


def _today_file() -> Path:
    assert _corrections_dir is not None
    return _corrections_dir / f"{date.today().isoformat()}.jsonl"


def _random_id() -> str:
    return os.urandom(6).hex()


def _append(record: dict) -> None:
    with _today_file().open("a") as f:
        f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def record_correction(
    session_id: str,
    group_id: Optional[str] = None,
    owner: Optional[str] = None,
    last_message_seq: Optional[int] = None,
    steering_commit: Optional[str] = None,
    rules_in_context: Optional[list[str]] = None,
    assistant_message: Optional[str] = None,
) -> dict:
    """Write a new open correction on one keypress. No required fields beyond session_id."""
    record = {
        "id": _random_id(),
        "kind": "correction",
        "session_id": session_id,
        "group_id": group_id,
        "owner": owner,
        "ts": time.time(),
        "last_message_seq": last_message_seq,
        "steering_commit": steering_commit,
        "rules_in_context": rules_in_context or [],
        "assistant_message": assistant_message or "",
        "status": "open",
        "note": "",
    }
    _append(record)
    return record


def record_unverified_claim(
    session_id: str,
    claim_text: str = "",
    observed_tools: Optional[list[str]] = None,
    last_message_seq: Optional[int] = None,
    group_id: Optional[str] = None,
    rules_in_context: Optional[list[str]] = None,
) -> dict:
    """Machine-detected unverified claim from the stop hook.

    Written when the turn's final assistant message contains a completion claim
    keyword and no observation tool was run after the last user message.
    Status starts as 'open'; user can withdraw it if the detection was a false positive.
    """
    record = {
        "id": _random_id(),
        "kind": "unverified_claim",
        "session_id": session_id,
        "group_id": group_id,
        "ts": time.time(),
        "last_message_seq": last_message_seq,
        "claim_text": claim_text,
        "observed_tools": observed_tools or [],
        "rules_in_context": rules_in_context or [],
        "status": "open",
        "note": "",
    }
    _append(record)
    return record


def update_correction(correction_id: str, status: str, note: str = "") -> Optional[dict]:
    """Set status to confirmed or withdrawn. Returns updated record or None if not found."""
    if status not in ("open", "confirmed", "withdrawn"):
        return None

    # Scan all day files, newest first
    if not _corrections_dir:
        return None

    for day_file in sorted(_corrections_dir.glob("*.jsonl"), reverse=True):
        try:
            lines = day_file.read_text().splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("id") != correction_id:
                continue
            # Found — write an updated record (append; never rewrite)
            updated = {**rec, "status": status, "note": note, "updated_ts": time.time()}
            _append(updated)
            return updated

    return None


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_session_corrections(session_id: str) -> list[dict]:
    """Return all corrections for a session, newest first, deduped by id (last status wins)."""
    if not _corrections_dir:
        return []

    # Collect all records for this session, keyed by id
    by_id: dict[str, dict] = {}
    for day_file in sorted(_corrections_dir.glob("*.jsonl")):
        try:
            lines = day_file.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("session_id") != session_id:
                continue
            # Later append overwrites earlier (status updates)
            by_id[rec["id"]] = rec

    return sorted(by_id.values(), key=lambda r: r.get("ts", 0), reverse=True)


def get_all_corrections(limit: int = 200) -> list[dict]:
    """Return all corrections across all sessions, deduped, newest first."""
    if not _corrections_dir:
        return []
    by_id: dict[str, dict] = {}
    for day_file in sorted(_corrections_dir.glob("*.jsonl")):
        try:
            lines = day_file.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            by_id[rec["id"]] = rec
    return sorted(by_id.values(), key=lambda r: r.get("ts", 0), reverse=True)[:limit]


def get_correction_summary() -> dict:
    """Per-session confirmed correction count. Cheap scan for the report endpoint."""
    if not _corrections_dir:
        return {}

    # id -> latest record
    by_id: dict[str, dict] = {}
    for day_file in sorted(_corrections_dir.glob("*.jsonl")):
        try:
            lines = day_file.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            by_id[rec["id"]] = rec

    confirmed = [r for r in by_id.values() if r.get("status") == "confirmed"]
    by_session: dict[str, int] = {}
    for r in confirmed:
        sid = r.get("session_id", "")
        by_session[sid] = by_session.get(sid, 0) + 1

    return {"confirmed_total": len(confirmed), "by_session": by_session}
