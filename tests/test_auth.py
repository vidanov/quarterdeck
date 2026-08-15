"""Auth middleware: loopback bypass, fail-closed remote, token acceptance."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import auth
from backend.api import app

TOKEN = "a" * 64
REMOTE = ("100.64.0.9", 51000)
REMOTE_V6 = ("fd7a:115c:a1e0::9", 51000)
OFF_TAILNET = ("192.168.1.20", 51000)
LOCAL = ("127.0.0.1", 45678)


@pytest.fixture
def no_token(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "absent")
    monkeypatch.delenv("OSA_KIRO_TOKEN", raising=False)
    # Stub keychain so file-based token tests are not confused by real keychain
    monkeypatch.setattr(auth, "_keychain_read", lambda account: "")
    monkeypatch.setattr(auth, "_keychain_write", lambda account, value: True)
    # Ensure device tokens don't interfere
    from backend import devices
    monkeypatch.setattr(devices, "DEVICES_FILE", tmp_path / "no-devices.json")


@pytest.fixture
def with_token(monkeypatch, tmp_path):
    path = tmp_path / "token"
    path.write_text(TOKEN + "\n")
    monkeypatch.setattr(auth, "TOKEN_FILE", path)
    monkeypatch.delenv("OSA_KIRO_TOKEN", raising=False)
    # Simulate keychain already holding the token so migration never fires and
    # the file is not deleted between repeated read_token calls.
    monkeypatch.setattr(auth, "_keychain_read",
                        lambda account: TOKEN if account == auth._KC_REMOTE_ACCOUNT else "")
    monkeypatch.setattr(auth, "_keychain_write", lambda account, value: True)
    # Ensure device tokens don't interfere with legacy token tests
    from backend import devices
    monkeypatch.setattr(devices, "DEVICES_FILE", tmp_path / "no-devices.json")
    return TOKEN


def remote_client() -> TestClient:
    return TestClient(app, client=REMOTE)


def local_client() -> TestClient:
    return TestClient(app, client=LOCAL)


def off_tailnet_client() -> TestClient:
    return TestClient(app, client=OFF_TAILNET)


@pytest.fixture
def with_local_token(monkeypatch, tmp_path):
    """Set up a local token that loopback POST requests must present."""
    path = tmp_path / "local-token"
    local = "b" * 64
    path.write_text(local + "\n")
    monkeypatch.setattr(auth, "LOCAL_TOKEN_FILE", path)
    # Stub keychain so file is used as the source
    monkeypatch.setattr(auth, "_keychain_read", lambda account: "")
    monkeypatch.setattr(auth, "_keychain_write", lambda account, value: True)
    return local


class TestLoopbackBypass:
    def test_local_get_without_token_is_rejected(self, no_token, with_local_token):
        """GET requests on loopback now require the local token too."""
        r = local_client().get("/api/options")
        assert r.status_code == 401
        assert "local token required" in r.json().get("error", "")

    def test_local_get_with_correct_local_token_is_allowed(self, no_token, with_local_token):
        r = local_client().get(
            "/api/options",
            headers={"X-Local-Token": with_local_token},
        )
        assert r.status_code != 401

    def test_local_get_works_even_when_a_remote_token_exists(self, with_token, with_local_token):
        r = local_client().get(
            "/api/options",
            headers={"X-Local-Token": with_local_token},
        )
        assert r.status_code != 401

    def test_app_static_get_needs_no_token(self, no_token, with_local_token):
        """/app/* is exempt so the webview can load before the token is injected."""
        # The /app/ handler returns a 200 or 404 depending on build state,
        # but it must not return 401.
        r = local_client().get("/app/")
        assert r.status_code != 401

    def test_local_post_without_local_token_is_rejected(self, no_token, with_local_token):
        r = local_client().post("/api/sessions/nonexistent/input",
                                json={"text": "hi"})
        assert r.status_code == 401
        assert "local token required" in r.json().get("error", "")

    def test_local_post_with_correct_local_token_is_allowed(self, no_token, with_local_token):
        r = local_client().post(
            "/api/sessions/nonexistent/input",
            json={"text": "hi"},
            headers={"X-Local-Token": with_local_token},
        )
        # 404 or similar from the handler — NOT a 401 from auth
        assert r.status_code != 401

    def test_local_post_with_wrong_local_token_is_rejected(self, no_token, with_local_token):
        r = local_client().post(
            "/api/sessions/nonexistent/input",
            json={"text": "hi"},
            headers={"X-Local-Token": "wrong" * 16},
        )
        assert r.status_code == 401

    def test_stale_injected_token_returns_401_not_backend_unreachable(
        self, monkeypatch, tmp_path
    ):
        """Simulates the webview holding token A while the server expects token B.

        This used to surface as 'Backend unreachable' because client.js swallowed
        the 401 and the caller hardcoded its own error string. With the fix the
        real error propagates.
        """
        from backend import devices
        monkeypatch.setattr(devices, "DEVICES_FILE", tmp_path / "no-devices.json")
        token_a = "a" * 64
        token_b = "b" * 64
        # Server has token_b in cache (simulates a restart that regenerated the token)
        monkeypatch.setattr(auth, "_local_token_cache", token_b)
        # Webview sends the old token_a
        r = local_client().post(
            "/api/sessions/nonexistent/input",
            json={"text": "hi"},
            headers={"X-Local-Token": token_a},
        )
        assert r.status_code == 401
        assert r.json()["error"] == "local token required"

    def test_dev_mode_no_local_token_allows_through(self, monkeypatch, tmp_path):
        """In dev mode (./start.sh), no local token is configured yet.
        The gate must fail-open so the browser at localhost:5173 keeps working.
        """
        from backend import devices
        monkeypatch.setattr(devices, "DEVICES_FILE", tmp_path / "no-devices.json")
        monkeypatch.setattr(auth, "_local_token_cache", "")
        monkeypatch.setattr(auth, "_keychain_read", lambda account: "")
        monkeypatch.setattr(auth, "LOCAL_TOKEN_FILE", tmp_path / "absent")
        # No token anywhere — GET and POST with no header should pass through (fail-open)
        r_get = local_client().get("/api/options")
        assert r_get.status_code != 401
        r_post = local_client().post(
            "/api/sessions/nonexistent/input",
            json={"text": "hi"},
        )
        assert r_post.status_code != 401


class TestSourceBoundary:
    def test_tailscale_ipv4_is_allowed(self):
        assert auth.is_tailnet_host(REMOTE[0]) is True

    def test_tailscale_ipv6_is_allowed(self):
        assert auth.is_tailnet_host(REMOTE_V6[0]) is True

    @pytest.mark.parametrize("host", [
        "192.168.1.20", "10.0.0.4", "8.8.8.8", "not-an-address", "",
    ])
    def test_other_source_addresses_are_not_remote_entry_points(self, host):
        assert auth.is_tailnet_host(host) is False

    def test_a_valid_token_does_not_open_an_off_tailnet_source(self, with_token):
        response = off_tailnet_client().get(
            "/api/options", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 403
        assert response.json()["error"] == "source not allowed"

    def test_even_the_login_form_is_tailnet_only(self, with_token):
        assert off_tailnet_client().get("/login").status_code == 403


class TestFailClosed:
    def test_remote_refused_when_no_token_configured(self, no_token):
        response = remote_client().get("/api/options")
        assert response.status_code == 403
        assert response.json()["error"] == "remote access disabled"

    def test_a_presented_token_does_not_help_if_none_is_configured(self, no_token):
        response = remote_client().get(
            "/api/options", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 403

    def test_remote_login_refused_when_no_token_configured(self, no_token):
        assert remote_client().get("/login").status_code == 403


class TestTokenChecking:
    def test_remote_without_credentials_is_401(self, with_token):
        response = remote_client().get("/api/options")
        assert response.status_code == 401

    def test_wrong_token_is_401(self, with_token):
        response = remote_client().get(
            "/api/options", headers={"Authorization": "Bearer " + "b" * 64}
        )
        assert response.status_code == 401

    def test_bearer_token_is_accepted(self, with_token):
        response = remote_client().get(
            "/api/options", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 200

    def test_env_var_is_used_when_no_file_exists(self, monkeypatch, tmp_path):
        from backend import devices
        monkeypatch.setattr(devices, "DEVICES_FILE", tmp_path / "no-devices.json")
        monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "absent")
        monkeypatch.setattr(auth, "_keychain_read", lambda account: "")
        monkeypatch.setattr(auth, "_keychain_write", lambda account, value: True)
        monkeypatch.setenv("OSA_KIRO_TOKEN", TOKEN)
        response = remote_client().get(
            "/api/options", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 200

    def test_file_wins_over_env_var(self, monkeypatch, tmp_path):
        from backend import devices
        monkeypatch.setattr(devices, "DEVICES_FILE", tmp_path / "no-devices.json")
        path = tmp_path / "token"
        path.write_text(TOKEN + "\n")
        monkeypatch.setattr(auth, "TOKEN_FILE", path)
        # Simulate keychain holding TOKEN so migration doesn't delete the file
        monkeypatch.setattr(auth, "_keychain_read",
                            lambda account: TOKEN if account == auth._KC_REMOTE_ACCOUNT else "")
        monkeypatch.setattr(auth, "_keychain_write", lambda account, value: True)
        monkeypatch.setenv("OSA_KIRO_TOKEN", "c" * 64)
        client = remote_client()
        assert client.get(
            "/api/options", headers={"Authorization": "Bearer " + "c" * 64}
        ).status_code == 401
        assert client.get(
            "/api/options", headers={"Authorization": f"Bearer {TOKEN}"}
        ).status_code == 200


class TestBrowserLogin:
    def test_navigation_redirects_to_login(self, with_token):
        response = remote_client().get(
            "/app/", headers={"Accept": "text/html"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/login?next=/app/"

    def test_login_page_is_reachable_without_credentials(self, with_token):
        response = remote_client().get("/login")
        assert response.status_code == 200
        assert "Access token" in response.text

    def test_correct_token_sets_a_cookie_and_unlocks_the_api(self, with_token):
        client = remote_client()
        response = client.post(
            "/login", data={"token": TOKEN, "next": "/app/"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/app/"

        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "SameSite=strict" in cookie.replace("samesite", "SameSite")

        # The cookie is now on the client, so the API opens without a header.
        assert client.get("/api/options").status_code == 200

    def test_wrong_token_sets_no_cookie(self, with_token):
        response = remote_client().post(
            "/login", data={"token": "b" * 64}, follow_redirects=False
        )
        assert response.status_code == 401
        assert "set-cookie" not in response.headers

    def test_offsite_next_is_ignored(self, with_token):
        response = remote_client().post(
            "/login",
            data={"token": TOKEN, "next": "//evil.example.com/"},
            follow_redirects=False,
        )
        assert response.headers["location"] == "/app/"

    def test_absolute_url_next_is_ignored(self, with_token):
        assert auth.safe_next("https://evil.example.com/") == "/app/"


@pytest.fixture
def rate_limited_app(monkeypatch, with_token):
    """A side-effect-free API for exercising the auth middleware's buckets."""
    monkeypatch.setattr(auth, "DISPATCH_RATE_LIMIT", 2)
    monkeypatch.setattr(auth, "INPUT_RATE_LIMIT", 2)
    monkeypatch.setattr(auth, "RATE_WINDOW_SECONDS", 60.0)
    with auth._rate_lock:
        auth._rate_windows.clear()

    test_app = FastAPI()

    @test_app.post("/api/dispatch")
    def dispatch():
        return {"ok": True}

    @test_app.post("/api/sessions/{session_id}/input")
    def session_input(session_id: str):
        return {"ok": True, "id": session_id}

    @test_app.post("/api/sessions/{session_id}/send")
    def session_send(session_id: str):
        return {"ok": True, "id": session_id}

    auth.install(test_app)
    yield test_app
    with auth._rate_lock:
        auth._rate_windows.clear()


class TestRateLimits:
    def _remote(self, rate_limited_app):
        return TestClient(rate_limited_app, client=REMOTE,
                          headers={"Authorization": f"Bearer {TOKEN}"})

    def test_dispatch_is_bounded_per_remote_device(self, rate_limited_app):
        client = self._remote(rate_limited_app)
        assert client.post("/api/dispatch").status_code == 200
        assert client.post("/api/dispatch").status_code == 200
        refused = client.post("/api/dispatch")
        assert refused.status_code == 429
        assert refused.json()["limit"] == 2
        assert int(refused.headers["retry-after"]) >= 1

    def test_input_alias_cannot_bypass_the_shared_bucket(self, rate_limited_app):
        client = self._remote(rate_limited_app)
        assert client.post("/api/sessions/one/input").status_code == 200
        assert client.post("/api/sessions/one/send").status_code == 200
        assert client.post("/api/sessions/two/input").status_code == 429

    def test_local_app_is_not_rate_limited(self, rate_limited_app, monkeypatch, tmp_path):
        local = "c" * 64
        path = tmp_path / "local-token-rate"
        path.write_text(local + "\n")
        monkeypatch.setattr(auth, "LOCAL_TOKEN_FILE", path)
        client = TestClient(rate_limited_app, client=LOCAL,
                            headers={"X-Local-Token": local})
        assert all(client.post("/api/dispatch").status_code == 200
                   for _ in range(4))


class TestLocalOnlyEndpoints:
    def test_pick_folder_refuses_remote_callers(self, with_token):
        response = remote_client().post(
            "/api/pick-folder", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 200
        assert response.json()["error"] == "local only"

    def test_open_folder_refuses_remote_callers(self, with_token):
        response = remote_client().post(
            "/api/open-folder",
            json={"path": str(Path.home())},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert response.json()["error"] == "local only"


class TestTokenFile:
    def test_write_token_stores_to_keychain(self, monkeypatch, tmp_path):
        """write_token goes to keychain; the returned path is TOKEN_FILE (for callers
        that used to check the file path)."""
        written = {}
        monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "state" / "token")
        monkeypatch.setattr(auth, "_keychain_write", lambda account, value: written.update({account: value}) or True)
        monkeypatch.setattr(auth, "_keychain_read", lambda account: written.get(account, ""))
        monkeypatch.delenv("OSA_KIRO_TOKEN", raising=False)
        path = auth.write_token(TOKEN)
        assert written.get(auth._KC_REMOTE_ACCOUNT) == TOKEN
        assert path == tmp_path / "state" / "token"
        assert auth.read_token() == TOKEN

    def test_write_token_falls_back_to_file_when_keychain_fails(self, monkeypatch, tmp_path):
        """If keychain is unavailable, write_token writes to the file so the
        token is never silently lost (e.g. macOS permission prompt timed out)."""
        state = tmp_path / "state"
        token_file = state / "token"
        monkeypatch.setattr(auth, "STATE_DIR", state)
        monkeypatch.setattr(auth, "TOKEN_FILE", token_file)
        monkeypatch.setattr(auth, "_keychain_write", lambda account, value: False)
        monkeypatch.setattr(auth, "_keychain_read", lambda account: "")
        monkeypatch.delenv("OSA_KIRO_TOKEN", raising=False)
        auth.write_token(TOKEN)
        assert token_file.exists()
        assert token_file.read_text().strip() == TOKEN
        assert token_file.stat().st_mode & 0o777 == 0o600

    def test_generated_tokens_differ(self):
        assert auth.generate_token() != auth.generate_token()
        assert len(auth.generate_token()) == 64


class TestDeviceTokens:
    """Per-device tokens: create, validate, revoke, and auth middleware integration."""

    @pytest.fixture(autouse=True)
    def isolate_devices(self, monkeypatch, tmp_path):
        from backend import devices
        monkeypatch.setattr(devices, "DEVICES_FILE", tmp_path / "devices.json")
        monkeypatch.setattr(devices, "STATE_DIR", tmp_path)
        # Also ensure no legacy token interferes
        monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "absent")
        monkeypatch.delenv("OSA_KIRO_TOKEN", raising=False)
        yield

    def test_create_device_returns_token(self):
        from backend import devices
        result = devices.create_device("phone")
        assert "token" in result
        assert len(result["token"]) == 64
        assert result["name"] == "phone"
        assert result["id"]

    def test_list_devices_masks_token(self):
        from backend import devices
        devices.create_device("laptop")
        listing = devices.list_devices()
        assert len(listing) == 1
        assert listing[0]["name"] == "laptop"
        assert "token_prefix" in listing[0]
        assert len(listing[0]["token_prefix"]) == 8
        assert "token" not in listing[0]

    def test_validate_token_succeeds(self):
        from backend import devices
        result = devices.create_device("test")
        token = result["token"]
        name = devices.validate_token(token, "100.64.0.1")
        assert name == "test"

    def test_validate_wrong_token_returns_none(self):
        from backend import devices
        devices.create_device("test")
        assert devices.validate_token("wrong" * 16, "100.64.0.1") is None

    def test_revoke_device_removes_it(self):
        from backend import devices
        result = devices.create_device("phone")
        device_id = result["id"]
        token = result["token"]
        # Revoke
        r = devices.revoke_device(device_id)
        assert r["ok"] is True
        # Token no longer works
        assert devices.validate_token(token, "") is None

    def test_has_device_tokens_false_when_empty(self):
        from backend import devices
        assert devices.has_device_tokens() is False

    def test_has_device_tokens_true_after_create(self):
        from backend import devices
        devices.create_device("phone")
        assert devices.has_device_tokens() is True

    def test_auth_middleware_accepts_device_token(self):
        from backend import devices
        result = devices.create_device("phone")
        token = result["token"]
        client = TestClient(app, client=REMOTE)
        r = client.get("/api/sessions", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200

    def test_auth_middleware_rejects_invalid_device_token(self):
        from backend import devices
        devices.create_device("phone")
        client = TestClient(app, client=REMOTE)
        r = client.get("/api/sessions", headers={"Authorization": "Bearer invalidtoken"})
        assert r.status_code == 401

    def test_device_tokens_override_legacy_token(self, monkeypatch, tmp_path):
        """Once device tokens exist, legacy token is ignored for remote auth."""
        from backend import devices
        # Set up a legacy token
        legacy_path = tmp_path / "legacy_token"
        legacy_path.write_text("legacylegacylegacylegacylegacylegacylegacylegacylegacylega\n")
        monkeypatch.setattr(auth, "TOKEN_FILE", legacy_path)
        # Create a device token
        result = devices.create_device("phone")
        device_token = result["token"]
        client = TestClient(app, client=REMOTE)
        # Legacy token should NOT work anymore
        r = client.get("/api/sessions", headers={"Authorization": "Bearer legacylegacylegacylegacylegacylegacylegacylegacylegacylega"})
        assert r.status_code == 401
        # Device token should work
        r = client.get("/api/sessions", headers={"Authorization": f"Bearer {device_token}"})
        assert r.status_code == 200

    def test_max_devices_enforced(self):
        from backend import devices
        for i in range(20):
            r = devices.create_device(f"device-{i}")
            assert "token" in r
        # 21st should fail
        r = devices.create_device("one-too-many")
        assert "error" in r
