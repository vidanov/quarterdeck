"""Background kiro processes run on a private copy of the configured profile."""
import json
import sqlite3

import pytest
from backend import config, internal_auth

ROWS = [{"key": "kirocli:odic:token", "value": json.dumps({"start_url": "https://a/start", "refresh_token": "r"})}]
STATE = json.dumps({"arn": "arn:free"})


@pytest.fixture
def store(tmp_path, monkeypatch):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "Free.jsonl").write_text(json.dumps(ROWS[0]) + "\n")
    (profiles / "Free.meta.json").write_text(json.dumps({"state_profile": STATE, "token_fingerprint": "fp1"}))
    home = tmp_path / "internal"
    monkeypatch.setattr(internal_auth, "PROFILES_DIR", profiles)
    monkeypatch.setattr(internal_auth, "HOME_DIR", home)
    settings = {}
    monkeypatch.setattr(config, "read_settings", lambda: settings)
    # Stand in for `kiro-cli whoami` creating its schema.
    def fake_run(cmd, env, **kw):
        db = internal_auth._db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE auth_kv (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)")
        con.commit()
        con.close()
        assert env["HOME"] == str(home)
    monkeypatch.setattr(internal_auth.subprocess, "run", fake_run)
    return settings, profiles


def _db_contents():
    con = sqlite3.connect(str(internal_auth._db_path()))
    try:
        auth = con.execute("SELECT key, value FROM auth_kv").fetchall()
        state = con.execute("SELECT value FROM state WHERE key='api.codewhisperer.profile'").fetchone()
        return auth, state[0] if state else None
    finally:
        con.close()


def test_no_setting_shares_the_login(store):
    assert internal_auth.env() is None
    assert internal_auth.login_key() is None


def test_unknown_profile_shares_the_login(store):
    store[0]["internal-profile"] = "Gone"
    assert internal_auth.env() is None
    assert internal_auth.login_key() is None


def test_configured_profile_seeds_private_home(store):
    store[0]["internal-profile"] = "Free"
    env = internal_auth.env()
    assert env["HOME"] == str(internal_auth.HOME_DIR)
    auth, state = _db_contents()
    assert auth == [(ROWS[0]["key"], ROWS[0]["value"])]
    assert state == STATE
    assert internal_auth.login_key() == ("internal:Free", "fp1")


def test_private_refreshes_survive_until_profile_resaved(store):
    settings, profiles = store
    settings["internal-profile"] = "Free"
    internal_auth.env()
    con = sqlite3.connect(str(internal_auth._db_path()))
    con.execute("UPDATE auth_kv SET value='refreshed'")
    con.commit()
    con.close()
    internal_auth.env()
    assert _db_contents()[0][0][1] == "refreshed"
    (profiles / "Free.meta.json").write_text(json.dumps({"state_profile": STATE, "token_fingerprint": "fp2"}))
    internal_auth.env()
    assert _db_contents()[0][0][1] == ROWS[0]["value"]
