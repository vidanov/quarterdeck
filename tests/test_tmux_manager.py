"""Tests for the tmux session manager's pure logic.

Process-tree walking, correlation, and state persistence are covered here
without touching tmux. Real spawn/send/capture behaviour is verified against a
live kiro-cli session instead — see docs/ARCHITECTURE-pty-api.md.
"""
import json
import os
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend import tmux_manager as tm


class TestProcessTree:
    def test_direct_child_is_descendant(self):
        tree = {200: 100, 100: 1}
        assert tm._is_descendant(200, 100, tree)

    def test_grandchild_is_descendant(self):
        tree = {300: 200, 200: 100, 100: 1}
        assert tm._is_descendant(300, 100, tree)

    def test_pid_is_its_own_ancestor(self):
        assert tm._is_descendant(100, 100, {100: 1})

    def test_sibling_is_not_descendant(self):
        tree = {200: 1, 100: 1}
        assert not tm._is_descendant(200, 100, tree)

    def test_cycle_does_not_hang(self):
        # Defensive: a malformed ps snapshot must not spin forever.
        assert not tm._is_descendant(200, 999, {200: 100, 100: 200})

    def test_unknown_pid_is_not_descendant(self):
        assert not tm._is_descendant(0, 100, {})

    def test_real_tree_has_launchd(self):
        tree = tm._process_tree()
        assert tree, "ps returned nothing"
        assert tm._is_descendant(os.getpid(), 1, tree)


class TestCorrelate:
    def _write_lock(self, tmp: Path, session_id: str, pid: int, started_at: str):
        (tmp / f"{session_id}.lock").write_text(
            json.dumps({"pid": pid, "started_at": started_at})
        )

    def test_finds_descendant_lock(self, tmp_path):
        self._write_lock(tmp_path, "aaa", 500, "2026-01-01T00:00:00Z")
        with patch.object(tm, "SESSIONS_DIR", tmp_path), \
             patch.object(tm, "_process_tree", return_value={500: 400, 400: 1}):
            assert tm._correlate(400, set()) == "aaa"

    def test_ignores_unrelated_lock(self, tmp_path):
        self._write_lock(tmp_path, "bbb", 500, "2026-01-01T00:00:00Z")
        with patch.object(tm, "SESSIONS_DIR", tmp_path), \
             patch.object(tm, "_process_tree", return_value={500: 1}):
            assert tm._correlate(400, set()) is None

    def test_prefers_earliest_started_lock(self, tmp_path):
        # Both descend from the pane pid; the main session locks first, the
        # subagent later. Earliest wins.
        self._write_lock(tmp_path, "main", 500, "2026-01-01T00:00:01Z")
        self._write_lock(tmp_path, "subagent", 600, "2026-01-01T00:00:09Z")
        with patch.object(tm, "SESSIONS_DIR", tmp_path), \
             patch.object(tm, "_process_tree",
                          return_value={500: 400, 600: 500, 400: 1}):
            assert tm._correlate(400, set()) == "main"

    def test_skips_claimed_ids(self, tmp_path):
        self._write_lock(tmp_path, "taken", 500, "2026-01-01T00:00:01Z")
        self._write_lock(tmp_path, "free", 600, "2026-01-01T00:00:09Z")
        with patch.object(tm, "SESSIONS_DIR", tmp_path), \
             patch.object(tm, "_process_tree",
                          return_value={500: 400, 600: 400, 400: 1}):
            assert tm._correlate(400, {"taken"}) == "free"

    def test_tolerates_half_written_lock(self, tmp_path):
        (tmp_path / "partial.lock").write_text('{"pid":')
        self._write_lock(tmp_path, "good", 500, "2026-01-01T00:00:00Z")
        with patch.object(tm, "SESSIONS_DIR", tmp_path), \
             patch.object(tm, "_process_tree", return_value={500: 400, 400: 1}):
            assert tm._correlate(400, set()) == "good"

    def test_no_locks_at_all(self, tmp_path):
        with patch.object(tm, "SESSIONS_DIR", tmp_path), \
             patch.object(tm, "_process_tree", return_value={}):
            assert tm._correlate(400, set()) is None


class TestState:
    def test_missing_file_gives_empty_state(self, tmp_path):
        with patch.object(tm, "MANAGED_FILE", tmp_path / "nope.json"):
            assert tm.load_state() == {"managed": {}, "pending": {}}

    def test_corrupt_file_gives_empty_state(self, tmp_path):
        path = tmp_path / "managed.json"
        path.write_text("{not json")
        with patch.object(tm, "MANAGED_FILE", path):
            assert tm.load_state() == {"managed": {}, "pending": {}}

    def test_partial_state_is_filled_in(self, tmp_path):
        path = tmp_path / "managed.json"
        path.write_text(json.dumps({"managed": {"x": {"tmux": "kiro-x"}}}))
        with patch.object(tm, "MANAGED_FILE", path):
            state = tm.load_state()
        assert state["managed"] == {"x": {"tmux": "kiro-x"}}
        assert state["pending"] == {}

    def test_roundtrip(self, tmp_path):
        path = tmp_path / "sub" / "managed.json"
        state = {"managed": {"abc": {"tmux": "kiro-abc"}}, "pending": {}}
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "STATE_DIR", path.parent):
            tm.save_state(state)
            assert tm.load_state() == state

    def test_save_leaves_no_temp_file(self, tmp_path):
        path = tmp_path / "managed.json"
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "STATE_DIR", tmp_path):
            tm.save_state({"managed": {}, "pending": {}})
        assert [p.name for p in tmp_path.iterdir()] == ["managed.json"]


