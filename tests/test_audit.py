"""The audit trail — what was done through this API, and by which device.

The point of these tests is not that a JSON line gets written. It is that the
three claims the log makes are true: that a request cannot avoid being recorded
by having been added later (the middleware, not per-endpoint calls), that the
token never lands in it, and that a record is small enough for two unrelated
processes to append to one file without a lock.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from backend import api, audit
from backend.api import app

client = TestClient(app, client=("127.0.0.1", 45678))


@pytest.fixture
def log(tmp_path):
    """An audit directory of its own, switched on, isolated from ~/.osa-kiro."""
    directory = tmp_path / "audit"
    directory.mkdir()
    flag = directory / "on"
    flag.touch()
    with patch.object(audit, "AUDIT_DIR", directory), \
         patch.object(audit, "AUDIT_FLAG", flag):
        yield directory


def records(directory, kind=""):
    out = []
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                if not kind or record.get("kind") == kind:
                    out.append(record)
    return out


class TestRequestRecord:
    def test_a_mutating_call_is_recorded_with_the_device_that_made_it(self, log):
        client.post("/api/settings", json={"audit-test-key": 1})
        written = records(log, "request")
        assert written, "a POST to the API must leave a record"
        entry = written[-1]
        assert entry["method"] == "POST"
        assert entry["path"] == "/api/settings"
        assert entry["status"] == 200
        # No per-device identity exists yet, so the source address and whether it
        # came over loopback is the honest answer. The shape is what a named
        # device token will slot into.
        assert entry["actor"] == {"host": "127.0.0.1", "via": "local"}
        assert entry["payload"] == {"audit-test-key": 1}

    def test_the_handler_still_gets_its_body(self, log):
        # The middleware reads the body before the handler does. Starlette caches
        # and replays it, but that is a property of the framework version rather
        # than of this code, so it is pinned here: if an upgrade breaks it, every
        # POST in the app silently starts seeing an empty payload.
        marker = f"audit-body-{time.time()}"
        r = client.post("/api/settings", json={"audit-body-probe": marker})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        saved = client.get("/api/settings").json()
        assert saved.get("audit-body-probe") == marker, \
            "the handler must still receive the body the middleware read"

    def test_a_poll_is_not_recorded(self, log):
        client.get("/api/sessions")
        assert records(log, "request") == [], \
            "a GET changes nothing, and poll traffic would bury what matters"

    def test_a_resize_is_not_recorded(self, log):
        # Mutating, but fired by the window rather than by a person.
        assert audit._should_record("POST", "/api/sessions/abc/resize", 200) is False

    def test_a_refused_request_is_recorded(self, log):
        # The only trace of a device trying an endpoint it has no token for. It
        # never reaches a handler, which is why this lives outside the auth
        # middleware rather than inside the endpoints.
        assert audit._should_record("GET", "/api/sessions", 401) is True
        assert audit._should_record("POST", "/api/dispatch", 403) is True

    def test_the_login_body_is_never_recorded(self, log):
        # It carries the token itself. Nothing about the request is worth that.
        assert audit._should_record("POST", "/login", 200) is False
        assert audit._should_record("POST", "/login", 401) is False


class TestRedactionAndSize:
    def test_the_token_is_never_written_down(self, log):
        audit.append("request", payload={
            "token": "deadbeef", "nested": {"password": "hunter2", "keep": "yes"},
            "authorization": "Bearer x",
        })
        entry = records(log)[0]
        assert entry["payload"]["token"] == audit.REDACTED
        assert entry["payload"]["nested"]["password"] == audit.REDACTED
        assert entry["payload"]["authorization"] == audit.REDACTED
        assert entry["payload"]["nested"]["keep"] == "yes", \
            "redaction must not swallow the record around it"

    def test_depth_collapses_containers_and_keeps_the_leaves(self, log):
        # The bug this pins: checking depth before type replaced every scalar in a
        # nested payload with an ellipsis, so a snapshot write recorded a kilobyte
        # of structure containing nothing. Verified against the real endpoint, not
        # invented — this is what /api/snapshots actually sends.
        deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": "leaf", "h": [1, 2]}}}}}}}
        audit.append("request", payload=deep)
        entry = records(log)[0]["payload"]
        node = entry
        for key in "abcde":
            node = node[key]
        # At the limit the container collapses to a summary...
        assert node["f"] == "{… 2 keys}"
        # ...but nothing above it lost its content on the way down.
        audit.append("request", payload={"x": {"y": {"z": "kept"}}})
        assert records(log)[-1]["payload"]["x"]["y"]["z"] == "kept"

    def test_a_whole_state_write_is_recorded_without_its_body(self, log):
        # The client sends its entire snapshot list on every change. That it
        # happened is the useful part; the body is unchanged rows around one edit.
        client.post("/api/snapshots", json={"snapshots": [{"id": 1, "sessions": []}]})
        entry = records(log, "request")[-1]
        assert entry["path"] == "/api/snapshots"
        assert "payload" not in entry, "the act is recorded, the state blob is not"

    def test_one_huge_value_is_clipped_not_truncated(self, log):
        # Per-value clipping handles the common case — a tool reading a large
        # file — without losing the arguments entirely.
        audit.append("tool", tool="fs_read", input={"path": "x" * 50_000})
        entry = records(log)[0]
        assert "truncated" not in entry
        assert len(entry["input"]["path"]) == audit.MAX_VALUE + 1  # + the ellipsis
        assert entry["input"]["path"].endswith("…")

    def test_a_record_stays_small_enough_to_append_atomically(self, log):
        # Two unrelated processes append to this file — the backend and the
        # postToolUse hook. A single write under PIPE_BUF will not interleave,
        # which is the only reason no cross-process lock is needed. Many clipped
        # values still add up past the cap, so the whole-record bound has to hold
        # after per-value clipping, not instead of it.
        audit.append("tool", tool="fs_read",
                     input={f"key{i}": "x" * 500 for i in range(40)})
        line = next(p for p in log.glob("*.jsonl")).read_text().splitlines()[0]
        assert len(line) <= audit.MAX_RECORD
        entry = json.loads(line)
        assert entry["truncated"] is True
        assert entry["tool"] == "fs_read", \
            "what happened must survive dropping the arguments it carried"

    def test_nothing_is_written_when_recording_is_off(self, log):
        audit.set_enabled(False)
        audit.append("request", path="/api/dispatch")
        assert records(log) == []
        audit.set_enabled(True)
        audit.append("request", path="/api/dispatch")
        assert len(records(log)) == 1

    def test_the_flag_file_is_the_runtime_switch(self, log):
        # One switch with two readers — this module and the shell hook — rather
        # than two switches that can disagree.
        with patch.object(audit, "read_settings", return_value={"audit": False}):
            audit.sync_flag()
            assert audit.enabled() is False
        with patch.object(audit, "read_settings", return_value={}):
            audit.sync_flag()
            assert audit.enabled() is True, "recording defaults to on"


class TestDecisionRecord:
    SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_a_decision_says_which_tool_it_allowed(self, log, tmp_path):
        approvals = tmp_path / "approvals"
        approvals.mkdir()
        (approvals / f"{self.SID}-r1").write_text(
            f'{self.SID}:r1:execute_bash:{{"command":"rm -rf /tmp/x"}}')
        with patch.object(api.tmux, "APPROVALS_DIR", approvals):
            r = client.post("/api/approvals/r1/allow", json={"session_id": self.SID})
        assert r.json() == {"ok": True}
        entry = records(log, "decision")[0]
        assert entry["allow"] is True
        assert entry["how"] == "api"
        assert entry["tool"] == "execute_bash"
        # Read before the answer is sent: answering retires the request, and with
        # it the only record of what was being held.
        assert entry["input"] == {"command": "rm -rf /tmp/x"}
        assert entry["actor"]["via"] == "local"

    def test_one_click_denying_nine_calls_is_not_nine_decisions(self, log, tmp_path):
        approvals = tmp_path / "approvals"
        approvals.mkdir()
        for i in range(3):
            (approvals / f"{self.SID}-r{i}").write_text(
                f"{self.SID}:r{i}:fs_write:{{}}")
        with patch.object(api.tmux, "APPROVALS_DIR", approvals):
            client.post("/api/approvals/dismiss-all")
        written = records(log, "decision")
        assert len(written) == 3
        assert all(e["how"] == "dismiss-all" for e in written), \
            "a blanket dismissal must not read as three considered decisions"

    def test_releasing_the_gate_is_recorded_as_such(self, log, tmp_path):
        held = [{"session_id": self.SID, "request_id": "r1", "tool_name": "fs_write",
                 "tool_input": {}, "age": 1.0}]
        with patch.object(api.tmux, "GATES_DIR", tmp_path), \
             patch.object(api.tmux, "pending_approvals", return_value=held), \
             patch.object(api.tmux, "respond_approval", return_value=True):
            api.tmux.set_gate(self.SID, True)
            client.post(f"/api/sessions/{self.SID}/gate", json={"enabled": False})
        entry = records(log, "decision")[0]
        assert entry["how"] == "gate-off", \
            "allowed because gating was switched off, not because anyone looked"


class TestReadAndRetention:
    def test_the_endpoint_returns_newest_first(self, log):
        for i in range(3):
            audit.append("request", path=f"/api/{i}")
        r = client.get("/api/audit?limit=10").json()
        assert [e["path"] for e in r["records"]][:3] == ["/api/2", "/api/1", "/api/0"]
        assert r["enabled"] is True

    def test_filtering_by_kind_and_session(self, log):
        audit.append("tool", session="s1", tool="fs_read")
        audit.append("tool", session="s2", tool="fs_write")
        audit.append("request", path="/api/x")
        assert len(audit.read(kind="tool")) == 2
        assert len(audit.read(session="s1")) == 1

    def test_retention_deletes_whole_days(self, log):
        (log / "2020-01-01.jsonl").write_text('{"kind":"request"}\n')
        (log / "2999-01-01.jsonl").write_text('{"kind":"request"}\n')
        assert audit.sweep(days=30) == 1
        assert not (log / "2020-01-01.jsonl").exists()
        assert (log / "2999-01-01.jsonl").exists()

    def test_the_flag_file_is_not_mistaken_for_a_day(self, log):
        # The switch lives in the same directory as the records. Sweeping records
        # must not switch auditing off as a side effect.
        (log / "2020-01-01.jsonl").write_text('{"kind":"request"}\n')
        assert audit.sweep(days=30) == 1
        assert (log / "on").exists()
        assert audit.enabled() is True


class TestPostToolUseHook:
    """The hook itself, run as kiro-cli runs it: a shell command fed the real
    payload shape, measured against kiro-cli 2.14.2.
    """

    PAYLOAD = json.dumps({
        "hook_event_name": "postToolUse",
        "cwd": "/Users/someone/project",
        "tool_name": "execute_bash",
        "tool_input": {"command": "echo hello"},
        "tool_response": {"success": True,
                          "result": [{"exit_status": "0", "stdout": "hello\n",
                                      "stderr": ""}]},
    })

    SID = "0627f932-da99-4c49-8b5b-95196e5be710"

    def _run(self, home, payload=None):
        env = {**os.environ, "HOME": str(home), "KIRO_SESSION_ID": self.SID}
        proc = subprocess.run(
            ["sh", "-c", audit.HOOK_COMMAND], env=env, text=True,
            input=payload if payload is not None else self.PAYLOAD,
            capture_output=True, timeout=30)
        return proc

    def _install(self, home):
        state = home / ".osa-kiro"
        state.mkdir(parents=True, exist_ok=True)
        with patch.object(audit, "STATE_DIR", state), \
             patch.object(audit, "HOOK_SCRIPT", state / "audit-hook.py"):
            assert audit.write_hook_script() is True
        return state

    def test_the_hook_records_a_tool_call(self, tmp_path):
        state = self._install(tmp_path)
        (state / "audit" / "on").parent.mkdir(parents=True, exist_ok=True)
        (state / "audit" / "on").touch()

        proc = self._run(tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "{}", "a hook must never wedge kiro-cli"

        written = records(state / "audit", "tool")
        assert written, "the hook must leave a record of the call"
        entry = written[0]
        assert entry["session"] == self.SID, \
            "the id comes from the environment — no hook payload carries one"
        assert entry["tool"] == "execute_bash"
        assert entry["input"] == {"command": "echo hello"}
        assert entry["ok"] is True
        assert entry["cwd"] == "/Users/someone/project"

    def test_the_hook_is_silent_when_recording_is_off(self, tmp_path):
        state = self._install(tmp_path)
        proc = self._run(tmp_path)
        assert proc.returncode == 0
        assert proc.stdout.strip() == "{}"
        assert not (state / "audit").exists(), \
            "switched off, the hook is one [ -f ] and out"

    def test_a_failed_tool_call_is_recorded_as_failed(self, tmp_path):
        state = self._install(tmp_path)
        (state / "audit").mkdir(parents=True, exist_ok=True)
        (state / "audit" / "on").touch()
        payload = json.dumps({
            "hook_event_name": "postToolUse", "cwd": "/x",
            "tool_name": "fs_write", "tool_input": {"path": "/etc/passwd"},
            "tool_response": {"success": False, "result": "permission denied"},
        })
        proc = self._run(tmp_path, payload)
        assert proc.returncode == 0
        entry = records(state / "audit", "tool")[0]
        assert entry["ok"] is False
        assert entry["tool"] == "fs_write"

    def test_junk_on_stdin_does_not_break_the_hook(self, tmp_path):
        state = self._install(tmp_path)
        (state / "audit").mkdir(parents=True, exist_ok=True)
        (state / "audit" / "on").touch()
        proc = self._run(tmp_path, "not json at all")
        assert proc.returncode == 0, "kiro-cli must never see a hook fail"
        assert proc.stdout.strip() == "{}"

    def test_a_missing_script_is_not_an_error(self, tmp_path):
        # The command references a file Quarterdeck writes. If it is not there, the hook
        # has to be a no-op rather than a failure.
        (tmp_path / ".osa-kiro" / "audit").mkdir(parents=True)
        (tmp_path / ".osa-kiro" / "audit" / "on").touch()
        proc = self._run(tmp_path)
        assert proc.returncode == 0
        assert proc.stdout.strip() == "{}"

    def test_the_script_is_rewritten_when_it_drifts(self, tmp_path):
        state = self._install(tmp_path)
        script = state / "audit-hook.py"
        script.write_text("# tampered with\n")
        with patch.object(audit, "STATE_DIR", state), \
             patch.object(audit, "HOOK_SCRIPT", script):
            audit.write_hook_script()
        assert "tampered" not in script.read_text(), \
            "rewritten on every start, so the logic cannot drift from source"
