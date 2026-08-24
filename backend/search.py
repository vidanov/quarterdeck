"""FTS5 content index for session search.

Maintains a SQLite FTS5 index over the first 5 user-prompt turns of every
session. The DB lives in ~/.osa-kiro/search.db and is updated lazily — only
sessions whose JSONL mtime is newer than the indexed version are re-indexed.

Public API
----------
search(query, limit=20)          → list[dict]  ranked results
index_session(session_id)        → None         index one session explicitly
refresh_stale(sessions_dir)      → None         background sweep (call at startup)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from backend.config import STATE_DIR

log = logging.getLogger(__name__)

_DB_PATH = STATE_DIR / "search.db"
_DB_LOCK = threading.Lock()

# How many user-prompt turns to index per session.
_MAX_TURNS = 5


# ---------------------------------------------------------------------------
# DB init
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS session_content USING fts5(
            session_id UNINDEXED,
            title,
            cwd UNINDEXED,
            content,
            tokenize="unicode61"
        );

        CREATE TABLE IF NOT EXISTS session_meta (
            session_id TEXT PRIMARY KEY,
            indexed_mtime REAL NOT NULL
        );
    """)
    conn.commit()


def _ensure_db() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    _init_db(conn)
    return conn


# ---------------------------------------------------------------------------
# Indexing helpers
# ---------------------------------------------------------------------------

def _extract_user_turns(jsonl_path: Path, max_turns: int = _MAX_TURNS) -> str:
    """Return concatenated text of the first *max_turns* user-prompt turns."""
    texts: list[str] = []
    try:
        with jsonl_path.open("rb") as f:
            for raw_line in f:
                if len(texts) >= max_turns:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("kind") != "Prompt":
                    continue
                data = entry.get("data", {})
                for block in data.get("content", []):
                    if isinstance(block, dict) and block.get("kind") == "text":
                        text = (block.get("data") or "").strip()
                        if text:
                            texts.append(text)
                            break  # one text block per prompt turn
    except OSError:
        pass
    return " ".join(texts)


def _index_one(conn: sqlite3.Connection, session_id: str,
               jsonl_path: Path, title: str, cwd: str) -> None:
    """Index (or re-index) a single session. Caller holds _DB_LOCK."""
    mtime = jsonl_path.stat().st_mtime
    content = _extract_user_turns(jsonl_path)
    if not content.strip():
        content = title  # fall back to title so the session is still searchable

    conn.execute(
        "DELETE FROM session_content WHERE session_id = ?", (session_id,)
    )
    conn.execute(
        "INSERT INTO session_content(session_id, title, cwd, content) VALUES (?,?,?,?)",
        (session_id, title, cwd, content),
    )
    conn.execute(
        "INSERT OR REPLACE INTO session_meta(session_id, indexed_mtime) VALUES (?,?)",
        (session_id, mtime),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Public: index one session explicitly (called from api.py after indexing)
# ---------------------------------------------------------------------------

def index_session(session_id: str, sessions_dir: Path,
                  title: str = "", cwd: str = "") -> None:
    """Index or re-index a single session. Non-fatal on any error."""
    jsonl_path = sessions_dir / f"{session_id}.jsonl"
    if not jsonl_path.exists():
        return
    try:
        with _DB_LOCK:
            conn = _ensure_db()
            _index_one(conn, session_id, jsonl_path, title, cwd)
    except Exception as exc:
        log.debug("search.index_session(%s): %s", session_id, exc)


# ---------------------------------------------------------------------------
# Public: sweep all sessions and update stale entries
# ---------------------------------------------------------------------------

def refresh_stale(sessions_dir: Path) -> None:
    """Re-index sessions whose JSONL is newer than the indexed mtime.

    Designed to run once at startup (in a background thread) and after
    bulk operations. Fast for large archives — only touches changed files.
    """
    if not sessions_dir.exists():
        return
    try:
        with _DB_LOCK:
            conn = _ensure_db()
            # Load all known indexed mtimes in one query
            indexed: dict[str, float] = {
                row["session_id"]: row["indexed_mtime"]
                for row in conn.execute("SELECT session_id, indexed_mtime FROM session_meta")
            }
            # Gather metadata (title/cwd) from JSON sidecars lazily
            import re as _re
            updated = 0
            for jsonl_path in sessions_dir.glob("*.jsonl"):
                sid = jsonl_path.stem
                try:
                    mtime = jsonl_path.stat().st_mtime
                except OSError:
                    continue
                if indexed.get(sid, 0) >= mtime:
                    continue  # up to date
                # Read title/cwd from sidecar JSON if present
                title, cwd = "", ""
                json_path = sessions_dir / f"{sid}.json"
                if json_path.exists():
                    try:
                        meta = json.loads(json_path.read_text())
                        raw_title = meta.get("title") or meta.get("name") or ""
                        title = _re.sub(r"\s+[0-9a-f]{8}$", "", raw_title, flags=_re.I).strip()
                        cwd = meta.get("cwd") or ""
                    except Exception:
                        pass
                _index_one(conn, sid, jsonl_path, title, cwd)
                updated += 1
            if updated:
                log.debug("search.refresh_stale: indexed %d sessions", updated)
    except Exception as exc:
        log.debug("search.refresh_stale: %s", exc)


# ---------------------------------------------------------------------------
# Public: search
# ---------------------------------------------------------------------------

def search(query: str, limit: int = 20) -> list[dict]:
    """Full-text search over indexed session content.

    Returns up to *limit* results sorted by FTS5 rank (best match first).
    Each result dict matches the shape returned by GET /api/archive.
    """
    if not query.strip():
        return []
    try:
        with _DB_LOCK:
            conn = _ensure_db()
        # FTS5 MATCH uses its own query syntax; escape special chars
        fts_query = _sanitise_fts_query(query)
        rows = conn.execute(
            """
            SELECT session_id, title, cwd, rank
            FROM session_content
            WHERE session_content MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
        results = []
        for row in rows:
            results.append({
                "id": row["session_id"],
                "title": row["title"] or "Untitled",
                "cwd": row["cwd"] or "",
                "rank": row["rank"],
            })
        return results
    except Exception as exc:
        log.debug("search.search(%r): %s", query, exc)
        return []


def _sanitise_fts_query(q: str) -> str:
    """Convert a plain-text query to an FTS5-safe query string.

    FTS5 treats punctuation specially. For a user-typed query we want
    simple prefix matching: join tokens with AND and add a * suffix to
    the last token so partial words match.
    """
    # Strip chars FTS5 treats as operators
    import re
    tokens = [t for t in re.split(r'[^\w]+', q) if t and len(t) >= 2]
    if not tokens:
        return q
    # Prefix-match last token so "deny patt" → "deny patt*"
    tokens[-1] = tokens[-1] + "*"
    return " AND ".join(tokens)
