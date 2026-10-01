"""The chosen profile survives a running session overwriting the shared login.

kiro-cli keeps one login for all its processes. A running session of another
account writes its refreshed token back into it, leaving account A's tokens
with account B's subscription — no saved profile matches, and the pill went
blank on every fresh start.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import backend.api as api_mod


@pytest.fixture
def chosen(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "_SELECTED_PROFILE_FILE", tmp_path / "selected.json")
    monkeypatch.setattr(api_mod, "_profile_data_path", lambda n: tmp_path / f"{n}.jsonl")
    monkeypatch.setattr(api_mod, "_profile_meta_path", lambda n: tmp_path / f"{n}.meta.json")
    (tmp_path / "StormDE_Free.jsonl").write_text("{}\n")
    (tmp_path / "StormDE_Free.meta.json").write_text(json.dumps({"email": "x@y.z"}))
    api_mod._set_selected_profile("StormDE_Free")
    switched = []
    monkeypatch.setattr(api_mod, "_switch_profile", lambda n: switched.append(n) or {"ok": True})
    return switched


def test_pill_keeps_the_chosen_profile_and_flags_drift(chosen, monkeypatch):
    monkeypatch.setattr(api_mod, "_fresh_active_profile", lambda: "")
    r = api_mod.current_profile()
    assert r["active_profile"] == "StormDE_Free"
    assert r["drift"] is True


def test_no_drift_when_live_login_is_the_chosen_one(chosen, monkeypatch):
    monkeypatch.setattr(api_mod, "_fresh_active_profile", lambda: "StormDE_Free")
    r = api_mod.current_profile()
    assert r["active_profile"] == "StormDE_Free" and r["drift"] is False


def test_spawn_reapplies_the_chosen_profile_only_when_it_drifted(chosen, monkeypatch):
    monkeypatch.setattr(api_mod, "_active_profile_name", lambda: "StormDE_Free")
    api_mod._ensure_selected_profile()
    assert chosen == []
    monkeypatch.setattr(api_mod, "_active_profile_name", lambda: "")
    api_mod._ensure_selected_profile()
    assert chosen == ["StormDE_Free"]


def test_busy_db_does_not_trigger_a_switch(chosen, monkeypatch):
    def busy():
        raise api_mod._sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(api_mod, "_active_profile_name", busy)
    api_mod._ensure_selected_profile()
    assert chosen == []


def test_manual_login_stops_reapplying(chosen, monkeypatch):
    monkeypatch.setattr(api_mod, "_launch_in_terminal", lambda *a: {"ok": True})
    api_mod.kiro_login({})
    assert api_mod._selected_profile() == ""


def test_spawn_runs_the_pre_spawn_hooks(tmp_path, monkeypatch):
    import backend.tmux_manager as tmux
    calls = []
    monkeypatch.setattr(tmux, "PRE_SPAWN_HOOKS", [lambda: calls.append(1)])
    monkeypatch.setattr(tmux, "tmux_available", lambda: False)
    tmux.spawn(str(tmp_path))
    assert calls == [1]


def test_without_a_choice_the_live_subscription_names_the_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "_SELECTED_PROFILE_FILE", tmp_path / "none.json")
    monkeypatch.setattr(api_mod, "_fresh_active_profile", lambda: "")
    monkeypatch.setattr(api_mod, "_profile_by_state_arn", lambda: "StormDE_Free")
    monkeypatch.setattr(api_mod, "_profile_meta_path", lambda n: tmp_path / f"{n}.meta.json")
    (tmp_path / "StormDE_Free.meta.json").write_text(json.dumps({"email": "x@y.z"}))
    r = api_mod.current_profile()
    assert r["active_profile"] == "StormDE_Free" and r["drift"] is True


def test_guess_is_flagged_so_the_ui_does_not_promise_a_reapply(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "_SELECTED_PROFILE_FILE", tmp_path / "none.json")
    monkeypatch.setattr(api_mod, "_fresh_active_profile", lambda: "")
    monkeypatch.setattr(api_mod, "_profile_by_state_arn", lambda: "StormDE_Free")
    monkeypatch.setattr(api_mod, "_profile_meta_path", lambda n: tmp_path / f"{n}.meta.json")
    assert api_mod.current_profile()["guessed"] is True
