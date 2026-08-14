"""Append-only record of what was done through this API, and by which device.

Why this exists, stated plainly: **any request that gets past the token is
arbitrary code execution on this machine.** That is inherent to the feature — the
app types into a shell-capable agent — so the question is not whether it can
happen but whether it can be reconstructed afterwards. Without a log there is no
way to answer "what did it do while I wasn't looking", which is the first
question after any incident and the one the app currently cannot answer at all.

Three kinds of record land here:

* `request` — a mutating API call: method, path, the device it came from, and a
  redacted payload. Written by the middleware, so nothing has to remember to
  log; an endpoint added tomorrow is covered by having been added.
* `decision` — a held tool call allowed or denied, and by whom. The point of the
  approval gate is that a human answered; this is the part that makes that
  claim checkable.
* `tool` — every tool call a hooked session made, with its input and whether it
  succeeded. Written by the `postToolUse` hook rather than by Quarterdeck, so it
  covers sessions someone started by hand as well as ones Quarterdeck launched.

Three design constraints worth knowing before changing anything here:

**Records are bounded, and the bound is not cosmetic.** The hook and the backend
append to the same file from different processes. A single `write()` to a file
opened `O_APPEND` will not interleave with another process's write as long as it
is under `PIPE_BUF` (512 bytes guaranteed by POSIX, 4096 in practice on macOS
and Linux). So `MAX_RECORD` keeps a line small enough to be atomic without a
cross-process lock, which is the only reason this can be a plain text file that
two unrelated processes append to.

**One switch, two representations.** `audit` in settings.json is the switch. The
flag file mirrors it because the shell hook cannot afford to parse JSON on every
tool call — it is one `[ -f ]` and out when auditing is off. `set_enabled` keeps
the two in step; nothing else should write either.

**Never raise.** A logging failure must not break the call being logged. Every
public function here swallows its own errors, because a full disk turning into a
500 on `/dispatch` would be a worse outcome than a missing line.
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import STATE_DIR, read_settings

AUDIT_DIR = STATE_DIR / "audit"
# Read by the postToolUse hook, which is a shell command and cannot cheaply read
# settings.json. Presence means "record tool calls"; see set_enabled.
AUDIT_FLAG = AUDIT_DIR / "on"
SETTINGS_KEY = "audit"

# Kept under PIPE_BUF so a single append cannot interleave with the hook's — see
# the module docstring. A tool call's full output belongs in the transcript,
# which already has it; what an audit needs is that it happened and how it went.
MAX_RECORD = 3500
MAX_VALUE = 400          # per string value, before the whole-record bound applies

# Old records are evidence, not cache, so the default is generous. Section 2 of
# the roadmap owns making this configurable along with the rest of retention.
RETENTION_DAYS = 90

# Keys never written down, at any depth. The token is the one secret the app
# holds, and an audit log that records it is a liability rather than a control.
REDACT_KEYS = {"token", "password", "secret", "authorization", "cookie",
               "api_key", "apikey", "access_token"}
REDACTED = "***"

_write_lock = threading.Lock()


def configured() -> bool:
    """What settings.json says recording should be. The durable declaration."""
    return bool(read_settings().get(SETTINGS_KEY, True))


def enabled() -> bool:
    """Whether anything is being recorded, right now.

    The flag file, not the setting — one `stat` rather than parsing JSON, which
    matters because this is consulted on every request and every tool call. It is
    also the same question the shell hook asks, so there is one runtime switch
    with two readers rather than two switches that can disagree. `sync_flag()`
    reconciles it with the setting on start; `set_enabled` moves both together.
    """
    return AUDIT_FLAG.exists()


def set_enabled(on: bool) -> None:
    """Point the flag file at the setting. Callers set the setting itself."""
    try:
        if on:
            AUDIT_DIR.mkdir(parents=True, exist_ok=True)
            AUDIT_FLAG.touch()
        else:
            AUDIT_FLAG.unlink(missing_ok=True)
    except OSError:
        pass


def sync_flag() -> None:
    """Make the flag file agree with settings.json. Called on backend start."""
    set_enabled(configured())


MAX_DEPTH = 6      # deep enough for real payloads; see the collapse note below
MAX_KEYS = 40
MAX_ITEMS = 20


def _clip(value, depth: int = 0):
    """Bound and redact one payload value, recursively.

    Depth collapses *containers*, never leaves. An earlier version checked depth
    first and returned "…" for anything below the limit, which replaced every
    scalar in a nested payload with an ellipsis — a snapshot write came out as a
    kilobyte of structure with no content in it at all. A record that survives
    its own bounding has to keep the leaves and lose the nesting, not the reverse.
    """
    if isinstance(value, dict):
        if depth >= MAX_DEPTH:
            return f"{{… {len(value)} keys}}"
        return {
            k: (REDACTED if str(k).lower() in REDACT_KEYS else _clip(v, depth + 1))
            for k, v in list(value.items())[:MAX_KEYS]
        }
    if isinstance(value, list):
        if depth >= MAX_DEPTH:
            return f"[… {len(value)} items]"
        clipped = [_clip(v, depth + 1) for v in value[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            clipped.append(f"… {len(value) - MAX_ITEMS} more")
        return clipped
    if isinstance(value, str):
        return value if len(value) <= MAX_VALUE else value[:MAX_VALUE] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_VALUE]


def _path_for(when: float) -> Path:
    """One file per UTC day, so retention is a matter of deleting whole files."""
    day = datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%d")
    return AUDIT_DIR / f"{day}.jsonl"


def append(kind: str, **fields) -> None:
    """Write one record. Silent on any failure — see the module docstring."""
    if not enabled():
        return
    now = time.time()
    record = {
        "at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        **{k: _clip(v) for k, v in fields.items() if v is not None},
    }
    line = json.dumps(record, separators=(",", ":"), default=str)
    if len(line) > MAX_RECORD:
        # Drop the payload rather than the record: that something happened, and
        # who did it, matters more than the arguments it carried.
        record["truncated"] = True
        for key in ("payload", "input", "result"):
            record.pop(key, None)
        line = json.dumps(record, separators=(",", ":"), default=str)[:MAX_RECORD]
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        with _write_lock:
            with open(_path_for(now), "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError:
        pass


def actor_of(request) -> dict:
    """Which device a request came from, in the terms available today.

    There is no per-device identity yet (roadmap section 3), so the honest answer
    is the source address and whether it arrived over loopback. The shape is the
    one a named device token will slot into, so records written now stay readable
    once there is a name to put here.
    """
    client = getattr(request, "client", None)
    host = getattr(client, "host", "") or "unknown"
    from .auth import LOOPBACK_HOSTS
    return {"host": host, "via": "local" if host in LOOPBACK_HOSTS else "remote"}


def _files_newest_first() -> list[Path]:
    if not AUDIT_DIR.is_dir():
        return []
    try:
        return sorted((p for p in AUDIT_DIR.iterdir()
                       if p.is_file() and p.suffix == ".jsonl"),
                      key=lambda p: p.name, reverse=True)
    except OSError:
        return []


def read(limit: int = 200, kind: str = "", session: str = "") -> list[dict]:
    """Recent records, newest first. Reads whole days but stops once full."""
    out: list[dict] = []
    for path in _files_newest_first():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if len(out) >= limit:
                return out
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if kind and record.get("kind") != kind:
                continue
            if session and record.get("session") != session:
                continue
            out.append(record)
    return out


def sweep(days: int = RETENTION_DAYS) -> int:
    """Delete whole days older than the retention window. Returns how many went."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    removed = 0
    for path in _files_newest_first():
        if path.stem >= cutoff:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def stats() -> dict:
    """Enough to render a settings row: on/off, how many days, how big."""
    files = _files_newest_first()
    size = 0
    for path in files:
        try:
            size += path.stat().st_size
        except OSError:
            continue
    return {"enabled": enabled(), "configured": configured(),
            "days": len(files), "bytes": size,
            "retention_days": RETENTION_DAYS,
            "newest": files[0].stem if files else ""}


