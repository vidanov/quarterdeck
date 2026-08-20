"""A plain login shell in a tmux session, driveable from the UI.

Why this exists: Quarterdeck spawns each session as *one* command — kiro-cli
with its flags — so there was nowhere to run an interactive command that is not
kiro-cli itself. `kiro login` and `kiro logout` are exactly that: they ask
questions and wait for answers, and `pre_command` cannot help because it runs
before the pane is handed over and its prompts have nobody to answer them.

Multiple shells are supported: each is keyed by cwd. The tmux session is named
`deck-shell-{8-char-hash}` so tmux_manager.reconcile won't adopt them as kiro
sessions. The legacy singleton `deck-shell` is kept for backwards compat.
"""
import hashlib
import os
import shlex
import threading
import time
from pathlib import Path

from . import tmux_manager as tmux
from .config import DEFAULT_COLS, DEFAULT_ROWS

# Legacy singleton name — kept so existing Settings panel still works
SHELL_TMUX_NAME = "deck-shell"
SHELL_PREFIX = "deck-shell-"

ALLOWED_KEYS = {
    "Enter", "Escape", "Tab", "BSpace", "Space",
    "Up", "Down", "Left", "Right",
    "Home", "End", "PageUp", "PageDown",
    "C-c", "C-d", "C-u", "C-l", "C-a", "C-e", "C-z", "C-r",
    "y", "n", "q",
}

_lock = threading.Lock()

READY_TIMEOUT = 8.0
READY_POLL = 0.15


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _shell_id(cwd: str) -> str:
    """8-char hash of the resolved cwd path, stable across restarts."""
    resolved = str(Path(cwd).expanduser().resolve())
    return hashlib.sha1(resolved.encode()).hexdigest()[:8]


def _tmux_name(shell_id: str) -> str:
    return f"{SHELL_PREFIX}{shell_id}"


def _login_shell() -> list[str]:
    shell = os.environ.get("SHELL") or "/bin/bash"
    return [shell, "-l"]


def _resolve_cwd(cwd: str) -> tuple[str, str]:
    target = os.path.expanduser(cwd.strip() or "~")
    if not Path(target).is_dir():
        return "", f"Directory not found: {cwd}"
    return str(Path(target).resolve()), ""


# ---------------------------------------------------------------------------
# Per-shell operations (new multi-shell API)
# ---------------------------------------------------------------------------

def list_shells() -> list[dict]:
    """Return all running deck-shell-* sessions."""
    out = tmux._tmux("list-sessions", "-F", "#{session_name}", check=False)
    results = []
    for name in out.strip().splitlines():
        name = name.strip()
        if not name.startswith(SHELL_PREFIX):
            continue
        alive = not tmux.pane_dead(name)
        cwd = tmux._tmux("display-message", "-p", "-t", name,
                         "#{pane_current_path}", check=False).strip()
        home = str(Path.home())
        results.append({
            "shell_id": name[len(SHELL_PREFIX):],
            "tmux_session": name,
            "alive": alive,
            "exists": tmux.session_exists(name),
            "cwd": cwd,
            "cwd_short": cwd.replace(home, "~") if cwd else "",
            "attach": f"tmux attach -t {shlex.quote(name)}",
        })
    return results


def open_for(cwd: str) -> dict:
    """Open (or return existing) shell for a given cwd.

    Returns as soon as the tmux session is created — no blocking poll.
    The frontend's pane-polling loop detects readiness within its next tick.
    """
    if not tmux.tmux_available():
        return {"ok": False, "error": "tmux not installed — brew install tmux"}
    target, error = _resolve_cwd(cwd)
    if error:
        return {"ok": False, "error": error}

    sid = _shell_id(target)
    name = _tmux_name(sid)

    with _lock:
        if tmux.session_exists(name) and not tmux.pane_dead(name):
            return {"ok": True, "already_running": True, **_shell_status(name, sid)}
        if tmux.session_exists(name):
            tmux._tmux("kill-session", "-t", name, check=False)
        try:
            tmux._tmux("new-session", "-d", "-s", name,
                       "-x", str(DEFAULT_COLS), "-y", str(DEFAULT_ROWS),
                       "-c", target, *_login_shell())
            tmux._tmux("set-option", "-t", name, "remain-on-exit", "on", check=False)
            tmux._tmux("set-hook", "-t", name, "client-attached",
                       "set-option -w window-size latest", check=False)
        except tmux.TmuxError as e:
            return {"ok": False, "error": str(e)}

    # Return immediately — no blocking wait. The pane poll loop in the frontend
    # picks up the output within its next interval (≤1200 ms).
    return {"ok": True, "ready": True, **_shell_status(name, sid)}


