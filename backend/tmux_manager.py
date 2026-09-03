"""tmux-backed process management for kiro-cli sessions.

Each managed kiro session runs in its own detached tmux session named
`kiro-<session_id>`, so it survives backend restarts and stays attachable from
any terminal on the machine via `tmux attach -t kiro-<session_id>`.

kiro-cli picks its own session id and only reveals it by writing
`~/.kiro/sessions/cli/<id>.json`, so spawning is two-phase: start tmux under a
placeholder name, then correlate and rename. See resolve_pending().

No FastAPI imports here on purpose — this module is testable standalone.
"""
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .cache import LruCache
from .config import (
    CAPTURE_LINES,
    DEFAULT_COLS,
    DEFAULT_ROWS,
    DEFAULT_TRUST_TOOLS,
    MAX_COLS,
    MAX_ROWS,
    MIN_COLS,
    MIN_ROWS,
    EFFORTS,
    KIRO_CLI,
    available_models,
    MANAGED_FILE,
    PENDING_PREFIX,
    SESSIONS_DIR,
    SPAWN_HOOK_GRACE,
    SPAWN_HOOK_TTL,
    SPAWN_POLL,
    SPAWN_TIMEOUT,
    SPAWNS_DIR,
    STATE_DIR,
    ADOPT_LIMIT,
    DEAD_PANE_TTL,
    TMUX_CONF,
    TMUX_PREFIX,
    tmux_base_argv,
    TURN_MARK_TTL,
    TURNS_DIR,
    APPROVALS_DIR,
    APPROVAL_TIMEOUT,
    GATES_DIR,
    STACKS_DIR,
)


class TmuxError(RuntimeError):
    """A tmux command failed."""


# --- tmux primitives ---

# Cache for tmux_available() — set on first call, never reset (tmux doesn't
# get uninstalled while the server is running).
_tmux_available_cache: bool | None = None

_server_start_reported = False


