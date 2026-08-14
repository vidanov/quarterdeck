"""Bearer-token auth for non-loopback access.

This API can start processes and, through `pre_command`, run arbitrary shell
commands on the host. The token is the only thing between a device that can
reach the port and code execution on this Mac. So: loopback keeps working with
no token (the pywebview app and local dev), and tailnet callers are refused
unless a token is configured *and* presented. Non-tailnet source addresses are
refused before authentication, so accidentally binding to `0.0.0.0` does not
turn a valid token into public-internet access.

Fail closed. With no token configured, remote requests are rejected outright
rather than allowed through.
"""
import asyncio
import hmac
import ipaddress
import math
import os
import re
import secrets
import subprocess
import threading
import time
from collections import deque
from html import escape
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .config import STATE_DIR

TOKEN_FILE = STATE_DIR / "token"
LOCAL_TOKEN_FILE = STATE_DIR / "local-token"

# macOS keychain service name — stable across renames/moves.
_KC_SERVICE = "com.vidanov.quarterdeck"
_KC_REMOTE_ACCOUNT = "remote-token"
_KC_LOCAL_ACCOUNT = "local-token"


def _keychain_read(account: str) -> str:
    """Read a generic password from the login keychain. Returns "" on miss."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-s", _KC_SERVICE, "-a", account, "-w"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def _keychain_write(account: str, value: str) -> bool:
    """Write a generic password to the login keychain. Returns True on success.

    Tries to update an existing item first; adds if it doesn't exist yet.
    """
    try:
        # Try update first (item already exists)
        result = subprocess.run(
            ["security", "add-generic-password",
             "-s", _KC_SERVICE, "-a", account,
             "-w", value, "-U"],
            capture_output=True, text=True, timeout=3,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _keychain_delete(account: str) -> None:
    """Remove a keychain item. Silent if absent."""
    try:
        subprocess.run(
            ["security", "delete-generic-password",
             "-s", _KC_SERVICE, "-a", account],
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
COOKIE_NAME = "osa_kiro_token"
COOKIE_MAX_AGE = 30 * 24 * 3600

# Loopback forms we may see in `request.client.host`, including the
# IPv4-mapped-IPv6 spelling uvicorn reports on a dual-stack bind.
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}

# Tailscale's assigned address ranges. Checking the socket peer rather than a
# forwarding header is deliberate: X-Forwarded-For is caller-controlled unless
# a trusted reverse proxy is configured, and Quarterdeck does not configure one.
TAILSCALE_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)

# Paths reachable without a token: the login form itself, and the favicon the
# browser fetches before the user has authenticated.
PUBLIC_PATHS = {"/login", "/favicon.ico"}

# A shared token is powerful enough to dispatch processes and type into agents.
# These limits do not make a stolen token safe; they bound how quickly it can be
# abused while keeping normal phone use well below the ceiling. `/send` is the
# deprecated alias for `/input` and must share its bucket or it becomes a bypass.
# So does `/api/shell/input`, which types into a login shell: it is the most
# direct command execution in the API, and leaving it out of this regex would
# have made it the one unmetered way in.
RATE_WINDOW_SECONDS = 60.0
DISPATCH_RATE_LIMIT = 10
INPUT_RATE_LIMIT = 60
_INPUT_PATH = re.compile(
    r"^/api/(?:sessions/[^/]+/(?:input|send)|shell/(?:input|key))$")
_rate_windows: dict[tuple[str, str], deque[float]] = {}
_rate_lock = threading.Lock()

# Reachability: timestamp of the last successfully authenticated remote request.
_last_remote_request: float | None = None
_last_remote_ip: str = ""


def last_remote_request_info() -> dict:
    """When and from where the last authenticated remote request came."""
    return {
        "last_remote_at": _last_remote_request,
        "last_remote_ip": _last_remote_ip,
    }


def read_token() -> str:
    """Remote token — keychain first, then legacy file, then env var.

    Keychain is preferred: unlike a file it is not visible to other processes
    running as the same user, and unlike an env var it is not in `ps` output or
    shell history. The file fallback keeps existing installs working without a
    migration step; env var is a last resort for CI / headless environments
    where the keychain is unavailable.
    """
    value = _keychain_read(_KC_REMOTE_ACCOUNT)
    if value:
        return value
    try:
        if TOKEN_FILE.exists():
            value = TOKEN_FILE.read_text().strip()
            if value:
                # Migrate on first read: move to keychain, remove the file.
                if _keychain_write(_KC_REMOTE_ACCOUNT, value):
                    try:
                        TOKEN_FILE.unlink()
                    except OSError:
                        pass
                return value
    except OSError:
        pass
    return os.environ.get("OSA_KIRO_TOKEN", "").strip()


def write_token(value: str) -> Path:
    """Persist the remote token. Keychain first; file fallback if keychain fails.

    The file fallback prevents silent token loss if the keychain is unavailable
    (first-run permission prompt timing out, sandboxing, CI). Both sources are
    checked on read so the file fallback is always honoured even when keychain
    later becomes available.

    Verifies the write by reading back: if the keychain round-trip fails or
    returns the wrong value, falls through to the file so "Rotate token" is
    never a silent no-op.
    """
    keychain_ok = False
    if _keychain_write(_KC_REMOTE_ACCOUNT, value):
        # Verify the write took — a silent failure here would leave the old
        # token authoritative and make token rotation a no-op.
        readback = _keychain_read(_KC_REMOTE_ACCOUNT)
        keychain_ok = (readback == value)

    if keychain_ok:
        # Keychain confirmed — remove legacy file to avoid divergence.
        try:
            if TOKEN_FILE.exists():
                TOKEN_FILE.unlink()
        except OSError:
            pass
    else:
        # Keychain unavailable or readback mismatch: fall back to file.
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(value + "\n")
        TOKEN_FILE.chmod(0o600)
    return TOKEN_FILE


def generate_token() -> str:
    return secrets.token_hex(32)


# Module-level cache: set once by ensure_local_token() at startup.
# Never re-read from keychain per-request — each call to security is a
# subprocess fork that can stall on a macOS permission dialog.
_local_token_cache: str = ""


def ensure_local_token() -> str:
    """Return the local token, generating and persisting it if absent.

    Stored in the keychain under the local-token account. Falls back to the
    legacy file for existing installs and migrates on first read.
    Sets the module-level cache so all subsequent calls to read_local_token()
    are instant (no subprocess, no I/O).
    """
    global _local_token_cache
    if _local_token_cache:
        return _local_token_cache

    value = _keychain_read(_KC_LOCAL_ACCOUNT)
    if value:
        _local_token_cache = value
        return value
    # File fallback — migrate existing installs silently.
    try:
        if LOCAL_TOKEN_FILE.exists():
            value = LOCAL_TOKEN_FILE.read_text().strip()
            if value:
                if _keychain_write(_KC_LOCAL_ACCOUNT, value):
                    try:
                        LOCAL_TOKEN_FILE.unlink()
                    except OSError:
                        pass
                _local_token_cache = value
                return value
    except OSError:
        pass
    # Generate fresh.
    value = secrets.token_hex(32)
    if not _keychain_write(_KC_LOCAL_ACCOUNT, value):
        # Keychain unavailable (headless / CI) — fall back to file.
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        LOCAL_TOKEN_FILE.write_text(value + "\n")
        LOCAL_TOKEN_FILE.chmod(0o600)
    _local_token_cache = value
    return value


def read_local_token() -> str:
    """Return the cached local token. Falls back to keychain/file on miss.

    The cache is populated by ensure_local_token() at startup. This function
    never spawns a subprocess during normal operation.
    """
    if _local_token_cache:
        return _local_token_cache
    # Cold path: called before ensure_local_token (tests, dev mode).
    value = _keychain_read(_KC_LOCAL_ACCOUNT)
    if value:
        return value
    try:
        return LOCAL_TOKEN_FILE.read_text().strip()
    except OSError:
        return ""


def is_loopback(request: Request) -> bool:
    client = request.client
    return bool(client) and client.host in LOOPBACK_HOSTS


def is_tailnet_host(host: str) -> bool:
    """Whether a socket peer is a Tailscale-assigned IPv4 or IPv6 address."""
    if not host:
        return False
    # IPv6 scope ids (`%utun3`) are not part of the address itself.
    candidate = host.strip("[]").split("%", 1)[0]
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    # Treat an IPv4-mapped IPv6 peer as its underlying IPv4 address.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address in network for network in TAILSCALE_NETWORKS
               if address.version == network.version)


def is_allowed_source(request: Request) -> bool:
    client = request.client
    if not client:
        return False
    return is_loopback(request) or is_tailnet_host(client.host)


def token_matches(presented: str, expected: str) -> bool:
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented, expected)


# --- QR exchange codes ----------------------------------------------------
#
# The QR used to encode `/login?t=<token>`, which put the real, long-lived,
# shared secret into a URL. Query strings are written down in places nobody
# thinks about: server access logs, browser history, and the Referer header of
# any outbound link from the page you land on. Tailscale encrypts the wire, so
# this was never an interception problem — it was a "the secret is now in three
# log files" problem.
#
# So the QR carries a code instead. Random, single-use, short-lived, and worth
# nothing after it is redeemed. The token itself never leaves the Mac except as
# the cookie the exchange sets.
#
# The codes live on disk, and that is the whole point rather than an
# implementation detail: the QR is rendered by the desktop app's backend, and
# the phone talks to the *separate* uvicorn that `/api/remote/start` spawns on
# the Tailscale address. Two processes. Held in memory the code was minted in
# one and looked for in the other, so scanning the QR always landed on the token
# form — the auto-login could not work in the only configuration it exists for.
# Every test passed, because tests run in one process.
#
# `unlink` is the single-use primitive: whichever process removes the file first
# is the one that redeemed the code, so no cross-process lock is needed.
CODE_TTL = 120.0          # long enough to walk to the phone and scan
CODES_DIR = STATE_DIR / "codes"
# The code arrives as a query parameter and becomes a filename, so it is checked
# against the alphabet `token_urlsafe` produces before it touches the
# filesystem. Nothing with a slash or a dot in it gets that far.
_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def _sweep_codes(now: float) -> None:
    """Drop codes nobody redeemed. Called on mint, the only other write."""
    try:
        for path in CODES_DIR.iterdir():
            try:
                if now - path.stat().st_mtime > CODE_TTL:
                    path.unlink()
            except OSError:
                continue
    except OSError:
        pass


def mint_exchange_code() -> str:
    """A fresh single-use code, valid for CODE_TTL seconds."""
    code = secrets.token_urlsafe(16)
    CODES_DIR.mkdir(parents=True, exist_ok=True)
    # 0700: a code is worth the token for two minutes, so it is no more readable
    # than the token file itself.
    CODES_DIR.chmod(0o700)
    _sweep_codes(time.time())
    path = CODES_DIR / code
    path.touch()
    path.chmod(0o600)
    return code


def redeem_exchange_code(code: str) -> bool:
    """True if the code was valid. Burns it either way it is found."""
    if not code or not _CODE_RE.match(code):
        return False
    path = CODES_DIR / code
    try:
        born = path.stat().st_mtime
        path.unlink()
    except OSError:
        # Missing, already burned by another process, or unreadable.
        return False
    return time.time() - born <= CODE_TTL


def presented_token(request: Request) -> str:
    """Bearer header first, then the cookie set by the login form."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return request.cookies.get(COOKIE_NAME, "")


