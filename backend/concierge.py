"""Persistent concierge session for Quarterdeck's command bar.

Spawns a dedicated kiro-cli session that stays alive between queries. The
session is steered by .kiro/steering/concierge.md which tells it to respond
with structured JSON that the UI can render directly.

The concierge is hidden from the main session grid (filtered by its tmux
prefix) and auto-restarts if killed.
"""
import json
import re
import threading
import time
from pathlib import Path

from . import tmux_manager as tmux
from .config import SESSIONS_DIR, KIRO_CLI, available_models, read_settings


def configured_model() -> str:
    """Which model the concierge runs, from shared settings. "auto" if unset.

    Validated against what kiro-cli actually offers: a stale name here would
    fail at spawn inside a detached pane, where the error is invisible and the
    concierge simply appears dead.
    """
    choice = read_settings().get("concierge_model", "auto")
    if not isinstance(choice, str) or choice not in available_models():
        return "auto"
    return choice

CONCIERGE_PREFIX = "deck-concierge"

# Ensure the steering file exists wherever we run from.
# In dev mode it's in the project .kiro/steering/. In the frozen app
# __file__ is inside Contents/Resources/ and the .kiro folder isn't bundled,
# so we write the content from this embedded constant into the state dir.
_STEERING_CONTENT = """\
# Concierge — Deck's Built-in Assistant

You are the concierge for Deck, an agent control surface that manages kiro-cli sessions. You help users find sessions, get reports, launch new work, and navigate their session history — all through natural language.

## Your Role

You are a smart command bar. The user types a question or request, and you:
1. Understand what they want
2. Query Deck's API to get the data
3. Return a structured JSON response the UI can render

## Communication Protocol

**Every response MUST be a single JSON block wrapped in triple backticks with the `json` language tag.** No prose before or after. The UI parses this directly.

```json
{
  "type": "results|report|action|error",
  "title": "Short headline for the UI",
  "items": [],
  "actions": [],
  "narrative": ""
}
```

## Available Deck API

Base URL: `http://127.0.0.1:19418`

**GET /api/sessions** — All sessions. Status: thinking, running, awaiting-approval, idle, done, error. Control: managed, foreign, starting, archived.
**GET /api/sessions/{id}** — Full detail including last_output.
**POST /api/dispatch** — Body: `{"task": "...", "cwd": "/path"}`. Launch new session.
**POST /api/sessions/{id}/resume** — Resume archived session.
**POST /api/sessions/{id}/input** — Body: `{"text": "..."}`. Send input.
**POST /api/sessions/{id}/kill** — Kill session.
**GET /api/stats?period=7d|30d** — Usage stats.

## Rules
1. Search first, act second.
2. Only dispatch when user says "start", "launch", "new session".
3. Keep narrative 1-3 sentences.
4. Always include resume actions for found sessions.
"""

def _ensure_steering() -> str:
    """Return a CWD that has .kiro/steering/concierge.md. Creates it if needed."""
    project_root = Path(__file__).parent.parent
    project_steering = project_root / ".kiro" / "steering" / "concierge.md"
    if project_steering.exists():
        return str(project_root)
    # Frozen app or non-project CWD — write to state dir
    from .config import STATE_DIR
    steering_dir = STATE_DIR / ".kiro" / "steering"
    steering_dir.mkdir(parents=True, exist_ok=True)
    dst = steering_dir / "concierge.md"
    dst.write_text(_STEERING_CONTENT)
    return str(STATE_DIR)

CONCIERGE_CWD = _ensure_steering()
RESPONSE_TIMEOUT = 60.0  # max seconds to wait for a response
POLL_INTERVAL = 0.5  # seconds between checks for new output
# After sending a query, wait at least this long before polling — gives the
# model time to start producing output.
INITIAL_DELAY = 2.0

_lock = threading.Lock()
_session_id: str | None = None
# Which model the live session was actually spawned with. Distinct from the
# configured one: changing the setting does not move a running session, and the
# settings page needs to be able to say so.
_running_model: str | None = None


def _tmux_name() -> str:
    return CONCIERGE_PREFIX


def is_alive() -> bool:
    """True if the concierge tmux session exists and its process is running."""
    if not tmux.session_exists(_tmux_name()):
        return False
    return not tmux.pane_dead(_tmux_name())


