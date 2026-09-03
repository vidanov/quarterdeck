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

from backend import config
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
            assert tm.load_state() == {"managed": {}, "pending": {}, "unclaimed": {}}

    def test_corrupt_file_gives_empty_state(self, tmp_path):
        path = tmp_path / "managed.json"
        path.write_text("{not json")
        with patch.object(tm, "MANAGED_FILE", path):
            assert tm.load_state() == {"managed": {}, "pending": {}, "unclaimed": {}}

    def test_partial_state_is_filled_in(self, tmp_path):
        path = tmp_path / "managed.json"
        path.write_text(json.dumps({"managed": {"x": {"tmux": "kiro-x"}}}))
        with patch.object(tm, "MANAGED_FILE", path):
            state = tm.load_state()
        assert state["managed"] == {"x": {"tmux": "kiro-x"}}
        assert state["pending"] == {}

    def test_roundtrip(self, tmp_path):
        path = tmp_path / "sub" / "managed.json"
        state = {"managed": {"abc": {"tmux": "kiro-abc"}}, "pending": {},
                 "unclaimed": {}}
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


class TestServerConfig:
    """Quarterdeck must not cold-start a tmux server under the user's config.

    ~/.tmux.conf ending in tpm with `@continuum-restore 'on'` turns a cold
    start into a fleet resurrection: one dispatch brought back 38 sessions from
    an eight-day-old tmux-resurrect snapshot and re-ran every saved pane
    command. Passing our own `-f` is what stops it, so the flag itself is the
    thing worth asserting.
    """

    def test_every_call_carries_our_server_config(self):
        argv = config.tmux_base_argv()
        assert argv[0] == "tmux"
        assert argv[1] == "-f"
        assert argv[2] == str(config.TMUX_CONF)

    def test_tmux_prepends_the_config_flag(self):
        with patch.object(tm.subprocess, "run") as run, \
             patch.object(tm, "_note_cold_start"):
            run.return_value = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            tm._tmux("list-sessions")
        sent = run.call_args[0][0]
        assert sent[:2] == ["tmux", "-f"]
        assert sent[-1] == "list-sessions"

    def test_config_loads_no_plugins(self):
        """tpm is the whole mechanism — the file must never grow a plugin line."""
        directives = [ln.strip() for ln in config.TMUX_CONF_BODY.splitlines()
                      if ln.strip() and not ln.lstrip().startswith("#")]
        assert not [d for d in directives if "tpm" in d or "@plugin" in d
                    or d.startswith("run") or "source-file" in d]
        assert "set -g @continuum-restore 'off'" in directives

    def test_written_config_is_refreshed_when_stale(self, tmp_path, monkeypatch):
        conf = tmp_path / "tmux.conf"
        conf.write_text("set -g @continuum-restore 'on'\n")  # a stale copy
        monkeypatch.setattr(config, "TMUX_CONF", conf)
        monkeypatch.setattr(config, "TMUX_CONF_MANAGED", True)
        assert config.tmux_base_argv() == ["tmux", "-f", str(conf)]
        assert conf.read_text() == config.TMUX_CONF_BODY

    def test_user_supplied_config_is_never_rewritten(self, tmp_path, monkeypatch):
        conf = tmp_path / "mine.conf"
        conf.write_text("set -g mouse off\n")
        monkeypatch.setattr(config, "TMUX_CONF", conf)
        monkeypatch.setattr(config, "TMUX_CONF_MANAGED", False)
        assert config.tmux_base_argv() == ["tmux", "-f", str(conf)]
        assert conf.read_text() == "set -g mouse off\n"

    def test_opting_out_passes_no_config(self, monkeypatch):
        monkeypatch.setattr(config, "TMUX_CONF", None)
        assert config.tmux_base_argv() == ["tmux"]

    def test_missing_user_config_is_not_invented(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "TMUX_CONF", tmp_path / "absent.conf")
        monkeypatch.setattr(config, "TMUX_CONF_MANAGED", False)
        assert config.tmux_base_argv() == ["tmux"]


