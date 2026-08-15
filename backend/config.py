"""Configuration for Quarterdeck."""
import os
from pathlib import Path

# --- ports ---
# The installed app and a dev checkout must not compete for one port. They did,
# and the failure was quiet in the worst way: the dev backend lost the bind and
# died, while the dev window attached to whatever was already listening. You
# then read the installed app's state through a window you thought was yours,
# and edited code that was not running.
#
# So dev gets its own port. `DECK_PORT` overrides both, for the case this does
# not anticipate.
DEFAULT_PORT = 19418      # installed app, and the address phones have bookmarked
DEV_PORT = 19419          # start.sh, and app.py under DECK_DEV
VITE_PORT = 5173

# The remote listener stays on DEFAULT_PORT whoever starts it. It binds the
# Tailscale address rather than loopback, so it does not collide with a local
# backend on the same number, and a phone's saved URL keeps working when the
# machine happens to be running a dev checkout.
REMOTE_PORT = DEFAULT_PORT


def _port() -> int:
    override = os.environ.get("DECK_PORT")
    if override and override.isdigit():
        return int(override)
    return DEV_PORT if os.environ.get("DECK_DEV") else DEFAULT_PORT


PORT = _port()

SESSIONS_DIR = Path.home() / ".kiro" / "sessions" / "cli"
CREW_SESSIONS_DIR = Path.home() / ".kiro" / "crew" / "sessions"

# Sessions whose cwd starts with any of these prefixes are hidden from the
# sidebar. KiroCrew spawns background agents (research workers, cron runs, _bg)
# as kiro-cli processes, which create session files here with no title and a
# kirocrew workspace path. They are infrastructure, not user work.
HIDDEN_CWD_PREFIXES: tuple[str, ...] = (
    str(Path.home() / "workplace" / "kirocrew-workspace"),
    str(Path.home() / ".kiro" / "crew" / "workspace"),
    str(Path.home() / ".kirocrew" / "workspace"),
)
POLL_INTERVAL = 2.0  # seconds between status checks
RECENT_SESSIONS_LIMIT = 20  # max sessions to show in grid
TAIL_LINES = 50  # lines to read from end of .jsonl for status detection

# --- tmux-backed session management ---
STATE_DIR = Path.home() / ".osa-kiro"
MANAGED_FILE = STATE_DIR / "managed.json"  # machine-local, never committed
# Where an agentSpawn hook drops "<nonce> contains <session id>". A file rather
# than an HTTP callback: no port to bake into the hook command, nothing to break
# when the backend restarts on a different port, and no dependency on curl.
SPAWNS_DIR = STATE_DIR / "spawns"
SPAWN_HOOK_TTL = 3600.0  # seconds before an unclaimed hook drop is swept away
# Where a `stop` hook records that a session finished answering. Unlike the
# spawn drop this is keyed by session id, and is written for every session using
# a hooked agent — including ones started by hand, which is the point: those have
# no pane to read, so their idle-vs-thinking was previously a 10s guess.
TURNS_DIR = STATE_DIR / "turns"
TURN_MARK_TTL = 14 * 24 * 3600.0  # a fortnight; these are tiny and worth keeping
# Where a `preToolUse` hook drops a pending approval request. The file name is
# <session_id>-<request_id>. The hook blocks until the file gains a "." prefix
# (allow) or a "!" prefix (deny).
APPROVALS_DIR = STATE_DIR / "approvals"
APPROVAL_TIMEOUT = 120.0  # seconds before an unanswered hook auto-denies
# Per-session opt-in for that hook. The hook itself is installed into every agent
# config alongside the other two, but it returns immediately unless a file named
# for the session id (or, before correlation, `n-<nonce>`) exists here. Gating
# every tool call is a per-session decision: installed globally and always on, it
# would put an approval banner in front of sessions that never asked for one, and
# block the first tool call of every session anyone started by hand.
GATES_DIR = STATE_DIR / "gates"

# Per-session ownership sidecars. Each file is a JSON object with owner, role,
# group_id, handoverable, visible. Written before a session's first prompt;
# never read from ~/.kiro/sessions/cli/ (doctrine 1).
OWNERS_DIR = STATE_DIR / "owners"

# Steering delivery records. Date-partitioned JSONL under YYYY-MM-DD.jsonl.
# One record per session per scan: which steering files were expected to arrive,
# and any probe token observations written by the echo test.
DELIVERY_DIR = STATE_DIR / "delivery"

# Correction records. Date-partitioned JSONL. One record per correction press;
# status updates are appended (never rewritten). Only confirmed corrections count.
CORRECTIONS_DIR = STATE_DIR / "corrections"

