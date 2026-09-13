"""A stale CodeWhisperer ARN must not outvote the live OAuth tokens.

`_active_profile_name` matched the state table's ARN first and returned on the
first meta.json carrying it. Switching to a profile whose meta has no
`state_profile`/`profile_arn` (meta rebuilt by the back-fill path) leaves the
*previous* profile's ARN in the table, so the label kept naming the old
profile and the switch looked like it had never happened.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import backend.api as api_mod


FREE_FP = "1111111111111111"
PAID_FP = "2222222222222222"
PAID_ARN = "arn:aws:codewhisperer:eu-central-1:1:profile/PAID"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Two saved profiles: Paid has an ARN, Free's meta lost its ARN."""
    (tmp_path / "StormDE_Paid.meta.json").write_text(json.dumps(
        {"email": "x@y.z", "profile_arn": PAID_ARN, "token_fingerprint": PAID_FP}))
    (tmp_path / "StormDE_Paid.jsonl").write_text(json.dumps({"fp": PAID_FP}) + "\n")
    (tmp_path / "StormDE_Free.meta.json").write_text(json.dumps(
        {"email": "x@y.z", "token_fingerprint": FREE_FP}))
    (tmp_path / "StormDE_Free.jsonl").write_text(json.dumps({"fp": FREE_FP}) + "\n")

    monkeypatch.setattr(api_mod, "_PROFILES_DIR", tmp_path)
    monkeypatch.setattr(api_mod, "_KIRO_AUTH_DB", tmp_path / "data.sqlite3")
    (tmp_path / "data.sqlite3").write_text("")  # only existence is checked

    import sqlite3

    class _FakeCon:
        def execute(self, *_a, **_k):
            return self

        def fetchone(self):
            return (json.dumps({"arn": PAID_ARN, "profile_name": "p"}),)

        def close(self):
            pass

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: _FakeCon())
    return tmp_path


def _live_fingerprint(monkeypatch, fp):
    """Live auth carries `fp`; a saved profile's fingerprint is the one in its file."""
    monkeypatch.setattr(api_mod, "_dump_auth_rows", lambda: [{"live": fp}])
    monkeypatch.setattr(
        api_mod, "_token_fingerprint",
        lambda rows: (rows[0].get("live") or rows[0].get("fp", "")) if rows else "")


class TestActiveProfileName:
    def test_arn_wins_when_tokens_agree(self, store, monkeypatch):
        _live_fingerprint(monkeypatch, PAID_FP)
        assert api_mod._active_profile_name() == "StormDE_Paid"

    def test_tokens_win_over_a_stale_arn(self, store, monkeypatch):
        _live_fingerprint(monkeypatch, FREE_FP)
        assert api_mod._active_profile_name() == "StormDE_Free"

    def test_the_named_profile_is_the_one_whose_tokens_are_live(self, store, monkeypatch):
        """Rename Free so alphabetical order cannot fake the right answer."""
        (store / "StormDE_Free.meta.json").rename(store / "ZFree.meta.json")
        (store / "StormDE_Free.jsonl").rename(store / "ZFree.jsonl")
        _live_fingerprint(monkeypatch, FREE_FP)
        assert api_mod._active_profile_name() == "ZFree"


class TestIncompleteSwitch:
    def test_switch_without_an_arn_is_refused_before_writing(self, tmp_path, monkeypatch):
        rows = [{"key": "kirocli:odic:token",
                 "value": json.dumps({"refresh_token": "RRR"})}]
        monkeypatch.setattr(api_mod, "_profile_data_path", lambda n: tmp_path / f"{n}.jsonl")
        monkeypatch.setattr(api_mod, "_profile_meta_path", lambda n: tmp_path / f"{n}.meta.json")
        monkeypatch.setattr(api_mod, "_dump_auth_rows", lambda: [])
        monkeypatch.setattr(api_mod, "_restore_auth_rows", lambda r: None)
        monkeypatch.setattr(api_mod, "_KIRO_AUTH_DB", tmp_path / "missing.sqlite3")
        (tmp_path / "NoArn.jsonl").write_text(json.dumps(rows[0]) + "\n")
        (tmp_path / "NoArn.meta.json").write_text(json.dumps({"email": "x@y.z"}))

        result = api_mod.switch_profile({"name": "NoArn"})

        assert not result.get("ok")
        assert "missing its subscription" in result["error"]