async def read_urlencoded(request: Request) -> dict[str, str]:
    """Parse an `application/x-www-form-urlencoded` body.

    Starlette's `request.form()` would need `python-multipart` as a runtime
    dependency, and the login form is the only form in the app — it posts with
    the default encoding, so `parse_qs` is enough.
    """
    body = (await request.body()).decode("utf-8", "replace")
    return {key: values[0] for key, values in parse_qs(body, keep_blank_values=True).items()}


def wants_html(request: Request) -> bool:
    """True for a browser navigation, as opposed to a fetch/curl call."""
    return "text/html" in request.headers.get("Accept", "")


def _rate_rule(request: Request) -> tuple[str, int] | None:
    if request.method != "POST":
        return None
    path = request.url.path
    if path == "/api/dispatch":
        return ("dispatch", DISPATCH_RATE_LIMIT)
    if _INPUT_PATH.fullmatch(path):
        return ("input", INPUT_RATE_LIMIT)
    return None


def _rate_limit(request: Request, now: float | None = None) -> tuple[int, int] | None:
    """Consume one request, or return (limit, retry_after_seconds).

    Buckets are per socket peer and per operation class. The caller is already
    authenticated before this runs.
    """
    rule = _rate_rule(request)
    client = request.client
    if rule is None or client is None:
        return None
    bucket, limit = rule
    current = time.monotonic() if now is None else now
    cutoff = current - RATE_WINDOW_SECONDS
    key = (client.host, bucket)
    with _rate_lock:
        window = _rate_windows.setdefault(key, deque())
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= limit:
            retry_after = max(1, math.ceil(window[0] + RATE_WINDOW_SECONDS - current))
            return limit, retry_after
        window.append(current)
    return None