# Per-session task queues. Each file is a JSON array of {id, text, added_at}.
STACKS_DIR = STATE_DIR / "stacks"
SLASH_QUEUES_DIR = STATE_DIR / "slash-queues"
SUMMARIES_DIR = STATE_DIR / "summaries"
PASTES_DIR = STATE_DIR / "pastes"
PASTE_MIN_CHARS = 1200   # threshold to trigger attachment-tile collapse
PASTE_MIN_LINES = 20     # either condition is sufficient
PASTE_RETENTION_DAYS = 30  # default sweep age
# How long to wait for the hook's answer once the process-tree walk already has
# one. Measured on kiro-cli 2.14.2: the agentSpawn hook fires ~0.25s after the
# .lock file the walk keys on, so the walk always wins the race and the hook is
# a check on its answer rather than a replacement for it. Only spent when the
# agent being spawned actually carries the hook.
SPAWN_HOOK_GRACE = 1.5
KIRO_CLI = "kiro-cli"
TMUX_PREFIX = "kiro-"  # tmux session name for a resolved kiro session
PENDING_PREFIX = "osa-pending-"  # tmux session name before the id is known
SPAWN_TIMEOUT = 45.0  # seconds to wait for kiro-cli to report its session id.
# Generous on purpose: correlation runs on a background thread, so waiting costs
# nothing, and a shell prelude (login shell + nvm/venv) can push startup past 15s.
SPAWN_POLL = 0.25  # seconds between correlation polls
DEFAULT_TRUST_TOOLS = "fs_read"  # read-only tools pre-trusted; writes/exec still prompt
CAPTURE_LINES = 40  # pane lines returned by capture() when no size is asked for
# Ceiling on what a client may ask capture-pane for. tmux keeps a bounded
# scrollback anyway, so a larger number buys nothing but a bigger response.
MAX_CAPTURE_LINES = 5000

# Geometry a detached session starts with. tmux would otherwise default to
# 80x24, which the TUI wraps badly and which makes a maximised view mostly dead
# space. The frontend measures its own pane and resizes to match, so this is
# only what you get before it has had a chance to.
DEFAULT_COLS = 120
DEFAULT_ROWS = 36
# Clamps for the resize endpoint: it takes numbers from a browser, and tmux
# accepts absurd geometry quite happily.
MIN_COLS, MAX_COLS = 20, 500
MIN_ROWS, MAX_ROWS = 6, 300

# Fallback only. The real list comes from `kiro-cli chat --list-models`, which
# knows what this subscription is actually entitled to — see available_models().
# A hardcoded list drifts silently: this one still offered claude-opus-4.6 after
# it stopped being available, so the dropdown advertised a model that fails at
# spawn. Kept for the case where kiro-cli cannot be reached at all, since an
# empty model list is worse than a slightly wrong one.
MODELS = (
    "auto", "claude-sonnet-4.6", "claude-opus-4.5",
    "claude-sonnet-4.5", "claude-sonnet-4", "claude-haiku-4.5",
    "minimax-m2.5", "minimax-m2.1", "qwen3-coder-next",
)
EFFORTS = ("low", "medium", "high", "xhigh", "max")

# Shared preferences, as opposed to the per-device ones the window keeps in
# localStorage. Defined here rather than in api.py so that concierge.py can read
# its own model without importing the API module back.
#
# It lives under ~/.osa-kiro with the rest of Quarterdeck's state, and emphatically not
# next to the code: `Path(__file__).parent.parent` is the repo root in a checkout
# but *inside the .app bundle* once PyInstaller has packaged it. Settings were
# therefore written inside the app bundle, which a
# reinstall overwrites and a signed or read-only bundle refuses outright — the
# reported "settings do not survive a restart".
SETTINGS_FILE = STATE_DIR / "settings.json"
CLIENT_PREFS_FILE = STATE_DIR / "client-prefs.json"  # UI state that must survive WKWebView restarts
# Snapshots and favourites live beside it, for the same reason and it is worth
# stating plainly: `Path(__file__).parent.parent` is the repo root in a checkout
# and `Quarterdeck.app/Contents/Frameworks` once PyInstaller has packaged it. So
# these were user data stored inside the application — a reinstall replaced the
# bundle and took them with it, and a signed or read-only bundle refuses the
# write outright. `settings.json` was moved here for exactly that reason; these
# two were left behind, and an install did lose them: a stale `snapshots.json`
# from an older build was the only file that survived a bundle replacement.
SNAPSHOTS_FILE = STATE_DIR / "snapshots.json"
FAVOURITES_FILE = STATE_DIR / "favourites.json"
COLLECTIONS_FILE = STATE_DIR / "collections.json"

# Where they used to live. Read once, if the new file does not exist yet, so a
# checkout that already had preferences keeps them.
LEGACY_SETTINGS_FILE = Path(__file__).parent.parent / "settings.json"
LEGACY_SNAPSHOTS_FILE = Path(__file__).parent.parent / "snapshots.json"
LEGACY_FAVOURITES_FILE = Path(__file__).parent.parent / "favourites.json"

def _migrations() -> tuple[tuple[Path, Path], ...]:
    """(current, legacy) for everything that moved out of the app directory.

    Read from the module at call time rather than frozen into a constant, so a
    test can point the paths at a temp directory. A module-level tuple captures
    the real `~/.osa-kiro` and quietly ignores the patch.
    """
    return (
        (SETTINGS_FILE, LEGACY_SETTINGS_FILE),
        (SNAPSHOTS_FILE, LEGACY_SNAPSHOTS_FILE),
        (FAVOURITES_FILE, LEGACY_FAVOURITES_FILE),
    )