def _write_state(tmp_path, managed=None, pending=None, unclaimed=None):
    path = tmp_path / "managed.json"
    path.write_text(json.dumps({
        "managed": managed or {}, "pending": pending or {},
        "unclaimed": unclaimed or {},
    }))
    return path


class TestBoundedAdoption:
    """reconcile() adopts stray kiro-* sessions — but not a whole restored fleet.

    tmux-continuum brought back 38 sessions from an old snapshot; reconcile
    adopted every one, so the UI showed them as the user's sessions and the
    summary worker queued a summary per resurrected agent. A handful of strays
    is a restarted backend finding its own work; dozens at once is not.
    """

    def test_a_few_strays_are_adopted(self, tmp_path):
        path = _write_state(tmp_path)
        names = [f"kiro-s{i}" for i in range(3)]
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=names), \
             patch.object(tm, "_read_meta", return_value={"cwd": "/tmp"}):
            result = tm.reconcile()
            assert sorted(result["adopted"]) == ["s0", "s1", "s2"]
            assert result["unclaimed"] == []
            assert sorted(tm.load_state()["managed"]) == ["s0", "s1", "s2"]

    def test_a_burst_is_held_back_not_adopted(self, tmp_path):
        path = _write_state(tmp_path)
        names = [f"kiro-s{i}" for i in range(tm.ADOPT_LIMIT + 1)]
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=names), \
             patch.object(tm, "_read_meta", return_value={"cwd": "/tmp"}):
            result = tm.reconcile()
            state = tm.load_state()
        assert result["adopted"] == []
        assert sorted(result["unclaimed"]) == sorted(names)
        assert state["managed"] == {}
        assert sorted(state["unclaimed"]) == sorted(names)

    def test_a_held_stray_is_not_adopted_on_the_next_pass(self, tmp_path):
        """Otherwise the fleet would be adopted one reconcile later, in twos."""
        path = _write_state(tmp_path, unclaimed={"kiro-s0": {"first_seen": 1.0}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-s0"]), \
             patch.object(tm, "_read_meta", return_value={"cwd": "/tmp"}):
            result = tm.reconcile()
        assert result["adopted"] == []
        assert list(json.loads(path.read_text())["unclaimed"]) == ["kiro-s0"]

    def test_a_stray_that_is_gone_is_forgotten(self, tmp_path):
        path = _write_state(tmp_path, unclaimed={"kiro-s0": {"first_seen": 1.0}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=[]):
            tm.reconcile()
        assert tm.load_state()["unclaimed"] == {}

    def test_claiming_moves_a_stray_into_managed(self, tmp_path):
        path = _write_state(tmp_path, unclaimed={"kiro-s0": {"first_seen": 1.0},
                                                 "kiro-s1": {"first_seen": 1.0}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-s0", "kiro-s1"]), \
             patch.object(tm, "_read_meta", return_value={"cwd": "/tmp"}):
            assert tm.claim_unclaimed(["kiro-s0"])["claimed"] == ["kiro-s0"]
            state = tm.load_state()
        assert "s0" in state["managed"]
        assert list(state["unclaimed"]) == ["kiro-s1"]

    def test_killing_strays_defaults_to_a_dry_run(self, tmp_path):
        path = _write_state(tmp_path, unclaimed={"kiro-s0": {"first_seen": 1.0}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-s0"]), \
             patch.object(tm, "_tmux") as run:
            result = tm.kill_unclaimed()
        assert result == {"ok": True, "dry_run": True, "would_kill": ["kiro-s0"]}
        run.assert_not_called()
        assert list(json.loads(path.read_text())["unclaimed"]) == ["kiro-s0"]

    def test_killing_strays_touches_only_recorded_strays(self, tmp_path):
        """A managed session's name passed in by mistake must not be killed."""
        path = _write_state(tmp_path,
                            managed={"mine": {"tmux": "kiro-mine"}},
                            unclaimed={"kiro-s0": {"first_seen": 1.0}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-s0", "kiro-mine"]), \
             patch.object(tm, "_tmux") as run:
            result = tm.kill_unclaimed(["kiro-s0", "kiro-mine"], dry_run=False)
        assert result["killed"] == ["kiro-s0"]
        killed = [c[0][2] for c in run.call_args_list]
        assert killed == ["kiro-s0"]
        assert "mine" in json.loads(path.read_text())["managed"]


class TestDeadPaneReaper:
    """remain-on-exit keeps a crashed pane readable; it also leaks corpses."""

    def test_first_sighting_only_records_the_time_of_death(self, tmp_path):
        path = _write_state(tmp_path, managed={"a": {"tmux": "kiro-a"}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-a"]), \
             patch.object(tm, "pane_dead", return_value=True), \
             patch.object(tm, "_tmux") as run:
            result = tm.reap_dead_panes(ttl=3600)
        assert result["killed"] == [] and result["watching"] == ["a"]
        run.assert_not_called()
        assert json.loads(path.read_text())["managed"]["a"]["dead_since"] > 0

    def test_a_corpse_past_the_ttl_is_killed(self, tmp_path):
        path = _write_state(tmp_path, managed={
            "a": {"tmux": "kiro-a", "dead_since": 1.0}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-a"]), \
             patch.object(tm, "pane_dead", return_value=True), \
             patch.object(tm, "_tmux") as run:
            result = tm.reap_dead_panes(ttl=3600)
        assert result["killed"] == ["a"]
        assert run.call_args[0][:3] == ("kill-session", "-t", "kiro-a")
        assert json.loads(path.read_text())["managed"] == {}

    def test_a_live_pane_is_never_killed(self, tmp_path):
        """Including one marked dead earlier — a resumed session must survive."""
        path = _write_state(tmp_path, managed={
            "a": {"tmux": "kiro-a", "dead_since": 1.0}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-a"]), \
             patch.object(tm, "pane_dead", return_value=False), \
             patch.object(tm, "_tmux") as run:
            result = tm.reap_dead_panes(ttl=3600)
        assert result["killed"] == [] and result["revived"] == ["a"]
        run.assert_not_called()
        assert "dead_since" not in json.loads(path.read_text())["managed"]["a"]

    def test_dry_run_reports_without_killing(self, tmp_path):
        path = _write_state(tmp_path, managed={
            "a": {"tmux": "kiro-a", "dead_since": 1.0}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-a"]), \
             patch.object(tm, "pane_dead", return_value=True), \
             patch.object(tm, "_tmux") as run:
            result = tm.reap_dead_panes(ttl=3600, dry_run=True)
        assert result["killed"] == ["a"]
        run.assert_not_called()
        assert "a" in json.loads(path.read_text())["managed"]

    def test_zero_ttl_disables_the_reaper(self, tmp_path):
        path = _write_state(tmp_path, managed={
            "a": {"tmux": "kiro-a", "dead_since": 1.0}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "pane_dead", return_value=True), \
             patch.object(tm, "_tmux") as run:
            assert tm.reap_dead_panes(ttl=0)["disabled"] is True
        run.assert_not_called()


class TestIdleReaper:
    """Kills live sessions, so what it refuses to touch is the important part."""

    def _managed(self, tmp_path, sessions):
        return _write_state(tmp_path, managed=sessions)

    def test_an_idle_session_is_a_candidate_but_not_killed_on_a_dry_run(self, tmp_path):
        path = self._managed(tmp_path, {"a": {"tmux": "kiro-a"}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-a"]), \
             patch.object(tm, "pane_dead", return_value=False), \
             patch.object(tm, "gated_sessions", return_value=set()), \
             patch.object(tm, "pending_approvals", return_value=[]), \
             patch.object(tm, "stack_get", return_value=[]), \
             patch.object(tm, "last_activity", return_value=1.0), \
             patch.object(tm, "kill") as kill:
            result = tm.reap_idle_sessions(idle=3600)
        assert [c["session_id"] for c in result["would_kill"]] == ["a"]
        kill.assert_not_called()

    def test_a_recently_active_session_is_kept(self, tmp_path):
        import time as _t
        path = self._managed(tmp_path, {"a": {"tmux": "kiro-a"}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-a"]), \
             patch.object(tm, "pane_dead", return_value=False), \
             patch.object(tm, "gated_sessions", return_value=set()), \
             patch.object(tm, "pending_approvals", return_value=[]), \
             patch.object(tm, "stack_get", return_value=[]), \
             patch.object(tm, "last_activity", return_value=_t.time()), \
             patch.object(tm, "kill") as kill:
            result = tm.reap_idle_sessions(idle=3600, dry_run=False)
        assert result["killed"] == []
        kill.assert_not_called()

    def test_a_gated_session_is_never_reaped(self, tmp_path):
        path = self._managed(tmp_path, {"a": {"tmux": "kiro-a"}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-a"]), \
             patch.object(tm, "pane_dead", return_value=False), \
             patch.object(tm, "gated_sessions", return_value={"a"}), \
             patch.object(tm, "pending_approvals", return_value=[]), \
             patch.object(tm, "stack_get", return_value=[]), \
             patch.object(tm, "last_activity", return_value=1.0), \
             patch.object(tm, "kill") as kill:
            result = tm.reap_idle_sessions(idle=3600, dry_run=False)
        assert result["killed"] == []
        assert result["kept"] == [{"session_id": "a", "why": "mid-workflow"}]
        kill.assert_not_called()

    def test_a_queued_stack_item_protects_a_session(self, tmp_path):
        path = self._managed(tmp_path, {"a": {"tmux": "kiro-a"}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-a"]), \
             patch.object(tm, "pane_dead", return_value=False), \
             patch.object(tm, "gated_sessions", return_value=set()), \
             patch.object(tm, "pending_approvals", return_value=[]), \
             patch.object(tm, "stack_get", return_value=[{"id": "1", "text": "next"}]), \
             patch.object(tm, "last_activity", return_value=1.0), \
             patch.object(tm, "kill") as kill:
            assert tm.reap_idle_sessions(idle=3600, dry_run=False)["killed"] == []
        kill.assert_not_called()

    def test_an_unknown_timestamp_is_not_treated_as_idle(self, tmp_path):
        path = self._managed(tmp_path, {"a": {"tmux": "kiro-a"}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-a"]), \
             patch.object(tm, "pane_dead", return_value=False), \
             patch.object(tm, "gated_sessions", return_value=set()), \
             patch.object(tm, "pending_approvals", return_value=[]), \
             patch.object(tm, "stack_get", return_value=[]), \
             patch.object(tm, "last_activity", return_value=0.0), \
             patch.object(tm, "kill") as kill:
            result = tm.reap_idle_sessions(idle=3600, dry_run=False)
        assert result["kept"] == [{"session_id": "a", "why": "no activity timestamp"}]
        kill.assert_not_called()

    def test_killing_is_graceful_so_the_session_stays_resumable(self, tmp_path):
        path = self._managed(tmp_path, {"a": {"tmux": "kiro-a"}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "list_tmux_sessions", return_value=["kiro-a"]), \
             patch.object(tm, "pane_dead", return_value=False), \
             patch.object(tm, "gated_sessions", return_value=set()), \
             patch.object(tm, "pending_approvals", return_value=[]), \
             patch.object(tm, "stack_get", return_value=[]), \
             patch.object(tm, "last_activity", return_value=1.0), \
             patch.object(tm, "kill", return_value={"ok": True, "mode": "quit"}) as kill:
            result = tm.reap_idle_sessions(idle=3600, dry_run=False)
        assert result["killed"][0]["session_id"] == "a"
        assert kill.call_args.kwargs["graceful"] is True

    def test_zero_idle_disables_it(self, tmp_path):
        path = self._managed(tmp_path, {"a": {"tmux": "kiro-a"}})
        with patch.object(tm, "MANAGED_FILE", path), \
             patch.object(tm, "kill") as kill:
            assert tm.reap_idle_sessions(idle=0, dry_run=False)["disabled"] is True
        kill.assert_not_called()