def _spawn() -> bool:
    """Spawn the concierge kiro-cli session. Returns True on success."""
    if is_alive():
        return True

    # Kill any leftover dead session first
    if tmux.session_exists(_tmux_name()):
        tmux._tmux("kill-session", "-t", _tmux_name(), check=False)

    # Trust shell so the concierge can run curl to call Quarterdeck's API.
    # Using specific tool trust avoids the scary confirmation prompt.
    # Model comes from settings; "auto" unless someone chose otherwise.
    global _running_model
    model = configured_model()
    argv = [KIRO_CLI, "chat", "--trust-tools=shell,read,write,glob,grep",
            f"--model={model}"]
    name = _tmux_name()

    try:
        tmux._tmux(
            "new-session", "-d", "-s", name,
            "-x", "120", "-y", "36",
            "-c", CONCIERGE_CWD, *argv,
        )
        tmux._tmux("set-option", "-t", name, "remain-on-exit", "on", check=False)
    except tmux.TmuxError:
        return False
    _running_model = model

    # Wait for kiro-cli to be ready (shows the chat prompt)
    deadline = time.time() + 15.0
    while time.time() < deadline:
        time.sleep(0.5)
        pane = _capture(20)
        # Look for the chat input prompt
        if "ask a question" in pane.lower() or "describe a task" in pane.lower():
            return True
        if "/quit to exit" in pane:
            return True

    # Even if we can't detect the prompt, the session may still be fine
    return tmux.session_exists(name)


def _capture(lines: int = 40) -> str:
    """Capture pane content from the concierge session."""
    name = _tmux_name()
    if not tmux.session_exists(name):
        return ""
    return tmux._tmux("capture-pane", "-p", "-t", name, "-S", f"-{lines}", check=False)


def _send(text: str) -> bool:
    """Send text to the concierge and submit."""
    name = _tmux_name()
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


def _find_session_id() -> str | None:
    """Find the kiro session id that belongs to the concierge process."""
    name = _tmux_name()
    root_pid = tmux.pane_pid(name)
    if not root_pid:
        return None

    # Look for a .lock file whose pid is a descendant of our pane
    tree = tmux._process_tree() if hasattr(tmux, '_process_tree') else {}
    if not tree:
        try:
            import subprocess
            out = subprocess.run(
                ["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=10
            ).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) == 2:
                    try:
                        tree[int(parts[0])] = int(parts[1])
                    except ValueError:
                        pass
        except:
            pass

    for lock_path in SESSIONS_DIR.glob("*.lock"):
        try:
            lock = json.loads(lock_path.read_text())
            pid = lock.get("pid")
            if not isinstance(pid, int):
                continue
            # Check if pid is descendant of root_pid
            current = pid
            seen = set()
            while current and current not in seen:
                if current == root_pid:
                    return lock_path.stem
                seen.add(current)
                current = tree.get(current, 0)
        except:
            continue
    return None


