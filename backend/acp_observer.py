"""ACP observation side-channel registry.

Maintains one ``ACPSession`` per managed V3 session that Quarterdeck spawned.
The tmux session owns the kiro-cli process; this module owns a *parallel*
``kiro-cli acp`` subprocess that fires a fresh session (``session/new``) and
listens for ``session/update`` and ``ToolCall`` notifications.

Design constraints (from Task 1 probe 2026-08-14):
- ``session/load`` hangs — this module never uses it.
- ACP is a side-channel for observation and command dispatch only.

Auth:
  ``kiro-cli acp`` sends a ``_kiro/auth/getAccessToken`` notification.
  We reply with the token read from the kiro-cli SQLite store.

Public API::

    # Lifecycle
    acp_observer.attach(session_id, cwd="/path")   # called on V3 dispatch
    acp_observer.detach(session_id)                # called on kill
    acp_observer.detach_all()                      # called on shutdown

    # Observation
    events = acp_observer.get_events(session_id)   # list of {method, params}
    status = acp_observer.detect_status(session_id) # or None if no observer
    caps   = acp_observer.get_capabilities(session_id) # list of slash cmds

    # Dispatch
    acp_observer.send_prompt(session_id, "text")    # Task 5: user prompt via ACP
    acp_observer.execute_command(session_id, "/cmd") # Task 5: slash cmd via ACP
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

    def last_session_update(self) -> dict | None:
        """Return the most recent session/update params, or None."""
        with self._lock:
            for ev in reversed(self._events):
                if ev.get("method") == "session/update":
                    return ev.get("params", {})
        return None


# ── registry entry ────────────────────────────────────────────────────────────

class _Entry:
    """Holds the ACPSession, its event store, the ACP session id, and capabilities."""
    __slots__ = ("sess", "store", "acp_sid", "capabilities")

    def __init__(self, sess: ACPSession, store: _EventStore, acp_sid: str) -> None:
        self.sess = sess
        self.store = store
        self.acp_sid = acp_sid
        self.capabilities: list[str] = []  # slash commands from _kiro.dev/commands/available


# session_id → _Entry
_registry: dict[str, _Entry] = {}
_registry_lock = threading.Lock()


# ── attach ────────────────────────────────────────────────────────────────────

def attach(session_id: str, cwd: str | None = None) -> bool:
    """Start an ACP side-channel for *session_id*.

    Returns True on success, False if already attached or the subprocess fails.
    Called from a background thread in the dispatch path — must not block long.
    """
    with _registry_lock:
        existing = _registry.get(session_id)
        if existing is not None and existing.sess.is_alive:
            return True
    if existing is not None:
        # A dead observer used to be indistinguishable from a live one here:
        # the id was in the registry, so attach() returned True and no new
        # side-channel was ever started. The session's V3 streaming then stayed
        # silently dead for as long as the process lived, and the corpse's
        # subprocess tree (kiro-cli-chat → bun → tui.js) was never reaped.
        log.info("ACP observer for %s is dead — replacing it", session_id)
        detach(session_id)

    sess = ACPSession(engine="v2", timeout=15.0)
    store = _EventStore()

    # Auth handler.
    def _on_auth(method: str, params: dict) -> None:
        rid = params.get("id")
        token = _read_access_token()
        if rid is not None:
            try:
                sess.notify("_kiro/auth/getAccessToken/response",
                            {"id": rid, "accessToken": token})
            except Exception:
                pass

    # Generic notification → event store.
    def _on_any(method: str, params: dict) -> None:
        store.append({"method": method, "params": params})

    sess.on("_kiro/auth/getAccessToken", _on_auth)
    sess.on("*", _on_any)

    try:
        sess.start()
        sess.initialize(client_name="quarterdeck-observer")
        acp_sid = sess.new_session(cwd=cwd)
    except (ACPError, TimeoutError, RuntimeError) as exc:
        log.warning("ACP attach failed for %s: %s", session_id, exc)
        try:
            sess.stop()
        except Exception:
            pass
        return False

    entry = _Entry(sess=sess, store=store, acp_sid=acp_sid)

    # Capability probe — register for _kiro.dev/commands/available.
    # The notification arrives shortly after session/new; we capture it once.
    def _on_caps(method: str, params: dict) -> None:
        cmds = params.get("commands", [])
        if isinstance(cmds, list):
            entry.capabilities = [str(c) for c in cmds]
            log.debug("ACP caps for %s: %s", session_id, entry.capabilities)
        sess.off("_kiro.dev/commands/available", _on_caps)

    sess.on("_kiro.dev/commands/available", _on_caps)

    with _registry_lock:
        _registry[session_id] = entry

    log.info("ACP observer attached for %s (acp_sid=%s)", session_id, acp_sid)
    return True


# ── detach ────────────────────────────────────────────────────────────────────

def detach(session_id: str) -> None:
    """Stop the ACP side-channel for *session_id* if one is running."""
    with _registry_lock:
        entry = _registry.pop(session_id, None)
    if entry:
        try:
            entry.sess.stop()
        except Exception:
            pass
        log.info("ACP observer detached for %s", session_id)


def detach_all() -> None:
    """Stop all observers — called on app shutdown."""
    with _registry_lock:
        ids = list(_registry.keys())
    for sid in ids:
        detach(sid)


def prune(live_session_ids: set[str] | None = None) -> list[str]:
    """Detach observers whose subprocess died or whose session is gone.

    detach() is called from the paths where Quarterdeck ends a session itself —
    kill, handoff, takeover. Sessions do not only end that way: the agent can
    exit on its own, kiro-cli can crash, someone can `tmux kill-session` from a
    terminal. Every one of those left an entry here holding a live ACP
    subprocess tree of its own, hundreds of megabytes each, for the life of the
    backend. Ten sessions had grown to fifty-odd kiro-cli processes that way.

    Pass `live_session_ids` to also drop observers for sessions that no longer
    exist; with no argument only dead subprocesses are collected.
    """
    with _registry_lock:
        candidates = list(_registry.items())
    stale = []
    for session_id, entry in candidates:
        try:
            alive = entry.sess.is_alive
        except Exception:
            alive = False
        if not alive:
            stale.append(session_id)
        elif live_session_ids is not None and session_id not in live_session_ids:
            stale.append(session_id)
    for session_id in stale:
        detach(session_id)
    return stale


def attached_count() -> int:
    """How many observers are registered — for diagnostics."""
    with _registry_lock:
        return len(_registry)


# ── observation ───────────────────────────────────────────────────────────────

def get_events(session_id: str) -> list[_Event]:
    """Return accumulated ACP notifications for *session_id* (oldest-first)."""
    with _registry_lock:
        entry = _registry.get(session_id)
    if not entry:
        return []
    return entry.store.snapshot()


def is_attached(session_id: str) -> bool:
    """True if there is a live ACP side-channel for *session_id*."""
    with _registry_lock:
        entry = _registry.get(session_id)
    if not entry:
        return False
    return entry.sess.is_alive


def get_capabilities(session_id: str) -> list[str]:
    """Return the slash commands reported by _kiro.dev/commands/available."""
    with _registry_lock:
        entry = _registry.get(session_id)
    return list(entry.capabilities) if entry else []


def get_stream_chunks(session_id: str, after: int = -1) -> tuple[list[dict], bool]:
    """Return agent_message_chunk events since cursor *after*.

    Each item is ``{"index": int, "text": str, "done": bool}`` where
    ``done=True`` signals the turn ended (sessionUpdate == "done"/"end_turn").

    Returns ``(chunks, attached)`` — ``attached`` is False if there is no
    live ACP observer (caller should fall back to polling).
    """
    with _registry_lock:
        entry = _registry.get(session_id)
    if not entry:
        return [], False

    events = entry.store.snapshot()
    result = []
    for i, ev in enumerate(events):
        if i <= after:
            continue
        if ev.get("method") != "session/update":
            continue
        update = (ev.get("params") or {}).get("update", {})
        su = update.get("sessionUpdate", "")
        if su == "agent_message_chunk":
            content = update.get("content", {})
            if content.get("type") == "text":
                result.append({"index": i, "text": content.get("text", ""), "done": False})
        elif su in ("done", "end_turn", "session_end"):
            result.append({"index": i, "text": "", "done": True})
    return result, True


def detect_status(session_id: str) -> str | None:
    """Derive session status from the most recent ACP session/update event.

    Returns one of the standard status strings (thinking, running, idle,
    awaiting-approval, error) or None if no observer is attached or no
    relevant event has arrived yet.

    The ACP stream is more reliable than pane-scraping for V3 sessions: the
    notification arrives within milliseconds of the state change, whereas the
    pane may not have redrawn yet.

    Status mapping from session/update sessionUpdate values:
      agent_message_chunk  → thinking  (model is generating)
      tool_call            → running   (agent is executing a tool)
      tool_result          → running   (tool returned, agent processing)
      done / end_turn      → idle
      error                → error
      pending_interaction  → awaiting-approval
    """
    with _registry_lock:
        entry = _registry.get(session_id)
    if not entry or not entry.sess.is_alive:
        return None

    params = entry.store.last_session_update()
    if not params:
        return None

    update = params.get("update", {})
    su = update.get("sessionUpdate", "")

    if su in ("done", "end_turn", "session_end"):
        return "idle"
    if su == "error":
        return "error"
    if su == "pending_interaction":
        return "awaiting-approval"
    if su == "tool_call":
        return "running"
    if su == "tool_result":
        return "running"
    if su == "agent_message_chunk":
        return "thinking"
    # agent_thinking, context_update, etc. — agent is active
    if su:
        return "thinking"

    return None


# ── dispatch (Task 5) ─────────────────────────────────────────────────────────

def send_prompt(session_id: str, text: str, timeout: float = 30.0) -> bool:
    """Send a user prompt via ACP to the observed session.

    Returns True on success. Raises RuntimeError/ACPError/TimeoutError on
    failure — caller should fall back to tmux send_text.
    """
    with _registry_lock:
        entry = _registry.get(session_id)
    if not entry or not entry.sess.is_alive:
        raise RuntimeError(f"No live ACP observer for {session_id}")
    entry.sess.prompt(entry.acp_sid, text, timeout=timeout)
    return True


def execute_command(session_id: str, command: str, timeout: float = 10.0) -> bool:
    """Send a slash command via _kiro.dev/commands/execute.

    Returns True on success, False if the command is not in the capability list
    (gate so we don't fire into the void). Falls back gracefully if the
    notification is not supported — caller handles.
    """
    with _registry_lock:
        entry = _registry.get(session_id)
    if not entry or not entry.sess.is_alive:
        raise RuntimeError(f"No live ACP observer for {session_id}")

    # Gate: only send if capabilities are known and command is listed.
    # If caps are empty (notification not yet received / not supported),
    # let the caller fall back to tmux rather than sending into the void.
    if entry.capabilities and command not in entry.capabilities:
        log.debug("ACP: %s not in capabilities for %s", command, session_id)
        return False

    try:
        entry.sess.notify("_kiro.dev/commands/execute", {
            "sessionId": entry.acp_sid,
            "command": command,
        })
        return True
    except (ACPError, RuntimeError) as exc:
        log.warning("ACP execute_command failed for %s: %s", session_id, exc)
        raise
