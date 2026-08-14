"""Persistent ACP (Agent Client Protocol) client.

Manages one ``kiro-cli acp`` subprocess, speaks JSON-RPC 2.0 over stdio, and
dispatches incoming notifications to registered callbacks.

Design constraints (from Task 1 probe, 2026-08-14):
- ``session/load`` against a tmux-owned session hangs without response.
  This client therefore uses ``session/new`` only — it owns every session
  it creates from spawn.  tmux continues to own sessions it spawned; there
  is no shared-ownership path.
- ACP is a side-channel for sessions the client spawns itself, never a
  transport for existing sessions.

Section 14 hook surface:
  Callbacks receive ``(method: str, params: dict)`` for every incoming
  notification.  A constraint-accumulating loop can register for
  ``session/update``, ``ToolCall``, ``ToolCallUpdate``, and ``session/end``
  without this module knowing about that use case.
"""
import json
import logging
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

KIRO_CLI = "kiro-cli"

# How long to wait for an RPC response before raising TimeoutError.
DEFAULT_RPC_TIMEOUT = 30.0

# How long the client waits for the subprocess to die after kill().
TERMINATE_TIMEOUT = 3.0


class ACPError(Exception):
    """Raised when the server returns a JSON-RPC error object."""
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class ACPSession:
    """One persistent ``kiro-cli acp`` subprocess with JSON-RPC 2.0 framing.

    Usage::

        session = ACPSession()
        session.on("session/update", my_callback)   # (method, params) -> None
        session.start()
        result = session.call("initialize", {...})
        ...
        session.stop()

    The instance is not thread-safe for concurrent ``call()`` invocations, but
    notification callbacks are dispatched from the reader thread and may arrive
    at any time.  Callbacks must not call ``call()`` (deadlock).

    Parameters
    ----------
    engine:
        ``"v2"`` (default) or ``"v3"``.  Appended as ``--agent-engine <engine>``.
        Note: ``--trust-all-tools`` is incompatible with v3; use ``--trust-tools``
        or omit entirely.
    model:
        Forwarded as ``--model <model>``.  Pass ``None`` to omit.
    extra_args:
        Additional flags for the ``kiro-cli acp`` command.
    timeout:
        Default RPC response timeout in seconds.
    """

    def __init__(
        self,
        engine: str = "v2",
        model: str | None = None,
        extra_args: list[str] | None = None,
        timeout: float = DEFAULT_RPC_TIMEOUT,
    ):
        self._engine = engine
        self._model = model
        self._extra_args = extra_args or []
        self._default_timeout = timeout

        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None

        # Pending RPC calls: id → (result_holder, event)
        self._pending: dict[int, tuple[dict, threading.Event]] = {}
        self._pending_lock = threading.Lock()
        self._next_id = 1

        # Notification callbacks: method → [callable]
        self._callbacks: dict[str, list[Callable]] = {}
        self._callbacks_lock = threading.Lock()

        self._alive = False
        self._stop_event = threading.Event()

    # ── registration ─────────────────────────────────────────────────────────

    def on(self, method: str, callback: Callable[[str, dict], None]) -> None:
        """Register *callback* for incoming notifications matching *method*.

        ``callback(method, params)`` is called from the reader thread.
        Multiple callbacks per method are allowed; they fire in registration
        order.  Use ``"*"`` to receive every notification.
        """
        with self._callbacks_lock:
            self._callbacks.setdefault(method, []).append(callback)

    def off(self, method: str, callback: Callable) -> None:
        """Remove a previously registered callback."""
        with self._callbacks_lock:
            bucket = self._callbacks.get(method, [])
            try:
                bucket.remove(callback)
            except ValueError:
                pass

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Spawn the subprocess.  Must be called before ``call()``."""
        if self._alive:
            return
        cmd = [KIRO_CLI, "acp", f"--agent-engine={self._engine}"]
        if self._model:
            cmd.append(f"--model={self._model}")
        cmd.extend(self._extra_args)
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._alive = True
        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="acp-reader"
        )
        self._reader_thread.start()

    def stop(self) -> None:
        """Terminate the subprocess and clean up."""
        self._alive = False
        self._stop_event.set()
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=TERMINATE_TIMEOUT)
            except subprocess.TimeoutExpired:
                pass
            self._proc = None
        # Unblock any waiting callers with a closed error
        with self._pending_lock:
            for holder, event in self._pending.values():
                holder["error"] = ACPError(-32000, "connection closed")
                event.set()
            self._pending.clear()

    def restart(self) -> None:
        """Stop and restart the subprocess."""
        self.stop()
        self._alive = False
        self.start()

    @property
    def is_alive(self) -> bool:
        if not self._alive or not self._proc:
            return False
        return self._proc.poll() is None

    # ── RPC ──────────────────────────────────────────────────────────────────

    def call(
        self,
        method: str,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Send a JSON-RPC request and block until the response arrives.

        Returns the ``result`` value on success.  Raises ``ACPError`` on a
        server error response.  Raises ``TimeoutError`` if no response arrives
        within *timeout* seconds.  Raises ``RuntimeError`` if the subprocess
        is not running.
        """
        if not self.is_alive:
            raise RuntimeError("ACPSession not started or subprocess died")
        t = timeout if timeout is not None else self._default_timeout
        with self._pending_lock:
            rid = self._next_id
            self._next_id += 1
            holder: dict = {}
            event = threading.Event()
            self._pending[rid] = (holder, event)
        self._send({"jsonrpc": "2.0", "method": method,
                    "params": params or {}, "id": rid})
        if not event.wait(timeout=t):
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise TimeoutError(f"No response for {method!r} within {t}s")
        if "error" in holder:
            raise holder["error"]
        return holder.get("result")

    def notify(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        if not self.is_alive:
            raise RuntimeError("ACPSession not started or subprocess died")
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ── high-level helpers ────────────────────────────────────────────────────

    def initialize(
        self,
        client_name: str = "quarterdeck",
        client_version: str = "1.0",
    ) -> dict:
        """Perform the JSON-RPC handshake.  Returns the server capabilities."""
        return self.call("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {"name": client_name, "version": client_version},
        })

    def new_session(self, cwd: str | None = None, mcp_servers: list | None = None) -> str:
        """Create a new agent session.  Returns the session id."""
        result = self.call("session/new", {
            "cwd": cwd or str(Path.home()),
            "mcpServers": mcp_servers or [],
        })
        return result.get("sessionId", "")

    def prompt(self, session_id: str, text: str, timeout: float | None = None) -> None:
        """Send a user prompt.  Returns when the server acknowledges the request.

        Actual response chunks arrive as ``session/update`` notifications.
        """
        self.call("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}],
        }, timeout=timeout)

    def collect_response(
        self,
        session_id: str,
        prompt_text: str,
        timeout: float = DEFAULT_RPC_TIMEOUT,
    ) -> str:
        """Send a prompt and collect the full text response synchronously.

        Registers a temporary callback, sends the prompt, waits for the
        session to go idle (``session/update`` with ``sessionUpdate ==
        "done"`` or ``"error"``), then returns the concatenated text chunks.

        This is the one-shot query pattern used by ``acp_query.py``.
        """
        chunks: list[str] = []
        done = threading.Event()

        def _on_update(method: str, params: dict) -> None:
            update = params.get("update", {})
            su = update.get("sessionUpdate", "")
            if su == "agent_message_chunk":
                content = update.get("content", {})
                if content.get("type") == "text":
                    chunks.append(content.get("text", ""))
            elif su in ("done", "error", "end_turn"):
                done.set()

        self.on("session/update", _on_update)
        try:
            self.prompt(session_id, prompt_text, timeout=timeout)
            done.wait(timeout=timeout)
        finally:
            self.off("session/update", _on_update)

        return "".join(chunks).strip()

    # ── internals ─────────────────────────────────────────────────────────────

    def _send(self, obj: dict) -> None:
        if not self._proc or not self._proc.stdin:
            return
        try:
            self._proc.stdin.write(json.dumps(obj) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._alive = False

    def _dispatch_notification(self, method: str, params: dict) -> None:
        with self._callbacks_lock:
            specific = list(self._callbacks.get(method, []))
            wildcard = list(self._callbacks.get("*", []))
        for cb in specific + wildcard:
            try:
                cb(method, params)
            except Exception:
                log.exception("ACP callback %r raised", cb)

    def _reader_loop(self) -> None:
        """Read lines from the subprocess stdout and dispatch."""
        try:
            for raw in self._proc.stdout:
                if self._stop_event.is_set():
                    break
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.debug("ACP non-JSON: %r", raw[:120])
                    continue

                mid = msg.get("id")
                method = msg.get("method", "")

                if mid is not None:
                    # Response to a call()
                    with self._pending_lock:
                        entry = self._pending.pop(mid, None)
                    if entry:
                        holder, event = entry
                        if "error" in msg:
                            err = msg["error"]
                            holder["error"] = ACPError(
                                err.get("code", -1),
                                err.get("message", "unknown error"),
                                err.get("data"),
                            )
                        else:
                            holder["result"] = msg.get("result")
                        event.set()

                if method:
                    # Notification — dispatch to callbacks
                    self._dispatch_notification(method, msg.get("params", {}))

        except Exception:
            log.debug("ACP reader loop exited", exc_info=True)
        finally:
            # Only mark dead if the actual process has exited.
            # In tests, stdout may be an exhausted iterator while the mock
            # proc.poll() still returns None — don't flip _alive in that case.
            if self._proc and self._proc.poll() is not None:
                self._alive = False
            self._stop_event.set()
            # Unblock any waiters
            with self._pending_lock:
                for holder, event in self._pending.values():
                    holder.setdefault("error", ACPError(-32000, "connection closed"))
                    event.set()
                self._pending.clear()
