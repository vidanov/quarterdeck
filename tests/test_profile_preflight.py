"""A switch never installs a saved login that can no longer refresh.

Restoring a dead refresh token does not fail at the switch: kiro-cli finds out
at its next refresh, drops the shared login, and every session loses it.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from backend import api, config, kiro_oidc

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def login(access, refresh, expires, secret_expires=NOW + timedelta(days=60)):
    return [
        {"key": "kirocli:odic:token", "value": json.dumps({
            "access_token": access, "refresh_token": refresh, "region": "eu-central-1",
            "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "start_url": "https://example.awsapps.com/start"})},
        {"key": "kirocli:odic:device-registration", "value": json.dumps({
            "client_id": "cid", "client_secret": "csecret", "region": "eu-central-1",
            "client_secret_expires_at": secret_expires.strftime("%Y-%m-%dT%H:%M:%SZ")})},
    ]


LIVE = login("live-a", "live-r", NOW + timedelta(hours=1))
STALE = login("old-a", "old-r", NOW - timedelta(days=12))


def test_valid_access_token_needs_no_network(monkeypatch):
    monkeypatch.setattr(kiro_oidc, "_post", lambda *a: pytest.fail("no call expected"))
    assert kiro_oidc.check(LIVE, now=NOW) == (kiro_oidc.VALID, None)


def test_rejected_refresh_is_expired(monkeypatch):
    monkeypatch.setattr(kiro_oidc, "_post", lambda url, body: (400, {"error": "invalid_grant"}))
    assert kiro_oidc.check(STALE, now=NOW) == (kiro_oidc.EXPIRED, None)


def test_expired_client_registration_is_expired_without_a_call(monkeypatch):
    monkeypatch.setattr(kiro_oidc, "_post", lambda *a: pytest.fail("no call expected"))
    rows = login("a", "r", NOW - timedelta(days=1), secret_expires=NOW - timedelta(days=1))
    assert kiro_oidc.check(rows, now=NOW)[0] == kiro_oidc.EXPIRED


def test_network_failure_is_unknown(monkeypatch):
    def boom(*a):
        raise OSError("offline")
    monkeypatch.setattr(kiro_oidc, "_post", boom)
    assert kiro_oidc.check(STALE, now=NOW) == (kiro_oidc.UNKNOWN, None)


def test_server_error_is_unknown_not_expired(monkeypatch):
    monkeypatch.setattr(kiro_oidc, "_post", lambda url, body: (500, {"error": "invalid_grant"}))
    assert kiro_oidc.check(STALE, now=NOW)[0] == kiro_oidc.UNKNOWN


def test_successful_refresh_returns_new_tokens(monkeypatch):
    seen = {}

    def ok(url, body):
        seen.update(url=url, body=body)
        return 200, {"accessToken": "new-a", "refreshToken": "new-r", "expiresIn": 3600}
    monkeypatch.setattr(kiro_oidc, "_post", ok)
    status, rows = kiro_oidc.check(STALE, now=NOW)
    assert status == kiro_oidc.VALID
    assert seen["url"] == "https://oidc.eu-central-1.amazonaws.com/token"
    assert seen["body"]["grantType"] == "refresh_token" and seen["body"]["refreshToken"] == "old-r"
    tok = json.loads(rows[0]["value"])
    assert tok["access_token"] == "new-a" and tok["refresh_token"] == "new-r"
    assert tok["start_url"] == "https://example.awsapps.com/start"  # rest kept
    assert rows[1] == STALE[1]  # registration untouched


@pytest.fixture
def store(tmp_path, monkeypatch):
    db = tmp_path / "auth.sqlite3"
    monkeypatch.setattr(api, "_KIRO_AUTH_DB", db)
    monkeypatch.setattr(api, "_PROFILES_DIR", tmp_path)
    monkeypatch.setattr(api, "_PROFILE_HEALTH_FILE", tmp_path / "health.json")
    monkeypatch.setattr(api, "_SELECTED_PROFILE_FILE", tmp_path / "selected.json")
    monkeypatch.setattr(api, "_active_profile_cache", (0, ""))
    monkeypatch.setattr(api, "_last_profile_switch_at", 0)
    monkeypatch.setattr(config, "_models_cache", None)
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE auth_kv (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)")
        con.executemany("INSERT INTO auth_kv VALUES (?,?)", [(r["key"], r["value"]) for r in LIVE])
        con.execute("INSERT INTO state VALUES (?,?)", ("api.codewhisperer.profile", json.dumps({"arn": "arn:live"})))

    def save(name, rows, arn):
        (tmp_path / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
        (tmp_path / f"{name}.meta.json").write_text(json.dumps({
            "email": "t@example.com", "provider": "test", "profile_arn": arn,
            "state_profile": json.dumps({"arn": arn}),
            "token_fingerprint": api._token_fingerprint(rows)}))
    return save, db, tmp_path


def live_rows(db):
    with sqlite3.connect(db) as con:
        return dict(con.execute("SELECT key, value FROM auth_kv").fetchall())


def test_switch_to_expired_profile_changes_nothing(store, monkeypatch):
    save, db, folder = store
    save("Stale", STALE, "arn:stale")
    before = live_rows(db)
    monkeypatch.setattr(kiro_oidc, "_post", lambda url, body: (400, {"error": "invalid_grant"}))
    result = api.switch_profile({"name": "Stale"})
    assert result.get("expired") and "expired" in result["error"]
    assert live_rows(db) == before
    assert not (folder / "_previous.jsonl").exists()
    assert api._profile_health()["Stale"]["status"] == kiro_oidc.EXPIRED


def test_switch_installs_refreshed_tokens_and_saves_them(store, monkeypatch):
    save, db, folder = store
    save("Stale", STALE, "arn:stale")
    monkeypatch.setattr(kiro_oidc, "_post",
                        lambda url, body: (200, {"accessToken": "new-a", "expiresIn": 3600}))
    result = api.switch_profile({"name": "Stale"})
    assert result["ok"] and "warning" not in result
    assert json.loads(live_rows(db)["kirocli:odic:token"])["access_token"] == "new-a"
    saved = [json.loads(l) for l in (folder / "Stale.jsonl").read_text().splitlines()]
    assert json.loads(saved[0]["value"])["access_token"] == "new-a"


def test_unknown_health_switches_with_a_warning(store, monkeypatch):
    save, db, folder = store
    save("Stale", STALE, "arn:stale")

    def boom(*a):
        raise OSError("offline")
    monkeypatch.setattr(kiro_oidc, "_post", boom)
    result = api.switch_profile({"name": "Stale"})
    assert result["ok"] and "warning" in result


def test_switch_away_refreshes_outgoing_profiles_copy(store, monkeypatch):
    save, db, folder = store
    # The saved copy of the live profile is old: same refresh token, stale access.
    save("Live", login("old-a", "live-r", NOW - timedelta(days=3)), "arn:live")
    save("Other", login("o-a", "o-r", datetime.now(timezone.utc) + timedelta(hours=1)), "arn:other")
    monkeypatch.setattr(kiro_oidc, "_post", lambda *a: pytest.fail("no call expected"))
    assert api.switch_profile({"name": "Other"})["ok"]
    saved = {r["key"]: r["value"] for r in
             (json.loads(l) for l in (folder / "Live.jsonl").read_text().splitlines())}
    assert json.loads(saved["kirocli:odic:token"])["access_token"] == "live-a"


def test_check_skips_the_live_login(store, monkeypatch):
    save, db, folder = store
    save("Live", LIVE, "arn:live")
    save("Stale", STALE, "arn:stale")
    calls = []

    def post(url, body):
        calls.append(body["refreshToken"])
        return 400, {"error": "invalid_grant"}
    monkeypatch.setattr(kiro_oidc, "_post", post)
    health = api.check_profiles({"force": True})["health"]
    assert calls == ["old-r"]
    assert health == {"Live": "valid", "Stale": "expired"}
    assert {p["name"]: p["health"] for p in api.list_profiles()["profiles"]} == health
