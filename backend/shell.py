"""A plain login shell in a tmux session, driveable from the UI.

Why this exists: Quarterdeck spawns each session as *one* command — kiro-cli
with its flags — so there was nowhere to run an interactive command that is not
kiro-cli itself. `kiro login` and `kiro logout` are exactly that: they ask
questions and wait for answers, and `pre_command` cannot help because it runs
before the pane is handed over and its prompts have nobody to answer them.

Deliberately a singleton. One shell answers the case this was asked for —
occasional interactive administration, `kiro login` first among them — and a
singleton needs no id allocation, no listing, and no way to lose track of a
process nobody can see. If several are ever wanted, that is a later change with
a reason behind it, not a guess now.

**This is arbitrary command execution, by design.** It does not widen the threat
model: the API already dispatches processes, types into shell-capable agents,
and runs `pre_command` verbatim, and the README says plainly that any request
past the token is code execution on this machine. What it does do is make that
capability obvious and direct, so it is held to the same controls as the rest —
the same token, the same tailnet-only source check, the same audit trail, and
the input rate limit (`auth._INPUT_PATH` covers `/api/shell/input` for that
reason; leaving it out would have made this the one unmetered way in).

The tmux session is named `deck-shell`, not `kiro-<id>`: `tmux_manager.reconcile`
adopts stray `kiro-*` sessions as managed kiro sessions, and a shell is not one.
"""
import os
import shlex
import threading
import time
from pathlib import Path

from . import tmux_manager as tmux
from .config import DEFAULT_COLS, DEFAULT_ROWS

SHELL_TMUX_NAME = "deck-shell"
# Keys the UI may send. An allowlist rather than a pass-through: these are tmux
# key *names*, and the set a terminal actually needs is small and enumerable.
# Ctrl-C and Ctrl-D are here because interrupting and ending a prompt are the
# two things you cannot do with text alone.
ALLOWED_KEYS = {
    "Enter", "Escape", "Tab", "BSpace", "Space",
    "Up", "Down", "Left", "Right",
    "Home", "End", "PageUp", "PageDown",
    "C-c", "C-d", "C-u", "C-l", "C-a", "C-e", "C-z", "C-r",
    "y", "n", "q",
}

_lock = threading.Lock()

# How long to wait for the shell to finish starting before returning from open.
# Not cosmetic: keystrokes sent before the prompt is drawn are swallowed by the
# shell's own initialisation, so "open, then tap `kiro-cli login`" typed the
# command into nothing and looked like the button did not work. Measured on this
# machine, zsh with the user's profile settles in about 0.4s.
READY_TIMEOUT = 8.0
READY_POLL = 0.15


def _login_shell() -> list[str]:
    """The user's own shell, as a login shell.

    Login matters here more than usual: `kiro` has to be on PATH, and in a
    packaged launch the app's PATH is not the user's. A login shell reads the
    user's profile and gets the PATH they actually have in a terminal.
    """
    shell = os.environ.get("SHELL") or "/bin/bash"
    return [shell, "-l"]


def is_alive() -> bool:
    """True if the shell's tmux session exists and its process is still there."""
    if not tmux.session_exists(SHELL_TMUX_NAME):
        return False
    return not tmux.pane_dead(SHELL_TMUX_NAME)


def _resolve_cwd(cwd: str) -> tuple[str, str]:
    """(path, error). Empty path means the error is set."""
    target = os.path.expanduser(cwd.strip() or "~")
    if not Path(target).is_dir():
        return "", f"Directory not found: {cwd}"
    return str(Path(target).resolve()), ""


def open_shell(cwd: str = "~") -> dict:
    """Start the shell, or report the one already running."""
    if not tmux.tmux_available():
        return {"ok": False, "error": "tmux not installed — brew install tmux"}
    target, error = _resolve_cwd(cwd)
    if error:
        return {"ok": False, "error": error}

    with _lock:
        if is_alive():
            return {"ok": True, "already_running": True, **status()}
        # A dead pane left over from a previous shell that exited. Clear it
        # rather than refusing, or `remain-on-exit` makes the feature one-use.
        if tmux.session_exists(SHELL_TMUX_NAME):
            tmux._tmux("kill-session", "-t", SHELL_TMUX_NAME, check=False)
        try:
            tmux._tmux("new-session", "-d", "-s", SHELL_TMUX_NAME,
                       "-x", str(DEFAULT_COLS), "-y", str(DEFAULT_ROWS),
                       "-c", target, *_login_shell())
            # Same two options a kiro session gets: keep a dead pane so the exit
            # is visible rather than silent, and hand sizing back to a real
            # terminal if someone attaches from one.
            tmux._tmux("set-option", "-t", SHELL_TMUX_NAME,
                       "remain-on-exit", "on", check=False)
            tmux._tmux("set-hook", "-t", SHELL_TMUX_NAME, "client-attached",
                       "set-option -w window-size latest", check=False)
        except tmux.TmuxError as e:
            return {"ok": False, "error": str(e)}
    ready = _wait_ready()
    # `ready: False` is reported rather than treated as a failure: the shell is
    # running either way, and a slow profile is not a reason to refuse it. The
    # caller can say "still starting" instead of typing into it.
    return {"ok": True, "ready": ready, **status()}


def _wait_ready(timeout: float = READY_TIMEOUT) -> bool:
    """Wait until the pane stops changing, i.e. the prompt has been drawn.

    The prompt string belongs to the user, so it cannot be matched against —
    what is checkable is that output has appeared and then settled. Two
    identical non-empty samples in a row is that.
    """
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
    """Type a line into the shell.

    Newlines are collapsed to spaces: each one is a submit at a shell prompt, so
    pasted multi-line text would run as several commands, and running the second
    half of something the user has not finished reading is the wrong default.
    """
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
    """Send one named key, from ALLOWED_KEYS."""
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
    """Reflow the shell to the viewer's width, the way a session does."""
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
    """Kill the shell. Whatever was running in it goes too, so the UI asks first."""
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
        # Reported even when dead, so a pane that exited can still be read
        # before it is cleared.
        "exists": tmux.session_exists(SHELL_TMUX_NAME),
        "cwd": cwd,
        "tmux_session": SHELL_TMUX_NAME,
        "attach": f"tmux attach -t {shlex.quote(SHELL_TMUX_NAME)}",
    }