def migrate_settings() -> list[str]:
    """Copy pre-move state into the state directory. Names what moved.

    Copies rather than moves: if this is wrong, the original is still there. The
    old file is left in place deliberately — it is also what a checkout has in
    git history, and deleting a user's data to tidy up is not this function's
    business.
    """
    import shutil
    moved = []
    for current, legacy in _migrations():
        if current.exists() or not legacy.is_file():
            continue
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(legacy, current)
        except OSError:
            continue
        moved.append(current.name)
    return moved


def read_settings() -> dict:
    """Shared settings, or {} if the file is absent or unreadable."""
    import json
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


MODELS_TTL = 600.0  # entitlements change rarely; a spawn should not wait on this
_models_cache: tuple[float, tuple[str, ...]] | None = None


def available_models(force: bool = False) -> tuple[str, ...]:
    """Models this kiro-cli install actually offers, newest answer cached.

    Asks kiro-cli rather than trusting MODELS above. Falls back to MODELS if the
    binary is missing, times out, or answers with something unrecognisable —
    callers need a list to render and to validate against, and refusing to
    produce one would break dispatch rather than degrade it.
    """
    global _models_cache
    import json
    import subprocess
    import time

    if not force and _models_cache and time.time() - _models_cache[0] < MODELS_TTL:
        return _models_cache[1]

    names: tuple[str, ...] = ()
    try:
        r = subprocess.run([KIRO_CLI, "chat", "--list-models", "-f", "json"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            payload = json.loads(r.stdout)
            names = tuple(
                m["model_name"] for m in payload.get("models", [])
                if isinstance(m, dict) and isinstance(m.get("model_name"), str)
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError,
            ValueError, TypeError, OSError):
        names = ()

    resolved = names or MODELS
    _models_cache = (time.time(), resolved)
    return resolved

# Where `kiro-cli agent list` looks for agent configs, and where its own
# preferences live. Agents are discovered rather than hardcoded: they are the
# user's files, they change without us, and a stale list would offer a `--agent`
# that fails at spawn.
AGENTS_DIR = Path.home() / ".kiro" / "agents"
WORKSPACE_AGENTS_SUBDIR = Path(".kiro") / "agents"
KIRO_CLI_SETTINGS = Path.home() / ".kiro" / "settings" / "cli.json"
DEFAULT_AGENT_KEY = "chat.defaultAgent"
# Shipped with kiro-cli, so they have no config file on disk. Listed because they
# are selectable; flagged because a built-in cannot carry hooks, which matters
# for the hook-based correlation in docs/ROADMAP.md section 4.
BUILTIN_AGENTS = ("kiro_default", "kiro_help", "kiro_planner")

# Terminals a managed session can be handed off to. Terminal.app and iTerm
# expose AppleScript hooks for running a command; Ghostty takes `-e` on the
# command line. Warp has neither, so it only gets an open-at-directory tab plus
# the command on the clipboard — driving it by simulated keystrokes is precisely
# what this branch removed.
TERMINALS = {
    "terminal": {"label": "Terminal.app", "runs_command": True},
    "iterm": {"label": "iTerm2", "runs_command": True},
    "ghostty": {"label": "Ghostty", "runs_command": True},
    "warp": {"label": "Warp (paste needed)", "runs_command": False},
}

# Slash commands offered as one-click chips in the session composer. `needs_arg`
# means the command is inserted for the user to complete rather than sent.
QUICK_COMMANDS = (
    {"cmd": "/goal ", "label": "goal", "needs_arg": True,
     "hint": "Set a goal with validation criteria for iterative completion"},
    {"cmd": "/compact", "label": "compact", "needs_arg": False,
     "hint": "Compact conversation history"},
    {"cmd": "/context show", "label": "context", "needs_arg": False,
     "hint": "Show context files and token usage"},
    {"cmd": "/plan", "label": "plan", "needs_arg": False,
     "hint": "Switch to the Plan agent"},
    {"cmd": "/tools", "label": "tools", "needs_arg": False,
     "hint": "Show available tools"},
    {"cmd": "/usage", "label": "usage", "needs_arg": False,
     "hint": "Show billing and usage information"},
    {"cmd": "/clear", "label": "clear", "needs_arg": False,
     "hint": "Clear conversation history"},
)


def ensure_tool_path() -> list[str]:
    """Add the usual Homebrew/user bin directories to PATH if they are missing.

    A .app launched from Finder inherits a minimal PATH (/usr/bin:/bin:...),
    not the shell's. Without this, `tmux` in /opt/homebrew/bin and `kiro-cli` in
    ~/.local/bin are invisible to the packaged app and every session action
    fails with "not installed". Returns the directories that were added.
    """
    import os

    candidates = [
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/local/bin",
        str(Path.home() / ".local" / "bin"),
    ]
    current = os.environ.get("PATH", "").split(os.pathsep)
    added = [d for d in candidates if d not in current and Path(d).is_dir()]
    if added:
        os.environ["PATH"] = os.pathsep.join([*current, *added])
    return added
