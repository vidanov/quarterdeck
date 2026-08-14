"""Side chat — non-blocking clarification sessions against frozen parent context.

Each parent session can have one active side chat. A side chat is a separate
kiro-cli process (ask mode, no tools) that starts with a frozen snapshot of
the parent's recent transcript injected as context. It never writes back to
the parent; its own JSONL lives under ~/.osa-kiro/side/.

Usage:
    open(parent_id, context_text)  -> tmux session name
    send(parent_id, text)          -> bool
    capture(parent_id, lines)      -> str (raw pane output)
    poll(parent_id)                -> dict {alive, lines, exchanges}
    close(parent_id)               -> bool
"""
import json
import re
import threading
import time
from pathlib import Path

from . import tmux_manager as tmux
from .config import STATE_DIR, KIRO_CLI

SIDE_PREFIX = "deck-side-"
SIDE_DIR = STATE_DIR / "side"
MAX_CONTEXT_CHARS = 8000   # max chars of parent transcript to inject
READY_TIMEOUT = 20.0
POLL_INTERVAL = 0.4

_lock = threading.Lock()
# parent_id -> {name, exchanges: [{role, text}], opened_at}
_sessions: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _tmux_name(parent_id: str) -> str:
    # Truncate so tmux name stays short
    return f"{SIDE_PREFIX}{parent_id[:16]}"


def _wait_ready(name: str) -> bool:
    deadline = time.time() + READY_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.5)
        pane = tmux._tmux("capture-pane", "-p", "-t", name, "-S", "-20", check=False)
        low = pane.lower()
        if "ask a question" in low or "describe a task" in low or "/quit to exit" in low:
            return True
    return tmux.session_exists(name)


def _capture_raw(parent_id: str, lines: int = 60) -> str:
    name = _tmux_name(parent_id)
    if not tmux.session_exists(name):
        return ""
    return tmux._tmux("capture-pane", "-p", "-t", name, "-S", f"-{lines}", check=False)


def _send_keys(parent_id: str, text: str) -> bool:
    name = _tmux_name(parent_id)
    if not tmux.session_exists(name):
        return False
    if tmux.pane_dead(name):
        return False
    try:
        tmux._tmux("send-keys", "-t", name, "-l", "--", text)
        tmux._tmux("send-keys", "-t", name, "Enter")
        return True
    except tmux.TmuxError:
        return False