# --- the request record ---------------------------------------------------
#
# Recorded in middleware rather than per endpoint, deliberately. An audit trail
# assembled by remembering to call it at each of forty call sites is one that
# quietly stops covering whatever was added last — and "which endpoint did this
# device hit" is precisely the question you cannot go back and answer.

# Reads. A GET changes nothing, and logging the session list every two seconds
# would bury the records that matter in poll traffic.
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Mutating, but fired by the UI rather than by a person, and carrying nothing a
# reviewer would want: the frontend resizes the pty whenever the window changes.
IGNORED_PATHS = {"/api/sessions/resize"}
IGNORE_SUFFIXES = ("/resize",)

# The login form posts the token itself. Nothing about that request is worth more
# than the risk of writing its body down, so it is skipped entirely — the
# outcome is still visible, because a refusal is recorded by status code.
NEVER_LOG_PATHS = {"/login"}

# Whole-state writes: the client sends its entire snapshot or favourites list on
# every change, so the body is the state rather than the act. That it happened,
# and from which device, is the useful part — the payload is a kilobyte of
# unchanged rows around whichever one moved. Recorded without it.
PAYLOAD_FREE_PATHS = {"/api/snapshots", "/api/favourites"}


def _should_record(method: str, path: str, status: int) -> bool:
    if path in NEVER_LOG_PATHS:
        return False
    # A refused request is worth recording whatever its method: it is the only
    # trace of a device trying to reach an endpoint it has no token for.
    if status in (401, 403):
        return True
    if method.upper() in SAFE_METHODS:
        return False
    if path in IGNORED_PATHS or path.endswith(IGNORE_SUFFIXES):
        return False
    return path.startswith("/api/")


