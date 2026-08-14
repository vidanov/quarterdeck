"""ACP observation side-channel registry.

Maintains one ``ACPSession`` per managed V3 session that Quarterdeck spawned.
The tmux session owns the kiro-cli process; this module owns a *parallel*
``kiro-cli acp`` subprocess that fires a fresh session (``session/new``) and
listens for ``session/update`` and ``ToolCall`` notifications.

Design constraints (from Task 1 probe 2026-08-14):
- ``session/load`` hangs — this module never uses it.
- ACP is a side-channel for observation only; the tmux session is the source
  of truth.  The ACP session is detached the moment the tmux session ends.

Auth:
  ``kiro-cli acp`` sends a ``_kiro/auth/getAccessToken`` notification.
  We reply with the token read from the kiro-cli SQLite store.

Usage::

    # When a V3 session is spawned (engine="v3"):
    acp_observer.attach(session_id, cwd="/path/to/project")

    # From the API endpoint:
    events = acp_observer.get_events(session_id)

    # When the session ends:
    acp_observer.detach(session_id)
"""
import json
import logging
import sqlite3
import threading
from collections import deque
from pathlib import Path
from typing import Any

from backend.acp_session import ACPError, ACPSession

log = logging.getLogger(__name__)

# ── auth token ────────────────────────────────────────────────────────────────

_SQLITE_DB = (
    Path.home() / "Library" / "Application Support" / "kiro-cli" / "data.sqlite3"
)
_TOKEN_KEY = "kirocli:odic:token"


def _read_access_token() -> str:
    """Return the current kiro-cli OIDC access token, or '' if unavailable."""
    try:
        con = sqlite3.connect(str(_SQLITE_DB), timeout=3)
        with con:
            row = con.execute(
                "SELECT value FROM auth_kv WHERE key = ?", (_TOKEN_KEY,)
            ).fetchone()
        con.close()
        if not row:
            return ""
        data = json.loads(row[0])
        return data.get("accessToken") or data.get("access_token") or ""
    except Exception:
        log.debug("Could not read kiro-cli auth token", exc_info=True)
        return ""


# ── event store ───────────────────────────────────────────────────────────────

# Per-session capped ring buffer of ACP notification events.
MAX_EVENTS = 200

_Event = dict[str, Any]


class _EventStore:
    def __init__(self) -> None:
        self._events: deque[_Event] = deque(maxlen=MAX_EVENTS)
        self._lock = threading.Lock()

    def append(self, event: _Event) -> None:
        with self._lock:
            self._events.append(event)

    def snapshot(self) -> list[_Event]:
        with self._lock:
            return list(self._events)


# ── registry ──────────────────────────────────────────────────────────────────

# session_id → (ACPSession, _EventStore)
_registry: dict[str, tuple[ACPSession, _EventStore]] = {}
_registry_lock = threading.Lock()


def attach(session_id: str, cwd: str | None = None) -> bool:
    """Start an ACP side-channel for *session_id*.

    Spawns a ``kiro-cli acp`` subprocess (engine v2 — the only one that doesn't
    hang on ``session/new``), calls ``initialize`` + ``session/new``, and
    registers callbacks that stream notifications into an event store.

    Returns True on success, False if the session is already attached or the
    subprocess fails to start.

    This function is called from a background thread (FastAPI dispatch path)
    and must not block for more than a few seconds.
    """
    with _registry_lock:
        if session_id in _registry:
            return True  # already attached

    sess = ACPSession(engine="v2", timeout=15.0)
    store = _EventStore()

    # Auth handler — reply to getAccessToken requests.
    def _on_auth(method: str, params: dict) -> None:
        rid = params.get("id")
        token = _read_access_token()
        if rid is not None:
            try:
                sess.notify("_kiro/auth/getAccessToken/response",
                            {"id": rid, "accessToken": token})
            except Exception:
                log.debug("ACP auth reply failed for %s", session_id)

    # Generic notification → event store.
    def _on_any(method: str, params: dict) -> None:
        store.append({"method": method, "params": params})

    sess.on("_kiro/auth/getAccessToken", _on_auth)
    sess.on("*", _on_any)

    try:
        sess.start()
        sess.initialize(client_name="quarterdeck-observer")
        sess.new_session(cwd=cwd)
    except (ACPError, TimeoutError, RuntimeError) as exc:
        log.warning("ACP attach failed for %s: %s", session_id, exc)
        try:
            sess.stop()
        except Exception:
            pass
        return False

    with _registry_lock:
        _registry[session_id] = (sess, store)

    log.info("ACP observer attached for %s", session_id)
    return True


def detach(session_id: str) -> None:
    """Stop the ACP side-channel for *session_id* if one is running."""
    with _registry_lock:
        entry = _registry.pop(session_id, None)
    if entry:
        sess, _ = entry
        try:
            sess.stop()
        except Exception:
            pass
        log.info("ACP observer detached for %s", session_id)


def get_events(session_id: str) -> list[_Event]:
    """Return accumulated ACP notifications for *session_id* (newest last)."""
    with _registry_lock:
        entry = _registry.get(session_id)
    if not entry:
        return []
    _, store = entry
    return store.snapshot()


def is_attached(session_id: str) -> bool:
    """True if there is a live ACP side-channel for *session_id*."""
    with _registry_lock:
        entry = _registry.get(session_id)
    if not entry:
        return False
    sess, _ = entry
    return sess.is_alive


def detach_all() -> None:
    """Stop all observers — called on app shutdown."""
    with _registry_lock:
        ids = list(_registry.keys())
    for sid in ids:
        detach(sid)