def _strip_ansi(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*[mABCDEFGHJKLMSTfhilnprsu]', '', text)


def _extract_response(pane: str, after_marker: str | None = None) -> str:
    """Extract the assistant's latest response from a pane capture."""
    clean = _strip_ansi(pane)
    lines = clean.splitlines()
    # Find the last "ask a question" prompt line — response is above it
    prompt_idx = -1
    for i in range(len(lines) - 1, -1, -1):
        if "ask a question" in lines[i].lower() or "describe a task" in lines[i].lower():
            prompt_idx = i
            break
    if prompt_idx <= 0:
        return ""
    # Take content between second-to-last prompt and last prompt
    prev_prompt = -1
    for i in range(prompt_idx - 1, -1, -1):
        if "ask a question" in lines[i].lower() or "describe a task" in lines[i].lower():
            prev_prompt = i
            break
    start = prev_prompt + 1 if prev_prompt >= 0 else 0
    response_lines = lines[start:prompt_idx]
    # Strip empty leading/trailing
    while response_lines and not response_lines[0].strip():
        response_lines.pop(0)
    while response_lines and not response_lines[-1].strip():
        response_lines.pop()
    return "\n".join(response_lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_open(parent_id: str) -> bool:
    name = _tmux_name(parent_id)
    return tmux.session_exists(name) and not tmux.pane_dead(name)


def open_session(parent_id: str, context_text: str) -> dict:
    """Spawn a side-chat kiro-cli session for parent_id.

    context_text is the frozen parent transcript excerpt injected as the
    opening system message.
    """
    with _lock:
        name = _tmux_name(parent_id)

        # Kill any leftover dead session
        if tmux.session_exists(name) and tmux.pane_dead(name):
            tmux._tmux("kill-session", "-t", name, check=False)

        if tmux.session_exists(name):
            return {"ok": True, "name": name, "reused": True}

        SIDE_DIR.mkdir(parents=True, exist_ok=True)

        # Write frozen context to file for reference
        ctx_excerpt = context_text[-MAX_CONTEXT_CHARS:] if len(context_text) > MAX_CONTEXT_CHARS else context_text
        context_file = SIDE_DIR / f"{parent_id[:16]}-context.md"
        context_file.write_text(ctx_excerpt, encoding="utf-8")

        # fs_read allows reading files so the side chat can answer questions
        # about code and project state. Write and execute tools stay off.
        argv = [KIRO_CLI, "chat", "--trust-tools", "fs_read"]
        cwd = str(Path.home())

        try:
            tmux._tmux(
                "new-session", "-d", "-s", name,
                "-x", "120", "-y", "36",
                "-c", cwd, *argv,
            )
            tmux._tmux("set-option", "-t", name, "remain-on-exit", "on", check=False)
        except tmux.TmuxError as e:
            return {"error": f"Could not spawn side chat: {e}"}

        ready = _wait_ready(name)
        if not ready:
            return {"error": "Side chat session did not become ready"}

        # Inject the frozen context as the first message
        intro = (
            "You are a read-only assistant for a Kiro CLI session. "
            "You may read files to answer questions but must not edit files or run commands. "
            "Here is the frozen session transcript:\n\n"
            f"{ctx_excerpt}\n\n"
            "Confirm you have received this context and are ready to answer questions about it."
        )
        _send_keys(parent_id, intro)

        _sessions[parent_id] = {
            "name": name,
            "exchanges": [],
            "opened_at": time.time(),
        }

        return {"ok": True, "name": name}


def send(parent_id: str, text: str) -> dict:
    """Send a user message to the side chat."""
    if not is_open(parent_id):
        return {"error": "No active side chat for this session"}

    with _lock:
        meta = _sessions.get(parent_id, {})
        if "exchanges" not in meta:
            meta = {"name": _tmux_name(parent_id), "exchanges": [], "opened_at": time.time()}
            _sessions[parent_id] = meta
        meta["exchanges"].append({"role": "user", "text": text, "at": time.time()})

    ok = _send_keys(parent_id, text)
    return {"ok": ok}


def poll(parent_id: str) -> dict:
    """Return current pane state for the side chat."""
    if not is_open(parent_id):
        return {"alive": False, "lines": [], "exchanges": []}

    raw = _capture_raw(parent_id, lines=80)
    clean = _strip_ansi(raw)
    lines = [l for l in clean.splitlines() if l.strip()]

    # Detect if currently "thinking" (no prompt line visible at bottom)
    last_lines = clean.splitlines()[-5:] if clean else []
    ready = any(
        "ask a question" in l.lower() or "describe a task" in l.lower()
        for l in last_lines
    )

    with _lock:
        meta = _sessions.get(parent_id, {})
        exchanges = meta.get("exchanges", [])

    return {
        "alive": True,
        "thinking": not ready,
        "lines": lines,
        "exchanges": exchanges,
        "raw": clean,
    }


def close(parent_id: str) -> dict:
    """Kill the side chat session."""
    name = _tmux_name(parent_id)
    if tmux.session_exists(name):
        tmux._tmux("kill-session", "-t", name, check=False)
    with _lock:
        _sessions.pop(parent_id, None)
    # Clean up context file
    context_file = SIDE_DIR / f"{parent_id[:16]}-context.md"
    context_file.unlink(missing_ok=True)
    return {"ok": True}


def list_open() -> list[str]:
    """Return parent_ids with active side chats."""
    return [pid for pid in list(_sessions) if is_open(pid)]


def get_transcript(parent_id: str) -> str:
    """Return the full side chat pane content for forking."""
    return _strip_ansi(_capture_raw(parent_id, lines=200))