class TestNaming:
    def test_tmux_name(self):
        assert tm.tmux_name("abc-123") == "kiro-abc-123"

    def test_attach_command(self):
        assert tm.attach_command("abc") == "tmux attach -t kiro-abc"

    def test_real_resolves_home(self):
        assert tm._real("~").startswith("/")


class TestGuards:
    def test_send_text_without_session(self):
        with patch.object(tm, "list_tmux_sessions", return_value=[]):
            r = tm.send_text("ghost", "hi")
        assert not r["ok"]
        assert "No tmux session" in r["error"]

    def test_send_key_without_session(self):
        with patch.object(tm, "list_tmux_sessions", return_value=[]):
            assert not tm.send_key("ghost", "y")["ok"]

    def test_send_text_on_dead_pane(self):
        with patch.object(tm, "list_tmux_sessions", return_value=["kiro-x"]), \
             patch.object(tm, "pane_dead", return_value=True):
            r = tm.send_text("x", "hi")
        assert not r["ok"]
        assert "exited" in r["error"]

    def test_capture_without_session_is_empty(self):
        with patch.object(tm, "list_tmux_sessions", return_value=[]):
            assert tm.capture("ghost") == ""

    def test_spawn_rejects_missing_dir(self, tmp_path):
        r = tm.spawn(str(tmp_path / "does-not-exist"))
        assert not r["ok"]
        assert "not found" in r["error"]

    def test_kill_unmanaged(self, tmp_path):
        with patch.object(tm, "MANAGED_FILE", tmp_path / "m.json"), \
             patch.object(tm, "list_tmux_sessions", return_value=[]):
            assert not tm.kill("ghost")["ok"]

    def test_resume_refuses_already_managed(self, tmp_path):
        with patch.object(tm, "MANAGED_FILE", tmp_path / "m.json"), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-abc"]):
            r = tm.spawn(str(tmp_path), resume_id="abc")
        assert not r["ok"]
        assert "already managed" in r["error"]


class TestPendingOwners:
    """A pending spawn whose session already exists on disk must be recognised.

    Otherwise the listing shows the same agent twice — a `starting` placeholder
    and a `foreign` session it does not know it owns.
    """

    def _state(self, tmp_path, pending, managed=None):
        path = tmp_path / "managed.json"
        path.write_text(json.dumps({"managed": managed or {}, "pending": pending}))
        return path

    def _lock(self, dir_, session_id, pid, started_at):
        (dir_ / f"{session_id}.lock").write_text(
            json.dumps({"pid": pid, "started_at": started_at}))

    def test_no_pendings_is_empty(self, tmp_path):
        path = self._state(tmp_path, {})
        with patch.object(tm, "MANAGED_FILE", path):
            assert tm.pending_owners() == {}

    def test_matches_pending_to_its_session(self, tmp_path):
        locks = tmp_path / "locks"
        locks.mkdir()
        self._lock(locks, "sess-a", 500, "2026-01-01T00:00:00Z")
        path = self._state(tmp_path, {"n1": {"tmux": "osa-pending-n1", "root_pid": 100}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "SESSIONS_DIR", locks), \
             patch.object(tm, "_process_tree", return_value={500: 100, 100: 1}):
            assert tm.pending_owners() == {"n1": "sess-a"}

    def test_unrelated_session_is_not_claimed(self, tmp_path):
        locks = tmp_path / "locks"
        locks.mkdir()
        self._lock(locks, "someone-else", 500, "2026-01-01T00:00:00Z")
        path = self._state(tmp_path, {"n1": {"tmux": "osa-pending-n1", "root_pid": 100}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "SESSIONS_DIR", locks), \
             patch.object(tm, "_process_tree", return_value={500: 999, 999: 1, 100: 1}):
            assert tm.pending_owners() == {}

    def test_two_pendings_do_not_claim_one_session(self, tmp_path):
        """The bug this guards: both nonces resolving to the same id would let
        one agent be hidden twice and another not at all."""
        locks = tmp_path / "locks"
        locks.mkdir()
        self._lock(locks, "shared", 500, "2026-01-01T00:00:00Z")
        path = self._state(tmp_path, {
            "n1": {"tmux": "osa-pending-n1", "root_pid": 100},
            "n2": {"tmux": "osa-pending-n2", "root_pid": 100},
        })
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "SESSIONS_DIR", locks), \
             patch.object(tm, "_process_tree", return_value={500: 100, 100: 1}):
            owners = tm.pending_owners()
        assert list(owners) == ["n1"]
        assert owners["n1"] == "shared"

    def test_already_managed_session_is_not_reclaimed(self, tmp_path):
        locks = tmp_path / "locks"
        locks.mkdir()
        self._lock(locks, "sess-a", 500, "2026-01-01T00:00:00Z")
        path = self._state(
            tmp_path,
            {"n1": {"tmux": "osa-pending-n1", "root_pid": 100}},
            managed={"sess-a": {"tmux": "kiro-sess-a"}},
        )
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "SESSIONS_DIR", locks), \
             patch.object(tm, "_process_tree", return_value={500: 100, 100: 1}):
            assert tm.pending_owners() == {}


