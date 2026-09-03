"""CLI binding registry — connects kiro-cli tmux panes to Quarterdeck sessions.

Lets users point Quarterdeck at an already-running kiro-cli instance so they
can send chat from the detail panel without spawning a new session. Handles
two real scenarios:

  1. A kiro-cli started manually in a terminal — idle, wants to receive tasks.
  2. A kiro-cli busy serving a web UI or running a long task — not available;
     show a "New session here" shortcut instead.

Bindings are stored in ~/.osa-kiro/cli_bindings.json:
  { "<session_id>": { "tmux_session": "...", "cwd": "...", "bound_at": 0.0 } }

Discovery scans all tmux sessions for panes running kiro-cli, reads the
KIRO_SESSION_ID env var from each to identify which session it belongs to,
then derives idle/busy status from the TUI footer.
"""
import json
import subprocess
import time
from pathlib import Path

from .config import STATE_DIR, tmux_base_argv

BINDINGS_FILE = STATE_DIR / "cli_bindings.json"
FOOTER_LINES = 12


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load() -> dict:
    """Return {session_id: {tmux_session, cwd, bound_at}} or {}."""
    if not BINDINGS_FILE.exists():
        return {}
    try:
        return json.loads(BINDINGS_FILE.read_text())
    except Exception:
        return {}