def install(app) -> None:
    """Attach the request recorder.

    Installed *outside* the auth middleware, so a request refused for want of a
    token is still recorded. That ordering is the point: an attempt that failed
    is more interesting than one that succeeded, and it is invisible from
    anywhere else.
    """
    from fastapi import Request

    @app.middleware("http")
    async def record_request(request: Request, call_next):
        # Read the body before the handler does. Starlette caches and replays it
        # for downstream (`_CachedRequest`), so this does not consume it — there
        # is a test that holds that guarantee down, because it is a property of
        # the framework version rather than of this code.
        body = b""
        if request.method.upper() not in SAFE_METHODS:
            try:
                body = await request.body()
            except Exception:
                body = b""

        response = await call_next(request)

        try:
            path = request.url.path
            if _should_record(request.method, path, response.status_code):
                payload = None
                if body and path not in PAYLOAD_FREE_PATHS:
                    try:
                        payload = json.loads(body)
                    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
                        payload = {"raw_bytes": len(body)}
                append("request",
                       method=request.method,
                       path=path,
                       status=response.status_code,
                       actor=actor_of(request),
                       payload=payload)
        except Exception:
            pass
        return response


# --- the postToolUse hook -------------------------------------------------
#
# The logic lives in a script Quarterdeck writes rather than in the command string
# stored in fifteen agent configs. Two reasons: a python script that reads JSON
# on stdin is far less fragile than shell string-mangling (the preToolUse hook's
# colon-separated file format is what that costs), and changing it means
# rewriting one file instead of re-installing into every agent.
#
# It is rewritten on every backend start, so it cannot drift from this source.
HOOK_SCRIPT = STATE_DIR / "audit-hook.py"
HOOK_MARKER = "deck-audit"

HOOK_SCRIPT_BODY = '''"""Written by Quarterdeck. Do not edit — rewritten on every backend start.

Appends one audit record for a tool call kiro-cli has just run. Reads the
postToolUse payload on stdin; the session id arrives in the environment, because
no hook payload carries one (measured against kiro-cli 2.14.2).
"""
import json
import os
import sys

sys.path.insert(0, {backend!r})

try:
    from backend import audit
    payload = json.load(sys.stdin)
    response = payload.get("tool_response") or {{}}
    result = response.get("result") if isinstance(response, dict) else None
    audit.append(
        "tool",
        session=os.environ.get("KIRO_SESSION_ID", ""),
        tool=payload.get("tool_name", ""),
        input=payload.get("tool_input"),
        cwd=payload.get("cwd", ""),
        ok=response.get("success") if isinstance(response, dict) else None,
        result=result,
    )
except Exception:
    # A hook that fails is a hook that wedges kiro-cli. Nothing here is worth
    # that, so every failure is silent.
    pass
'''

# One `[ -f ]` and out when auditing is off, which is what makes it acceptable to
# install this unconditionally on every tool call of every hooked session. stdin
# is drained either way: exiting without reading it risks a broken pipe on
# kiro-cli's side. Never fails, always prints {} — the rule every Quarterdeck hook obeys.
HOOK_COMMAND = (
    'if [ ! -f ~/.osa-kiro/audit/on ] || [ ! -f ~/.osa-kiro/audit-hook.py ]; then '
    'cat >/dev/null 2>&1; echo \'{}\'; exit 0; fi; '
    'python3 ~/.osa-kiro/audit-hook.py >/dev/null 2>&1; '
    "echo '{}'; exit 0"
)
HOOK_TIMEOUT_MS = 5000


def write_hook_script() -> bool:
    """(Re)write the hook script. True if it is there and current afterwards."""
    # The import root, so the script can reach this module rather than
    # duplicating the record format. Resolved at write time, not baked into a
    # config the user might carry to another checkout.
    backend_root = str(Path(__file__).resolve().parent.parent)
    body = HOOK_SCRIPT_BODY.format(backend=backend_root)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if HOOK_SCRIPT.exists() and HOOK_SCRIPT.read_text() == body:
            return True
        tmp = HOOK_SCRIPT.with_suffix(".py.tmp")
        tmp.write_text(body)
        os.replace(tmp, HOOK_SCRIPT)
        return True
    except OSError:
        return False