class TestReapPendings:
    """A pending entry outlives the thread that would resolve it if the backend
    dies first, leaving a `starting` card no UI action can remove."""

    def _state(self, tmp_path, pending):
        path = tmp_path / "managed.json"
        path.write_text(json.dumps({"managed": {}, "pending": pending}))
        return path

    def test_drops_pending_whose_tmux_is_gone(self, tmp_path):
        path = self._state(tmp_path, {"n1": {"tmux": "osa-pending-n1"}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "STATE_DIR", tmp_path), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-other"]):
            assert tm.reap_pendings() == ["n1"]
            assert tm.load_state()["pending"] == {}

    def test_keeps_pending_whose_tmux_is_alive(self, tmp_path):
        path = self._state(tmp_path, {"n1": {"tmux": "osa-pending-n1"}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "STATE_DIR", tmp_path), \
             patch.object(tm, "list_tmux_sessions", return_value=["osa-pending-n1"]):
            assert tm.reap_pendings() == []
            assert "n1" in tm.load_state()["pending"]

    def test_no_pendings_does_not_call_tmux(self, tmp_path):
        path = self._state(tmp_path, {})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions") as listed:
            assert tm.reap_pendings() == []
            listed.assert_not_called()


class TestCancelPending:
    def _state(self, tmp_path, pending):
        path = tmp_path / "managed.json"
        path.write_text(json.dumps({"managed": {}, "pending": pending}))
        return path

    def test_unknown_nonce_is_reported(self, tmp_path):
        path = self._state(tmp_path, {})
        with patch.object(tm, "MANAGED_FILE", path):
            result = tm.cancel_pending("nope")
        assert result["ok"] is False

    def test_kills_live_tmux_and_drops_entry(self, tmp_path):
        path = self._state(tmp_path, {"n1": {"tmux": "osa-pending-n1"}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "STATE_DIR", tmp_path), \
             patch.object(tm, "list_tmux_sessions", return_value=["osa-pending-n1"]), \
             patch.object(tm, "_tmux") as run:
            result = tm.cancel_pending("n1")
        assert result["killed_tmux"] is True
        assert run.call_args[0][:2] == ("kill-session", "-t")
        with patch.object(tm, "MANAGED_FILE", path):
            assert tm.load_state()["pending"] == {}

    def test_dead_tmux_still_drops_entry(self, tmp_path):
        path = self._state(tmp_path, {"n1": {"tmux": "osa-pending-n1"}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "STATE_DIR", tmp_path), \
             patch.object(tm, "list_tmux_sessions", return_value=[]), \
             patch.object(tm, "_tmux") as run:
            result = tm.cancel_pending("n1")
        assert result["killed_tmux"] is False
        run.assert_not_called()
        with patch.object(tm, "MANAGED_FILE", path):
            assert tm.load_state()["pending"] == {}


class TestResize:
    """Geometry is clamped before it reaches tmux, which accepts absurd sizes."""

    def test_unmanaged_session_is_refused(self):
        with patch.object(tm, "session_exists", return_value=False):
            assert tm.resize("abc", 100, 30)["ok"] is False

    def test_clamps_out_of_range_geometry(self):
        with patch.object(tm, "session_exists", return_value=True), \
             patch.object(tm, "geometry", return_value=(1, 1)), \
             patch.object(tm, "_tmux") as run:
            result = tm.resize("abc", 99999, 1)
        assert result["cols"] == tm.MAX_COLS
        assert result["rows"] == tm.MIN_ROWS
        assert "-x" in run.call_args[0] and "-y" in run.call_args[0]

    def test_unchanged_geometry_skips_the_tmux_call(self):
        """A redraw per poll would flicker the TUI for no reason."""
        with patch.object(tm, "session_exists", return_value=True), \
             patch.object(tm, "geometry", return_value=(100, 30)), \
             patch.object(tm, "_tmux") as run:
            result = tm.resize("abc", 100, 30)
        assert result["changed"] is False
        run.assert_not_called()

    def test_geometry_parses_tmux_output(self):
        with patch.object(tm, "session_exists", return_value=True), \
             patch.object(tm, "_tmux", return_value="180x50\n"):
            assert tm.geometry("abc") == (180, 50)

    def test_geometry_tolerates_junk(self):
        with patch.object(tm, "session_exists", return_value=True), \
             patch.object(tm, "_tmux", return_value="not-a-size"):
            assert tm.geometry("abc") is None
