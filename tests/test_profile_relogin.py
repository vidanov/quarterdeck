"""Logging in again to an expired profile saves the new login back into it."""
import json
import sqlite3

import pytest
from backend import api, config

SU = "https://a.awsapps.com/start"


def tok(refresh, start_url=SU):
    return [{"key": "kirocli:odic:token",
             "value": json.dumps({"refresh_token": refresh, "start_url": start_url, "region": "eu-central-1"})}]


@pytest.fixture
def store(tmp_path, monkeypatch):
    db = tmp_path / "auth.sqlite3"
    monkeypatch.setattr(api, "_KIRO_AUTH_DB", db)
    monkeypatch.setattr(api, "_PROFILES_DIR", tmp_path)
    monkeypatch.setattr(api, "_PROFILE_HEALTH_FILE", tmp_path / "health.json")
    monkeypatch.setattr(api, "_SELECTED_PROFILE_FILE", tmp_path / "selected.json")
    monkeypatch.setattr(api, "_active_profile_cache", (0, ""))
    monkeypatch.setattr(api, "_last_profile_switch_at", 0)
    monkeypatch.setattr(api, "_RELOGIN_TIMEOUT", 1.0)
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    monkeypatch.setattr(config, "_models_cache", None)
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE auth_kv (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)")
    (tmp_path / "Free.jsonl").write_text(json.dumps(tok("dead")[0]) + "\n")
    (tmp_path / "Free.meta.json").write_text(json.dumps({
        "state_profile": json.dumps({"arn": "arn:free"}), "profile_arn": "arn:free",
        "token_fingerprint": api._token_fingerprint(tok("dead"))}))
    return db, tmp_path


def set_live(db, rows):
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM auth_kv")
        con.executemany("INSERT INTO auth_kv VALUES (?,?)", [(r["key"], r["value"]) for r in rows])


def test_new_login_is_saved_into_profile_with_its_subscription(store):
    db, folder = store
    set_live(db, tok("fresh"))  # the login just landed, without a subscription
    api._watch_relogin("Free", api._token_fingerprint(tok("old")), SU)
    with sqlite3.connect(db) as con:
        state = con.execute("SELECT value FROM state").fetchone()[0]
    assert json.loads(state)["arn"] == "arn:free"
    saved = [json.loads(line) for line in (folder / "Free.jsonl").read_text().splitlines()]
    assert saved == tok("fresh")
    meta = json.loads((folder / "Free.meta.json").read_text())
    assert meta["token_fingerprint"] == api._token_fingerprint(tok("fresh"))
    assert api._selected_profile() == "Free"
    assert api._profile_health()["Free"]["status"] == "valid"


def test_login_to_another_account_is_left_alone(store):
    db, folder = store
    set_live(db, tok("fresh", "https://other.awsapps.com/start"))
    api._watch_relogin("Free", api._token_fingerprint(tok("old")), SU)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT value FROM state").fetchone() is None
    assert json.loads((folder / "Free.jsonl").read_text())["value"] == tok("dead")[0]["value"]


def test_no_new_login_changes_nothing(store):
    db, folder = store
    set_live(db, tok("old"))
    api._watch_relogin("Free", api._token_fingerprint(tok("old")), SU)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT value FROM state").fetchone() is None


def test_login_endpoint_uses_the_profile_account(store, monkeypatch):
    db, folder = store
    launched, watched = [], []
    monkeypatch.setattr(api, "_launch_in_terminal", lambda t, cmd, cwd: launched.append(cmd) or {"ok": True})
    monkeypatch.setattr(api, "_watch_relogin", lambda *a: watched.append(a))

    class InlineThread:
        def __init__(self, target, args, **kw):
            self.run = lambda: target(*args)

        def start(self):
            self.run()
    monkeypatch.setattr(api.threading, "Thread", InlineThread)
    result = api.kiro_login({"profile": "Free"})
    assert result == {"ok": True}
    assert f"--identity-provider {SU}" in launched[0] and "--region eu-central-1" in launched[0]
    assert "--license pro" in launched[0]
    assert watched and watched[0][0] == "Free"