def _note_cold_start(args: tuple[str, ...]) -> None:
    """Print a line when this process is the one starting the tmux server.

    A cold start is the only moment a tmux config is read, and therefore the
    only moment a plugin-driven session restore can fire. When a burst of
    sessions appears seconds after Quarterdeck dispatched one, this line is the
    difference between "Quarterdeck leaked spawns" and "Quarterdeck started a
    server and something else filled it".
    """
    global _server_start_reported
    if _server_start_reported or not args or args[0] != "new-session":
        return
    _server_start_reported = True
    try:
        probe = subprocess.run(
            [*tmux_base_argv(), "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return  # diagnostics are never worth failing a spawn over
    if probe.returncode == 0 and probe.stdout.strip():
        return  # server already up — our config is not in play
    conf = TMUX_CONF or "user config (QUARTERDECK_TMUX_CONF=none)"
    print(f"[deck] cold-starting tmux server with {conf}", file=sys.stderr)


def _tmux(*args: str, check: bool = True, timeout: float = 10) -> str:
    """Run a tmux command and return stdout. Raises TmuxError on failure.

    Every call carries `-f` with Quarterdeck's own server config — see
    config.tmux_base_argv. tmux only reads it when it has to start a server,
    so this changes nothing when one is already running.
    """
    _note_cold_start(args)
    try:
        result = subprocess.run(
            [*tmux_base_argv(), *args], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        raise TmuxError("tmux not installed — brew install tmux")
    except subprocess.TimeoutExpired:
        raise TmuxError(f"tmux {' '.join(args)} timed out")
    if check and result.returncode != 0:
        raise TmuxError(f"tmux {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def tmux_available() -> bool:
    """Check tmux is installed and its server is reachable.

    Result is cached for the process lifetime — tmux does not get uninstalled
    while the server is running, and the first call can take 50-80 ms on macOS
    due to process startup cost when the binary is not yet in the kernel's
    exec cache.
    """
    global _tmux_available_cache
    if _tmux_available_cache is not None:
        return _tmux_available_cache
    try:
        _tmux("-V")
        _tmux_available_cache = True
    except TmuxError:
        _tmux_available_cache = False
    return _tmux_available_cache


_list_sessions_cache: dict = {}


def list_tmux_sessions() -> list[str]:
    """Names of all live tmux sessions. Empty when no server is running.
    
    Cached for 1 second — the callers (managed_sessions, is_managed) run on
    every poll cycle; a fresh subprocess call per cycle adds up fast.
    """
    import time as _t
    now = _t.time()
    cached = _list_sessions_cache.get("data")
    if cached is not None and (now - _list_sessions_cache.get("ts", 0)) < 1.0:
        return cached
    # Short timeout: this runs on the poll path, and a tmux server that has
    # wedged must fail the poll rather than park a request thread for 10s.
    out = _tmux("list-sessions", "-F", "#{session_name}", check=False, timeout=3)
    result = [line for line in out.splitlines() if line]
    _list_sessions_cache["data"] = result
    _list_sessions_cache["ts"] = now
    return result


def session_exists(name: str) -> bool:
    """True if the named tmux session exists.

    Uses the cached session list from list_tmux_sessions() to avoid a separate
    subprocess call per session — the list call is already cached at 1s TTL.
    """
    return name in list_tmux_sessions()


def pane_pid(name: str) -> int:
    """Pid of the process running in the session's pane, or 0 if unknown."""
    out = _tmux("list-panes", "-t", name, "-F", "#{pane_pid}", check=False, timeout=3)
    first = out.strip().splitlines()
    try:
        return int(first[0])
    except (IndexError, ValueError):
        return 0


def pane_dead(name: str) -> bool:
    """True if the tmux session exists but its process has exited.

    Sessions are created with remain-on-exit so a crashed kiro-cli leaves its
    error visible in the pane instead of taking the tmux session down with it.
    """
    out = _tmux("list-panes", "-t", name, "-F", "#{pane_dead}", check=False, timeout=3)
    return out.strip().startswith("1")


def tmux_name(session_id: str) -> str:
    return f"{TMUX_PREFIX}{session_id}"


def attach_command(session_id: str) -> str:
    """Shell command a user can run locally to attach to this session."""
    return f"tmux attach -t {tmux_name(session_id)}"


# --- state persistence ---

def _empty_state() -> dict:
    # `unclaimed` holds stray kiro-* tmux sessions reconcile() refused to adopt
    # because too many showed up at once — see ADOPT_LIMIT. Keyed by tmux name,
    # because a stray has no entry anywhere else to hang the record off.
    return {"managed": {}, "pending": {}, "unclaimed": {}}


def load_state() -> dict:
    """Read managed.json. Returns an empty state if missing or corrupt."""
    if not MANAGED_FILE.exists():
        return _empty_state()
    try:
        data = json.loads(MANAGED_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    state = _empty_state()
    for key in ("managed", "pending", "unclaimed"):
        if isinstance(data.get(key), dict):
            state[key] = data[key]
    return state


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MANAGED_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(MANAGED_FILE)  # atomic, so a crash mid-write can't truncate state


def is_managed(session_id: str) -> bool:
    """True if we own this session's tmux session and it still exists."""
    if session_id not in load_state()["managed"]:
        return False
    return session_exists(tmux_name(session_id))


# --- session id correlation ---

def _real(path: str) -> str:
    """Resolve symlinks so /tmp and /private/tmp compare equal on macOS."""
    return os.path.realpath(os.path.expanduser(path))


# --- the agentSpawn hook: kiro-cli telling us its own session id ---

# The environment variable Quarterdeck sets on the tmux session, and the one kiro-cli
# sets for hook commands. The pairing of the two is the whole mechanism.
NONCE_ENV = "DECK_NONCE"
SESSION_ENV = "KIRO_SESSION_ID"

# Kept on one line because it is stored verbatim in the user's agent config, and
# a multi-line command there is unreadable and easy to corrupt. Rules it obeys:
#   * do nothing unless Quarterdeck started this session (DECK_NONCE present)
#   * never fail — always print {} and exit 0, so a broken Quarterdeck cannot wedge
#     kiro-cli, which is the pattern the CMUX hooks already use
#   * touch only one file, named by a nonce Quarterdeck generated
SPAWN_HOOK_COMMAND = (
    'if [ -n "$DECK_NONCE" ] && [ -n "$KIRO_SESSION_ID" ]; then '
    'mkdir -p ~/.osa-kiro/spawns 2>/dev/null && '
    'printf %s "$KIRO_SESSION_ID" > ~/.osa-kiro/spawns/"$DECK_NONCE" 2>/dev/null; '
    "fi; echo '{}'"
)
# The `stop` hook: kiro-cli saying "this turn is over".
#
# Deliberately *not* guarded by DECK_NONCE, unlike the spawn hook. A session
# someone started by hand has no pane for Quarterdeck to read, so its idle-vs-thinking
# was a guess about file mtimes; this makes it an event for those too. The cost
# is one small file per session, swept on a fortnight's timer.
STOP_HOOK_COMMAND = (
    'if [ -n "$KIRO_SESSION_ID" ]; then '
    'mkdir -p ~/.osa-kiro/turns 2>/dev/null && '
    'touch ~/.osa-kiro/turns/"$KIRO_SESSION_ID" 2>/dev/null; '
    # Chain the claim-detector in the background so it never delays the agent.
    # The script is installed by Quarterdeck into ~/.osa-kiro/hooks/ so it works
    # even when the repo is not on PATH.
    'if [ -x ~/.osa-kiro/hooks/verify-claim.sh ]; then '
    'KIRO_SESSION_ID="$KIRO_SESSION_ID" ~/.osa-kiro/hooks/verify-claim.sh & '
    'fi; '
    "fi; echo '{}'"
)

# The `preToolUse` hook: kiro-cli asking "may I run this tool?".
#
# Protocol: the hook writes the request to a file, then polls until Quarterdeck
# signals allow (renames with "." prefix) or deny (renames with "!" prefix),
# or the timeout expires (deny by default — safe failure mode).
# Exit code 2 blocks the tool; exit code 0 allows it.
#
# Installed into every agent config, but *opt-in per session*: unless a gate
# file exists for this session it returns immediately, so a session that never
# asked to be gated never blocks and never raises a banner. The gate is keyed by
# session id, or — for a spawn whose id has not been correlated yet — by the
# nonce Quarterdeck put in the environment, so gating can be decided before the first
# tool call rather than only after the session appears in the UI.
PRETOOL_HOOK_COMMAND = (
    # Read stdin first, unconditionally: kiro-cli writes the payload to this
    # process, and exiting without draining it risks a broken pipe on its side.
    'HOOK_PAYLOAD=$(cat); '
    'if [ -z "$KIRO_SESSION_ID" ]; then echo "{}"; exit 0; fi; '
    'if [ ! -f ~/.osa-kiro/gates/"$KIRO_SESSION_ID" ] && '
    '{ [ -z "$DECK_NONCE" ] || [ ! -f ~/.osa-kiro/gates/n-"$DECK_NONCE" ]; }; '
    'then echo "{}"; exit 0; fi; '
    # Extract tool_name and tool_input; fall back gracefully if python3 absent.
    'TOOL_NAME=$(echo "$HOOK_PAYLOAD" | python3 -c '
    '"import sys,json; d=json.load(sys.stdin); print(d.get(\'tool_name\',\'\'))" '
    '2>/dev/null || echo "unknown"); '
    'TOOL_INPUT=$(echo "$HOOK_PAYLOAD" | python3 -c '
    '"import sys,json; d=json.load(sys.stdin); v=d.get(\'tool_input\',{}); '
    'print(json.dumps(v) if isinstance(v,dict) else str(v))" '
    '2>/dev/null || echo "{}"); '
    'mkdir -p ~/.osa-kiro/approvals 2>/dev/null; '
    'REQ_ID=$(od -An -N4 -tx1 /dev/urandom 2>/dev/null | tr -d " \n"); '
    'REQ_FILE=~/.osa-kiro/approvals/"$KIRO_SESSION_ID"-"$REQ_ID"; '
    # File format: session_id:req_id:tool_name:tool_input_json (tool_input may contain colons)
    'printf \'%s\' "$KIRO_SESSION_ID:$REQ_ID:$TOOL_NAME:$TOOL_INPUT" > "$REQ_FILE" 2>/dev/null; '
    f'DEADLINE=$(($(date +%s)+{int(APPROVAL_TIMEOUT)})); '
    'while [ $(date +%s) -lt $DEADLINE ]; do '
    '  if [ -f ~/.osa-kiro/approvals/."$KIRO_SESSION_ID"-"$REQ_ID" ]; then '
    '    rm -f "$REQ_FILE" ~/.osa-kiro/approvals/."$KIRO_SESSION_ID"-"$REQ_ID" 2>/dev/null; '
    '    echo "{}"; exit 0; '
    '  fi; '
    '  if [ -f ~/.osa-kiro/approvals/!"$KIRO_SESSION_ID"-"$REQ_ID" ]; then '
    '    rm -f "$REQ_FILE" ~/.osa-kiro/approvals/!"$KIRO_SESSION_ID"-"$REQ_ID" 2>/dev/null; '
    '    echo \'{"decision":"deny"}\'; exit 2; '
    '  fi; '
    # 0.2s, not 0.5: this interval is how long the agent sits idle *after* a
    # human has already answered, which is the part of the wait that feels worst.
    # Five wakeups a second of a two-test shell loop costs nothing, and only
    # while a call is actually held.
    '  sleep 0.2; '
    'done; '
    'rm -f "$REQ_FILE" 2>/dev/null; echo \'{"decision":"timeout"}\'; exit 2'
)
PRETOOL_HOOK_MARKER = "deck-pretool-approval"
# The hook waits for a human, so it needs a timeout of its own: the 5s the other
# two carry would kill it long before anyone reached their phone. A little longer
# than the hook's own deadline, so the hook's deny-by-default wins the race and
# kiro-cli sees a decision rather than a killed process.
PRETOOL_HOOK_TIMEOUT_MS = int(APPROVAL_TIMEOUT * 1000) + 5000

# Marks our entries in an agent config so install is idempotent and uninstall can
# find what to remove, without matching on the command text itself.
HOOK_MARKER = "deck-spawn-correlation"
STOP_HOOK_MARKER = "deck-turn-end"


def _session_id_ok(session_id: str) -> bool:
    """True for something shaped like a kiro session id, used as a filename."""
    return bool(re.fullmatch(r"[0-9a-fA-F-]{8,64}", session_id))


def turn_ended_at(session_id: str) -> float:
    """When a `stop` hook last reported this session finishing. 0.0 if never."""
    if not _session_id_ok(session_id):
        return 0.0
    try:
        return (TURNS_DIR / session_id).stat().st_mtime
    except OSError:
        return 0.0


def sweep_turn_marks(ttl: float = TURN_MARK_TTL) -> int:
    """Drop turn marks for sessions nobody has touched in a long time."""
    if not TURNS_DIR.is_dir():
        return 0
    cutoff = time.time() - ttl
    removed = 0
    for path in TURNS_DIR.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


# --- per-session gating: whether the preToolUse hook holds tool calls at all ---

def _gate_path(key: str) -> Path:
    return GATES_DIR / key


def gate_enabled(session_id: str) -> bool:
    """True if this session's tool calls are held for a human decision."""
    if not _session_id_ok(session_id):
        return False
    return _gate_path(session_id).exists()


def set_gate(session_id: str, enabled: bool) -> bool:
    """Turn approval gating on or off for one session. False if the id is bad."""
    if not _session_id_ok(session_id):
        return False
    path = _gate_path(session_id)
    if enabled:
        GATES_DIR.mkdir(parents=True, exist_ok=True)
        path.touch()
    else:
        try:
            path.unlink()
        except OSError:
            pass
    return True


def set_pending_gate(nonce: str, enabled: bool) -> bool:
    """Gate a spawn before its session id is known, keyed by its nonce.

    Without this, gating a new session means waiting for correlation and then
    toggling — by which time the first tool call has usually already run, which
    is exactly the call most worth holding.
    """
    if not _nonce_ok(nonce):
        return False
    path = _gate_path(f"n-{nonce}")
    if enabled:
        GATES_DIR.mkdir(parents=True, exist_ok=True)
        path.touch()
    else:
        try:
            path.unlink()
        except OSError:
            pass
    return True


def adopt_pending_gate(nonce: str, session_id: str) -> None:
    """Move a nonce-keyed gate onto the session id correlation just found.

    Written before the nonce file is removed, so there is no instant in which
    neither exists and a tool call slips through ungated. The nonce form is
    dropped afterwards because the session keeps that nonce in its environment
    for as long as it runs — left in place, it would quietly re-gate a session
    the user had switched gating off for.
    """
    if not (_nonce_ok(nonce) and _session_id_ok(session_id)):
        return
    if not _gate_path(f"n-{nonce}").exists():
        return
    set_gate(session_id, True)
    set_pending_gate(nonce, False)


def sweep_gates(ttl: float = SPAWN_HOOK_TTL) -> int:
    """Drop nonce-keyed gates whose spawn never correlated.

    Only the `n-` form expires. A gate on a real session id is a decision the
    user made and stays until they undo it or the session is deleted.
    """
    if not GATES_DIR.is_dir():
        return 0
    cutoff = time.time() - ttl
    removed = 0
    for path in GATES_DIR.iterdir():
        try:
            if (path.is_file() and path.name.startswith("n-")
                    and path.stat().st_mtime < cutoff):
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def gated_sessions() -> set[str]:
    """Session ids currently gated. One directory read, for list endpoints."""
    if not GATES_DIR.is_dir():
        return set()
    try:
        return {p.name for p in GATES_DIR.iterdir()
                if p.is_file() and not p.name.startswith("n-")}
    except OSError:
        return set()


def clear_gate(session_id: str) -> None:
    """Drop a session's gate — called when the session itself is deleted."""
    set_gate(session_id, False)


# --- preToolUse approval queue ---

def pending_approvals() -> list[dict]:
    """All tool calls currently waiting for a human decision, newest first."""
    from . import deny as _deny
    if not APPROVALS_DIR.is_dir():
        return []
    results = []
    for path in APPROVALS_DIR.iterdir():
        if not path.is_file():
            continue
        name = path.name
        # Active requests have no prefix. Allow/deny signals use "." or "!" prefix.
        if name.startswith(".") or name.startswith("!"):
            continue
        try:
            content = path.read_text().strip()
            parts = content.split(":", 3)
            if len(parts) < 3:
                continue
            session_id = parts[0]
            req_id = parts[1]
            tool_name = parts[2] or "unknown"
            tool_input_raw = parts[3] if len(parts) > 3 else ""
            try:
                tool_input = json.loads(tool_input_raw) if tool_input_raw else {}
            except (json.JSONDecodeError, ValueError):
                tool_input = {}
            # Auto-deny if a pattern matches — write the deny signal and skip.
            matched, note = _deny.matches(tool_name, tool_input)
            if matched:
                deny_path = APPROVALS_DIR / f"!{session_id}-{req_id}"
                deny_path.write_text(f"auto-deny:{note}")
                path.unlink(missing_ok=True)
                continue
            # Auto-allow if the session has an active trust TTL.
            trust_file = APPROVALS_DIR.parent / "trust" / session_id
            try:
                trust_until = float(trust_file.read_text().strip())
                if time.time() < trust_until:
                    allow_path = APPROVALS_DIR / f".{session_id}-{req_id}"
                    allow_path.write_text("auto-allow:trust-ttl")
                    path.unlink(missing_ok=True)
                    continue
            except (OSError, ValueError):
                pass
            results.append({
                "session_id": session_id,
                "request_id": req_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "age": time.time() - path.stat().st_mtime,
            })
        except OSError:
            continue
    results.sort(key=lambda r: r["age"])
    return results


def respond_approval(session_id: str, request_id: str, allow: bool) -> bool:
    """Signal allow or deny to a waiting hook. Returns True if the file existed."""
    APPROVALS_DIR.mkdir(parents=True, exist_ok=True)
    req = APPROVALS_DIR / f"{session_id}-{request_id}"
    if not req.exists():
        return False
    prefix = "." if allow else "!"
    signal = APPROVALS_DIR / f"{prefix}{session_id}-{request_id}"
    try:
        signal.touch()
    except OSError:
        return False
    # Retire the request here rather than leaving it for the hook to clean up.
    # The hook polls, so it can be up to one poll interval behind — and until the
    # request file goes, `pending_approvals()` keeps reporting a call that has
    # already been answered. The UI removes the row optimistically on click, the
    # next two-second poll put it straight back, and answering read as broken.
    # The signal is written first, so a crash between the two lines leaves the
    # hook its answer rather than stranding it until the deny-by-default timeout.
    try:
        req.unlink()
    except OSError:
        pass
    return True


def sweep_approvals(ttl: float = APPROVAL_TIMEOUT) -> int:
    """Remove stale approval files — requests that timed out on the hook side."""
    if not APPROVALS_DIR.is_dir():
        return 0
    cutoff = time.time() - ttl
    removed = 0
    for path in APPROVALS_DIR.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


# --- per-session task stack ---

def _stack_path(session_id: str) -> Path:
    return STACKS_DIR / f"{session_id}.json"


def stack_get(session_id: str) -> list[dict]:
    """Return the ordered task list for a session (empty if none)."""
    if not _session_id_ok(session_id):
        return []
    try:
        return json.loads(_stack_path(session_id).read_text())
    except (OSError, json.JSONDecodeError):
        return []


def stack_save(session_id: str, items: list[dict]) -> None:
    """Atomically write the stack for a session."""
    STACKS_DIR.mkdir(parents=True, exist_ok=True)
    path = _stack_path(session_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items))
    tmp.replace(path)


def stack_add(session_id: str, text: str) -> dict:
    """Append an item to the stack. Returns the new item."""
    item = {"id": str(uuid.uuid4())[:8], "text": text.strip(), "added_at": time.time()}
    items = stack_get(session_id)
    items.append(item)
    stack_save(session_id, items)
    return item


def stack_delete(session_id: str, item_id: str) -> bool:
    """Remove one item by id. Returns True if it existed."""
    items = stack_get(session_id)
    new = [i for i in items if i["id"] != item_id]
    if len(new) == len(items):
        return False
    stack_save(session_id, new)
    return True


def stack_update(session_id: str, item_id: str, text: str) -> list[dict] | None:
    """Edit the text of one stack item. Returns updated list or None if not found."""
    items = stack_get(session_id)
    for item in items:
        if item["id"] == item_id:
            item["text"] = text
            stack_save(session_id, items)
            return items
    return None


def stack_reorder(session_id: str, ordered_ids: list[str]) -> list[dict]:
    """Reorder the stack to match the given id sequence. Unknown ids are dropped."""
    items = {i["id"]: i for i in stack_get(session_id)}
    reordered = [items[oid] for oid in ordered_ids if oid in items]
    stack_save(session_id, reordered)
    return reordered


def stack_pop(session_id: str) -> dict | None:
    """Remove and return the first item. Returns None if stack is empty."""
    items = stack_get(session_id)
    if not items:
        return None
    item = items.pop(0)
    stack_save(session_id, items)
    return item


def _nonce_ok(nonce: str) -> bool:
    """True for a nonce we generated. Guards a value used as a filename."""
    return bool(re.fullmatch(r"[0-9a-f]{6,64}", nonce))


def hook_reported_session(nonce: str) -> str:
    """The session id an agentSpawn hook recorded for this spawn, if any."""
    if not _nonce_ok(nonce):
        return ""
    try:
        reported = (SPAWNS_DIR / nonce).read_text().strip()
    except OSError:
        return ""
    # The hook writes whatever kiro-cli gave it, so validate before trusting it
    # as a session id: it becomes a tmux session name and a file path.
    if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", reported):
        return ""
    # A --no-interactive run reports an id and then writes nothing, so require
    # some evidence the session is real. Any of its files will do: kiro-cli
    # writes `.lock` first and `.json` last, and demanding `.json` would hand the
    # race back to the process-tree walk, which keys on `.lock` — defeating the
    # point of asking.
    if not any((SESSIONS_DIR / f"{reported}{ext}").exists()
               for ext in (".lock", ".jsonl", ".history", ".json")):
        return ""
    return reported


def forget_hook_report(nonce: str) -> None:
    """Drop a hook drop once it has been used, or when its spawn is abandoned."""
    if not _nonce_ok(nonce):
        return
    try:
        (SPAWNS_DIR / nonce).unlink()
    except OSError:
        pass


def _await_hook(nonce: str, claimed: set, grace: float) -> str:
    """Wait up to `grace` for the hook's answer. Returns "" if it never comes."""
    deadline = time.time() + grace
    while time.time() < deadline:
        reported = hook_reported_session(nonce)
        if reported and reported not in claimed:
            return reported
        time.sleep(0.05)
    return ""


def sweep_hook_reports(ttl: float = SPAWN_HOOK_TTL) -> int:
    """Delete hook drops nobody claimed. Returns how many went.

    A drop is left behind whenever a spawn dies between the hook firing and the
    resolver reading it — a backend restart, a crash. Without a sweep the
    directory grows forever.
    """
    if not SPAWNS_DIR.is_dir():
        return 0
    cutoff = time.time() - ttl
    removed = 0
    for path in SPAWNS_DIR.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _agent_name_ok(name: str) -> bool:
    """True for a plain agent name, the shape kiro-cli's own configs use.

    The first character may not be `-` or `.`: the name becomes the value of
    `--agent`, and something like `-rf` would be read as flags by the argument
    parser rather than as a name.
    """
    return bool(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,63}", name))


def _read_meta(session_id: str) -> dict | None:
    path = SESSIONS_DIR / f"{session_id}.json"
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _read_lock(session_id: str) -> dict | None:
    path = SESSIONS_DIR / f"{session_id}.lock"
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _process_tree() -> dict[int, int]:
    """Snapshot of pid -> ppid for every process on the machine."""
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    tree = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                tree[int(parts[0])] = int(parts[1])
            except ValueError:
                pass
    return tree


def _is_descendant(pid: int, ancestor: int, tree: dict[int, int]) -> bool:
    seen = set()
    while pid and pid not in seen:
        if pid == ancestor:
            return True
        seen.add(pid)
        pid = tree.get(pid, 0)
    return False


def _correlate(root_pid: int, claimed: set[str]) -> str | None:
    """Find the session id belonging to the kiro-cli process in our pane.

    Matches on process lineage rather than cwd: every live session writes a
    `.lock` holding its pid, and the pid that owns the session is a descendant
    of the pane pid (`kiro-cli` re-execs as `kiro-cli-chat`).

    Cwd cannot be used to discriminate, and neither can
    `session_created_reason` — passing an initial task argument makes a normal
    top-level session record itself as "subagent", so that field does not
    distinguish a real subagent from the session we just started. Genuine
    subagents *are* descendants of our pane too, so ties break on the earliest
    lock `started_at`: the main session's lock is written before it can spawn
    any subagent.
    """
    tree = _process_tree()
    candidates = []
    for lock_path in SESSIONS_DIR.glob("*.lock"):
        session_id = lock_path.stem
        if session_id in claimed:
            continue
        lock = _read_lock(session_id)
        if not lock:
            continue  # mid-write; a later poll will catch it
        pid = lock.get("pid")
        if not isinstance(pid, int):
            continue
        if not _is_descendant(pid, root_pid, tree):
            continue
        candidates.append((lock.get("started_at", ""), session_id))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


# --- spawning ---

def spawn(
    cwd: str,
    task: str = "",
    trust_tools: str | None = DEFAULT_TRUST_TOOLS,
    trust_all: bool = False,
    resume_id: str = "",
    model: str = "",
    effort: str = "",
    agent: str = "",
    engine: str = "",
    expect_hook: bool = False,
    pre_command: str = "",
    wait: bool = True,
) -> dict:
    """Start a kiro-cli session in a detached tmux session.

    Pass resume_id to continue an existing session; its id is then already
    known and no correlation is needed. Otherwise a placeholder tmux session is
    created and resolved by polling for the new session .json.

    Returns a dict with `ok`, plus `session_id` and `tmux` when resolved, or
    `nonce` and `pending: True` when correlation has not completed yet.
    """
    # Argument shape first, before anything that touches the filesystem or
    # starts a process: bad input should be refused, not half acted on.
    if model and model not in available_models():
        return {"ok": False, "error": f"Unknown model: {model}"}
    if effort and effort not in EFFORTS:
        return {"ok": False, "error": f"Unknown effort: {effort}"}
    if agent and not _agent_name_ok(agent):
        # Agents are the user's own files, so the set cannot be enumerated up
        # front. Reject anything that is not a plain name rather than passing it
        # through: this string becomes an argv entry, and a bad one fails inside
        # a detached pane where nobody sees the error.
        return {"ok": False, "error": f"Invalid agent name: {agent}"}

    target_cwd = _real(cwd)
    if not Path(target_cwd).is_dir():
        return {"ok": False, "error": f"Directory not found: {cwd}"}
    if not tmux_available():
        return {"ok": False, "error": "tmux not installed — brew install tmux"}

    argv = [KIRO_CLI, "chat"]
    if trust_all:
        argv.append("--trust-all-tools")
    elif trust_tools is not None:
        argv.append(f"--trust-tools={trust_tools}")
    if model:
        argv += ["--model", model]
    if effort:
        argv += ["--effort", effort]
    if agent:
        argv += ["--agent", agent]
    if engine and engine in ("v1", "v2", "v3"):
        argv += ["--agent-engine", engine]
    if resume_id:
        argv += ["--resume-id", resume_id]
        if session_exists(tmux_name(resume_id)):
            return {"ok": False, "error": f"Session {resume_id} is already managed"}
    if task:
        argv.append(task)

    if pre_command:
        # Run a shell prelude first — `cd`, `nvm use`, activating a virtualenv —
        # then hand the pane over to kiro-cli. `exec` keeps the same pid, so the
        # lock-pid lineage used for correlation still holds. A login shell is
        # used so the user's PATH and shell setup apply.
        inner = " ".join(shlex.quote(a) for a in argv)
        argv = ["bash", "-lc", f"{pre_command}\nexec {inner}"]

    state = load_state()
    if resume_id:
        name = tmux_name(resume_id)
        nonce = ""
    else:
        nonce = uuid.uuid4().hex[:12]
        name = f"{PENDING_PREFIX}{nonce}"

    try:
        # -e puts DECK_NONCE in the session's environment, where an agentSpawn
        # hook can read it and pair it with kiro-cli's own KIRO_SESSION_ID. The
        # nonce is hex we generated, so it is safe as both an env value and the
        # filename the hook writes. A resume already knows its id and needs none.
        env_args = [] if resume_id else ["-e", f"{NONCE_ENV}={nonce}"]
        # Inject per-project secrets as environment variables. Values come from
        # the macOS keychain — they never appear in JSONL or process arguments.
        try:
            from .secrets import get_env as _get_secret_env
            for _k, _v in _get_secret_env(target_cwd).items():
                env_args += ["-e", f"{_k}={_v}"]
        except Exception:
            pass  # secrets unavailable — don't break the spawn
        _tmux("new-session", "-d", "-s", name,
              "-x", str(DEFAULT_COLS), "-y", str(DEFAULT_ROWS),
              *env_args, "-c", target_cwd, *argv)
        # Keep dead panes so a kiro-cli crash is diagnosable instead of silent.
        _tmux("set-option", "-t", name, "remain-on-exit", "on", check=False)
        # resize() flips `window-size` to manual, which would leave a real
        # terminal stuck with the browser's geometry after `tmux attach`. Hand
        # sizing back to whichever client actually attaches.
        _tmux("set-hook", "-t", name, "client-attached",
              "set-option -w window-size latest", check=False)
    except TmuxError as e:
        return {"ok": False, "error": str(e)}

    root_pid = pane_pid(name)
    now = time.time()
    if resume_id:
        state["managed"][resume_id] = {
            "tmux": name, "cwd": target_cwd, "spawned_at": now, "resumed": True,
            "agent": agent,
        }
        save_state(state)
        return {"ok": True, "session_id": resume_id, "tmux": name, "resumed": True}

    if not root_pid:
        _tmux("kill-session", "-t", name, check=False)
        return {"ok": False, "error": "tmux pane has no pid — spawn failed"}

    # The agent is remembered here because kiro-cli does not record it in the
    # session metadata. Without it, resuming or handing off a session would run
    # it under a different agent than it started with.
    state["pending"][nonce] = {
        "tmux": name, "cwd": target_cwd, "spawned_at": now,
        "task": task[:200], "root_pid": root_pid, "agent": agent,
        # Whether this agent carries the spawn hook, so the resolver knows
        # if waiting a moment for its answer is worth anything.
        "expect_hook": bool(expect_hook),
    }
    save_state(state)

    if not wait:
        return {"ok": True, "nonce": nonce, "tmux": name, "pending": True}

    session_id = resolve_pending(nonce, timeout=SPAWN_TIMEOUT)
    if not session_id:
        return {
            "ok": True, "nonce": nonce, "tmux": name, "pending": True,
            "unresolved": True,
            "error": f"Session id not found within {SPAWN_TIMEOUT:.0f}s; "
                     f"tmux session {name} is still running",
        }
    return {"ok": True, "session_id": session_id, "tmux": tmux_name(session_id)}


def pending_owners() -> dict[str, str]:
    """Map pending nonce -> the session id it has already produced on disk.

    Between a spawn and the moment correlation finishes, the session exists as a
    `.lock` on disk while its nonce is still pending — so a naive listing shows
    the same agent twice, once as a `starting` placeholder and once as a
    `foreign` session it does not recognise. Callers use this to collapse the
    two into one card.

    Read-only on purpose: promotion to `managed` belongs to the resolver thread,
    and mutating state from a listing request would race it.
    """
    state = load_state()
    if not state["pending"]:
        return {}
    claimed = set(state["managed"])
    owners: dict[str, str] = {}
    for nonce, entry in state["pending"].items():
        root_pid = entry.get("root_pid") or pane_pid(entry.get("tmux", ""))
        if not root_pid:
            continue
        session_id = _correlate(root_pid, claimed)
        if session_id:
            owners[nonce] = session_id
            # Claim it so a second pending cannot point at the same session.
            claimed.add(session_id)
    return owners


def reap_pendings() -> list[str]:
    """Drop pending entries whose tmux session is gone, returning their nonces.

    A pending entry is resolved by a background thread. If the backend dies
    first — a reload, a crash, a stopped dev server — that thread goes with it
    and the entry is left behind with no tmux session and no owner. Until this
    ran only on startup, which left a permanent `starting` card that no UI
    action could remove.
    """
    state = load_state()
    if not state["pending"]:
        return []
    live = set(list_tmux_sessions())
    dead = [nonce for nonce, entry in state["pending"].items()
            if entry.get("tmux") not in live]
    if not dead:
        return []
    for nonce in dead:
        state["pending"].pop(nonce, None)
    save_state(state)
    return dead


def cancel_pending(nonce: str) -> dict:
    """Give up on a pending spawn, killing its tmux session if it still exists.

    The escape hatch for a spawn that never correlated: without it an
    `unresolved` card is unremovable, because it has no session id for any of
    the id-keyed endpoints to act on.
    """
    state = load_state()
    entry = state["pending"].get(nonce)
    if not entry:
        return {"ok": False, "error": "no such pending session"}
    name = entry.get("tmux", "")
    killed = False
    if name and name in set(list_tmux_sessions()):
        _tmux("kill-session", "-t", name, check=False)
        killed = True
    state = load_state()
    state["pending"].pop(nonce, None)
    save_state(state)
    return {"ok": True, "killed_tmux": killed, "tmux": name}


def resolve_pending(nonce: str, timeout: float = SPAWN_TIMEOUT) -> str | None:
    """Poll for the session id of a pending spawn, then rename its tmux session.

    Returns the session id, or None on timeout. The pending entry survives a
    timeout (marked unresolved) so a still-running agent is never orphaned
    silently — reconcile() will retry it.
    """
    state = load_state()
    entry = state["pending"].get(nonce)
    if not entry:
        return None
    cwd = entry["cwd"]
    name = entry["tmux"]
    root_pid = entry.get("root_pid") or pane_pid(name)
    if not root_pid:
        return None

    deadline = time.time() + timeout
    via = "lineage"
    while True:
        state = load_state()  # reread: another request may have claimed an id
        claimed = set(state["managed"])
        # Ask first, guess second. An agentSpawn hook reports the id kiro-cli
        # chose, which is exact; the process-tree walk infers it, and has to
        # break ties between a session and its subagents. The hook is only
        # present when the user installed it, so the walk stays as the fallback
        # rather than being replaced.
        reported = hook_reported_session(nonce)
        if reported and reported not in claimed:
            session_id, via = reported, "hook"
            break
        session_id = _correlate(root_pid, claimed)
        if session_id:
            # The walk got there first, which on kiro-cli 2.14.2 it always does:
            # the hook fires about a quarter of a second after the .lock file the
            # walk keys on. So give the hook a bounded moment to answer, and let
            # its exact id override the inferred one if they disagree — the walk
            # has to break ties between a session and its subagents, and this is
            # the only way to catch it choosing wrong.
            if entry.get("expect_hook"):
                confirmed = _await_hook(nonce, claimed, SPAWN_HOOK_GRACE)
                if confirmed and confirmed != session_id:
                    session_id, via = confirmed, "hook-corrected"
                elif confirmed:
                    via = "hook-confirmed"
            break
        if time.time() >= deadline:
            entry_now = state["pending"].get(nonce)
            if entry_now is not None:
                entry_now["unresolved"] = True
                save_state(state)
            return None
        time.sleep(SPAWN_POLL)

    forget_hook_report(nonce)
    # A spawn asked to be gated was gated by nonce, since its id did not exist
    # yet. Now it does.
    adopt_pending_gate(nonce, session_id)
    new_name = tmux_name(session_id)
    if name != new_name:
        _tmux("rename-session", "-t", name, new_name, check=False)
    state["pending"].pop(nonce, None)
    state["managed"][session_id] = {
        "tmux": new_name,
        "cwd": cwd,
        "spawned_at": entry.get("spawned_at", time.time()),
        "task": entry.get("task", ""),
        "agent": entry.get("agent", ""),
        # Which route found the id. Worth recording: it is the only way to tell
        # whether the hooks are actually working in the field.
        "correlated_via": via,
    }
    save_state(state)
    return session_id


# --- input ---

def send_text(session_id: str, text: str, submit: bool = True) -> dict:
    """Type text into a managed session, optionally submitting it.

    Newlines are sent literally, and the kiro TUI treats each as a submit, so
    multi-line text arrives as several prompts. Callers wanting one prompt
    should flatten first.
    """
    name = tmux_name(session_id)
    if not session_exists(name):
        return {"ok": False, "error": f"No tmux session for {session_id}"}
    if pane_dead(name):
        return {"ok": False, "error": "Session process has exited"}
    text = text.replace("\r\n", "\n")
    try:
        # For large text, tmux send-keys -l silently truncates at the terminal
        # pipe buffer limit (~4 KB on macOS). Use load-buffer + paste-buffer
        # instead: it reads from a file so there is no size ceiling.
        if len(text) > 1024:
            import tempfile as _tempfile
            buf_name = f"qd-{session_id[:8]}"
            # Wrap in bracketed-paste escape sequences so the TUI treats the
            # entire block as a single paste event, not line-by-line submits.
            bracketed = f"\x1b[200~{text}\x1b[201~"
            with _tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                              delete=False, prefix="qd-paste-") as f:
                f.write(bracketed)
                tmp_path = f.name
            try:
                _tmux("load-buffer", "-b", buf_name, tmp_path)
                _tmux("paste-buffer", "-b", buf_name, "-t", name, "-r")
            finally:
                try:
                    import os as _os
                    _os.unlink(tmp_path)
                except OSError:
                    pass
                try:
                    _tmux("delete-buffer", "-b", buf_name, check=False)
                except Exception:
                    pass
        else:
            # -l sends the string literally; -- stops text starting with '-' from
            # being read as a flag.
            _tmux("send-keys", "-t", name, "-l", "--", text)
        if submit:
            _tmux("send-keys", "-t", name, "Enter")
    except TmuxError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


def send_key(session_id: str, key: str) -> dict:
    """Send a single tmux key name (`y`, `n`, `Enter`, `Escape`, `C-c`).

    Used for permission prompts, where the TUI wants one keystroke rather than
    a line of text.
    """
    name = tmux_name(session_id)
    if not session_exists(name):
        return {"ok": False, "error": f"No tmux session for {session_id}"}
    try:
        _tmux("send-keys", "-t", name, key)
    except TmuxError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


# --- output ---

def geometry(session_id: str) -> tuple[int, int] | None:
    """Current (cols, rows) of a managed session's window, or None."""
    name = tmux_name(session_id)
    if not session_exists(name):
        return None
    out = _tmux("display-message", "-p", "-t", name,
                "#{window_width}x#{window_height}", check=False).strip()
    try:
        cols, rows = out.split("x")
        return int(cols), int(rows)
    except ValueError:
        return None


def resize(session_id: str, cols: int, rows: int) -> dict:
    """Resize a managed session's window so its TUI reflows to fit the viewer.

    A detached session keeps whatever geometry it was created with, so without
    this a maximised browser pane just shows a small frame surrounded by dead
    space. `resize-window` works on a session with no clients attached, and sets
    `window-size` to manual as a side effect — spawn() installs a
    `client-attached` hook to hand sizing back when a real terminal attaches.
    """
    name = tmux_name(session_id)
    if not session_exists(name):
        return {"ok": False, "error": "not managed"}
    cols = max(MIN_COLS, min(int(cols), MAX_COLS))
    rows = max(MIN_ROWS, min(int(rows), MAX_ROWS))
    if geometry(session_id) == (cols, rows):
        return {"ok": True, "cols": cols, "rows": rows, "changed": False}
    try:
        _tmux("resize-window", "-t", name, "-x", str(cols), "-y", str(rows))
    except TmuxError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "cols": cols, "rows": rows, "changed": True}


# ponytail: TTL cache for capture-pane results; avoids spawning a subprocess on
# every poll for each managed session. TTL of 0.4s is shorter than the 0.5s
# poll-busy-ms minimum, so a fast poll still sees fresh output.
# session_id -> (ts, lines, result). Capped: an entry can hold MAX_CAPTURE_LINES
# of pane text, and entries are only dropped when their tmux session is gone —
# so a long-lived process accumulated one per session it ever captured.
_capture_cache: LruCache = LruCache(maxsize=64)
_CAPTURE_TTL = 0.4  # seconds

def capture(session_id: str, lines: int = CAPTURE_LINES) -> str:
    """Return the last N lines visible in the session's pane.

    This is what the TUI is actually showing, which is how permission prompts
    are detected — unlike the JSONL heuristics, it involves no guessing.
    Cached for _CAPTURE_TTL seconds to avoid a subprocess fork on every poll cycle.
    """
    import time as _t
    now = _t.time()
    cached = _capture_cache.get(session_id)
    if cached and (now - cached[0]) < _CAPTURE_TTL and cached[1] >= lines:
        # Return cached result if it was captured with at least as many lines
        return cached[2]
    name = tmux_name(session_id)
    if not session_exists(name):
        _capture_cache.pop(session_id, None)
        return ""
    result = _tmux("capture-pane", "-p", "-t", name, "-S", f"-{lines}", check=False,
                   timeout=4)
    _capture_cache[session_id] = (now, lines, result)
    return result


# --- lifecycle ---

def kill(session_id: str, graceful: bool = True, timeout: float = 8.0) -> dict:
    """End a managed session and forget it.

    Prefers a clean shutdown: `/quit` lets kiro-cli flush its conversation, so
    the session stays resumable afterwards. Killing the tmux session outright
    skips that, so it is only the fallback when the session will not exit.

    kiro-cli spawns a process tree (shell → kiro-cli-chat → bun tui.js) whose
    members survive tmux kill-session because they run in their own process
    groups. After killing the tmux session we therefore also SIGTERM the pane's
    process group so nothing is left orphaned.
    """
    name = tmux_name(session_id)
    existed = session_exists(name)
    mode = "none"
    if existed:
        lock_path = SESSIONS_DIR / f"{session_id}.lock"
        if graceful and not pane_dead(name):
            send_text(session_id, "/quit")
            deadline = time.time() + timeout
            while time.time() < deadline:
                if not lock_path.exists() or pane_dead(name):
                    mode = "quit"
                    break
                time.sleep(0.2)
        if mode != "quit":
            mode = "kill"

        # Grab the pane's root pid *before* killing the tmux session — after
        # kill-session the pane is gone and pane_pid() returns 0.
        root_pid = pane_pid(name)
        _tmux("kill-session", "-t", name, check=False)

        # Kill the whole process group so kiro-cli sub-processes don't survive.
        if root_pid:
            try:
                pgid = os.getpgid(root_pid)
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                # Process already gone or in a group we can't signal — fine.
                pass

    state = load_state()
    if state["managed"].pop(session_id, None) is not None or existed:
        save_state(state)
        return {"ok": True, "killed": existed, "mode": mode}
    return {"ok": False, "error": f"{session_id} is not managed"}


def reconcile() -> dict:
    """Sync persisted state against live tmux sessions.

    Called on startup so a restarted backend re-adopts its own sessions:
    drops entries whose tmux session is gone, adopts stray `kiro-*` sessions
    missing from state, and retries pending spawns that never resolved.
    """
    live = set(list_tmux_sessions())
    state = load_state()
    dropped, adopted, resolved, still_pending = [], [], [], []

    for session_id in list(state["managed"]):
        if state["managed"][session_id].get("tmux", tmux_name(session_id)) not in live:
            state["managed"].pop(session_id)
            dropped.append(session_id)

    # Adoption, bounded. A stray kiro-* session is normally our own work after a
    # backend restart, so adopting it is right. A pile of them arriving at once
    # is not: tmux-continuum's restore recreated 38 sessions from an eight-day
    # old snapshot, reconcile adopted every one, and the UI then presented them
    # as the user's sessions while the summary worker queued one per session.
    # Over the limit, nothing is adopted — the strays are recorded instead, and
    # claiming or killing them is a decision a human makes.
    strays = [name for name in live
              if name.startswith(TMUX_PREFIX)
              and name[len(TMUX_PREFIX):] not in state["managed"]]
    now = time.time()
    unclaimed = state["unclaimed"]
    for name in list(unclaimed):
        if name not in live:
            unclaimed.pop(name)  # gone; nothing left to decide about
    fresh = [name for name in strays if name not in unclaimed]

    if len(fresh) > ADOPT_LIMIT:
        for name in fresh:
            unclaimed[name] = {"first_seen": now, "reason": "burst"}
        print(f"[deck] {len(fresh)} stray tmux sessions appeared at once — not "
              f"adopting them. Quarterdeck did not spawn these, and something "
              f"that restores tmux sessions (tmux-continuum) is the usual "
              f"cause. See GET /api/tmux/strays.", file=sys.stderr)
        held = list(fresh)
    else:
        held = []
        for name in fresh:
            session_id = name[len(TMUX_PREFIX):]
            meta = _read_meta(session_id) or {}
            state["managed"][session_id] = {
                "tmux": name, "cwd": meta.get("cwd", ""),
                "spawned_at": now, "adopted": True,
            }
            adopted.append(session_id)

    save_state(state)

    for nonce in list(load_state()["pending"]):
        entry = load_state()["pending"][nonce]
        if entry.get("tmux") not in live:
            state = load_state()
            state["pending"].pop(nonce, None)
            save_state(state)
            dropped.append(nonce)
            continue
        # One quick pass, not a full timeout — reconcile must not block startup.
        session_id = resolve_pending(nonce, timeout=0)
        if session_id:
            resolved.append(session_id)
        else:
            still_pending.append(nonce)

    return {
        "dropped": dropped, "adopted": adopted,
        "resolved": resolved, "pending": still_pending,
        "unclaimed": held,
    }


def last_activity(session_id: str) -> float:
    """Newest mtime that says something happened in this session. 0 if unknown.

    Three sources, because none of them is complete on its own: the stop hook's
    turn mark only exists for hooked agents, the .lock only moves when kiro-cli
    rewrites it, and the .jsonl moves on every exchange but is absent for a
    session that has not said anything yet.
    """
    ts = turn_ended_at(session_id)
    if not _session_id_ok(session_id):
        return ts
    for path in (SESSIONS_DIR / f"{session_id}.jsonl",
                 SESSIONS_DIR / f"{session_id}.lock"):
        try:
            ts = max(ts, path.stat().st_mtime)
        except OSError:
            pass
    return ts


def reap_idle_sessions(idle: float, dry_run: bool = True) -> dict:
    """Quit managed sessions that are alive but have done nothing for `idle`.

    This one ends *running* agents, which is why it is a dry run unless told
    otherwise and never runs on a timer. Fifty idle kiro-cli processes are what
    a load average of 30 is made of, and none of them is doing anything — but
    "idle" is measured from the outside, and a session parked mid-task looks
    exactly like a session nobody wants.

    Deliberately skipped: sessions with a gate, a pending approval, or a queued
    stack item (someone is mid-workflow), dead panes (the other reaper's job),
    and anything whose last activity cannot be established at all — an unknown
    timestamp is not evidence of idleness.
    """
    if idle <= 0:
        return {"ok": True, "disabled": True, "killed": [], "kept": []}
    live = set(list_tmux_sessions())
    now = time.time()
    gated = gated_sessions()
    awaiting = {a.get("session_id") for a in pending_approvals()}
    killed, candidates, kept = [], [], []
    for session_id, entry in list(load_state()["managed"].items()):
        name = entry.get("tmux", tmux_name(session_id))
        if name not in live or pane_dead(name):
            continue
        if session_id in gated or session_id in awaiting or stack_get(session_id):
            kept.append({"session_id": session_id, "why": "mid-workflow"})
            continue
        seen = last_activity(session_id)
        if not seen:
            kept.append({"session_id": session_id, "why": "no activity timestamp"})
            continue
        age = now - seen
        if age < idle:
            kept.append({"session_id": session_id, "why": f"active {age / 60:.0f}m ago"})
            continue
        candidates.append({"session_id": session_id, "idle_minutes": round(age / 60)})
    if dry_run:
        return {"ok": True, "dry_run": True, "would_kill": candidates, "kept": kept}
    for c in candidates:
        # Graceful: /quit lets kiro-cli flush, so the session stays resumable.
        result = kill(c["session_id"], graceful=True)
        killed.append({**c, "mode": result.get("mode", "none")})
    return {"ok": True, "dry_run": False, "killed": killed, "kept": kept}


def unclaimed_sessions() -> list[dict]:
    """Stray kiro-* tmux sessions reconcile() held back instead of adopting.

    Live check included: a stray the user has since killed by hand is not worth
    reporting, and the record is dropped the next time reconcile runs.
    """
    live = set(list_tmux_sessions())
    out = []
    for name, entry in load_state()["unclaimed"].items():
        if name not in live:
            continue
        session_id = name[len(TMUX_PREFIX):]
        meta = _read_meta(session_id) or {}
        out.append({
            "tmux": name,
            "session_id": session_id,
            "cwd": meta.get("cwd", ""),
            "first_seen": entry.get("first_seen", 0),
            "reason": entry.get("reason", ""),
            "dead_pane": pane_dead(name),
            "attach": attach_command(session_id),
        })
    return sorted(out, key=lambda r: r["first_seen"])


def claim_unclaimed(names: list[str] | None = None) -> dict:
    """Adopt held-back strays into managed state — the "these are mine" answer."""
    live = set(list_tmux_sessions())
    state = load_state()
    targets = [n for n in (names if names is not None else list(state["unclaimed"]))
               if n in state["unclaimed"] and n in live]
    now = time.time()
    for name in targets:
        session_id = name[len(TMUX_PREFIX):]
        meta = _read_meta(session_id) or {}
        state["managed"][session_id] = {
            "tmux": name, "cwd": meta.get("cwd", ""),
            "spawned_at": now, "adopted": True,
        }
        state["unclaimed"].pop(name, None)
    save_state(state)
    return {"ok": True, "claimed": targets}


def kill_unclaimed(names: list[str] | None = None, dry_run: bool = True) -> dict:
    """Kill held-back strays — the "these are not mine" answer.

    Defaults to a dry run on purpose. These sessions hold live kiro-cli
    processes that nobody asked Quarterdeck to start, but "nobody asked for it"
    is not the same as "nothing of value is in it": a resurrected pane can be
    sitting in a half-finished login, and the caller should see the list before
    it goes. Restricted to recorded strays, so this can never reach a managed
    session or a tmux session belonging to something else entirely.
    """
    live = set(list_tmux_sessions())
    state = load_state()
    targets = [n for n in (names if names is not None else list(state["unclaimed"]))
               if n in state["unclaimed"] and n in live]
    if dry_run:
        return {"ok": True, "dry_run": True, "would_kill": targets}
    for name in targets:
        _tmux("kill-session", "-t", name, check=False)
    state = load_state()
    for name in targets:
        state["unclaimed"].pop(name, None)
    save_state(state)
    return {"ok": True, "dry_run": False, "killed": targets}


def reap_dead_panes(ttl: float = DEAD_PANE_TTL, dry_run: bool = False) -> dict:
    """Kill tmux sessions whose process has already exited and stayed dead.

    remain-on-exit keeps a crashed kiro-cli's last screen readable instead of
    taking the tmux session down with it, which is the right default and also a
    slow leak: the corpses accumulate, each one still a tmux session, still
    listed, still counted. Only panes tmux itself reports as dead are eligible,
    so nothing running is ever killed — and the first sighting only records the
    time of death, so a corpse always gets the full ttl before it goes.

    ttl <= 0 disables the reaper entirely.
    """
    if ttl <= 0:
        return {"ok": True, "disabled": True, "killed": [], "watching": []}
    live = set(list_tmux_sessions())
    state = load_state()
    now = time.time()
    killed, watching, revived = [], [], []
    for session_id, entry in list(state["managed"].items()):
        name = entry.get("tmux", tmux_name(session_id))
        if name not in live:
            continue
        if not pane_dead(name):
            if entry.pop("dead_since", None) is not None:
                revived.append(session_id)  # resumed in place; forget the mark
            continue
        dead_since = entry.get("dead_since")
        if not dead_since:
            entry["dead_since"] = now
            watching.append(session_id)
            continue
        if now - dead_since < ttl:
            watching.append(session_id)
            continue
        if dry_run:
            killed.append(session_id)
            continue
        _tmux("kill-session", "-t", name, check=False)
        state["managed"].pop(session_id, None)
        killed.append(session_id)
    save_state(state)
    return {"ok": True, "dry_run": dry_run, "killed": killed,
            "watching": watching, "revived": revived}


def managed_sessions() -> dict:
    """Managed session records, annotated with live tmux state.

    pane_dead is computed lazily (only when the detail panel requests it) to
    avoid N subprocess calls on every list_sessions poll. The grid only needs
    `alive`; the detail panel that shows the pane can afford a fresh check.
    """
    live = set(list_tmux_sessions())
    out = {}
    for session_id, entry in load_state()["managed"].items():
        name = entry.get("tmux", tmux_name(session_id))
        alive = name in live
        out[session_id] = {
            **entry,
            "alive": alive,
            # pane_dead deferred: would require N list-panes calls per poll.
            # Read fresh in detail view where it matters.
            "dead_pane": False,
            "attach": attach_command(session_id),
        }
    return out