def _save(bindings: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    BINDINGS_FILE.write_text(json.dumps(bindings, indent=2))


# ---------------------------------------------------------------------------
# tmux helpers (local, no import from tmux_manager to avoid circular deps)
# ---------------------------------------------------------------------------

def _tmux(*args: str) -> str:
    """Run a tmux command and return stdout. Returns '' on failure.

    Goes through config.tmux_base_argv so this module talks to the same tmux
    server, started from the same config, as tmux_manager does.
    """
    try:
        r = subprocess.run([*tmux_base_argv(), *args],
                           capture_output=True, text=True, timeout=5)
        return r.stdout
    except Exception:
        return ""


def _session_exists(name: str) -> bool:
    r = subprocess.run([*tmux_base_argv(), "has-session", "-t", name],
                       capture_output=True, timeout=3)
    return r.returncode == 0


def _capture_pane(name: str, lines: int = 40) -> str:
    return _tmux("capture-pane", "-p", "-t", name, "-S", f"-{lines}")


def _pane_pid(name: str) -> int:
    out = _tmux("list-panes", "-t", name, "-F", "#{pane_pid}")
    try:
        return int(out.strip())
    except ValueError:
        return 0


def _tmux_env(session: str, var: str) -> str:
    """Read an env var from a tmux session's own environment.

    tmux keeps a copy of the environment a session was created with,
    independent of what the OS will let another process's owner read back.
    The previous approach shelled out to `ps -Ewww -p <pid>` and grepped for
    VAR=, which is silently dead on macOS: Apple's ps omits another process's
    environment from the listing — no error, just nothing to find — so every
    call returned "". Reading it from tmux's own copy instead of the OS
    works regardless, and is what session-scoped markers like Captain's
    CAPTAIN_SESSION or a non-standard KIRO_SESSION_ID should go through.
    """
    out = _tmux("show-environment", "-t", session, var)
    if not out or out.startswith("-"):  # tmux prints "-VAR" for an unset var
        return ""
    _, _, value = out.strip().partition("=")
    return value


def _pane_cmd(name: str) -> str:
    return _tmux("list-panes", "-t", name, "-F", "#{pane_current_command}").strip()


# ---------------------------------------------------------------------------
# Status detection (same logic as api.py pane_status)
# ---------------------------------------------------------------------------

def _pane_cli_status(pane_text: str) -> str:
    """Derive idle/thinking/unknown from the TUI footer."""
    if not pane_text:
        return "unknown"
    footer = [line.strip() for line in pane_text.rstrip().splitlines()[-FOOTER_LINES:]]
    if any(line.startswith("ask a question or describe a task") for line in footer):
        return "idle"
    if any(
        line.startswith("Kiro is working") or line.startswith("esc to cancel")
        for line in footer
    ):
        return "thinking"
    # Approval prompt
    if any("allow" in line.lower() and "deny" in line.lower() for line in footer):
        return "awaiting-approval"
    return "unknown"


# ---------------------------------------------------------------------------
# Discovery — scan tmux for kiro-cli panes
# ---------------------------------------------------------------------------

def discover_cli_instances() -> list[dict]:
    """Return all discoverable kiro-cli chat panes with their status.

    Each entry:
      tmux_session   — tmux session name
      kiro_session   — session UUID (from tmux name for kiro-* sessions,
                       or KIRO_SESSION_ID env var, or empty)
      cwd            — current working directory of the pane
      status         — idle | thinking | awaiting-approval | unknown | dead
      pane_pid       — PID of the shell in the pane
    """
    import re as _re
    out = _tmux("list-sessions", "-F", "#{session_name}")
    if not out.strip():
        return []

    results = []
    for name in out.strip().splitlines():
        name = name.strip()
        if not name:
            continue
        # Skip internal Quarterdeck sessions, and anything Captain owns — its
        # Bosun and worker sessions run kiro-cli too, but they are managed by
        # Captain's own loop, not something to list here as an adoptable
        # instance. The name prefix ("captain-") covers a session started
        # since Captain began stamping it; CAPTAIN_SESSION covers one started
        # before that but still running, which carries the marker in its
        # tmux environment even though its name predates the convention.
        if name.startswith("deck-") or name.startswith("captain-"):
            continue
        if _tmux_env(name, "CAPTAIN_SESSION"):
            continue
        # Check that the pane is running kiro-cli (or kiro-cli-chat)
        cmd = _pane_cmd(name)
        if "kiro" not in cmd.lower():
            continue

        pid = _pane_pid(name)

        # Derive session ID: kiro-{uuid} name pattern is the primary source
        kiro_sid = ""
        m = _re.match(r"^kiro-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", name)
        if m:
            kiro_sid = m.group(1)
        else:
            # Fall back to env var for non-standard names. Read through tmux,
            # not the OS: ps -Ewww cannot see another process's environment
            # on macOS, so the old pid-based _pane_env always came back empty
            # here.
            kiro_sid = _tmux_env(name, "KIRO_SESSION_ID")

        pane_text = _capture_pane(name, lines=40)
        status = _pane_cli_status(pane_text)

        cwd_out = _tmux("list-panes", "-t", name, "-F", "#{pane_current_path}")
        cwd = cwd_out.strip()

        # Shorten CWD for display
        home = str(Path.home())
        cwd_short = cwd.replace(home, "~") if cwd else ""

        results.append({
            "tmux_session": name,
            "kiro_session": kiro_sid,
            "cwd": cwd,
            "cwd_short": cwd_short,
            "status": status,
            "pane_pid": pid,
        })

    return results


# ---------------------------------------------------------------------------
# Binding management
# ---------------------------------------------------------------------------

def bind(session_id: str, tmux_session: str) -> dict:
    """Bind a Quarterdeck session to a CLI tmux pane."""
    if not _session_exists(tmux_session):
        return {"ok": False, "error": f"tmux session {tmux_session!r} not found"}
    bindings = _load()
    # Remove any existing binding for this tmux_session from other sessions
    for sid, b in list(bindings.items()):
        if b.get("tmux_session") == tmux_session and sid != session_id:
            del bindings[sid]
    cwd = _tmux("list-panes", "-t", tmux_session, "-F", "#{pane_current_path}").strip()
    bindings[session_id] = {
        "tmux_session": tmux_session,
        "cwd": cwd,
        "bound_at": time.time(),
    }
    _save(bindings)
    return {"ok": True, "session_id": session_id, "tmux_session": tmux_session}


def unbind(session_id: str) -> dict:
    """Remove a CLI binding for a Quarterdeck session."""
    bindings = _load()
    if session_id not in bindings:
        return {"ok": False, "error": "No binding found"}
    del bindings[session_id]
    _save(bindings)
    return {"ok": True}


def get_binding(session_id: str) -> dict | None:
    """Return the binding for a session, or None if not bound."""
    return _load().get(session_id)


def get_status(session_id: str) -> dict:
    """Return binding + live status for a session's bound CLI pane.

    Returns:
      bound: bool
      tmux_session: str | None
      cwd: str
      status: idle | thinking | awaiting-approval | unknown | dead | unbound
    """
    binding = get_binding(session_id)
    if not binding:
        return {"bound": False, "tmux_session": None, "cwd": "", "status": "unbound"}

    name = binding["tmux_session"]
    if not _session_exists(name):
        return {
            "bound": True,
            "tmux_session": name,
            "cwd": binding.get("cwd", ""),
            "status": "dead",
        }
    pane_text = _capture_pane(name, lines=40)
    status = _pane_cli_status(pane_text)
    cwd = _tmux("list-panes", "-t", name, "-F", "#{pane_current_path}").strip()
    return {
        "bound": True,
        "tmux_session": name,
        "cwd": cwd,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Sending text
# ---------------------------------------------------------------------------

def send(session_id: str, text: str) -> dict:
    """Send text to the CLI pane bound to a Quarterdeck session.

    Returns ok=False when the pane is busy (thinking/awaiting-approval) to
    prevent stomping on a running agent turn. The caller may offer "new session
    here" instead.
    """
    status_info = get_status(session_id)
    if not status_info["bound"]:
        return {"ok": False, "error": "No CLI bound to this session"}
    if status_info["status"] == "dead":
        return {"ok": False, "error": "Bound CLI pane is no longer alive"}
    if status_info["status"] in ("thinking", "awaiting-approval"):
        return {
            "ok": False,
            "busy": True,
            "error": "CLI is busy — start a new session instead",
            "cwd": status_info["cwd"],
        }

    name = status_info["tmux_session"]
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return {"ok": False, "error": "Empty text"}

    try:
        import tempfile
        if len(text) > 512:
            # Use load-buffer for large pastes
            buf_name = f"qd-cli-{session_id[:8]}"
            bracketed = f"\x1b[200~{text}\x1b[201~"
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                             delete=False, prefix="qd-cli-") as f:
                f.write(bracketed)
                tmp_path = f.name
            _tmux("load-buffer", "-b", buf_name, tmp_path)
            _tmux("paste-buffer", "-b", buf_name, "-t", name, "-r")
            import os
            os.unlink(tmp_path)
        else:
            _tmux("send-keys", "-t", name, "-l", "--", text)
            _tmux("send-keys", "-t", name, "Enter")
        return {"ok": True, "tmux_session": name}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
