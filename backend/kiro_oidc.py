"""Check that a saved kiro-cli login can still be refreshed, before using it.

kiro-cli keeps one login for every process on the machine. Restoring a saved
profile whose refresh token has died (the IAM Identity Center session behind it
ran out) does not fail at the switch: it fails at the next refresh, when kiro-cli
gets `InvalidGrantException`, drops the token, and every running session loses
its login at once ("No token"). So a switch tries the refresh itself first — the
same OIDC call kiro-cli would make — and refuses when it is rejected.

Only the `kirocli:odic:*` pair is used; the legacy `codewhisperer:odic:*` rows
are carried along untouched.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

TOKEN_KEY = "kirocli:odic:token"
REGISTRATION_KEY = "kirocli:odic:device-registration"

VALID = "valid"        # access token still good, or the refresh just worked
EXPIRED = "expired"    # the server rejected the login: it needs a fresh one
UNKNOWN = "unknown"    # could not tell (offline, unexpected answer)

# An access token this close to expiry is refreshed rather than trusted: the
# sessions about to read it would only have to refresh it straight away.
_MIN_LEFT = timedelta(minutes=10)
_TIMEOUT = 8
# Errors that mean the login itself is dead, not that the call went wrong.
_DEAD = {"invalid_grant", "invalid_client", "unauthorized_client",
         "InvalidGrantException", "InvalidClientException",
         "UnauthorizedClientException", "ExpiredTokenException"}


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _format_time(dt: datetime) -> str:
    # The shape kiro-cli writes: microseconds, a literal Z.
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _post(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def check(rows: list[dict], now: datetime | None = None) -> tuple[str, list[dict] | None]:
    """Return (status, refreshed_rows).

    `refreshed_rows` is the same rows with a new access token (and refresh token,
    if the server rotated it) when a refresh was made, else None. Never raises.
    """
    now = now or datetime.now(timezone.utc)
    by_key = {r.get("key"): r for r in rows}
    try:
        token = json.loads(by_key[TOKEN_KEY]["value"])
        reg = json.loads(by_key[REGISTRATION_KEY]["value"])
    except (KeyError, TypeError, ValueError):
        return UNKNOWN, None  # not a shape this can check; let kiro-cli judge
    if not token.get("refresh_token") or not reg.get("client_id") or not reg.get("client_secret"):
        return UNKNOWN, None

    expires = _parse_time(token.get("expires_at", ""))
    if expires and expires - now > _MIN_LEFT:
        return VALID, None
    secret_expires = _parse_time(reg.get("client_secret_expires_at", ""))
    if secret_expires and secret_expires <= now:
        return EXPIRED, None  # the client registration itself ran out

    region = token.get("region") or reg.get("region") or "us-east-1"
    try:
        status, data = _post(f"https://oidc.{region}.amazonaws.com/token", {
            "clientId": reg["client_id"],
            "clientSecret": reg["client_secret"],
            "grantType": "refresh_token",
            "refreshToken": token["refresh_token"],
        })
    except Exception:
        return UNKNOWN, None
    if status == 200 and data.get("accessToken"):
        token = dict(token, access_token=data["accessToken"],
                     expires_at=_format_time(now + timedelta(seconds=int(data.get("expiresIn") or 3600))))
        if data.get("refreshToken"):
            token["refresh_token"] = data["refreshToken"]
        new_rows = [dict(r, value=json.dumps(token)) if r.get("key") == TOKEN_KEY else r
                    for r in rows]
        return VALID, new_rows
    error = str(data.get("error") or data.get("__type") or "").split("#")[-1]
    if 400 <= status < 500 and error in _DEAD:
        return EXPIRED, None
    return UNKNOWN, None