LOGIN_PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Quarterdeck</title>
<style>
  body {{ font: 15px -apple-system, system-ui, sans-serif; background: #0f172a;
         color: #e2e8f0; display: flex; min-height: 100vh; margin: 0;
         align-items: center; justify-content: center; }}
  form {{ width: min(340px, 88vw); display: flex; flex-direction: column; gap: 12px; }}
  h1 {{ font-size: 17px; margin: 0 0 4px; font-weight: 600; }}
  input {{ font-size: 16px; padding: 12px; border-radius: 8px; border: 1px solid #334155;
           background: #1e293b; color: #e2e8f0; }}
  button {{ font-size: 16px; padding: 12px; border-radius: 8px; border: 0;
            background: #2563eb; color: #fff; font-weight: 600; }}
  .err {{ color: #f87171; font-size: 13px; min-height: 16px; }}
</style>
<form method="post" action="/login">
  <h1>Quarterdeck</h1>
  <input type="hidden" name="next" value="{next}">
  <input type="password" name="token" placeholder="Access token" autofocus
         autocomplete="current-password" autocapitalize="off" spellcheck="false">
  <div class="err">{error}</div>
  <button type="submit">Unlock</button>
</form>
"""


def login_page(next_url: str = "/app/", error: str = "", status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        LOGIN_PAGE.format(next=escape(next_url, quote=True), error=escape(error)),
        status_code=status,
    )


def safe_next(value: str) -> str:
    """Only allow same-site relative redirects.

    `//evil.com` is protocol-relative and would leave the site, so a second
    slash disqualifies it just as a scheme does.
    """
    if not value.startswith("/") or value.startswith("//"):
        return "/app/"
    return value


def _authenticate_token(request: Request) -> bool:
    """Check if the presented token is valid.

    Tries device tokens first (if any exist), then falls back to legacy single
    token. This allows a gradual migration: create device tokens, and the legacy
    token becomes irrelevant without breaking existing sessions.
    """
    from . import devices

    presented = presented_token(request)
    if not presented:
        return False

    # If device tokens are configured, they take precedence.
    if devices.has_device_tokens():
        client_ip = request.client.host if request.client else ""
        device_name = devices.validate_token(presented, client_ip)
        return device_name is not None

    # Fall back to legacy single token.
    expected = read_token()
    return token_matches(presented, expected)


def _any_token_configured() -> bool:
    """True if at least one authentication method (device or legacy) is set."""
    from . import devices
    if devices.has_device_tokens():
        return True
    return bool(read_token())


def install(app) -> None:
    """Attach the auth middleware and the login routes."""

    @app.middleware("http")
    async def check_auth(request: Request, call_next):
        if not is_allowed_source(request):
            return JSONResponse(
                {
                    "error": "source not allowed",
                    "detail": "Quarterdeck accepts remote requests only from Tailscale addresses.",
                },
                status_code=403,
            )

        if is_loopback(request):
            # GET requests stay open — safe for dev tools and curl debugging.
            # Mutating methods require the local token so that any process on
            # this machine (compromised dependency, rogue script) cannot write
            # to sessions or dispatch new ones without the key.
            # If no local token file exists yet (first run before app.py has
            # called ensure_local_token), fall through — fail-open preserves
            # backward compatibility for dev mode and tests.
            #
            # Exception: the TCP proxy forwards mobile connections through
            # loopback, so they arrive here as loopback requests but carry a
            # valid device token. Accept those — they already authenticated
            # before reaching the proxy.
            if request.method in ("GET", "HEAD", "OPTIONS"):
                return await call_next(request)
            # If a valid device token is present, this is a proxied mobile
            # request — bypass the local-token check.
            if _authenticate_token(request):
                limited = _rate_limit(request)
                if limited:
                    limit, retry_after = limited
                    return JSONResponse(
                        {"error": "rate limit exceeded", "limit": limit,
                         "window_seconds": int(RATE_WINDOW_SECONDS),
                         "retry_after": retry_after},
                        status_code=429,
                        headers={"Retry-After": str(retry_after)},
                    )
                return await call_next(request)
            local = read_local_token()
            if not local:
                # Token not yet generated — allow through (startup or dev mode)
                return await call_next(request)
            presented = request.headers.get("X-Local-Token", "")
            if hmac.compare_digest(presented, local):
                return await call_next(request)
            return JSONResponse(
                {"error": "local token required",
                 "detail": "Mutating requests from loopback must carry X-Local-Token."},
                status_code=401,
            )

        # Preflight carries no credentials by spec and triggers no side effects.
        if request.method == "OPTIONS":
            return await call_next(request)

        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        if not _any_token_configured():
            return JSONResponse(
                {"error": "remote access disabled",
                 "detail": "No token configured. See ./remote.sh."},
                status_code=403,
            )

        if _authenticate_token(request):
            global _last_remote_request, _last_remote_ip
            _last_remote_request = time.time()
            _last_remote_ip = request.client.host if request.client else ""
            limited = _rate_limit(request)
            if limited:
                limit, retry_after = limited
                return JSONResponse(
                    {
                        "error": "rate limit exceeded",
                        "limit": limit,
                        "window_seconds": int(RATE_WINDOW_SECONDS),
                        "retry_after": retry_after,
                    },
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
            return await call_next(request)

        if wants_html(request):
            target = f"/login?next={request.url.path}"
            return RedirectResponse(target, status_code=303)
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    @app.get("/login", include_in_schema=False)
    async def login_form(request: Request):
        if not _any_token_configured() and not is_loopback(request):
            return JSONResponse({"error": "remote access disabled"}, status_code=403)
        if _authenticate_token(request):
            return RedirectResponse(safe_next(request.query_params.get("next", "/app/")), 303)
        # QR login. The code in ?c= is single-use and expires in two minutes;
        # redeeming it sets the same cookie the form post would. The real token
        # is never in the URL, so nothing durable is written to the phone's
        # history or this server's access log. See mint_exchange_code above.
        next_url = safe_next(request.query_params.get("next", "/app/"))
        if _any_token_configured() and redeem_exchange_code(request.query_params.get("c", "")):
            from . import devices
            # Use device token for cookie if available, else legacy
            cookie_value = devices.get_token_for_cookie() or read_token()
            response = RedirectResponse(next_url, status_code=303)
            response.set_cookie(
                COOKIE_NAME, cookie_value,
                max_age=COOKIE_MAX_AGE,
                httponly=True,
                samesite="strict",
                path="/",
            )
            return response
        return login_page(next_url)

    @app.post("/login", include_in_schema=False)
    async def login_submit(request: Request):
        form = await read_urlencoded(request)
        next_url = safe_next(form.get("next", "/app/"))
        if not _any_token_configured():
            return JSONResponse({"error": "remote access disabled"}, status_code=403)

        presented = form.get("token", "")
        # Validate against device tokens or legacy
        from . import devices
        valid = False
        cookie_value = ""
        if devices.has_device_tokens():
            device_name = devices.validate_token(presented, request.client.host if request.client else "")
            if device_name:
                valid = True
                cookie_value = presented
        else:
            expected = read_token()
            if token_matches(presented, expected):
                valid = True
                cookie_value = expected

        if not valid:
            # The token is 64 hex chars, so brute force is not the threat here;
            # the delay just keeps a misconfigured client from hammering.
            await asyncio.sleep(0.5)
            return login_page(next_url, "Wrong token.", status=401)

        response = RedirectResponse(next_url, status_code=303)
        response.set_cookie(
            COOKIE_NAME, cookie_value,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="strict",  # the CSRF control — no TLS, so `secure` would break it
            path="/",
        )
        return response
