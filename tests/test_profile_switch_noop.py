"""A switch to the already-active profile must not touch auth_kv.

Every running kiro-cli reads its bearer token from that one table, so the
DELETE + reinsert in _restore_auth_rows pulls credentials out from under every
live session on the machine. Captain calls /api/profiles/switch whenever
Bosun, a worker, Wulf or the engineer starts, and all four of its role
profiles were set to the profile already active — so launching Captain wiped
the auth of every Quarterdeck session for no gain.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import backend.api as api_mod


LIVE_ROWS = [
    {"key": "kirocli:odic:token",
     "value": json.dumps({"access_token": "AAA", "refresh_token": "RRR"})},
    {"key": "kirocli:odic:device-registration", "value": "{}"},
]
OTHER_ROWS = [
    {"key": "kirocli:odic:token",
     "value": json.dumps({"access_token": "BBB", "refresh_token": "SSS"})},
]


@pytest.fixture
def profile_store(tmp_path, monkeypatch):
    """Profile files on disk plus a recording stub for the auth rewrite."""
    restores: list[list[dict]] = []

    def _data_path(name):
        return tmp_path / f"{name}.jsonl"

    def _meta_path(name):
        return tmp_path / f"{name}.meta.json"

    monkeypatch.setattr(api_mod, "_profile_data_path", _data_path)
    monkeypatch.setattr(api_mod, "_profile_meta_path", _meta_path)
    monkeypatch.setattr(api_mod, "_dump_auth_rows", lambda: list(LIVE_ROWS))
    monkeypatch.setattr(api_mod, "_restore_auth_rows", lambda rows: restores.append(rows))
    # The real thing shells out to kiro-cli; irrelevant to this behaviour.
    monkeypatch.setattr(api_mod, "_current_profile_identity", lambda: {"email": "x@y.z"},
                        raising=False)
    monkeypatch.setattr(api_mod, "available_models", lambda force=False: (), raising=False)

    def write_profile(name, rows):
        _data_path(name).write_text("".join(json.dumps(r) + "\n" for r in rows))
        _meta_path(name).write_text(json.dumps({"email": "x@y.z", "profile_arn": "arn:x"}))

    return write_profile, restores, tmp_path


class TestSwitchToActiveProfile:
    def test_switching_to_the_live_profile_does_not_rewrite_auth(self, profile_store):
        write_profile, restores, _ = profile_store
        write_profile("StormDE_Free", LIVE_ROWS)

        result = api_mod.switch_profile({"name": "StormDE_Free"})

        assert result["ok"] is True
        assert result["unchanged"] is True
        assert restores == [], "auth_kv was rewritten for a no-op switch"

    def test_no_previous_snapshot_written_for_a_no_op(self, profile_store):
        """_previous holds live OAuth tokens; a no-op must not churn it."""
        write_profile, _, tmp_path = profile_store
        write_profile("StormDE_Free", LIVE_ROWS)

        api_mod.switch_profile({"name": "StormDE_Free"})

        assert not (tmp_path / "_previous.jsonl").exists()

    def test_a_real_switch_still_rewrites_auth(self, profile_store):
        write_profile, restores, tmp_path = profile_store
        write_profile("OtherAccount", OTHER_ROWS)

        result = api_mod.switch_profile({"name": "OtherAccount"})

        assert result["ok"] is True
        assert result["unchanged"] is False
        assert restores == [OTHER_ROWS]
        assert (tmp_path / "_previous.jsonl").exists()

    def test_unknown_profile_is_refused(self, profile_store):
        assert "error" in api_mod.switch_profile({"name": "nope"})

    def test_missing_name_is_refused(self, profile_store):
        assert "error" in api_mod.switch_profile({"name": "  "})