def capture_named(shell_id: str, lines: int = 40) -> str:
    name = _tmux_name(shell_id)
    if not tmux.session_exists(name):
        return ""
    return tmux._tmux("capture-pane", "-p", "-t", name, "-S", f"-{lines}", check=False)


def send_text_named(shell_id: str, text: str, submit: bool = True) -> dict:
    name = _tmux_name(shell_id)
    if not tmux.session_exists(name):
        return {"ok": False, "error": "Shell not found"}
    if tmux.pane_dead(name):
        return {"ok": False, "error": "Shell has exited"}
    flat = " ".join(text.replace("\r\n", "\n").split("\n"))
    try:
        tmux._tmux("send-keys", "-t", name, "-l", "--", flat)
        if submit:
            tmux._tmux("send-keys", "-t", name, "Enter")
    except tmux.TmuxError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


def send_key_named(shell_id: str, key: str, raw: bool = False) -> dict:
    """Send a named tmux key (e.g. 'C-c', 'Enter') to a named shell.

    When raw=True the key string bypasses the ALLOWED_KEYS whitelist and is
    passed directly to tmux send-keys — used by the frontend's raw terminal
    mode where keydown events are translated to tmux key names.
    """
    if not raw and key not in ALLOWED_KEYS:
        return {"ok": False, "error": f"Key not allowed: {key}"}
    name = _tmux_name(shell_id)
    if not tmux.session_exists(name):
        return {"ok": False, "error": "Shell not found"}
    try:
        tmux._tmux("send-keys", "-t", name, key)
    except tmux.TmuxError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


def resize_named(shell_id: str, cols: int, rows: int) -> dict:
    name = _tmux_name(shell_id)
    if not tmux.session_exists(name):
        return {"ok": False, "error": "Shell not found"}
    try:
        tmux._tmux("set-option", "-t", name, "-w", "window-size", "manual", check=False)
        tmux._tmux("resize-window", "-t", name, "-x", str(cols), "-y", str(rows))
    except tmux.TmuxError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "cols": cols, "rows": rows}


def close_named(shell_id: str) -> dict:
    name = _tmux_name(shell_id)
    existed = tmux.session_exists(name)
    if existed:
        tmux._tmux("kill-session", "-t", name, check=False)
    return {"ok": True, "closed": existed}


def get_pane_named(shell_id: str, lines: int = 40) -> dict:
    """Full shell state — cached for 0.4s to avoid 4 subprocess forks per poll.
    
    At 150ms raw-mode polling this means at most 1 real fetch per 2-3 polls
    instead of 4 forks * 6.7 polls/sec = 27 forks/sec.
    """
    now = time.time()
    cached = _get_pane_cache.get(shell_id)
    if cached and (now - cached[0]) < 0.4:
        return cached[1]
    result = _get_pane_named_uncached(shell_id, lines)
    _get_pane_cache[shell_id] = (now, result)
    return result

_get_pane_cache: dict = {}


def _get_pane_named_uncached(shell_id: str, lines: int = 40) -> dict:
    name = _tmux_name(shell_id)
    exists = tmux.session_exists(name)
    alive = exists and not tmux.pane_dead(name)
    pane = ""
    if alive or exists:
        pane = tmux._tmux("capture-pane", "-p", "-t", name, "-S", f"-{lines}", check=False)
    cursor_x, cursor_y, pane_height = -1, -1, 0
    cwd = ""
    if alive:
        # Single display-message for cwd + cursor — one fork instead of two.
        raw = tmux._tmux(
            "display-message", "-p", "-t", name,
            "#{pane_current_path}\t#{cursor_x}\t#{cursor_y}\t#{pane_height}", check=False
        ).strip()
        try:
            parts = raw.split("\t")
            cwd = parts[0]
            cursor_x, cursor_y = int(parts[1]), int(parts[2])
            pane_height = int(parts[3])
        except (IndexError, ValueError):
            pass
    home = str(Path.home())
    st = {
        "shell_id": shell_id,
        "tmux_session": name,
        "alive": alive,
        "exists": exists,
        "cwd": cwd,
        "cwd_short": cwd.replace(home, "~") if cwd else "",
        "attach": f"tmux attach -t {shlex.quote(name)}",
    }
    return {"pane": pane, "cursor_x": cursor_x, "cursor_y": cursor_y,
            "pane_height": pane_height, **st}


def _shell_status(name: str, shell_id: str) -> dict:
    exists = tmux.session_exists(name)
    alive = exists and not tmux.pane_dead(name)
    cwd = ""
    if alive:
        cwd = tmux._tmux("display-message", "-p", "-t", name,
                         "#{pane_current_path}", check=False).strip()
    home = str(Path.home())
    return {
        "shell_id": shell_id,
        "tmux_session": name,
        "alive": alive,
        "exists": exists,
        "cwd": cwd,
        "cwd_short": cwd.replace(home, "~") if cwd else "",
        "attach": f"tmux attach -t {shlex.quote(name)}",
    }


