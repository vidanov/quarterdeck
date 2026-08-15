"""Paste store — file-backed delivery for large clipboard pastes.

Files land at:
  PASTES_DIR/<session_id or _unassigned>/<YYYYMMDD-HHMMSS>-<slug>.md

The name is always a plain filename (no path separators). All resolution
goes through _resolve(), which rejects any name whose resolved parent is
not inside PASTES_DIR — traversal guard for browser-supplied values.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import PASTES_DIR, PASTE_MIN_CHARS, PASTE_MIN_LINES, PASTE_RETENTION_DAYS

PREVIEW_LINES = 15


# ── helpers ────────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    """Turn the first meaningful words of text into a safe filename fragment."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    clean = re.sub(r"[^\w\s-]", "", first)[:40].strip()
    slug = re.sub(r"[\s_]+", "-", clean).strip("-") or "paste"
    return slug.lower()


def _bucket(session_id: str | None) -> Path:
    return PASTES_DIR / (session_id.strip() if session_id and session_id.strip() else "_unassigned")


def _resolve(session_id: str | None, name: str) -> Path:
    """Return the absolute path; raise ValueError on traversal attempt."""
    bucket = _bucket(session_id).resolve()
    candidate = (bucket / name).resolve()
    if bucket not in candidate.parents and candidate != bucket:
        raise ValueError(f"Traversal rejected: {name!r}")
    # Must stay inside PASTES_DIR
    pastes_root = PASTES_DIR.resolve()
    if pastes_root not in candidate.parents and candidate != pastes_root:
        raise ValueError(f"Path escapes PASTES_DIR: {name!r}")
    return candidate


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f} KB"
    return f"{n/1024/1024:.1f} MB"


# ── public API ─────────────────────────────────────────────────────────────

def should_collapse(text: str) -> bool:
    """True when the text exceeds either threshold and should become an attachment."""
    return len(text) >= PASTE_MIN_CHARS or text.count("\n") + 1 >= PASTE_MIN_LINES


def save(session_id: str | None, text: str, name: str | None = None) -> dict:
    """Write text to a new paste file and return metadata.

    Returns:
        {id, name, path, lines, bytes, size_display, preview}
    """
    bucket = _bucket(session_id)
    bucket.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = _slug(name or text)
    filename = f"{ts}-{slug}.md"
    path = bucket / filename

    # Avoid name collisions by appending a counter
    counter = 0
    while path.exists():
        counter += 1
        filename = f"{ts}-{slug}-{counter}.md"
        path = bucket / filename

    path.write_text(text, encoding="utf-8")

    lines = text.count("\n") + 1
    byte_count = len(text.encode("utf-8"))
    preview_lines = text.splitlines()[:PREVIEW_LINES]
    preview = "\n".join(preview_lines)

    return {
        "id": filename,
        "name": filename,
        "session_id": session_id or "_unassigned",
        "path": str(path),
        "lines": lines,
        "bytes": byte_count,
        "size_display": _fmt_bytes(byte_count),
        "preview": preview,
    }


def read(session_id: str | None, name: str) -> str:
    """Read and return the text of a paste file."""
    path = _resolve(session_id, name)
    if not path.is_file():
        raise FileNotFoundError(f"Paste not found: {name!r}")
    return path.read_text(encoding="utf-8")


def delete(session_id: str | None, name: str) -> None:
    """Delete a paste file. Silently ignores missing files."""
    try:
        path = _resolve(session_id, name)
        path.unlink(missing_ok=True)
    except ValueError:
        pass  # traversal rejection — nothing to delete


def sweep(days: int = PASTE_RETENTION_DAYS) -> int:
    """Delete paste files older than `days` days. Returns count removed."""
    if not PASTES_DIR.exists():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for f in PASTES_DIR.rglob("*.md"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    # Clean up empty bucket dirs
    for d in PASTES_DIR.iterdir():
        if d.is_dir() and not any(d.iterdir()):
            try:
                d.rmdir()
            except OSError:
                pass
    return removed


def storage_bytes() -> int:
    """Total bytes used by all paste files."""
    if not PASTES_DIR.exists():
        return 0
    total = 0
    for f in PASTES_DIR.rglob("*.md"):
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total


def reference_line(session_id: str | None, name: str, lines: int, size_display: str) -> str:
    """The one-line wire format sent to the agent."""
    path = _resolve(session_id, name)
    return f"[pasted document: {path} — {lines} lines, {size_display}]"
