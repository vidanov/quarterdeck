"""Unit tests for backend/acp_session.py.

Tests the ACPSession internals directly — no real kiro-cli subprocess.
The reader loop, response correlation, and notification dispatch are all
tested by injecting messages into the internal state, rather than through
a full subprocess round-trip.
"""
import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.acp_session import ACPSession, ACPError


# ── helpers ───────────────────────────────────────────────────────────────────

def _live_session(**kwargs) -> ACPSession:
    """Return an ACPSession with a mock proc that appears alive."""
    proc = MagicMock()
    proc.poll.return_value = None   # alive
    proc.stdin = MagicMock()
    proc.stdout = iter([])          # no lines — reader exits cleanly
    proc.stderr = MagicMock()
    sess = ACPSession(**kwargs)
    with patch("backend.acp_session.subprocess.Popen", return_value=proc):
        sess.start()
    return sess


def _inject_response(sess: ACPSession, rid: int, result: dict | None = None,
                     error: dict | None = None) -> None:
    """Simulate a server response arriving for pending request *rid*."""
    msg: dict = {"jsonrpc": "2.0", "id": rid}
    if error:
        msg["error"] = error
    else:
        msg["result"] = result or {}

    with sess._pending_lock:
        entry = sess._pending.get(rid)
    if entry:
        holder, event = entry
        if "error" in msg:
            e = msg["error"]
            holder["error"] = ACPError(e.get("code", -1), e.get("message", ""))
        else:
            holder["result"] = msg.get("result")
        event.set()


def _send_notification(sess: ACPSession, method: str, params: dict) -> None:
    """Directly dispatch a notification as if it arrived from the server."""
    sess._dispatch_notification(method, params)


# ── handshake / basic call ────────────────────────────────────────────────────

class TestHandshake:
    def test_initialize_returns_server_capabilities(self):
        sess = _live_session(timeout=1)
        caps = {"protocolVersion": 1, "serverInfo": {"name": "kiro"}}

        # Fire the response from a background thread (as the reader would)
        def _respond():
            time.sleep(0.01)
            _inject_response(sess, 1, result=caps)

        threading.Thread(target=_respond, daemon=True).start()
        result = sess.initialize()
        assert result["protocolVersion"] == 1

    def test_initialize_error_raises_acp_error(self):
        sess = _live_session(timeout=1)

        def _respond():
            time.sleep(0.01)
            _inject_response(sess, 1, error={"code": -32001, "message": "not ready"})

        threading.Thread(target=_respond, daemon=True).start()
        with pytest.raises(ACPError) as exc_info:
            sess.initialize()
        assert exc_info.value.code == -32001
        assert "not ready" in str(exc_info.value)

    def test_call_raises_when_not_started(self):
        sess = ACPSession()
        with pytest.raises(RuntimeError, match="not started"):
            sess.call("initialize")

    def test_call_raises_timeout_when_no_response(self):
        sess = _live_session(timeout=0.1)
        with pytest.raises(TimeoutError):
            sess.call("silent_method")


# ── id correlation ────────────────────────────────────────────────────────────

class TestIdCorrelation:
    def test_responses_are_matched_by_id(self):
        """Each call receives the response with its own id, regardless of order."""
        sess = _live_session(timeout=2)
        results: dict = {}

        def _c1():
            results["a"] = sess.call("method_a", timeout=2)

        def _c2():
            time.sleep(0.01)
            results["b"] = sess.call("method_b", timeout=2)

        t1 = threading.Thread(target=_c1)
        t2 = threading.Thread(target=_c2)
        t1.start(); t2.start()
        time.sleep(0.05)

        # Respond in reverse order
        _inject_response(sess, 2, result={"for": "b"})
        _inject_response(sess, 1, result={"for": "a"})

        t1.join(1); t2.join(1)
        assert results.get("a") == {"for": "a"}
        assert results.get("b") == {"for": "b"}


# ── notification dispatch ─────────────────────────────────────────────────────

class TestNotificationDispatch:
    def test_notification_reaches_registered_callback(self):
        received: list[tuple] = []
        sess = _live_session()
        sess.on("session/update", lambda m, p: received.append((m, p)))

        _send_notification(sess, "session/update", {"update": {"sessionUpdate": "done"}})

        assert len(received) == 1
        assert received[0][0] == "session/update"

    def test_wildcard_callback_receives_all_notifications(self):
        seen: list[str] = []
        sess = _live_session()
        sess.on("*", lambda m, p: seen.append(m))

        _send_notification(sess, "foo", {})
        _send_notification(sess, "bar", {})

        assert "foo" in seen
        assert "bar" in seen

    def test_off_removes_callback(self):
        fired: list[bool] = []
        cb = lambda m, p: fired.append(True)
        sess = _live_session()
        sess.on("session/update", cb)
        sess.off("session/update", cb)

        _send_notification(sess, "session/update", {})
        assert not fired

    def test_callback_exception_does_not_propagate(self):
        """A crashing callback must not kill the reader or affect other callbacks."""
        good_received: list[bool] = []
        sess = _live_session()
        sess.on("ev", lambda m, p: (_ for _ in ()).throw(RuntimeError("boom")))
        sess.on("ev", lambda m, p: good_received.append(True))

        _send_notification(sess, "ev", {})
        assert good_received  # second callback still fired