def _get_last_assistant_output(session_id: str, after_pos: int = 0) -> str:
    """Read the last assistant message from the session's JSONL.
    
    If after_pos is given, only consider content after that file position.
    """
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
    if not jsonl_path.exists():
        return ""
    try:
        with open(jsonl_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if after_pos > 0:
                # Only read content after the specified position
                if size <= after_pos:
                    return ""
                f.seek(after_pos)
                content = f.read().decode("utf-8", errors="replace")
            else:
                chunk_size = min(size, 262144)  # last 256KB
                f.seek(size - chunk_size)
                content = f.read().decode("utf-8", errors="replace")

        last_output = ""
        for line in content.strip().split("\n"):
            try:
                entry = json.loads(line)
                if entry.get("kind") != "AssistantMessage":
                    continue
                text_parts = []
                for block in entry.get("data", {}).get("content", []):
                    if isinstance(block, dict) and block.get("kind") == "text":
                        t = block.get("data", "").strip()
                        if t:
                            text_parts.append(t)
                combined = "\n".join(text_parts)
                if combined:
                    last_output = combined
            except (json.JSONDecodeError, KeyError):
                continue
        return last_output
    except OSError:
        return ""


def _count_assistant_messages(session_id: str) -> int:
    """Count total assistant messages in the JSONL — used to detect new output."""
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
    if not jsonl_path.exists():
        return 0
    try:
        count = 0
        with open(jsonl_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk_size = min(size, 524288)
            f.seek(size - chunk_size)
            content = f.read().decode("utf-8", errors="replace")
        for line in content.strip().split("\n"):
            try:
                entry = json.loads(line)
                if entry.get("kind") == "AssistantMessage":
                    count += 1
            except:
                continue
        return count
    except:
        return 0


def _jsonl_size(session_id: str) -> int:
    """Get current size of the session's JSONL file."""
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
    try:
        return jsonl_path.stat().st_size
    except:
        return 0


def _extract_json(text: str) -> dict | None:
    """Extract a JSON block from the assistant's response text.

    The concierge is instructed to respond with a ```json block.
    """
    # Try to find a fenced json block
    match = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try bare JSON object
    match = re.search(r'\{[^{}]*"type"[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Try the entire text as JSON
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    return None


# ---------------------------------------------------------------------------
# Fast-path resolver — no AI round-trip needed for common intents
# ---------------------------------------------------------------------------

def _search_sessions_fast(q: str) -> list[dict]:
    """Search active + archived sessions by title/cwd keywords. Returns up to 12 matches.

    Uses OR matching ranked by token hit count — a session matching 3 of 4 tokens
    ranks above one matching 1. This handles queries like "Porsche S3 Gateway"
    where no single session title contains all words.
    """
    import json as _json
    import re as _re
    results: list[dict] = []
    seen: set[str] = set()

    if not SESSIONS_DIR.exists():
        return results

    active_ids: set[str] = set()
    for lock_file in SESSIONS_DIR.glob("*.lock"):
        active_ids.add(lock_file.stem)

    tokens = [t for t in q.lower().split() if len(t) >= 2]
    if not tokens:
        return results

    candidates: list[tuple[int, float, dict]] = []

    for json_file in sorted(SESSIONS_DIR.glob("*.json"),
                            key=lambda f: f.stat().st_mtime, reverse=True):
        sid = json_file.stem
        if sid in seen:
            continue
        try:
            meta = _json.loads(json_file.read_text())
        except Exception:
            continue
        title = meta.get("title") or meta.get("name") or ""
        title = _re.sub(r"\s+[0-9a-f]{8}$", "", title, flags=_re.I).strip() or "Untitled"
        cwd = meta.get("cwd") or ""
        haystack = f"{title.lower()} {cwd.lower()}"
        hits = sum(1 for t in tokens if t in haystack)
        if hits == 0:
            continue
        seen.add(sid)
        cwd_short = cwd.replace(str(Path.home()), "~") if cwd else ""
        mtime = json_file.stat().st_mtime
        candidates.append((hits, mtime, {
            "id": sid,
            "title": title[:80],
            "cwd": cwd,
            "cwd_short": cwd_short,
            "status": "active" if sid in active_ids else "done",
            "updated_at": meta.get("updated_at") or meta.get("created_at") or "",
        }))

    # Best hits first, then most recent
    candidates.sort(key=lambda x: (-x[0], -x[1]))
    # Require at least half the tokens to match (avoids very noisy results)
    min_hits = max(1, (len(tokens) + 1) // 2)
    for hits, _, entry in candidates:
        if hits < min_hits:
            break
        results.append(entry)
        if len(results) >= 12:
            break
    return results


def _resolve_cwd_for_query(q: str) -> str | None:
    """Try to find a project CWD that matches a keyword in the query."""
    import os
    home = Path.home()
    projects_root = home / "Documents" / "PROJECTS"
    if not projects_root.exists():
        return None
    q_lower = q.lower()
    best = None
    for category in projects_root.iterdir():
        if not category.is_dir():
            continue
        for project in category.iterdir():
            if not project.is_dir():
                continue
            name = project.name.lower()
            if any(tok in name for tok in q_lower.split() if len(tok) > 3):
                best = str(project)
                break
        if best:
            break
    return best


_FIND_VERBS = {"find", "open", "show", "look", "search", "get", "where", "check"}
_START_VERBS = {"start", "launch", "new", "create", "begin", "run", "dispatch", "resume"}


def _fast_path(text: str) -> dict | None:
    """Handle common intents without going through the AI concierge.

    Returns a structured result dict if we can answer directly, else None.
    Handles:
    - "find/show/open X" → search sessions, return results with resume actions
    - "start/launch X" → search first; if no match offer dispatch; if match offer resume
    - Anything with 2+ words that looks like a session search
    """
    import re as _re
    t = text.strip().lower()
    words = t.split()
    if not words:
        return None

    meaningful = [w for w in words if len(w) >= 2 and w not in (
        "the", "a", "an", "for", "with", "and", "or", "in", "on", "at", "to", "of", "my",
        "me", "is", "it", "its", "this", "that", "can", "you", "all", "any",
        "working", "looking", "about", "some", "more",
    )]
    if len(meaningful) < 1:
        return None

    first_word = words[0]
    is_find = first_word in _FIND_VERBS or any(w in _FIND_VERBS for w in words[:2])
    is_start = first_word in _START_VERBS

    if first_word in _FIND_VERBS | _START_VERBS:
        rest = words[1:]
        # Strip filler that sometimes follows verbs: "working on", "looking for", etc.
        filler = {"working", "on", "looking", "for", "a", "the", "me", "my", "some"}
        while rest and rest[0] in filler:
            rest = rest[1:]
        keywords = " ".join(rest)
    else:
        keywords = text.strip()

    if not keywords or len(keywords) < 3:
        return None

    matches = _search_sessions_fast(keywords)

    if is_start and not matches:
        # Nothing found — offer to dispatch
        cwd = _resolve_cwd_for_query(keywords) or str(Path.home())
        cwd_short = cwd.replace(str(Path.home()), "~")
        kw = keywords[:50]
        return {
            "type": "results",
            "title": f"No sessions found for \u201c{kw}\u201d",
            "narrative": f"No existing sessions match. Start a new one in {cwd_short}?",
            "items": [],
            "actions": [
                {
                    "label": f"Start: {kw}",
                    "action": "dispatch",
                    "cwd": cwd,
                    "task": keywords,
                }
            ],
        }

    if not matches:
        return None  # let AI handle truly ambiguous queries

    # Build items + actions
    items = []
    actions = []
    for s in matches[:8]:
        items.append({
            "id": s["id"],
            "title": s["title"],
            "cwd": s["cwd_short"],
            "status": s["status"],
            "updated_at": s["updated_at"],
        })
        label = s["title"][:40] if len(s["title"]) > 40 else s["title"]
        verb = "Open" if s["status"] == "active" else "Resume"
        actions.append({
            "label": f"{verb}: {label}",
            "action": "resume",
            "session_id": s["id"],
        })

    if is_start and matches:
        # Start was requested but sessions exist — also offer fresh dispatch
        cwd = matches[0]["cwd"] or _resolve_cwd_for_query(keywords) or str(Path.home())
        cwd_short = cwd.replace(str(Path.home()), "~")
        kw = keywords[:40]
        actions.append({
            "label": f"New session: {kw}",
            "action": "dispatch",
            "cwd": cwd,
            "task": keywords,
        })

    n = len(matches)
    noun = "session" if n == 1 else "sessions"
    kw = keywords
    return {
        "type": "results",
        "title": f"{n} {noun} matching \u201c{kw}\u201d",
        "narrative": None,
        "items": items,
        "actions": actions,
    }



    """Make sure the concierge is running. Spawns if needed."""
    global _session_id
    with _lock:
        if is_alive():
            if not _session_id:
                _session_id = _find_session_id()
            return True
        ok = _spawn()
        if ok:
            # Give kiro-cli time to write its session file
            time.sleep(3)
            _session_id = _find_session_id()
        return ok


def _has_tool_results_after(session_id: str, after_pos: int) -> bool:
    """Return True if a ToolResults entry exists after after_pos."""
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
    try:
        with open(jsonl_path, "rb") as f:
            f.seek(after_pos)
            content = f.read().decode("utf-8", errors="replace")
        for line in content.strip().split("\n"):
            try:
                if json.loads(line).get("kind") == "ToolResults":
                    return True
            except Exception:
                continue
    except OSError:
        pass
    return False


def _has_final_after_tools(session_id: str, after_pos: int) -> bool:
    """Return True if there's an AssistantMessage with text AFTER a ToolResults entry."""
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
    try:
        with open(jsonl_path, "rb") as f:
            f.seek(after_pos)
            content = f.read().decode("utf-8", errors="replace")
        saw_tool_results = False
        for line in content.strip().split("\n"):
            try:
                entry = json.loads(line)
                kind = entry.get("kind")
                if kind == "ToolResults":
                    saw_tool_results = True
                elif kind == "AssistantMessage" and saw_tool_results:
                    for block in entry.get("data", {}).get("content", []):
                        if isinstance(block, dict) and block.get("kind") == "text" and block.get("data", "").strip():
                            return True
            except Exception:
                continue
    except OSError:
        pass
    return False


def query(text: str) -> dict:
    """Send a query to the concierge and return the structured response.

    Tries the fast-path resolver first (direct session search, no AI round-trip).
    Falls through to the AI concierge only for ambiguous or report-style queries.
    Tracks file position to only read NEW responses after the query is sent.
    """
    # Fast path: session search / dispatch intents resolved without AI
    fast = _fast_path(text)
    if fast is not None:
        return fast

    if not ensure_alive():
        return {
            "type": "error",
            "title": "Concierge unavailable",
            "narrative": "Could not start the assistant session. Is kiro-cli installed?",
            "items": [],
            "actions": [],
        }

    global _session_id
    
    # Always try to resolve session_id if we don't have it
    if not _session_id:
        _session_id = _find_session_id()
    
    session_id = _session_id
    if not session_id:
        return {
            "type": "error",
            "title": "Session not found",
            "narrative": "Concierge is running but session ID could not be determined.",
            "items": [],
            "actions": [],
        }
    
    # Record file position BEFORE sending — we only want responses after this
    baseline_pos = _jsonl_size(session_id)
    
    # Clear previous conversation context (doesn't affect file, just memory)
    _send("/clear")
    time.sleep(0.5)

    # Send the query
    if not _send(text):
        return {
            "type": "error",
            "title": "Failed to send query",
            "narrative": "The concierge session is not accepting input.",
            "items": [],
            "actions": [],
        }

    # Wait for a new assistant message to appear AFTER our query
    time.sleep(INITIAL_DELAY)
    deadline = time.time() + RESPONSE_TIMEOUT
    last_output_len = 0

    while time.time() < deadline:
        current_size = _jsonl_size(session_id)
        if current_size > baseline_pos:
            # New content written — read only the new part
            output = _get_last_assistant_output(session_id, after_pos=baseline_pos)
            if output:
                # Quick check: if we already have valid JSON, return immediately
                parsed = _extract_json(output)
                if parsed and parsed.get("type") in ("results", "report", "action", "error"):
                    return parsed

                # Check if output is still growing (streaming)
                if len(output) == last_output_len and last_output_len > 0:
                    # Output stabilized — but only return if there are no
                    # pending tool results (i.e. a ToolResults entry exists
                    # and is followed by a final AssistantMessage, not just a
                    # preamble before tool use).
                    has_tool_results = _has_tool_results_after(session_id, baseline_pos)
                    final_after_tools = _has_final_after_tools(session_id, baseline_pos)
                    if has_tool_results and not final_after_tools:
                        # Still waiting for the post-tool final response
                        last_output_len = len(output)  # reset so we keep waiting
                        time.sleep(POLL_INTERVAL)
                        continue
                    # Try to parse one more time
                    parsed = _extract_json(output)
                    if parsed:
                        return parsed
                    # Stable output with no JSON — return as plain report
                    return {
                        "type": "report",
                        "title": "Assistant response",
                        "narrative": output[:2000],
                        "items": [],
                        "actions": [],
                    }
                last_output_len = len(output)

        time.sleep(POLL_INTERVAL)

    return {
        "type": "error",
        "title": "Timeout",
        "narrative": f"No response within {RESPONSE_TIMEOUT:.0f}s. The assistant may still be working.",
        "items": [],
        "actions": [],
    }


def status() -> dict:
    """Return the concierge's current status."""
    alive = is_alive()
    return {
        "alive": alive,
        "session_id": _session_id if alive else None,
        "tmux_session": _tmux_name(),
        # Reported so the settings page shows what is running rather than what
        # was picked. A change only takes effect on the next spawn, and a picker
        # that cannot show the difference is how the old one came to lie.
        "model": configured_model(),
        "running_model": _running_model if alive else None,
    }


def kill() -> dict:
    """Kill the concierge session."""
    global _session_id
    name = _tmux_name()
    if tmux.session_exists(name):
        tmux._tmux("kill-session", "-t", name, check=False)
    _session_id = None
    return {"ok": True}