def _wait_ready_named(name: str, timeout: float = READY_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    previous = None
    while time.time() < deadline:
        time.sleep(READY_POLL)
        current = tmux._tmux("capture-pane", "-p", "-t", name, "-S", "-20",
                              check=False).strip()
        if current and current == previous:
            return True
        previous = current
    return False


# ---------------------------------------------------------------------------
# Legacy singleton API (kept for Settings panel backwards compat)
# ---------------------------------------------------------------------------

def is_alive() -> bool:
    if not tmux.session_exists(SHELL_TMUX_NAME):
        return False
    return not tmux.pane_dead(SHELL_TMUX_NAME)


def open_shell(cwd: str = "~") -> dict:
    if not tmux.tmux_available():
        return {"ok": False, "error": "tmux not installed — brew install tmux"}
    target, error = _resolve_cwd(cwd)
    if error:
        return {"ok": False, "error": error}

    with _lock:
        if is_alive():
            return {"ok": True, "already_running": True, **status()}
        if tmux.session_exists(SHELL_TMUX_NAME):
            tmux._tmux("kill-session", "-t", SHELL_TMUX_NAME, check=False)
        try:
            tmux._tmux("new-session", "-d", "-s", SHELL_TMUX_NAME,
                       "-x", str(DEFAULT_COLS), "-y", str(DEFAULT_ROWS),
                       "-c", target, *_login_shell())
            tmux._tmux("set-option", "-t", SHELL_TMUX_NAME,
                       "remain-on-exit", "on", check=False)
            tmux._tmux("set-hook", "-t", SHELL_TMUX_NAME, "client-attached",
                       "set-option -w window-size latest", check=False)
        except tmux.TmuxError as e:
            return {"ok": False, "error": str(e)}
    # Return immediately — frontend poll detects readiness.
    return {"ok": True, "ready": True, **status()}


def _wait_ready(timeout: float = READY_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    previous = None
    while time.time() < deadline:
        time.sleep(READY_POLL)
        current = capture(20).strip()
        if current and current == previous:
            return True
        previous = current
    return False


def capture(lines: int = 40) -> str:
    if not tmux.session_exists(SHELL_TMUX_NAME):
        return ""
    return tmux._tmux("capture-pane", "-p", "-t", SHELL_TMUX_NAME,
                      "-S", f"-{lines}", check=False)


def send_text(text: str, submit: bool = True) -> dict:
    if not tmux.session_exists(SHELL_TMUX_NAME):
        return {"ok": False, "error": "No shell is running"}
    if tmux.pane_dead(SHELL_TMUX_NAME):
        return {"ok": False, "error": "The shell has exited"}
    flat = " ".join(text.replace("\r\n", "\n").split("\n"))
    try:
        tmux._tmux("send-keys", "-t", SHELL_TMUX_NAME, "-l", "--", flat)
        if submit:
            tmux._tmux("send-keys", "-t", SHELL_TMUX_NAME, "Enter")
    except tmux.TmuxError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


def send_key(key: str) -> dict:
    if key not in ALLOWED_KEYS:
        return {"ok": False, "error": f"Key not allowed: {key}"}
    if not tmux.session_exists(SHELL_TMUX_NAME):
        return {"ok": False, "error": "No shell is running"}
    try:
        tmux._tmux("send-keys", "-t", SHELL_TMUX_NAME, key)
    except tmux.TmuxError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


def resize(cols: int, rows: int) -> dict:
    if not tmux.session_exists(SHELL_TMUX_NAME):
        return {"ok": False, "error": "No shell is running"}
    try:
        tmux._tmux("set-option", "-t", SHELL_TMUX_NAME, "-w",
                   "window-size", "manual", check=False)
        tmux._tmux("resize-window", "-t", SHELL_TMUX_NAME,
                   "-x", str(cols), "-y", str(rows))
    except tmux.TmuxError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "cols": cols, "rows": rows}


def close() -> dict:
    with _lock:
        existed = tmux.session_exists(SHELL_TMUX_NAME)
        if existed:
            tmux._tmux("kill-session", "-t", SHELL_TMUX_NAME, check=False)
    return {"ok": True, "closed": existed}


def status() -> dict:
    alive = is_alive()
    cwd = ""
    if alive:
        cwd = tmux._tmux("display-message", "-p", "-t", SHELL_TMUX_NAME,
                         "#{pane_current_path}", check=False).strip()
    return {
        "alive": alive,
        "exists": tmux.session_exists(SHELL_TMUX_NAME),
        "cwd": cwd,
        "tmux_session": SHELL_TMUX_NAME,
        "attach": f"tmux attach -t {shlex.quote(SHELL_TMUX_NAME)}",
    }