# ── lifecycle ─────────────────────────────────────────────────────────────────

class TestLifecycle:
    def test_stop_unblocks_pending_call(self):
        """stop() must release a caller blocked waiting for a response."""
        sess = _live_session(timeout=5)
        exc_holder: list = []

        def _call():
            try:
                sess.call("will_hang", timeout=5)
            except (ACPError, RuntimeError) as e:
                exc_holder.append(e)

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        time.sleep(0.02)
        sess.stop()
        t.join(timeout=0.3)
        assert not t.is_alive(), "call() should have unblocked after stop()"
        assert exc_holder

    def test_is_alive_false_after_stop(self):
        sess = _live_session()
        assert sess.is_alive
        sess.stop()
        assert not sess.is_alive


# ── collect_response ──────────────────────────────────────────────────────────

class TestCollectResponse:
    def test_collect_response_assembles_chunks(self):
        sess = _live_session(timeout=2)

        def _serve():
            time.sleep(0.01)
            # Respond to initialize (id=1), session/new (id=2), session/prompt (id=3)
            _inject_response(sess, 1, result={"protocolVersion": 1})
            time.sleep(0.01)
            _inject_response(sess, 2, result={"sessionId": "s1"})
            time.sleep(0.01)
            # Prompt ack + chunks + done
            _inject_response(sess, 3, result={})
            time.sleep(0.01)
            _send_notification(sess, "session/update", {
                "update": {"sessionUpdate": "agent_message_chunk",
                           "content": {"type": "text", "text": "Hello"}}})
            _send_notification(sess, "session/update", {
                "update": {"sessionUpdate": "agent_message_chunk",
                           "content": {"type": "text", "text": " world"}}})
            _send_notification(sess, "session/update", {
                "update": {"sessionUpdate": "done"}})

        threading.Thread(target=_serve, daemon=True).start()
        sess.initialize()
        sid = sess.new_session()
        text = sess.collect_response(sid, "hi", timeout=2)
        assert text == "Hello world"

    def test_collect_response_partial_on_no_done(self):
        sess = _live_session(timeout=2)

        def _serve():
            time.sleep(0.01)
            _inject_response(sess, 1, result={"protocolVersion": 1})
            time.sleep(0.01)
            _inject_response(sess, 2, result={"sessionId": "s1"})
            time.sleep(0.01)
            _inject_response(sess, 3, result={})
            time.sleep(0.01)
            _send_notification(sess, "session/update", {
                "update": {"sessionUpdate": "agent_message_chunk",
                           "content": {"type": "text", "text": "partial"}}})
            # no "done" — timeout fires

        threading.Thread(target=_serve, daemon=True).start()
        sess.initialize()
        sid = sess.new_session()
        text = sess.collect_response(sid, "hi", timeout=0.2)
        assert "partial" in text


# ── section 14 hook surface ───────────────────────────────────────────────────

class TestToolCallNotification:
    def test_tool_call_notification_reaches_callback(self):
        """ToolCall notifications reach registered callbacks — section 14 entry point."""
        tool_calls: list[dict] = []
        sess = _live_session()
        sess.on("ToolCall", lambda m, p: tool_calls.append(p))

        _send_notification(sess, "ToolCall",
                           {"toolName": "fs_read", "input": {"path": "/tmp/x"}})

        assert len(tool_calls) == 1
        assert tool_calls[0]["toolName"] == "fs_read"

    def test_multiple_notification_types_all_dispatched(self):
        """ToolCall, ToolCallUpdate, and session/update all reach their callbacks."""
        received: dict[str, list] = {}
        sess = _live_session()
        for method in ("ToolCall", "ToolCallUpdate", "session/update"):
            received[method] = []
            sess.on(method, lambda m, p, k=method: received[k].append(p))

        _send_notification(sess, "ToolCall", {"toolName": "a"})
        _send_notification(sess, "ToolCallUpdate", {"progress": 50})
        _send_notification(sess, "session/update", {"update": {}})

        assert len(received["ToolCall"]) == 1
        assert len(received["ToolCallUpdate"]) == 1
        assert len(received["session/update"]) == 1
