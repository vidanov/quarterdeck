"""Quarterdeck — Agent Control Surface API."""
import json
import os
import re
import shlex
import signal
import sys
import threading
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response as FastAPIResponse, StreamingResponse

from . import acp_observer
from . import audit
from . import auth
from . import concierge
from . import side_chat
from . import corrections
from . import delivery
from . import duration
from . import ownership
from . import pastes as paste_store
from . import screenshots
from . import shell
from . import tmux_manager as tmux
from . import v3 as v3mod
from .config import (
    AGENTS_DIR, BUILTIN_AGENTS, CAPTURE_LINES, DEFAULT_AGENT_KEY, CORRECTIONS_DIR, DELIVERY_DIR, EFFORTS,
    GATES_DIR, HIDDEN_CWD_PREFIXES, KIRO_CLI_SETTINGS, MAX_CAPTURE_LINES, OWNERS_DIR, PORT, QUICK_COMMANDS,
    PASTES_DIR, PASTE_MIN_CHARS, PASTE_MIN_LINES, PASTE_RETENTION_DAYS,
    RECENT_SESSIONS_LIMIT, REMOTE_PORT, STATE_DIR, STACKS_DIR,
    SESSIONS_DIR, CREW_SESSIONS_DIR, TAIL_LINES, TERMINALS, VITE_PORT, WORKSPACE_AGENTS_SUBDIR,
    SETTINGS_FILE, SNAPSHOTS_FILE, FAVOURITES_FILE, SUMMARIES_DIR, SLASH_QUEUES_DIR,
    available_models, ensure_tool_path, migrate_settings,
)


def _install_claim_hook_script() -> None:
    """Copy scripts/verify-claim.sh into ~/.osa-kiro/hooks/ so the stop hook can find it.

    The stop hook runs from the agent's shell and cannot assume the repo is on PATH.
    Rewritten on every startup so the logic stays in sync with this version of the app.
    """
    import shutil
    hooks_dir = STATE_DIR / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_dir / "verify-claim.sh"
    # Find source: next to this file's repo root (dev mode) or bundled in Resources
    candidates = [
        Path(__file__).parent.parent / "scripts" / "verify-claim.sh",  # dev: repo root / scripts/
        Path(__file__).parent / ".." / "scripts" / "verify-claim.sh",  # bundled: backend/../scripts/
    ]
    for src in candidates:
        if src.exists():
            try:
                body = src.read_text()
                if dest.exists() and dest.read_text() == body:
                    return  # already current
                tmp = dest.with_suffix(".sh.tmp")
                tmp.write_text(body)
                tmp.chmod(0o755)
                tmp.replace(dest)
            except OSError:
                pass
            return


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Re-adopt our own tmux sessions on startup, so a restart is survivable."""
    added = ensure_tool_path()
    if added:
        print(f"[deck] PATH extended for bundled launch: {', '.join(added)}")
    moved = migrate_settings()
    if moved:
        print(f"[deck] moved out of the app directory into ~/.osa-kiro: {', '.join(moved)}")
    # The audit hook's script is rewritten on every start, so the logic cannot
    # drift from this source, and the flag file is pointed at the setting — the
    # shell hook reads the file because it cannot afford to parse JSON per tool
    # call. Both are cheap and idempotent.
    audit.write_hook_script()
    audit.sync_flag()
    audit.sweep()
    ownership.init(OWNERS_DIR)
    delivery.init(DELIVERY_DIR, AGENTS_DIR)
    corrections.init(CORRECTIONS_DIR)
    # Tighten profile credential files to owner-only on every startup.
    # These contain live OAuth refresh tokens; the umask on older saves may
    # have left them world-readable (security defect D2).
    try:
        for _pf in _PROFILES_DIR.glob("*.jsonl"):
            try:
                _pf.chmod(0o600)
            except OSError:
                pass
    except Exception:
        pass
    # Install the verify-claim stop-hook script into ~/.osa-kiro/hooks/ so
    # the stop hook can call it without knowing the repo location.
    _install_claim_hook_script()
    if tmux.tmux_available():
        result = tmux.reconcile()
        if any(result.values()):
            print(f"[deck] tmux reconcile: {result}")
    else:
        print("[deck] tmux not found — monitoring only, session control disabled")

    # Auto-advance: when a session's stop hook fires and it has auto-advance
    # enabled, pop the next stack item and send it. Runs in a daemon thread so
    # it dies with the process and never blocks startup.
    stop_thread = threading.Thread(target=_auto_advance_loop, daemon=True)
    stop_thread.start()

    # Start screenshot watcher if a folder is configured
    if screenshots.configured_path():
        r = screenshots.start()
        if r.get("ok"):
            print(f"[deck] screenshots watcher started: {r.get('watching')}")

    # Auto-restart remote serving if it was running before (flag set by remote/start).
    # This ensures remote access survives an app restart without needing a LaunchAgent
    # that can't run uvicorn in the bundled environment.
    settings = _load_settings()
    if settings.get("remote-autostart"):
        ts_ip = _tailscale_ip()
        if ts_ip:
            global _remote_thread
            _remote_thread = threading.Thread(
                target=_start_proxy, args=(ts_ip,), daemon=True, name="remote-proxy")
            _remote_thread.start()
            print(f"[deck] remote proxy auto-started on {ts_ip}:{REMOTE_PORT}")

    # Warm the projects cache in background so the first UI hit doesn't block ~37s.
    # Use a named function to avoid a lambda capturing the wrong scope.
    def _warm_projects_cache():
        try:
            get_projects()
        except Exception:
            pass
    threading.Thread(target=_warm_projects_cache, daemon=True, name="projects-warmup").start()

    yield

    # Shutdown: stop all ACP observer subprocesses cleanly.
    acp_observer.detach_all()


def _auto_advance_loop():
    """Poll turn marks and send the next stack item for auto-advance sessions."""
    # Track the last mtime we saw per session so we only fire on new stop events.
    last_seen: dict[str, float] = {}
    # In-flight guard: don't start a second summary thread while one is running.
    summarising: set[str] = set()
    # Pre-populate last_seen with current turn files so existing sessions don't
    # all trigger summary generation simultaneously on startup.
    turns_dir = tmux.TURNS_DIR
    if turns_dir.is_dir():
        for tf in turns_dir.iterdir():
            if tf.is_file():
                last_seen[tf.stem] = tf.stat().st_mtime
    # The loop must survive a bad iteration, but swallowing silently is how this
    # ran dead for a whole branch: a NameError every tick, nothing on stderr, and
    # a feature that simply never fired. Report each distinct fault once — loud
    # enough to notice, quiet enough not to print every two seconds forever.
    reported: set[str] = set()
    while True:
        try:
            _check_auto_advance(last_seen, summarising)
        except Exception as exc:
            key = f"{type(exc).__name__}: {exc}"
            if key not in reported:
                reported.add(key)
                print(f"[deck] auto-advance failed: {key}", file=sys.stderr)
        time.sleep(2)


def _sq_send_delayed(session_id: str, text: str, delay: float = 3.5) -> None:
    """Send a slash-queue item after a short delay in a background thread.

    kiro-cli needs a moment to redraw the TUI after finishing a turn before it
    can accept a new command. Sending immediately drops the input silently.

    For V3 sessions with an ACP observer, slash commands are routed through
    _kiro.dev/commands/execute (no timing dependency). Falls back to tmux.
    """
    def _do():
        # Task 5: ACP path for slash commands on observed sessions.
        if text.startswith("/") and acp_observer.is_attached(session_id):
            try:
                if acp_observer.execute_command(session_id, text):
                    return
            except Exception:
                pass  # fall back to tmux below
        time.sleep(delay)
        tmux.send_text(session_id, text)
    threading.Thread(target=_do, daemon=True).start()


def _check_auto_advance(last_seen: dict, summarising: set | None = None):
    turns_dir = tmux.TURNS_DIR
    if not turns_dir.is_dir():
        return
    settings = _load_settings()
    # auto-advance is per-session, stored as "stack-auto:<session_id>" in settings
    for turn_file in turns_dir.iterdir():
        if not turn_file.is_file():
            continue
        session_id = turn_file.stem
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", session_id):
            continue
        mtime = turn_file.stat().st_mtime
        if last_seen.get(session_id, 0) >= mtime:
            continue
        last_seen[session_id] = mtime
        # Record duration data for this turn — additive, never raises.
        # Only fires when the turn file itself is the stop signal (not a .json record
        # written by this module, which passes the fullmatch guard above).
        turn_file_size = 0
        try:
            turn_file_size = turn_file.stat().st_size
        except OSError:
            pass
        if turn_file_size < 4096:
            # Bare stop marks are tiny; skip writing over a real duration record.
            threading.Thread(
                target=duration.write_record, args=(session_id,), daemon=True
            ).start()
        # Trigger a summary when a NEW stop event fires for a managed session.
        # concierge-enabled defaults to False — it's heavyweight (starts kiro-cli).
        # In-flight guard prevents spawning a second thread before the first finishes.
        # Regenerate if the session has new entries since the last summary.
        # Only summarise sessions that ended in idle (waiting for user) — not errors.
        lock_data_s = read_lock(session_id)
        current_status = detect_status(session_id, lock_data_s) if lock_data_s else "done"
        if (not settings.get("auto_summary_disabled", False)
                and tmux.is_managed(session_id)
                and current_status in ("idle", "done")
                and (summarising is None or session_id not in summarising)):
            existing = _read_summary(session_id)
            # Get current jsonl line count as a cheap proxy for "new content"
            jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
            try:
                current_seq = sum(1 for _ in open(jsonl_path)) if jsonl_path.exists() else 0
            except Exception:
                current_seq = 0
            cached_seq = (existing or {}).get("last_seq", -1)
            needs_summary = not existing or (current_seq > cached_seq + 2)
            if needs_summary:
                if summarising is not None:
                    summarising.add(session_id)
                def _run_summary(sid=session_id, seq=current_seq):
                    try:
                        _generate_summary_async(sid, last_seq=seq)
                    finally:
                        if summarising is not None:
                            summarising.discard(sid)
                threading.Thread(target=_run_summary, daemon=True).start()
        # Only advance if this session has auto-advance on
        if not settings.get(f"stack-auto:{session_id}"):
            # Slash queue drains unconditionally — no opt-in needed.
            # Does NOT require is_managed: the stop hook fires for any session
            # that installed it, not just Quarterdeck-spawned ones.
            # Reuse lock_data_s / current_status already fetched above.
            if (lock_data_s and is_process_alive(lock_data_s.get("pid", 0))
                    and current_status not in ("awaiting-approval", "thinking")
                    and tmux.session_exists(tmux.tmux_name(session_id))):
                sq_item = sq_pop(session_id)
                if sq_item:
                    _sq_send_delayed(session_id, sq_item["text"])
            continue
        # Safety: do not send into awaiting-approval
        lock_data = read_lock(session_id)
        if not lock_data or not is_process_alive(lock_data.get("pid", 0)):
            continue
        status = detect_status(session_id, lock_data, tmux.capture(session_id))
        if status in ("awaiting-approval", "thinking"):
            continue
        if not tmux.is_managed(session_id):
            continue
        # Slash queue: drain one item per turn, no opt-in needed.
        # Checked before the task stack so slash commands (e.g. /compact) run
        # first and the agent is ready before the next task fires.
        sq_item = sq_pop(session_id)
        if sq_item:
            _sq_send_delayed(session_id, sq_item["text"])
            continue  # one item per turn; let it complete before sending the next
        item = tmux.stack_pop(session_id)
        if item:
            tmux.send_text(session_id, item["text"])

    # Second pass: drain slash queues for sessions that are already idle but
    # whose queue items were added AFTER the last stop-hook fire.  The turn-file
    # mtime gate above only fires when mtime changes; a command queued during an
    # idle stretch would wait forever.  This pass has its own cooldown dict so
    # it only drains once per 3s per session (avoids busy-sending on every tick).
    if not SLASH_QUEUES_DIR.is_dir():
        return
    for sq_file in SLASH_QUEUES_DIR.iterdir():
        if not sq_file.is_file() or sq_file.suffix != ".json":
            continue
        session_id = sq_file.stem
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", session_id):
            continue
        # Quick size check — skip immediately if file is empty or "[]"
        if sq_file.stat().st_size <= 2:
            continue
        # Cooldown: don't re-drain the same session more than once per 3s
        now = time.time()
        if now - last_seen.get(f"sq:{session_id}", 0) < 3:
            continue
        lock_data_i = read_lock(session_id)
        if not lock_data_i or not is_process_alive(lock_data_i.get("pid", 0)):
            continue
        if not tmux.session_exists(tmux.tmux_name(session_id)):
            continue
        status_i = detect_status(session_id, lock_data_i)
        if status_i in ("awaiting-approval", "thinking", "running"):
            continue
        # Session is idle — drain one item
        sq_item = sq_pop(session_id)
        if sq_item:
            last_seen[f"sq:{session_id}"] = now
            _sq_send_delayed(session_id, sq_item["text"])


app = FastAPI(title="Quarterdeck", lifespan=lifespan)

# Remote clients are served the built frontend from this same origin, so they
# never need CORS. The only cross-origin caller is the Vite dev server, which is
# loopback — so the wildcard buys nothing and is not worth its blast radius.
LOCAL_ORIGINS = [
    f"http://{host}:{port}"
    for host in ("127.0.0.1", "localhost")
    for port in (VITE_PORT, PORT)
]
app.add_middleware(CORSMiddleware, allow_origins=LOCAL_ORIGINS,
                   allow_methods=["*"], allow_headers=["*"])

auth.install(app)
# After auth, so it sits outside it: a request refused for want of a token is
# recorded too. Starlette runs the most recently added middleware first.
audit.install(app)

_start_time = time.time()


# ---------------------------------------------------------------------------
# Build freshness check
# ---------------------------------------------------------------------------

def _build_source_hash_from(root: Path, patterns: list) -> str:
    """SHA-256 over the content of files matching glob patterns under root, truncated to 16 chars."""
    import hashlib
    h = hashlib.sha256()
    files: list[Path] = []
    for p in patterns:
        files += sorted(root.glob(p))
    for f in files:
        try:
            h.update(f.read_bytes())
        except Exception:
            pass
    return h.hexdigest()[:16]


@app.get("/api/health/build")
async def health_build():
    """Compare running build stamp against current source tree.

    Returns stale=true when backend or frontend source has changed since the
    last ./build-app.sh run, or when git HEAD has moved.
    This endpoint is the single self-check an agent should call before claiming
    a Quarterdeck fix is done.
    """
    # 1. Load the stamp written at build time
    stamp_path = Path.home() / ".osa-kiro" / "build-stamp.json"
    # Fallback: stamp bundled alongside this module (present in the .app)
    bundled = Path(__file__).parent / "build-stamp.json"
    stamp: dict = {}
    for p in (stamp_path, bundled):
        if p.exists():
            try:
                stamp = json.loads(p.read_text())
                break
            except Exception:
                pass

    if not stamp:
        return {
            "stale": False,
            "stale_reason": "no build stamp found — run ./build-app.sh to generate one",
            "stamp_missing": True,
            "uptime_s": int(time.time() - _start_time),
        }

    # 2. Determine repo root — try git, fallback to stamp path parent
    try:
        repo_root_str = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True, stderr=subprocess.DEVNULL,
            cwd=str(Path.home()),
        ).strip()
        repo_root = Path(repo_root_str)
        # Verify it looks like the right repo
        if not (repo_root / "backend" / "api.py").exists():
            repo_root = None
    except Exception:
        repo_root = None

    # Fallback: look for repo next to stamp path
    if repo_root is None:
        candidate = stamp_path.parent.parent  # ~/.osa-kiro/.. = ~
        # Try common locations
        for loc in [
            Path.home() / "Documents" / "PROJECTS" / "PERSONAL" / "osa-kiro",
            Path.home() / "osa-kiro",
        ]:
            if (loc / "backend" / "api.py").exists():
                repo_root = loc
                break

    # 3. Get current git HEAD
    try:
        running_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL,
            cwd=str(repo_root) if repo_root else str(Path.home()),
        ).strip()
    except Exception:
        running_sha = stamp.get("git_sha", "")

    # 4. Compare content hashes against stamp (works when no git or dirty tree)
    changed_files: list[str] = []
    stale_reason = ""

    if running_sha and stamp.get("git_sha") and running_sha != stamp["git_sha"]:
        stale_reason = f"git HEAD moved: built at {stamp.get('git_short', stamp['git_sha'][:7])}, now at {running_sha[:7]}"

    if not stale_reason and repo_root:
        stamp_hashes = stamp.get("source_hashes", {})
        current_backend = _build_source_hash_from(repo_root, ["backend/*.py"])
        current_frontend = _build_source_hash_from(repo_root, [
            "frontend/src/**/*.jsx",
            "frontend/src/**/*.js",
            "frontend/src/**/*.css",
        ])
        if current_backend != stamp_hashes.get("backend"):
            stale_reason = "backend source changed since build"
            changed_files = [str(f.relative_to(repo_root))
                             for f in sorted(repo_root.glob("backend/*.py"))]
        elif current_frontend != stamp_hashes.get("frontend_src"):
            stale_reason = "frontend source changed since build"
            changed_files = ["frontend/src/"]

    stale = bool(stale_reason)
    dev_mode = bool(os.environ.get("DECK_DEV"))
    return {
        "built_at":      stamp.get("built_at"),
        "git_sha":       stamp.get("git_sha"),
        "git_short":     stamp.get("git_short"),
        "git_branch":    stamp.get("git_branch"),
        "dirty":         stamp.get("dirty", False),
        "running_sha":   running_sha,
        "stale":         stale,
        "stale_reason":  stale_reason,
        "changed_files": changed_files,
        "uptime_s":      int(time.time() - _start_time),
        "dev_mode":      dev_mode,
        "bundle_mode":   getattr(sys, "frozen", False),
    }


@app.post("/api/build/rebuild")
def build_rebuild():
    """Rebuild the frontend (and restart) to clear a stale-build banner.

    Repo mode (not frozen): runs ``npm run build`` then restarts via
    ``os.execv`` so the new frontend/dist is served immediately.

    Bundle mode (frozen .app): launches ``./build-app.sh --install`` in a
    detached subprocess (it quits the running app itself) and returns
    immediately.

    Streams progress lines terminated by ``__DONE__`` or ``__ERROR__``.
    """
    bundled = getattr(sys, "frozen", False)

    def _stream():
        def _run_step(label, *args, cwd=None):
            yield f"▶ {label}\n"
            proc = subprocess.Popen(
                list(args),
                cwd=str(cwd or _REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in proc.stdout:
                yield line
            proc.wait()
            if proc.returncode != 0:
                yield f"✗ {label} failed (exit {proc.returncode})\n"
                raise RuntimeError(label)
            yield f"✓ {label}\n"

        try:
            if bundled:
                # Bundle mode: run build-app.sh --install and stream its output.
                # The script calls `quit app "Quarterdeck"` which terminates this
                # process, so we stream output until the pipe closes (process exits
                # or kills us). Only emit __DONE__ if the script exits 0 first.
                build_sh = _REPO_ROOT / "build-app.sh"
                if not build_sh.exists():
                    yield "✗ build-app.sh not found\n__ERROR__\n"
                    return
                proc = subprocess.Popen(
                    ["bash", str(build_sh), "--install"],
                    cwd=str(_REPO_ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                for line in proc.stdout:
                    yield line
                proc.wait()
                if proc.returncode == 0:
                    yield "✓ Build complete. App will relaunch shortly.\n"
                    yield "__DONE__\n"
                else:
                    yield f"✗ build-app.sh --install failed (exit {proc.returncode})\n"
                    yield "__ERROR__\n"
            else:
                # Repo mode: rebuild frontend then restart in-place.
                yield from _run_step(
                    "npm run build",
                    "npm", "run", "build",
                    cwd=_REPO_ROOT / "frontend",
                )
                yield "✓ Rebuild complete. Restarting…\n"
                yield "__DONE__\n"

                def _restart():
                    import time as _time
                    _time.sleep(1.0)
                    import os as _os
                    _os.execv(sys.executable, [sys.executable] + sys.argv)
                threading.Thread(target=_restart, daemon=True).start()

        except RuntimeError:
            yield "__ERROR__\n"

    return StreamingResponse(_stream(), media_type="text/plain")


def _atomic_write_json(path: Path, data) -> None:
    """Write JSON beside its destination, then atomically replace it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def require_local(request: Request) -> bool:
    """True when the caller is on this machine.

    Guards endpoints that act on the Mac's own GUI — a Finder window or a native
    dialog. Remotely they are at best meaningless and at worst a way to make the
    machine do something its user never asked for.
    """
    return auth.is_loopback(request)


_proc_tree_cache: dict = {"ts": 0.0, "data": {}}
_PROC_TREE_TTL = 2.0  # seconds — cheap enough to refresh every poll cycle


def _proc_tree() -> dict[int, dict]:
    """Return {pid: {ppid, cmd}} for all processes. Cached for 2s."""
    now = time.time()
    if now - _proc_tree_cache["ts"] < _PROC_TREE_TTL:
        return _proc_tree_cache["data"]
    try:
        r = subprocess.run(
            ["ps", "-eo", "pid,ppid,command"],
            capture_output=True, text=True, timeout=3
        )
        tree: dict[int, dict] = {}
        for line in r.stdout.splitlines()[1:]:
            parts = line.strip().split(None, 2)
            if len(parts) >= 2:
                try:
                    pid, ppid = int(parts[0]), int(parts[1])
                    tree[pid] = {"ppid": ppid, "cmd": parts[2] if len(parts) > 2 else ""}
                except (ValueError, IndexError):
                    pass
        _proc_tree_cache["ts"] = now
        _proc_tree_cache["data"] = tree
        return tree
    except Exception:
        return _proc_tree_cache["data"]


def _find_subagent_count(session_id: str, lock_data: dict | None) -> int:
    """Count kiro-cli sub-agents spawned by this session.

    Sub-agents are kiro-cli chat processes whose ancestor chain passes through
    the bun/tui.js process of the parent session. Each sub-agent has its own
    lock file in SESSIONS_DIR.
    """
    if not lock_data:
        return 0
    parent_lock_pid = lock_data.get("pid", 0)
    if not parent_lock_pid:
        return 0
    try:
        tree = _proc_tree()

        # Walk up from the parent lock pid to find its bun/tui.js process
        parent_bun_pid = None
        p = parent_lock_pid
        for _ in range(6):
            info = tree.get(p)
            if not info:
                break
            cmd = info["cmd"]
            if "bun" in cmd or "tui.js" in cmd:
                parent_bun_pid = p
                break
            p = info["ppid"]

        if not parent_bun_pid:
            return 0

        # Build a set of all pids that are descendants of parent_bun_pid
        def descendants(root: int) -> set[int]:
            result: set[int] = set()
            queue = [root]
            while queue:
                pid = queue.pop()
                for child_pid, info in tree.items():
                    if info["ppid"] == pid and child_pid not in result:
                        result.add(child_pid)
                        queue.append(child_pid)
            return result

        child_pids = descendants(parent_bun_pid)

        # Load all other session lock pids
        count = 0
        for lock_file in SESSIONS_DIR.glob("*.lock"):
            child_sid = lock_file.stem
            if child_sid == session_id:
                continue
            try:
                child_lock = json.loads(lock_file.read_text())
                child_pid = child_lock.get("pid", 0)
                if child_pid and child_pid in child_pids and is_process_alive(child_pid):
                    count += 1
            except Exception:
                continue
        return count
    except Exception:
        return 0


def is_process_alive(pid: int) -> bool:
    """Check if a process is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def read_lock(session_id: str) -> dict | None:
    """Read .lock file if it exists."""
    lock_path = SESSIONS_DIR / f"{session_id}.lock"
    if not lock_path.exists():
        return None
    try:
        data = json.loads(lock_path.read_text())
        return data
    except (json.JSONDecodeError, OSError):
        return None


def read_metadata(session_id: str) -> dict | None:
    """Read session .json metadata (V1 or V3)."""
    # V3: sess_* prefix → workspace-hash directory layout
    if v3mod.is_v3_session(session_id):
        return v3mod.read_metadata(session_id)
    json_path = SESSIONS_DIR / f"{session_id}.json"
    if not json_path.exists():
        return None
    try:
        return json.loads(json_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def tail_jsonl(session_id: str, lines: int = TAIL_LINES) -> list[str]:
    """Read last N lines from .jsonl file."""
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
    if not jsonl_path.exists():
        return []
    try:
        # Fast tail: read from end
        with open(jsonl_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            # Read last 64KB max (enough for tail)
            chunk_size = min(size, 65536)
            f.seek(size - chunk_size)
            content = f.read().decode("utf-8", errors="replace")
            return content.strip().split("\n")[-lines:]
    except OSError:
        return []


def get_last_output(session_id: str) -> str:
    """Get the last assistant message from a session (V1 or V3)."""
    if v3mod.is_v3_session(session_id):
        return v3mod.get_last_output(session_id)
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
    if not jsonl_path.exists():
        return ""
    try:
        with open(jsonl_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk_size = min(size, 262144)
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
                    last_output = combined  # always overwrite — last one wins
            except (json.JSONDecodeError, KeyError):
                continue
        return last_output
    except OSError:
        return ""


# --- Transcript, addressed by line ---
#
# The conversation is an append-only JSONL, so an entry's line index is a stable
# identity for it: nothing is ever rewritten above it. That is what makes "cut
# the session here" expressible as a number, and it is why `seq` is counted from
# the start of the file rather than from the tail — a tail-relative index moves
# every time the agent writes.

# Per-message caps. Generous enough to read, small enough that a 400-turn
# session does not become a megabyte of JSON on the wire.
MESSAGE_TEXT_MAX = 16000
MESSAGE_LIMIT_DEFAULT = 200
MESSAGE_LIMIT_MAX = 2000
# A single jsonl line above this is not parsed. Tool results are one line each
# and can be enormous — 100MB in one real session here — and none of their
# content is returned, so decoding them buys nothing and costs seconds.
MESSAGE_LINE_MAX = 1 << 20
# Bytes of jsonl tail read to find a session's last spoken line. The listing
# calls this for every waiting session on a two-second poll, so it is a tail
# read and a cache, never a full parse.
LAST_MESSAGE_TAIL = 65536
LAST_MESSAGE_MAX = 400

_ROLE_OF_KIND = {
    "Prompt": "user",
    "AssistantMessage": "assistant",
    "ToolResults": "tool",
}


def _content_blocks(entry: dict) -> list:
    data = entry.get("data")
    if not isinstance(data, dict):
        return []
    content = data.get("content")
    return content if isinstance(content, list) else []


def _block_text(entry: dict) -> str:
    """The spoken text of an entry — its `text` blocks, joined."""
    parts = []
    for block in _content_blocks(entry):
        if isinstance(block, dict) and block.get("kind") == "text":
            text = block.get("data")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts)


def _block_tools(entry: dict) -> list[dict]:
    """Tool calls an entry asks for: name and id, never the arguments.

    Arguments can be a whole file's contents, and nothing consuming this needs
    them — the detail panel already renders them from its own pass.
    """
    tools = []
    for block in _content_blocks(entry):
        if not isinstance(block, dict) or block.get("kind") != "toolUse":
            continue
        data = block.get("data")
        if not isinstance(data, dict):
            continue
        tools.append({
            "id": data.get("toolUseId") or "",
            "name": data.get("name") or "tool",
        })
    return tools


def _result_count(entry: dict) -> int:
    return sum(1 for b in _content_blocks(entry)
               if isinstance(b, dict) and b.get("kind") == "toolResult")


def _transcript_entry(seq: int, entry: dict) -> dict:
    """One addressable transcript entry.

    `is_turn` is the field the branch feature needs: a `Prompt` carrying text a
    human typed. Not every `Prompt` is one — kiro-cli also delivers tool results
    inside a `Prompt` entry, and cutting there would cut mid-turn.
    """
    kind = entry.get("kind") or ""
    text = _block_text(entry)
    data = entry.get("data")
    if not isinstance(data, dict):
        data = {}
    meta = data.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    return {
        "seq": seq,
        "kind": kind,
        "role": _ROLE_OF_KIND.get(kind, "other"),
        "text": text[:MESSAGE_TEXT_MAX],
        "truncated": len(text) > MESSAGE_TEXT_MAX,
        "is_turn": kind == "Prompt" and bool(text),
        "tools": _block_tools(entry),
        "results": _result_count(entry),
        "timestamp": meta.get("timestamp"),
        "message_id": data.get("message_id") or "",
    }


def _oversized_entry(seq: int, size: int) -> dict:
    """Placeholder for a line too big to parse. Keeps `seq` continuous."""
    return {
        "seq": seq,
        "kind": "",
        "role": "other",
        "text": "",
        "truncated": True,
        "is_turn": False,
        "tools": [],
        "results": 0,
        "timestamp": None,
        "message_id": "",
        "bytes": size,
    }


def _is_crew_session(session_id: str) -> bool:
    """True if the session lives in CREW_SESSIONS_DIR."""
    return session_id.startswith("dashboard_chat-") or \
           (CREW_SESSIONS_DIR / f"{session_id}.jsonl").exists()


def _crew_transcript_entry(seq: int, entry: dict) -> dict:
    """Translate a crew-schema event (role: user/assistant/tool) to transcript shape."""
    role = entry.get("role", "")
    content = entry.get("content", "")
    if isinstance(content, list):
        content = " ".join(
            c.get("data", "") if isinstance(c, dict) else str(c)
            for c in content
        )
    content = str(content)
    kind_map = {"user": "Prompt", "assistant": "AssistantMessage", "tool": "ToolResults",
                "inject": "Prompt", "nudge": "Prompt", "system": "Prompt"}
    kind = kind_map.get(role, "Prompt")
    ts = entry.get("ts")
    return {
        "seq": seq,
        "kind": kind,
        "role": role if role in ("user", "assistant") else "other",
        "text": content[:MESSAGE_TEXT_MAX],
        "truncated": len(content) > MESSAGE_TEXT_MAX,
        "is_turn": role == "user" and bool(content),
        "tools": [],
        "results": 0,
        "timestamp": ts,
        "message_id": "",
    }


def read_transcript(session_id: str, after: int = -1,
                    limit: int = MESSAGE_LIMIT_DEFAULT) -> dict:
    """Read a kiro-cli session transcript from its JSONL file (V1 or V3)."""
    if v3mod.is_v3_session(session_id):
        return v3mod.read_transcript(session_id, after=after, limit=limit)
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
    limit = max(1, min(limit, MESSAGE_LIMIT_MAX))
    from collections import deque
    tail_mode = after < 0
    wanted: deque | list = deque(maxlen=limit) if tail_mode else []
    total = 0
    oversized = 0
    try:
        with open(jsonl_path, "rb") as f:
            for seq, raw in enumerate(f):
                total = seq + 1
                if len(raw) > 2_000_000:
                    oversized += 1
                    continue
                if seq > after:
                    if tail_mode or len(wanted) < limit:
                        wanted.append(seq)
    except FileNotFoundError:
        return {"error": "Transcript not found"}
    except OSError as exc:
        return {"error": f"Could not read transcript: {exc}"}

    wanted_set = set(wanted)
    messages = []
    malformed = 0
    try:
        with open(jsonl_path, "rb") as f:
            for seq, raw in enumerate(f):
                if seq not in wanted_set:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if isinstance(entry, dict):
                    messages.append(_transcript_entry(seq, entry))
    except OSError as exc:
        return {"error": f"Could not read transcript: {exc}"}

    wanted_list = list(wanted)
    return {
        "session_id": session_id,
        "messages": messages,
        "count": len(messages),
        "total": total,
        "more_before": tail_mode and bool(wanted_list) and wanted_list[0] > 0,
        "more_after": bool(wanted_list) and wanted_list[-1] < total - 1,
        "malformed": malformed,
        "oversized": oversized,
    }


def read_crew_transcript(session_id: str, after: int = -1,
                         limit: int = MESSAGE_LIMIT_DEFAULT) -> dict:
    """Read a crew-schema session transcript."""
    jsonl_path = CREW_SESSIONS_DIR / f"{session_id}.jsonl"
    limit = max(1, min(limit, MESSAGE_LIMIT_MAX))
    from collections import deque
    tail_mode = after < 0
    wanted = deque(maxlen=limit) if tail_mode else []
    total = 0
    try:
        with open(jsonl_path, "rb") as f:
            offset = 0
            for seq, raw in enumerate(f):
                total = seq + 1
                end = offset + len(raw)
                if seq > after:
                    if tail_mode or len(wanted) < limit:
                        wanted.append((seq, offset, end))
                offset = end
    except FileNotFoundError:
        return {"error": "Transcript not found"}
    except OSError as exc:
        return {"error": f"Could not read transcript: {exc}"}

    messages = []
    malformed = 0
    try:
        with open(jsonl_path, "rb") as f:
            f.seek(0)
            seq = 0
            for raw in f:
                if seq not in {w[0] for w in wanted}:
                    seq += 1
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    malformed += 1
                    seq += 1
                    continue
                if isinstance(entry, dict) and entry.get("_type") != "metadata":
                    messages.append(_crew_transcript_entry(seq, entry))
                seq += 1
    except OSError as exc:
        return {"error": f"Could not read transcript: {exc}"}

    return {
        "session_id": session_id,
        "messages": messages,
        "count": len(messages),
        "total": total,
        "more_before": tail_mode and bool(wanted) and list(wanted)[0][0] > 0,
        "more_after": bool(wanted) and list(wanted)[-1][0] < total - 1,
        "malformed": malformed,
        "oversized": 0,
    }



    """Read a session's transcript as line-addressed entries.

    Two passes, deliberately. The first walks the file for line offsets without
    decoding anything, the second parses only the lines being returned. That
    split is not premature: a real session here is 545MB with a single 100MB
    tool-result line in it, where parsing every line costs six seconds and
    walking them costs a third of one. Nothing in the payload comes from those
    giant lines anyway.
    """
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
    limit = max(1, min(limit, MESSAGE_LIMIT_MAX))
    from collections import deque
    # No cursor means "give me the latest page"; an explicit cursor means
    # "continue forward from here". Using the same maxlen deque for both made
    # `after=10` return the end of the file instead of entries 11..N.
    tail_mode = after < 0
    wanted = deque(maxlen=limit) if tail_mode else []
    total = 0
    try:
        with open(jsonl_path, "rb") as f:
            offset = 0
            for seq, raw in enumerate(f):
                total = seq + 1
                end = offset + len(raw)
                if seq > after:
                    if tail_mode or len(wanted) < limit:
                        wanted.append((seq, offset, end))
                offset = end
    except FileNotFoundError:
        return {"error": "Transcript not found"}
    except OSError as exc:
        return {"error": f"Could not read transcript: {exc}"}

    messages: list[dict] = []
    malformed = 0
    oversized = 0
    try:
        with open(jsonl_path, "rb") as f:
            for seq, start, end in wanted:
                if end - start > MESSAGE_LINE_MAX:
                    # A line this big is a tool result, and its content is not
                    # part of the payload. Skipping it keeps the entry's `seq`
                    # addressable — a hole in the numbering would break the one
                    # guarantee this endpoint makes.
                    oversized += 1
                    messages.append(_oversized_entry(seq, end - start))
                    continue
                f.seek(start)
                raw = f.read(end - start).decode("utf-8", errors="replace").strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if not isinstance(entry, dict):
                    malformed += 1
                    continue
                messages.append(_transcript_entry(seq, entry))
    except OSError as exc:
        return {"error": f"Could not read transcript: {exc}"}

    return {
        "session_id": session_id,
        "messages": messages,
        "count": len(messages),
        "total": total,
        # True when entries before the first one returned were left out, so a
        # caller knows the window is a window rather than the whole file.
        "more_before": tail_mode and bool(wanted) and wanted[0][0] > 0,
        "more_after": bool(wanted) and wanted[-1][0] < total - 1,
        "malformed": malformed,
        "oversized": oversized,
    }


# Cached by (mtime, size), because the sessions listing asks for this on every
# poll and the answer only changes when the file does.
_last_message_cache: dict[str, tuple[float, int, str]] = {}


def last_message(session_id: str, max_chars: int = LAST_MESSAGE_MAX) -> str:
    """The agent's last spoken line, for the card that says it needs you.

    Quoting, not describing: it costs no tokens and cannot be wrong. Read
    backwards from the end of the file and stop at the first assistant entry
    that actually said something — most `AssistantMessage` entries are tool
    calls with an empty text block.
    """
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
    try:
        stat = jsonl_path.stat()
    except OSError:
        _last_message_cache.pop(session_id, None)
        return ""

    cached = _last_message_cache.get(session_id)
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]

    text = ""
    try:
        with open(jsonl_path, "rb") as f:
            chunk = min(stat.st_size, LAST_MESSAGE_TAIL)
            f.seek(stat.st_size - chunk)
            lines = f.read().decode("utf-8", errors="replace").split("\n")
        # The first line of a mid-file seek is usually a fragment; a failed
        # parse drops it, so no special case is needed.
        _PASTE_RE = re.compile(r"^\[pasted document:", re.IGNORECASE)
        found_assistant = False
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind", "")
            if not found_assistant:
                if kind != "AssistantMessage":
                    continue
                said = _block_text(entry)
                if said:
                    text = said
                    found_assistant = True
                    # Keep scanning to check the preceding user turn
            else:
                # Check if the immediately preceding human message was paste-only
                if kind == "HumanMessage":
                    human_text = _block_text(entry).strip()
                    # All lines are paste references → suppress card preview
                    if human_text and all(
                        _PASTE_RE.match(ln.strip()) or not ln.strip()
                        for ln in human_text.splitlines()
                    ):
                        text = ""
                break
    except OSError:
        return ""

    # Collapse to a single paragraph: the card clamps to a few lines, and blank
    # lines inside the excerpt spend that budget saying nothing.
    text = re.sub(r"\n{2,}", "\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    _last_message_cache[session_id] = (stat.st_mtime, stat.st_size, text)
    return text


# Statuses where the last thing the agent said is worth carrying in the
# listing. A working session's excerpt is stale before it renders, and an
# archived one is not being triaged.
LAST_MESSAGE_STATUSES = {"idle", "awaiting-approval", "error"}


# Exact strings from a real kiro-cli permission prompt:
#     shell requires approval
#     ❯ Yes, single permission
#       Trust, always allow in this session
#       No (Tab to edit)
#     esc to close · ↑↓ to navigate · ↵ to select · Tab to edit
# It is an arrow-key menu, not a y/n/t question — which is why answering it
# takes navigation keys rather than a single letter.
def pane_awaiting_approval(pane: str) -> bool:
    """True when the TUI is showing its permission menu."""
    if not pane:
        return False
    tail = pane[-3000:]
    return "requires approval" in tail and ("to select" in tail or "to navigate" in tail)


# How much of the pane counts as the footer. kiro-cli's footer is the composer
# line, the model/context line and the path line; a dozen covers it with room
# for wrapping, and stops the conversation above being read as status.
FOOTER_LINES = 12


def pane_status(pane: str) -> str | None:
    """Status read straight off the TUI, or None if the pane is inconclusive.

    kiro-cli swaps its footer depending on what it is doing, which makes this
    exact rather than inferred:
        idle     " ask a question or describe a task"
        working  " Kiro is working · Type to steer · Ctrl+S to queue"
        prompt   " esc to close · up/down to navigate · enter to select"
    """
    if not pane:
        return None
    if pane_awaiting_approval(pane):
        return "awaiting-approval"
    # Only the footer, and matched at the start of a line. Searching the whole
    # tail for "Kiro is working" caught kiro-cli's own tip — "Type while Kiro is
    # working to steer it mid-turn" — which sits above the composer of an *idle*
    # session, so a finished session reported thinking until it scrolled away.
    footer = [line.strip() for line in pane.rstrip().splitlines()[-FOOTER_LINES:]]
    # Idle first: when kiro-cli is working, this line is replaced by the working
    # one, so its presence is the stronger claim of the two.
    if any(line.startswith("ask a question or describe a task") for line in footer):
        return "idle"
    if any(line.startswith("Kiro is working") or line.startswith("esc to cancel")
           for line in footer):
        return "thinking"
    return None


def detect_status(session_id: str, lock_data: dict | None, pane: str = "") -> str:
    """Derive session status from the pane when we have one, else from files.

    The pane is authoritative when it is conclusive; file mtimes and jsonl kinds
    are the fallback for sessions we do not own.

    For V3 sessions with an ACP observer attached, ACP session/update events
    take precedence over pane-scraping — they arrive within milliseconds of the
    state change and do not depend on TUI rendering.
    """
    # ACP observer path (Task 4): if we have a live side-channel, derive status
    # from the most recent session/update notification. Fall through if the
    # observer has no relevant event yet (returns None).
    if acp_observer.is_attached(session_id):
        from_acp = acp_observer.detect_status(session_id)
        if from_acp is not None:
            return from_acp

    # V3 sessions without ACP: use messages.jsonl payload.type
    if v3mod.is_v3_session(session_id):
        return v3mod.detect_status(session_id)

    if lock_data is None:
        return "done"

    pid = lock_data.get("pid")
    if pid and not is_process_alive(pid):
        return "error"

    # The pane wins whenever it says something definite: it reflects the TUI as
    # it is right now, while the jsonl only gains entries once a turn finishes.
    from_pane = pane_status(pane)
    if from_pane:
        return from_pane

    # A preToolUse hook request is a structural approval signal — more reliable
    # than TUI scraping, and works for sessions without a readable pane.
    if any(a["session_id"] == session_id for a in tmux.pending_approvals()):
        return "awaiting-approval"

    # Check file freshness — if jsonl was modified in last 10s, agent is actively working
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
    json_path = SESSIONS_DIR / f"{session_id}.json"

    now = time.time()
    last_modified = 0
    if jsonl_path.exists():
        last_modified = max(last_modified, jsonl_path.stat().st_mtime)
    if json_path.exists():
        last_modified = max(last_modified, json_path.stat().st_mtime)

    # A `stop` hook records the moment kiro-cli finished answering. When one has
    # fired since the session files last changed, the turn is over — that is a
    # fact rather than the inference the freshness window below makes, and it is
    # the only signal available for a session with no pane to read.
    ended = tmux.turn_ended_at(session_id)
    if ended and ended >= last_modified:
        return "idle"

    freshness = now - last_modified if last_modified > 0 else 9999

    # Read last jsonl entry for kind
    lines = tail_jsonl(session_id, 3)
    last_kind = ""
    if lines:
        try:
            last_kind = json.loads(lines[-1]).get("kind", "")
        except json.JSONDecodeError:
            pass

    # Approval is decided by the pane alone (see pane_awaiting_approval, called
    # before this point). It used to be guessed from phrasing — "shall i",
    # "proceed?", a trailing question mark — which flagged every ordinary
    # question the agent asked as a permission request. A question is not an
    # approval prompt, so that heuristic is gone rather than merely tuned.

    # 1. File modified in last 10 seconds = actively thinking
    if freshness < 10:
        return "thinking"

    # 2. Last entry is ToolResults or Prompt = mid-turn (thinking)
    if last_kind in ("ToolResults", "Prompt"):
        return "thinking"

    # 3. Last entry is AssistantMessage = turn complete (idle)
    if last_kind == "AssistantMessage":
        return "idle"

    return "idle"


def get_last_activity(session_id: str) -> str:
    """Get a snippet of the last meaningful activity."""
    lines = tail_jsonl(session_id, 10)
    for line in reversed(lines):
        try:
            entry = json.loads(line)
            kind = entry.get("kind", "")
            if kind == "AssistantMessage":
                data = entry.get("data", {})
                content = data.get("content", []) if isinstance(data, dict) else []
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("kind") == "text":
                            text = block.get("data", "").strip()
                            if text:
                                return text[:200]
        except json.JSONDecodeError:
            continue
    return ""


def shorten_path(path: str) -> str:
    """Shorten a path for display."""
    home = str(Path.home())
    if path.startswith(home):
        path = "~" + path[len(home):]
    path = path.replace("/Documents/PROJECTS", "/…")
    return path


def get_full_prompt(session_id: str) -> str:
    """Read the full first user prompt from JSONL."""
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
    if not jsonl_path.exists():
        return ""
    try:
        first_line = jsonl_path.open().readline()
        entry = json.loads(first_line)
        if entry.get("kind") != "Prompt":
            return ""
        text = entry.get("data", {}).get("content", [{}])[0].get("data", "")
        # If it's a /goal prompt, extract the actual goal
        marker = "goal for you to achieve:\n\n"
        idx = text.find(marker)
        if idx >= 0:
            return text[idx + len(marker):].split("\n## ")[0].strip()[:2000]
        return text.strip()[:2000]
    except Exception:
        return ""


def meta_title(meta: dict) -> str:
    """Return the display title for a session metadata dict.

    Prefers the deck-owned rename stored in ``~/.osa-kiro/names.json`` so that
    kiro-cli replacing the whole session JSON (new inode on every write) cannot
    clobber a user rename.  Falls back to the kiro-cli ``title`` field, then to
    the first user prompt in the JSONL, then to the task stored in managed.json.
    The last two cover the 3-4 second window where kiro-cli has written the
    session files but hasn't populated the title field yet.
    """
    session_id = meta.get("session_id", "")
    if session_id:
        deck_name = _get_deck_name(session_id)
        if deck_name:
            return deck_name
    title = meta.get("title") or ""
    if title:
        return title
    if not session_id:
        return ""
    # Title not written yet — try the first user prompt from the JSONL.
    prompt = get_full_prompt(session_id)
    if prompt:
        return prompt[:200]
    # JSONL also not written yet — fall back to the task recorded in managed.json
    # at spawn time. This is the dispatch task text, available immediately.
    try:
        state = tmux.load_state()
        task = state["managed"].get(session_id, {}).get("task", "")
        if task:
            return task[:200]
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Deck-owned rename store — lives in STATE_DIR/names.json, completely separate
# from the kiro-cli session files that get replaced on every agent turn.
# ---------------------------------------------------------------------------
_NAMES_FILE = STATE_DIR / "names.json"
_names_lock = threading.Lock()


def _load_names() -> dict:
    try:
        return json.loads(_NAMES_FILE.read_text())
    except Exception:
        return {}


def _save_names(names: dict) -> None:
    _NAMES_FILE.write_text(json.dumps(names))


def _get_deck_name(session_id: str) -> str:
    return _load_names().get(session_id, "")


def _set_deck_name(session_id: str, name: str) -> None:
    with _names_lock:
        names = _load_names()
        names[session_id] = name
        _save_names(names)


def _clear_deck_name(session_id: str) -> None:
    with _names_lock:
        names = _load_names()
        names.pop(session_id, None)
        _save_names(names)


def clean_title(title: str, session_id: str) -> str:
    """Extract actual user goal from /goal template titles."""
    if not title or not title.startswith("## Objective"):
        return title
    jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
    if not jsonl_path.exists():
        return title
    try:
        first_line = jsonl_path.open().readline()
        entry = json.loads(first_line)
        text = entry.get("data", {}).get("content", [{}])[0].get("data", "")
        marker = "goal for you to achieve:\n\n"
        idx = text.find(marker)
        if idx >= 0:
            goal = text[idx + len(marker):].split("\n")[0].strip()
            if goal:
                return goal
    except Exception:
        pass
    return title


def _context_pct(pane: str) -> str:
    """Extract the context usage percentage from a tmux pane capture.

    kiro-cli renders '◑ 31%' in its status bar line. Several Unicode circle
    chars are used depending on version: ◔ (U+25D4), ◉ (U+25C9), ◑ (U+25D1).
    Returns e.g. '31%' or '' if not found.
    """
    import re as _re
    m = _re.search(r'[◔◉◑◐◕]\s*(\d+(?:\.\d+)?%)', pane)
    return m.group(1) if m else ""


def _session_model_effort(meta: dict) -> tuple[str, str]:
    """Extract current model and effort from session_state.rts_model_state.

    Returns (model_id, effort) — both may be empty strings if not present.
    kiro-cli stores the active model and effort level in the session .json so
    that /model and /effort switches survive across sessions.
    """
    try:
        rts = meta.get("session_state", {}).get("rts_model_state", {})
        model_id = rts.get("model_info", {}).get("model_id", "") or ""
        effort = (
            rts.get("additional_fields", {})
               .get("overrides", {})
               .get("output_config", {})
               .get("effort", "")
        ) or ""
        return model_id, effort
    except Exception:
        return "", ""


def _is_stalled(session_id: str, status: str, meta: dict) -> bool:
    """True if session is thinking/running but JSONL hasn't grown for stall_minutes."""
    if status not in ("thinking", "running"):
        return False
    settings = _load_settings()
    stall_minutes = float(settings.get("stall-minutes", 10))
    if stall_minutes <= 0:
        return False
    jsonl = SESSIONS_DIR / f"{session_id}.jsonl"
    try:
        age = time.time() - jsonl.stat().st_mtime
        return age > stall_minutes * 60
    except OSError:
        return False


def _ownership_fields(session_id: str) -> dict:
    """Return ownership-related fields for a session record.

    These are additive: existing callers that don't read them are unaffected.
    Defaults are always safe: human-owned, handoverable, visible.
    """
    o = ownership.get_ownership(session_id)
    recorded_profile = o.get("kiro_profile") or ""
    active_profile = _cached_active_profile()
    # kiro_profile is recorded at dispatch time; fall back to the currently
    # active profile so older sessions that pre-date profile tracking still
    # show a value. Cached to avoid a SQLite read per session per poll.
    profile = recorded_profile or active_profile
    # profile_verified: true when the profile was recorded at dispatch AND no
    # global switch has happened since that session spawned.
    # A kiro-cli process holds the credentials it loaded at launch; a global
    # switch cannot reach into it. After a switch the badge is unverified.
    recorded_at_dispatch = bool(recorded_profile)
    if recorded_at_dispatch:
        spawn_ts = o.get("spawned_at", 0.0)
        switch_ts = _last_switch_ts()
        profile_verified = (
            recorded_profile == active_profile
            and (switch_ts == 0.0 or spawn_ts >= switch_ts)
        )
    else:
        profile_verified = False
    # When profile is unverified, show the current active profile instead of
    # the stale dispatch-time one. The tooltip carries the recorded name so the
    # user can see what changed.
    display_profile = active_profile if (not profile_verified and active_profile) else profile
    return {
        "owner": o.get("owner", "human"),
        "role": o.get("role", "primary"),
        "group_id": o.get("group_id"),
        "handoverable": o.get("handoverable", True),
        "visible": o.get("visible", True),
        "kiro_profile": display_profile,
        "kiro_profile_recorded": recorded_profile if not profile_verified else "",
        "kiro_profile_arn": o.get("kiro_profile_arn", ""),
        "profile_verified": profile_verified,
    }


_active_profile_cache: tuple[float, str] = (0.0, "")
_ACTIVE_PROFILE_TTL = 10.0  # seconds
# Timestamp of the last global profile switch. Sessions spawned before this
# time have an unverified profile label (the running process may be on the
# old profile). Zero means no switch has occurred this session.
_last_profile_switch_at: float = 0.0


def _last_switch_ts() -> float:
    """Wall-clock of the last profile switch, persisted across restarts.

    `_last_profile_switch_at` is a module-level float that resets to 0.0 on
    every backend restart. That caused every session to show profile_verified:
    true after a restart, even if a switch had happened before the restart.
    The fix: _previous.jsonl is rewritten on every switch, so its mtime *is*
    the last-switch time and it already survives restarts. Take the max of the
    in-memory value (exact, covers the current session) and the mtime (covers
    across restarts).
    """
    try:
        file_ts = _profile_data_path("_previous").stat().st_mtime
    except OSError:
        file_ts = 0.0
    return max(_last_profile_switch_at, file_ts)


def _cached_active_profile() -> str:
    global _active_profile_cache
    now = time.time()
    ts, val = _active_profile_cache
    if now - ts < _ACTIVE_PROFILE_TTL:
        return val
    try:
        val = _active_profile_name()
    except Exception:
        val = ""
    _active_profile_cache = (now, val)
    return val


def _delivery_notes(session_id: str, agent_name: str, cwd: str) -> list[str]:
    """Return a brief list of steering delivery notes for the session grid.

    This is a lightweight summary, not the full delivery record. For the full
    record use GET /api/sessions/{id}/delivery.
    """
    try:
        rec = delivery.record_session_delivery(session_id, agent_name, cwd)
        return rec.get("notes", [])
    except Exception:
        return []


@app.get("/api/sessions")
def list_sessions():
    """List all active and recent sessions."""
    sessions = []
    seen_ids = set()
    now = time.time()

    managed = tmux.managed_sessions()

    # Pendings whose owning backend died leave a card nothing can remove, so
    # reap them here rather than only on startup. Then find which of the
    # survivors already have a session on disk, so one agent yields one card
    # instead of a placeholder plus an unrecognised "foreign" twin.
    tmux.reap_pendings()
    # An agentSpawn hook can fire after its spawn has already been correlated by
    # the fallback, leaving a drop nobody will ever claim. Same reasoning as the
    # pending reap: sweep on listing, not only at startup.
    tmux.sweep_hook_reports()
    tmux.sweep_turn_marks()
    tmux.sweep_approvals()
    # Gates on real session ids are decisions and stay; only the nonce-keyed
    # ones from spawns that never correlated expire.
    tmux.sweep_gates()
    gated = tmux.gated_sessions()
    pending_owners = tmux.pending_owners()
    starting_ids = set(pending_owners.values())

    # First pass: find active sessions (those with .lock files)
    # Get the concierge session ID to filter it out (it's internal to Quarterdeck)
    concierge_session_id = concierge._session_id
    if not concierge_session_id and concierge.is_alive():
        concierge_session_id = concierge._find_session_id()

    for lock_file in (SESSIONS_DIR.glob("*.lock") if SESSIONS_DIR.exists() else []):
        session_id = lock_file.stem
        
        # Skip the concierge session — it's internal to Quarterdeck
        if session_id == concierge_session_id:
            continue
        
        seen_ids.add(session_id)
        lock_data = read_lock(session_id)
        meta = read_metadata(session_id)
        if meta is None:
            continue

        # Clean up stale locks (process is dead)
        pid = lock_data.get("pid") if lock_data else None
        if pid and not is_process_alive(pid):
            try:
                lock_file.unlink()
            except OSError:
                pass
            continue

        raw_cwd = meta.get("cwd") or ""

        # Hide KiroCrew background/infrastructure sessions (no user work in them)
        if any(raw_cwd.startswith(p) for p in HIDDEN_CWD_PREFIXES):
            continue

        raw_title = clean_title(meta_title(meta) or "Untitled", session_id) or "Untitled"

        record = managed.get(session_id)
        is_managed = bool(record and record.get("alive"))
        # Ours, but correlation has not finished renaming its tmux session yet.
        # Reporting it as `foreign` would invite a takeover that kills a process
        # we are in the middle of adopting.
        is_starting = not is_managed and session_id in starting_ids
        pane = tmux.capture(session_id, 30) if is_managed else ""
        status = detect_status(session_id, lock_data, pane)
        sessions.append({
            "id": session_id,
            "title": raw_title[:200],
            "name": raw_title[:120] or (Path(raw_cwd).name if raw_cwd else ""),
            "folder": Path(raw_cwd).name if raw_cwd else "",
            "cwd": raw_cwd,
            "cwd_display": shorten_path(raw_cwd),
            "status": status,
            # What it last said, for the sessions that are waiting on you. Comes
            # from this pass rather than a per-card fetch: the grid polls every
            # two seconds, so one request per card would multiply that by the
            # number of sessions on screen.
            "last_message": (last_message(session_id)
                             if status in LAST_MESSAGE_STATUSES else ""),
            # managed: we own the tmux session, so input is possible.
            # foreign: alive but started elsewhere — read-only until taken over.
            "control": "managed" if is_managed else ("starting" if is_starting else "foreign"),
            "attach": record["attach"] if is_managed else "",
            "agent": record.get("agent", "") if is_managed else "",
            "status_source": "pane" if is_managed else "files",
            # Whether this session's tool calls are held for a human decision.
            "gated": session_id in gated,
            "created_at": meta.get("created_at") or "",
            "updated_at": meta.get("updated_at") or "",
            "last_activity": get_last_activity(session_id),
            "parent_id": meta.get("parent_id") or "",
            "branch_point": meta.get("branch_point"),
            "summary": (_read_summary(session_id) or {}).get("text") or "",
            "stalled": _is_stalled(session_id, status, meta),
            "trust_until": _trust_until(session_id) or None,
            "context_pct": _context_pct(pane),
            "subagent_count": _find_subagent_count(session_id, lock_data) if is_managed else 0,
            **_ownership_fields(session_id),
            **dict(zip(("model", "effort"), _session_model_effort(meta))),
            "sq_depth": len(sq_list(session_id)),
            "delivery_notes": _delivery_notes(session_id,
                                               record.get("agent", "") if is_managed else "",
                                               raw_cwd),
        })

    # Second pass: recent non-active sessions (by modification time)
    json_files = sorted(
        SESSIONS_DIR.glob("*.json"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for json_file in json_files[:RECENT_SESSIONS_LIMIT * 2]:
        session_id = json_file.stem
        if session_id in seen_ids:
            continue
        seen_ids.add(session_id)

        meta = read_metadata(session_id)
        if meta is None:
            continue

        raw_cwd = meta.get("cwd") or ""

        # Hide KiroCrew background/infrastructure sessions (no user work in them)
        if any(raw_cwd.startswith(p) for p in HIDDEN_CWD_PREFIXES):
            continue

        raw_title = clean_title(meta_title(meta) or "Untitled", session_id)

        sessions.append({
            "id": session_id,
            "title": raw_title[:200],
            "name": (raw_title[:120] if raw_title and raw_title != "Untitled"
                     else (Path(raw_cwd).name if raw_cwd else "")),
            "folder": Path(raw_cwd).name if raw_cwd else "",
            "cwd": raw_cwd,
            "cwd_display": shorten_path(raw_cwd),
            "status": "done",
            # Not triaged, so not worth a tail read each poll — but present, so
            # the field is never conditionally absent.
            "last_message": "",
            "control": "archived",
            "attach": "",
            "status_source": "files",
            "created_at": meta.get("created_at") or "",
            "updated_at": meta.get("updated_at") or "",
            "last_activity": "",
            "parent_id": meta.get("parent_id") or "",
            "branch_point": meta.get("branch_point"),
            **dict(zip(("model", "effort"), _session_model_effort(meta))),
            "sq_depth": len(sq_list(session_id)),
        })

        if len(sessions) >= RECENT_SESSIONS_LIMIT:
            break

    # Third pass: V3 sessions (workspace-hash directory layout)
    v3_sessions = sorted(
        v3mod.all_v3_sessions(),
        key=lambda t: (t[1] / "session.json").stat().st_mtime if (t[1] / "session.json").exists() else 0,
        reverse=True,
    )
    for v3_id, v3_dir in v3_sessions[:RECENT_SESSIONS_LIMIT]:
        if v3_id in seen_ids:
            continue
        seen_ids.add(v3_id)

        meta = v3mod.read_metadata(v3_id)
        if meta is None:
            continue

        raw_cwd = meta.get("cwd") or ""
        if any(raw_cwd.startswith(p) for p in HIDDEN_CWD_PREFIXES):
            continue

        raw_title = meta.get("title") or "Untitled"
        status = v3mod.detect_status(v3_id)
        last_msg = v3mod.last_message(v3_id)
        ctx_pct = v3mod.context_pct(v3_id)

        sessions.append({
            "id": v3_id,
            "title": raw_title[:200],
            "name": raw_title[:120] or (Path(raw_cwd).name if raw_cwd else ""),
            "folder": Path(raw_cwd).name if raw_cwd else "",
            "cwd": raw_cwd,
            "cwd_display": shorten_path(raw_cwd),
            "status": status,
            "last_message": last_msg,
            "control": "acp" if acp_observer.is_attached(v3_id) else "archived",
            "attach": "",
            "agent": meta.get("agent_mode") or "",
            "model": meta.get("model") or "",
            "status_source": "files",
            "gated": False,
            "created_at": meta.get("created_at") or "",
            "updated_at": meta.get("updated_at") or "",
            "last_activity": meta.get("updated_at") or "",
            "parent_id": "",
            "branch_point": None,
            "summary": (_read_summary(v3_id) or {}).get("text") or "",
            "stalled": False,
            "trust_until": None,
            "context_pct": ctx_pct,
            "format": "v3",
            **_ownership_fields(v3_id),
            "delivery_notes": [],
        })

        if len(sessions) >= RECENT_SESSIONS_LIMIT * 2:
            break

    # Sessions that have started but whose id has not been correlated yet. They
    # are real running processes, so showing them beats an empty grid.
    for nonce, entry in tmux.load_state()["pending"].items():
        # Its real session is already in the list above, marked `starting`.
        if nonce in pending_owners:
            continue
        cwd_ = entry.get("cwd", "")
        sessions.insert(0, {
            "id": f"pending:{nonce}",
            # Exposed so the UI can cancel a spawn that never correlated. It has
            # no session id, so none of the id-keyed endpoints can touch it.
            "nonce": nonce,
            "title": entry.get("task", "") or "Starting…",
            "name": entry.get("task", "") or "Starting…",
            "folder": Path(cwd_).name if cwd_ else "",
            "cwd": cwd_,
            "cwd_display": shorten_path(cwd_),
            "status": "thinking",
            "control": "starting",
            "attach": "",
            "last_message": "",
            "status_source": "pending",
            "created_at": "", "updated_at": "", "last_activity": "",
            "unresolved": bool(entry.get("unresolved")),
        })

    # Sort: active first (thinking, awaiting-approval), then most recently active first within each status group
    status_order = {"thinking": 0, "running": 0, "awaiting-approval": 1, "idle": 2, "error": 3, "done": 4}
    # First sort by status, then by updated_at descending
    # This ensures groupby works correctly (requires sorted input)
    sessions.sort(key=lambda s: (status_order.get(s["status"], 5), s.get("updated_at", "") or ""), reverse=False)
    # Now apply descending updated_at within each status group
    from itertools import groupby
    sorted_sessions = []
    for _, group in groupby(sessions, key=lambda s: status_order.get(s["status"], 5)):
        g = list(group)
        g.sort(key=lambda s: s.get("updated_at", "") or "", reverse=True)
        sorted_sessions.extend(g)
    sessions = sorted_sessions

    # Crew pass: read-only sessions from ~/.kiro/crew/sessions (dashboard_chat-*.jsonl)
    # These use a different schema (role: user/assistant/tool) and are never managed.
    if CREW_SESSIONS_DIR.exists():
        crew_seen = set()
        for jsonl_file in sorted(
            CREW_SESSIONS_DIR.glob("dashboard_chat-*.jsonl"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )[:RECENT_SESSIONS_LIMIT]:
            crew_id = jsonl_file.stem
            if crew_id in crew_seen or crew_id in seen_ids:
                continue
            crew_seen.add(crew_id)
            try:
                # Read first line only for metadata
                with open(jsonl_file) as f:
                    first_line = f.readline().strip()
                if not first_line:
                    continue
                import json as _json
                meta = _json.loads(first_line)
                if meta.get("_type") != "metadata":
                    continue
                title = meta.get("title", "") or crew_id
                created_at = meta.get("created_at", "")
                mtime = jsonl_file.stat().st_mtime
                import datetime as _dt
                updated_at = _dt.datetime.fromtimestamp(mtime).isoformat()
                sessions.append({
                    "id": crew_id,
                    "title": title[:200],
                    "name": title[:120] or crew_id,
                    "folder": "crew",
                    "cwd": str(CREW_SESSIONS_DIR),
                    "cwd_display": "~/.kiro/crew/sessions",
                    "status": "idle",
                    "last_message": "",
                    "control": "crew",
                    "attach": "",
                    "agent": meta.get("agent", ""),
                    "status_source": "files",
                    "gated": False,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "last_activity": updated_at,
                    "parent_id": "",
                    "branch_point": None,
                    "summary": "",
                    "stalled": False,
                    "trust_until": None,
                })
            except Exception:
                continue

    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}")
def get_session_detail(session_id: str):
    """Get terminal-like output for a session."""
    # Crew sessions: return detail with activity output populated
    if _is_crew_session(session_id):
        jsonl_path = CREW_SESSIONS_DIR / f"{session_id}.jsonl"
        if not jsonl_path.exists():
            return {"error": "Session not found"}
        try:
            with open(jsonl_path) as f:
                first_line = f.readline().strip()
            meta = json.loads(first_line) if first_line else {}
        except Exception:
            meta = {}

        # Read last 60 lines for activity view
        try:
            with open(jsonl_path, "rb") as f:
                from collections import deque
                last_lines = list(deque(f, maxlen=60))
        except Exception:
            last_lines = []

        output = []
        last_output = ""
        for raw in last_lines:
            try:
                entry = json.loads(raw)
            except Exception:
                continue
            role = entry.get("role", "")
            content = entry.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    c.get("data", "") if isinstance(c, dict) else str(c)
                    for c in content
                )
            content = str(content).strip()
            if not content:
                continue

            if role == "user":
                # Skip injected system prompts (too long, not typed by user)
                if len(content) < 2000:
                    output.append({"type": "user", "text": content[:500]})
            elif role == "assistant":
                if content:
                    output.append({"type": "assistant", "text": content[:1000]})
                    last_output = content.strip()
            elif role == "tool":
                # Tool events have an icon prefix — show as tool line
                text = content[:200]
                output.append({"type": "tool", "text": text, "detail": ""})

        import datetime as _dt
        mtime = jsonl_path.stat().st_mtime
        updated_at = _dt.datetime.fromtimestamp(mtime).isoformat()
        return {
            "id": session_id,
            "title": meta.get("title", session_id),
            "prompt": "",
            "cwd": str(CREW_SESSIONS_DIR),
            "cwd_display": "~/.kiro/crew/sessions",
            "status": "idle",
            "control": "crew",
            "attach": "",
            "agent": meta.get("agent", ""),
            "output": output,
            "last_output": last_output,
            "created_at": meta.get("created_at", ""),
            "updated_at": updated_at,
            "gated": False,
            "stalled": False,
        }

    meta = read_metadata(session_id)
    if meta is None:
        return {"error": "Session not found"}

    # V3 session: build detail from v3 module
    if v3mod.is_v3_session(session_id):
        status = v3mod.detect_status(session_id)
        transcript = v3mod.read_transcript(session_id, after=-1, limit=60)
        output = [
            {"type": m["role"], "text": m.get("text", "")}
            for m in transcript.get("messages", [])
            if m.get("role") in ("user", "assistant") and m.get("text")
        ]
        last_output = v3mod.get_last_output(session_id)
        raw_cwd = meta.get("cwd") or ""
        return {
            "id": session_id,
            "title": meta.get("title") or "Untitled",
            "prompt": next((m["text"] for m in transcript.get("messages", []) if m.get("role") == "user"), ""),
            "cwd": raw_cwd,
            "cwd_display": shorten_path(raw_cwd),
            "status": status,
            "control": "acp" if acp_observer.is_attached(session_id) else "archived",
            "attach": "",
            "agent": meta.get("agent_mode") or "",
            "model": meta.get("model") or "",
            "output": output,
            "last_output": last_output,
            "created_at": meta.get("created_at") or "",
            "updated_at": meta.get("updated_at") or "",
            "gated": False,
            "stalled": False,
            "format": "v3",
        }

    lock_data = read_lock(session_id)
    record_ = tmux.managed_sessions().get(session_id)
    pane = tmux.capture(session_id, 30) if (record_ and record_.get("alive")) else ""
    status = detect_status(session_id, lock_data, pane)
    last_lines = tail_jsonl(session_id, 60)

    # Build terminal-like output stream
    output = []
    last_output = ""
    for line in last_lines:
        try:
            entry = json.loads(line)
            kind = entry.get("kind", "")
            data = entry.get("data", {})
            if not isinstance(data, dict):
                continue
            content = data.get("content", [])
            if not isinstance(content, list):
                continue

            if kind == "Prompt":
                for block in content:
                    if isinstance(block, dict) and block.get("kind") == "text":
                        text = block.get("data", "").strip()
                        if text:
                            output.append({"type": "user", "text": text[:500]})

            elif kind == "AssistantMessage":
                msg_text = ""
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("kind") == "text":
                        text = block.get("data", "").strip()
                        if text:
                            output.append({"type": "assistant", "text": text[:1000]})
                            msg_text += text + "\n"
                    elif block.get("kind") == "toolUse":
                        td = block.get("data", {})
                        if isinstance(td, dict):
                            name = td.get("name", "tool")
                            inp = td.get("input", {})
                            detail = ""
                            if isinstance(inp, dict):
                                if "command" in inp:
                                    detail = inp["command"][:120]
                                elif "path" in inp:
                                    detail = inp["path"]
                                elif "query" in inp:
                                    detail = inp["query"][:80]
                                elif "pattern" in inp:
                                    detail = inp["pattern"][:80]
                            output.append({"type": "tool", "text": name, "detail": detail})
                if msg_text:
                    last_output = msg_text.strip()

            elif kind == "ToolResults":
                for block in content:
                    if isinstance(block, dict) and block.get("kind") == "toolResult":
                        td = block.get("data", {})
                        if isinstance(td, dict):
                            rc_list = td.get("content", [])
                            if isinstance(rc_list, list):
                                for rc in rc_list:
                                    if isinstance(rc, dict) and rc.get("kind") == "text":
                                        text = rc.get("data", "").strip()
                                        if text:
                                            output.append({"type": "result", "text": text[:400]})
        except json.JSONDecodeError:
            continue

    raw_cwd = meta.get("cwd") or ""
    record = record_
    is_managed = bool(record and record.get("alive"))
    if is_managed:
        control = "managed"
    elif lock_data and is_process_alive(lock_data.get("pid") or 0):
        control = "foreign"
    else:
        control = "archived"

    return {
        "id": session_id,
        # A renamed session must keep its name: meta_title() checks the
        # deck-owned sidecar (~/.osa-kiro/names.json) first, so kiro-cli
        # replacing the whole session JSON never clobbers a user rename.
        "title": meta_title(meta)
                 or clean_title(meta.get("title") or "", session_id)
                 or get_full_prompt(session_id) or "Untitled",
        "prompt": get_full_prompt(session_id),
        "cwd": raw_cwd,
        "cwd_display": shorten_path(raw_cwd),
        "status": status,
        "control": control,
        "awaiting_prompt": pane_awaiting_approval(pane),
        "gated": tmux.gate_enabled(session_id),
        "attach": record["attach"] if is_managed else "",
        "agent": (record.get("agent", "") if is_managed and record else ""),
        "dead_pane": bool(record and record.get("dead_pane")),
        "created_at": meta.get("created_at") or "",
        "updated_at": meta.get("updated_at") or "",
        "output": output[-40:],
        "last_output": get_last_output(session_id)[:5000],
    }


@app.get("/api/sessions/{session_id}/messages")
def get_messages(session_id: str, after: int = -1,
                 limit: int = MESSAGE_LIMIT_DEFAULT):
    """The conversation as line-addressed entries.

    `after` walks forward from a `seq` the caller already has; without it the
    last `limit` entries come back. The whole point of the endpoint is that
    every entry carries a `seq` stable enough to branch from — the detail
    panel's own rendering is positional and cannot be pointed at.
    """
    if _is_crew_session(session_id):
        return read_crew_transcript(session_id, after=after, limit=limit)
    if read_metadata(session_id) is None:
        return {"error": "Session not found"}
    return read_transcript(session_id, after=after, limit=limit)


@app.get("/api/sessions/{session_id}/export")
def export_session_markdown(session_id: str):
    """Render a session's JSONL as a readable markdown transcript."""
    """Render a session's JSONL as a readable markdown transcript."""
    meta = read_metadata(session_id)
    if not meta:
        return {"error": "Session not found"}
    title = meta_title(meta) or "Untitled"
    cwd = meta.get("cwd") or ""
    created = (meta.get("created_at") or "")[:10]

    transcript = read_transcript(session_id, after=-1, limit=MESSAGE_LIMIT_MAX)
    messages = transcript.get("messages", [])

    lines = [f"# {title}", ""]
    if cwd:
        lines.append(f"**Directory:** `{cwd}`")
    if created:
        lines.append(f"**Date:** {created}")
    parent_id = meta.get("parent_id")
    branch_point = meta.get("branch_point")
    if parent_id:
        bp = f" at turn {branch_point}" if branch_point is not None else ""
        lines.append(f"**Branch of:** `{parent_id[:8]}`{bp}")
    lines += ["", "---", ""]

    for msg in messages:
        role = msg.get("role", "other")
        text = (msg.get("text") or "").strip()
        tools = msg.get("tools") or []
        if role == "user" and text:
            lines += [f"> **You:** {text}", ""]
        elif role == "assistant" and text:
            lines += [text, ""]
        elif tools:
            tool_names = [t if isinstance(t, str) else t.get('name', str(t)) for t in tools]
            lines += [f"*⚙ {', '.join(tool_names)}*", ""]
        if msg.get("truncated"):
            lines += ["*[…truncated]*", ""]

    body = "\n".join(lines)
    safe_title = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title)[:60].strip()
    filename = f"{safe_title or session_id[:8]}.md"
    return FastAPIResponse(
        content=body,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/sessions/{session_id}/export-open")
def export_session_open(session_id: str, request: Request):
    """Write the transcript to ~/Downloads and open it with the default app.

    WKWebView (pywebview) intercepts HTTP downloads and renders them inline
    instead of saving. This endpoint bypasses that by writing the file to disk
    directly and calling `open` to hand it to the OS.
    """
    if not require_local(request):
        return {"error": "local only"}
    # Re-use the export function to get the body
    resp = export_session_markdown(session_id)
    if not hasattr(resp, "body"):
        return {"error": "Export failed"}
    body = resp.body.decode()
    meta = read_metadata(session_id)
    title = (meta_title(meta) or "session") if meta else "session"
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title)[:60].strip()
    downloads = Path.home() / "Downloads"
    downloads.mkdir(exist_ok=True)
    out = downloads / f"{safe or session_id[:8]}.md"
    # Avoid overwriting by appending a counter
    counter = 1
    while out.exists():
        out = downloads / f"{safe or session_id[:8]}-{counter}.md"
        counter += 1
    out.write_text(body)
    subprocess.run(["open", str(out)], capture_output=True)
    return {"ok": True, "path": str(out)}


# ── Session summaries ────────────────────────────────────────────────────────

def _summary_path(session_id: str) -> Path:
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    return SUMMARIES_DIR / f"{session_id}.json"


# ---------------------------------------------------------------------------
# Slash command queue — per-session queue that drains on idle after each turn.
# Unlike the task stack (auto-advance feature), slash queues need no opt-in:
# every stop event checks and sends the front item if the session is idle.
# ---------------------------------------------------------------------------

def _sq_path(session_id: str) -> Path:
    return SLASH_QUEUES_DIR / f"{session_id}.json"


def sq_list(session_id: str) -> list[dict]:
    try:
        return json.loads(_sq_path(session_id).read_text())
    except (OSError, json.JSONDecodeError):
        return []


def sq_push(session_id: str, text: str) -> dict:
    SLASH_QUEUES_DIR.mkdir(parents=True, exist_ok=True)
    item = {"id": str(__import__("uuid").uuid4())[:8], "text": text.strip(), "queued_at": time.time()}
    items = sq_list(session_id)
    items.append(item)
    path = _sq_path(session_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items))
    tmp.replace(path)
    return item


def sq_remove(session_id: str, item_id: str) -> bool:
    items = sq_list(session_id)
    new_items = [i for i in items if i.get("id") != item_id]
    if len(new_items) == len(items):
        return False
    path = _sq_path(session_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(new_items))
    tmp.replace(path)
    return True


def sq_pop(session_id: str) -> dict | None:
    items = sq_list(session_id)
    if not items:
        return None
    item, rest = items[0], items[1:]
    path = _sq_path(session_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rest))
    tmp.replace(path)
    return item


def _read_summary(session_id: str) -> dict | None:
    p = _summary_path(session_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _write_summary(session_id: str, text: str, last_seq: int | None = None) -> None:
    data: dict = {
        "text": text,
        "generated_at": __import__("datetime").datetime.now().isoformat(),
    }
    if last_seq is not None:
        data["last_seq"] = last_seq
    _summary_path(session_id).write_text(json.dumps(data))


def _generate_summary_async(session_id: str, last_seq: int | None = None) -> None:
    """Generate a one-line summary using kiro-cli ACP (no tmux, no lock contention)."""
    meta = read_metadata(session_id)
    if not meta:
        return
    # Use the already-working last_output extractor instead of parsing JSONL
    # directly (which breaks on different session formats).
    snippet = get_last_output(session_id)[:1500].strip()
    if not snippet:
        # Fall back to transcript extraction
        if _is_crew_session(session_id):
            transcript = read_crew_transcript(session_id, after=-1, limit=60)
            messages = transcript.get("messages", [])
        else:
            raw_lines = tail_jsonl(session_id, lines=60)
            messages = []
            for seq, raw in enumerate(raw_lines):
                try:
                    entry = json.loads(raw)
                    t = _transcript_entry(seq, entry)
                    if t.get("role") in ("user", "assistant") and t.get("text"):
                        messages.append(t)
                except Exception:
                    continue
        tail = []
        for m in messages[-10:]:
            role = m.get("role", "other")
            text = (m.get("text") or "").strip()[:300]
            if not text:
                continue
            if role == "user":
                tail.append(f"User: {text}")
            elif role == "assistant":
                tail.append(f"Assistant: {text}")
        snippet = "\n".join(tail)
    if not snippet:
        return
    prompt = (
        f"Summarise what this agent session accomplished in ONE short sentence "
        f"(max 15 words). Reply with only the sentence, nothing else.\n\n{snippet}"
    )
    try:
        # Run as a separate process so the ACP kiro-cli subprocess cannot
        # block FastAPI's thread pool or the tmux polling loop.
        # Pass prompt via stdin to avoid command-line length/encoding issues.
        # Use python3 explicitly — inside a frozen PyInstaller bundle,
        # sys.executable points to the frozen binary, not the Python interpreter.
        script = Path(__file__).parent / "acp_query.py"
        if not getattr(sys, "frozen", False):
            python = sys.executable
        else:
            # Inside the frozen bundle sys.executable is the app binary.
            # system python3 is 3.9 on macOS and does not support X|Y union syntax.
            # Prefer python3.14 (Homebrew), fall back to python3 only if needed.
            import shutil as _shutil_py
            python = (
                _shutil_py.which("python3.14") or
                _shutil_py.which("python3.13") or
                _shutil_py.which("python3.12") or
                _shutil_py.which("python3.11") or
                "python3"
            )
        # When running inside the frozen app bundle, the script lives inside
        # backend/ which also contains collections.py (a Quarterdeck module).
        # System python adds the script's directory to sys.path, causing
        # collections.py to shadow stdlib and crash with a circular import.
        # Copy both acp_query.py and acp_session.py to /tmp so they run
        # outside that directory with no bundle path contamination.
        import shutil as _shutil, os as _os
        tmp_script = Path("/tmp/qd_acp_query.py")
        _shutil.copy2(script, tmp_script)
        _shutil.copy2(script.parent / "acp_session.py", Path("/tmp/acp_session.py"))
        run_script = tmp_script
        env = _os.environ.copy()
        env.pop("PYTHONPATH", None)  # strip any bundle-injected paths
        r = subprocess.run(
            [python, str(run_script)],
            input=prompt,
            capture_output=True, text=True, timeout=50,
            cwd=str(Path.home()),
            env=env,
        )
        text = r.stdout.strip().splitlines()[0][:120] if r.returncode == 0 and r.stdout.strip() else ""
        if text:
            _write_summary(session_id, text, last_seq=last_seq)
    except Exception:
        pass


@app.get("/api/sessions/{session_id}/summary")
def get_session_summary(session_id: str):
    """Return the cached one-line summary for a session, if available."""
    s = _read_summary(session_id)
    if not s:
        return {"summary": None}
    return {"summary": s.get("text"), "generated_at": s.get("generated_at")}


@app.get("/api/sessions/{session_id}/acp-events")
def get_acp_events(session_id: str):
    """Return ACP notification events accumulated by the observer side-channel.

    Returns ``{"attached": bool, "events": [...]}`` where each event is
    ``{"method": str, "params": dict}``.  Events are ordered oldest-first,
    capped at the last 200.  Returns an empty list for sessions that have
    no ACP observer attached (non-V3, unmanaged, or observer not yet started).
    """
    return {
        "attached": acp_observer.is_attached(session_id),
        "events": acp_observer.get_events(session_id),
        "capabilities": acp_observer.get_capabilities(session_id),
        "status": acp_observer.detect_status(session_id),
    }


@app.post("/api/sessions/{session_id}/summarize")
def trigger_summary(session_id: str, request: Request):
    """Kick off background summary generation (or return cached result)."""
    if not require_local(request):
        return {"error": "local only"}
    existing = _read_summary(session_id)
    if existing:
        return {"summary": existing.get("text"), "cached": True}
    # Check concierge is enabled
    settings = _load_settings()
    if settings.get("auto_summary_disabled", False):
        return {"summary": None, "reason": "auto summaries disabled"}
    threading.Thread(target=_generate_summary_async, args=(session_id,), daemon=True).start()
    return {"summary": None, "pending": True}


@app.post("/api/sessions/{session_id}/branch")
def branch_session(session_id: str):
    """Copy a session's conversation into a new session, started under tmux."""
    import shutil
    import uuid
    from datetime import datetime

    meta = read_metadata(session_id)
    if not meta:
        return {"error": "Session not found"}
    cwd = meta.get("cwd", "")
    if not cwd:
        return {"error": "No cwd for session"}

    new_id = str(uuid.uuid4())
    src_jsonl = SESSIONS_DIR / f"{session_id}.jsonl"
    if src_jsonl.exists():
        shutil.copy2(src_jsonl, SESSIONS_DIR / f"{new_id}.jsonl")

    new_meta = dict(meta)
    stamp = datetime.now().isoformat() + "Z"
    new_meta["session_id"] = new_id
    new_meta["created_at"] = stamp
    new_meta["updated_at"] = stamp
    new_meta["title"] = f"(branch) {meta.get('title', 'Untitled')}"
    (SESSIONS_DIR / f"{new_id}.json").write_text(json.dumps(new_meta))

    result = tmux.spawn(cwd, resume_id=new_id)
    if not result.get("ok"):
        return {"error": result.get("error", "spawn failed"), "new_id": new_id}
    return {"ok": True, "new_id": new_id, "attach": tmux.attach_command(new_id)}


@app.post("/api/sessions/{session_id}/branch-at")
def branch_at_turn(session_id: str, payload: dict):
    """Branch a session at a specific turn, creating a truncated copy.

    `after_seq` is the seq number of the last entry to keep. Everything after
    that line is dropped. The new session resumes from the truncation point,
    as if the subsequent turns never happened.

    Verified: kiro-cli tolerates a truncated JSONL on --resume-id. The file is
    read forward and missing trailing entries are simply absent from context.
    """
    import uuid
    from datetime import datetime

    after_seq = payload.get("after_seq")
    if after_seq is None or not isinstance(after_seq, int) or after_seq < 0:
        return {"error": "after_seq must be a non-negative integer"}

    meta = read_metadata(session_id)
    if not meta:
        return {"error": "Session not found"}
    cwd = meta.get("cwd", "")
    if not cwd:
        return {"error": "No cwd for session"}

    src_jsonl = SESSIONS_DIR / f"{session_id}.jsonl"
    if not src_jsonl.exists():
        return {"error": "No conversation history"}

    # Read lines up to and including after_seq
    kept_lines = []
    with open(src_jsonl, "r") as f:
        for seq, line in enumerate(f):
            if seq > after_seq:
                break
            kept_lines.append(line)

    if not kept_lines:
        return {"error": "No lines to keep at that seq"}

    # Validate: the last kept line should end a turn cleanly (AssistantMessage).
    # If after_seq lands mid-turn (on a ToolResults), include through the next
    # AssistantMessage to avoid a dangling tool call.
    try:
        last_kind = json.loads(kept_lines[-1]).get("kind", "")
    except (json.JSONDecodeError, IndexError):
        last_kind = ""

    if last_kind == "ToolResults":
        # Read one more line to close the turn
        with open(src_jsonl, "r") as f:
            for seq, line in enumerate(f):
                if seq == after_seq + 1:
                    kept_lines.append(line)
                    break

    new_id = str(uuid.uuid4())
    # Write truncated JSONL
    (SESSIONS_DIR / f"{new_id}.jsonl").write_text("".join(kept_lines))

    # Write metadata
    new_meta = dict(meta)
    stamp = datetime.now().isoformat() + "Z"
    new_meta["session_id"] = new_id
    new_meta["created_at"] = stamp
    new_meta["updated_at"] = stamp
    original_title = meta.get("title", "Untitled")
    new_meta["title"] = f"(branch @{after_seq}) {original_title}"
    # Record lineage
    new_meta["parent_id"] = session_id
    new_meta["branch_point"] = after_seq
    (SESSIONS_DIR / f"{new_id}.json").write_text(json.dumps(new_meta))

    # Spawn under tmux
    result = tmux.spawn(cwd, resume_id=new_id)
    if not result.get("ok"):
        return {"error": result.get("error", "spawn failed"), "new_id": new_id}
    return {
        "ok": True,
        "new_id": new_id,
        "lines_kept": len(kept_lines),
        "parent_id": session_id,
        "branch_point": after_seq,
        "attach": tmux.attach_command(new_id),
    }


@app.post("/api/sessions/{session_id}/resume")
def resume_session(session_id: str, payload: dict | None = None):
    """Start an archived session under tmux, continuing its conversation."""
    meta = read_metadata(session_id)
    if not meta:
        return {"error": "Session not found"}
    lock_data = read_lock(session_id)
    if lock_data and is_process_alive(lock_data.get("pid") or 0):
        return {"error": "Session is already running — use takeover instead"}
    cwd = meta.get("cwd", "")
    if not cwd:
        return {"error": "No cwd for session"}

    result = tmux.spawn(cwd, resume_id=session_id, **_spawn_kwargs(payload, session_id))
    if not result.get("ok"):
        return {"error": result.get("error", "spawn failed")}
    return {"ok": True, "id": session_id, "attach": tmux.attach_command(session_id)}


@app.post("/api/sessions/{session_id}/takeover")
def takeover_session(session_id: str, payload: dict | None = None):
    """Adopt a foreign session: kill its process, then re-spawn it under tmux.

    Killing first is what keeps two processes off one session id. The lock file
    disappearing is the signal that the old process is really gone, so the
    re-spawn waits for it rather than assuming.
    """
    meta = read_metadata(session_id)
    if not meta:
        return {"error": "Session not found"}
    cwd = meta.get("cwd", "")
    if not cwd:
        return {"error": "No cwd for session"}
    if tmux.is_managed(session_id):
        return {"error": "Session is already managed"}

    handoverable, reason = ownership.is_handoverable(session_id)
    if not handoverable:
        return {"error": reason}

    lock_data = read_lock(session_id)
    pid = lock_data.get("pid") if lock_data else None
    killed = False
    if pid and is_process_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            killed = True
        except (ProcessLookupError, PermissionError) as e:
            return {"error": f"Could not signal pid {pid}: {e}"}
        deadline = time.time() + 5
        while is_process_alive(pid) and time.time() < deadline:
            time.sleep(0.1)
        if is_process_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    # The old process removes its lock on exit; a leftover lock would make the
    # resumed session look foreign again.
    deadline = time.time() + 5
    lock_path = SESSIONS_DIR / f"{session_id}.lock"
    while lock_path.exists() and time.time() < deadline:
        time.sleep(0.1)
    if lock_path.exists():
        try:
            lock_path.unlink()
        except OSError:
            return {"error": "Old process still holds the session lock"}

    result = tmux.spawn(cwd, resume_id=session_id, **_spawn_kwargs(payload, session_id))
    if not result.get("ok"):
        return {"error": result.get("error", "spawn failed"), "killed_pid": pid}
    return {
        "ok": True, "id": session_id, "killed_pid": pid if killed else None,
        "attach": tmux.attach_command(session_id),
    }


@app.post("/api/sessions/{session_id}/release")
def release_session(session_id: str):
    """Set released=True on the ownership sidecar, restoring handoverability.

    This is the only sanctioned path from machine-owned back to human-owned.
    A session with no sidecar is already human-owned, so this is a no-op.
    """
    ownership.release_sidecar(session_id)
    return {"ok": True, "session_id": session_id, **_ownership_fields(session_id)}


@app.get("/api/sessions/{session_id}/ownership")
def get_session_ownership(session_id: str):
    """Return the ownership record for a session."""
    return ownership.get_ownership(session_id)


@app.put("/api/sessions/{session_id}/ownership")
def set_session_ownership(session_id: str, payload: dict):
    """Write or update the ownership sidecar for a session.

    Only Quarterdeck-internal callers (dispatch, SuperChat) should use this.
    The sidecar must be written before the session's first prompt.
    """
    allowed = {"owner", "role", "group_id", "handoverable", "visible", "released"}
    data = {k: v for k, v in payload.items() if k in allowed}
    if not data:
        return {"error": "No recognised ownership fields provided"}
    ownership.write_sidecar(session_id, data)
    return {"ok": True, "session_id": session_id, **_ownership_fields(session_id)}


@app.get("/api/sessions/{session_id}/delivery")
def get_delivery(session_id: str):
    """Return steering delivery records for a session.

    Includes static inference (which steering files should have been delivered
    based on agent config) and any probe echo observations recorded manually.
    """
    return delivery.get_session_delivery(session_id)


@app.post("/api/sessions/{session_id}/delivery/probe")
def record_probe(session_id: str, payload: dict):
    """Record the result of a manual probe echo test.

    Body: {mode, token, delivered, agent?, scope?}
    delivered=true means the model echoed the token back.
    """
    mode = payload.get("mode", "")
    token = payload.get("token", "")
    delivered = bool(payload.get("delivered", False))
    if not mode or not token:
        return {"error": "mode and token are required"}
    agent = payload.get("agent", "")
    scope = payload.get("scope", "workspace")
    rec = delivery.record_probe_observation(session_id, mode, token, delivered, agent, scope)
    return {"ok": True, **rec}


@app.post("/api/sessions/{session_id}/corrections")
def add_correction(session_id: str, payload: dict):
    """Record a correction on one keypress.

    Pulls last_message_seq and rules_in_context automatically from session state.
    Optional body fields: group_id, owner, note.
    """
    # Get latest message seq from the messages API
    last_seq = None
    assistant_message = ""
    try:
        result = get_messages(session_id, after=-1, limit=1)
        msgs = result.get("messages", []) if isinstance(result, dict) else []
        if msgs:
            last_msg = msgs[-1]
            last_seq = last_msg.get("seq")
            # Capture the text if it's an assistant message
            if last_msg.get("role") == "assistant":
                assistant_message = (last_msg.get("text") or "")[:2000]
            else:
                # Last message was a tool/user — look back for the most recent assistant text.
                # after= is exclusive, so after=last_seq-50 returns up to 50 msgs ending at last_seq.
                # Use -1 when last_seq is small so we don't skip the first message.
                try:
                    lookback_after = last_seq - 50 if last_seq is not None and last_seq >= 50 else -1
                    lookback = get_messages(session_id, after=lookback_after, limit=50)
                    for m in reversed(lookback.get("messages", [])):
                        if m.get("role") == "assistant" and m.get("text"):
                            assistant_message = m["text"][:2000]
                            break
                except Exception:
                    pass
    except Exception:
        pass

    # Get rules in context from delivery records
    delivery_data = delivery.get_session_delivery(session_id)
    rules = delivery_data.get("expected_files", [])

    # Get steering tree git hash
    steering_commit = None
    try:
        import subprocess
        r = subprocess.run(
            ["git", "-C", str(Path.home() / ".kiro" / "steering"), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3
        )
        if r.returncode == 0:
            steering_commit = r.stdout.strip()
    except Exception:
        pass

    # Ownership info
    owner_rec = ownership.get_ownership(session_id)
    group_id = payload.get("group_id") or (owner_rec.get("group_id") if owner_rec else None)
    owner = payload.get("owner") or (owner_rec.get("owner") if owner_rec else None)

    rec = corrections.record_correction(
        session_id=session_id,
        group_id=group_id,
        owner=owner,
        last_message_seq=last_seq,
        steering_commit=steering_commit,
        rules_in_context=rules,
        assistant_message=assistant_message,
    )
    return {"ok": True, **rec}


@app.get("/api/corrections")
def get_all_corrections_endpoint(limit: int = 200):
    """Return all corrections across sessions for the dashboard."""
    return {"corrections": corrections.get_all_corrections(limit=limit)}


@app.post("/api/sessions/{session_id}/unverified-claim")
def add_unverified_claim(session_id: str, payload: dict):
    """Record a machine-detected unverified claim from the stop hook.

    Body fields (all optional):
      claim_text:       the sentence that triggered keyword detection
      observed_tools:   list of tool names run after the last user message ([] = none)
      last_message_seq: message sequence at detection time
    """
    # Resolve delivery rules for context
    delivery_data = delivery.get_session_delivery(session_id)
    rules = delivery_data.get("expected_files", [])

    # Ownership info
    owner_rec = ownership.get_ownership(session_id)
    group_id = payload.get("group_id") or (owner_rec.get("group_id") if owner_rec else None)

    rec = corrections.record_unverified_claim(
        session_id=session_id,
        claim_text=payload.get("claim_text", ""),
        observed_tools=payload.get("observed_tools", []),
        last_message_seq=payload.get("last_message_seq"),
        group_id=group_id,
        rules_in_context=rules,
    )
    return {"ok": True, **rec}


@app.patch("/api/corrections/{correction_id}")
def update_correction(correction_id: str, payload: dict):
    """Confirm or withdraw a correction.

    Body: {status: "confirmed" | "withdrawn" | "open", note?: "..."}
    """
    status = payload.get("status", "")
    note = payload.get("note", "")
    if status not in ("open", "confirmed", "withdrawn"):
        return {"error": "status must be confirmed, withdrawn, or open"}
    updated = corrections.update_correction(correction_id, status, note)
    if updated is None:
        return {"error": "correction not found"}
    return {"ok": True, **updated}


@app.get("/api/sessions/{session_id}/corrections")
def get_corrections(session_id: str):
    """Return all corrections for a session."""
    return {"corrections": corrections.get_session_corrections(session_id)}


@app.get("/api/corrections/summary")
def corrections_summary():
    """Per-session confirmed correction counts."""
    return corrections.get_correction_summary()


# ---------------------------------------------------------------------------
# Task 7: Duration endpoints
# ---------------------------------------------------------------------------

@app.get("/api/stats/duration")
def get_duration_stats(type_tag: str = "", project: str = ""):
    """Calibrated p50/p90 duration estimate for a type+project combination."""
    return duration.estimate(project, type_tag)


@app.get("/api/sessions/{session_id}/duration")
def get_session_duration(session_id: str):
    """Return the task record for a session, or null if not yet recorded."""
    rec = duration.get_record(session_id)
    if rec is None:
        return {"ok": True, "record": None}
    return {"ok": True, "record": rec}


@app.post("/api/sessions/{session_id}/duration/type-tag")
def set_session_type_tag(session_id: str, payload: dict):
    """Let the user correct the auto-classified type tag."""
    tag = (payload.get("tag") or "").strip()
    if tag not in duration.VALID_TAGS:
        return {"error": f"invalid tag; must be one of {sorted(duration.VALID_TAGS)}"}
    ok = duration.update_type_tag(session_id, tag)
    if not ok:
        return {"error": "no duration record for this session yet"}
    return {"ok": True, "tag": tag}


def _can_read_files(session_id: str) -> bool:
    """True when the session can read a paste file without a tool-approval prompt.

    False when preToolUse gating is active (GATES_DIR/<id> exists).
    A managed session with DEFAULT_TRUST_TOOLS="fs_read" will read silently;
    a gated or ACP-only session needs the content inlined instead.
    """
    gate_file = GATES_DIR / session_id
    return not gate_file.exists()


@app.post("/api/sessions/{session_id}/input")
def send_input(session_id: str, payload: dict):
    """Type text into a managed session and submit it.

    Optional attachments: [{session_id, name}] — paste files to prepend as
    reference lines (if the session can read files) or inline content (gated).
    For V3 sessions with an ACP observer, routes via ACP session/prompt.
    Falls back to tmux on any error.
    """
    text = payload.get("text", payload.get("task", "")) or ""
    attachments = payload.get("attachments") or []

    # Build the full prompt: attachment references + typed text
    parts = []
    for att in attachments:
        att_sid = att.get("session_id") or "_unassigned"
        att_name = att.get("name", "")
        if not att_name:
            continue
        try:
            if _can_read_files(session_id):
                # send a one-line file reference the agent reads via fs_read
                att_meta_lines = att.get("lines", 0)
                att_meta_size = att.get("size_display", "")
                ref = paste_store.reference_line(att_sid, att_name, att_meta_lines, att_meta_size)
                parts.append(ref)
            else:
                # gated session: inline the content with newlines preserved
                content = paste_store.read(att_sid, att_name)
                parts.append(content)
        except (FileNotFoundError, ValueError):
            pass  # stale attachment — skip silently

    if text.strip():
        parts.append(text)

    if not parts:
        return {"error": "No text provided"}

    # For inline/gated delivery, use newlines-preserved path via tmux load-buffer
    # (tmux_manager already handles >1024 chars with bracketed-paste).
    # For reference-line delivery (short single line), flatten normally.
    has_inline = any(
        not _can_read_files(session_id) and (att.get("name")) for att in attachments
    )
    if has_inline:
        full_text = "\n\n".join(parts)
    else:
        full_text = " ".join(p.strip() for p in parts if p.strip())
        full_text = " ".join(full_text.split())  # collapse internal whitespace

    flat = full_text

    # ACP-observed sessions (V3 foreign) bypass the tmux is_managed gate —
    # they communicate directly via the ACP session/prompt channel.
    if acp_observer.is_attached(session_id):
        try:
            if flat.startswith("/"):
                if acp_observer.execute_command(session_id, flat):
                    return {"ok": True, "sent": flat[:200], "via": "acp-cmd"}
            else:
                acp_observer.send_prompt(session_id, flat)
                return {"ok": True, "sent": flat[:200], "via": "acp"}
        except Exception:
            pass  # fall through to tmux

    if not tmux.is_managed(session_id):
        return {"error": "Session is not managed — take it over first"}

    result = tmux.send_text(session_id, flat)
    if not result.get("ok"):
        return {"error": result.get("error", "send failed")}
    return {"ok": True, "sent": flat[:200]}


@app.post("/api/sessions/{session_id}/send")
def send_to_session(session_id: str, payload: dict):
    """Deprecated alias for /input, kept for the existing frontend."""
    return send_input(session_id, payload)


RESPOND_KEYS = {"Enter", "Escape", "C-c", "C-x", "Up", "Down", "Tab", "y", "n", "t", "DC"}

# The permission menu opens with the first entry selected, so each answer is
# "move down N times, then select".
PROMPT_CHOICES = {
    "allow": ["Enter"],                    # Yes, single permission
    "trust": ["Down", "Enter"],            # Trust, always allow in this session
    "deny": ["Down", "Down", "Enter"],     # No
    "dismiss": ["Escape"],
}


@app.post("/api/sessions/{session_id}/respond")
def respond_to_prompt(session_id: str, payload: dict):
    """Answer a pending permission prompt.

    Accepts a named choice (allow / trust / deny / dismiss) which expands to the
    menu navigation kiro-cli expects, or raw `keys` for anything else.
    """
    # ACP-observed V3 sessions bypass the tmux gate — approval keys sent via tmux
    # if the session also has a pane, otherwise returns an error noting ACP-only.
    is_acp = acp_observer.is_attached(session_id)
    if not is_acp and not tmux.is_managed(session_id):
        return {"error": "Session is not managed — take it over first"}

    choice = str(payload.get("choice", "")).strip()
    keys = payload.get("keys")
    if choice in PROMPT_CHOICES:
        keys = PROMPT_CHOICES[choice]
    elif choice:
        keys = [choice]
    if not isinstance(keys, list) or not keys:
        return {"error": f"choice must be one of {sorted(PROMPT_CHOICES)}, or pass keys[]"}
    bad = [k for k in keys if k not in RESPOND_KEYS]
    if bad:
        return {"error": f"unsupported keys: {bad}"}

    for key in keys:
        if not tmux.is_managed(session_id):
            # ACP-only session: no tmux pane to send keys to
            return {"error": "No tmux pane for this session — approval keys require a managed pane"}
        result = tmux.send_key(session_id, key)
        if not result.get("ok"):
            return {"error": result.get("error", "send failed")}
        time.sleep(0.12)  # let the TUI redraw between navigation keys
    return {"ok": True, "keys": keys}


@app.get("/api/sessions/{session_id}/pane")
def get_pane(session_id: str, lines: int = CAPTURE_LINES):
    """Raw tmux pane tail — what the TUI is actually showing, plus scrollback."""
    if not tmux.is_managed(session_id):
        return {"managed": False, "pane": "", "awaiting_prompt": False}
    # The count comes from a browser, and tmux will happily be asked for a
    # million lines. Bound it: past the scrollback limit there is nothing more
    # to return anyway, only a larger response to build.
    lines = max(1, min(int(lines), MAX_CAPTURE_LINES))
    pane = tmux.capture(session_id, lines)
    size = tmux.geometry(session_id)
    return {"managed": True, "pane": pane,
            "cols": size[0] if size else 0, "rows": size[1] if size else 0,
            "awaiting_prompt": pane_awaiting_approval(pane)}


@app.post("/api/sessions/{session_id}/resize")
def resize_session(session_id: str, payload: dict):
    """Match the session's tmux geometry to the viewer's pane.

    A detached session keeps the size it was created with, so the TUI would
    otherwise render a small fixed frame no matter how big the window is.
    """
    try:
        cols = int(payload.get("cols", 0))
        rows = int(payload.get("rows", 0))
    except (TypeError, ValueError):
        return {"ok": False, "error": "cols and rows must be numbers"}
    if cols <= 0 or rows <= 0:
        return {"ok": False, "error": "cols and rows must be positive"}
    return tmux.resize(session_id, cols, rows)


@app.post("/api/pending/{nonce}/cancel")
def cancel_pending_spawn(nonce: str):
    """Abandon a spawn that never reported a session id.

    Kills its tmux session if one is still there. Separate from
    `/api/sessions/{id}/kill` because a pending spawn has no session id yet,
    which is exactly why it was previously impossible to clear from the UI.
    """
    return tmux.cancel_pending(nonce)


@app.get("/api/managed")
def get_managed():
    """tmux/session reconciliation state, for debugging."""
    return {
        "tmux_available": tmux.tmux_available(),
        "sessions": tmux.managed_sessions(),
        "pending": tmux.load_state()["pending"],
    }


def remembered_agent(session_id: str) -> str:
    """The agent a session was started under, if Quarterdeck spawned it.

    kiro-cli does not record the agent in session metadata, so this is the only
    source. Resuming or handing off without it would silently run the session
    under a different agent than it began with.
    """
    record = tmux.managed_sessions().get(session_id) or {}
    agent = record.get("agent", "")
    return agent if isinstance(agent, str) else ""


def _spawn_kwargs(payload: dict | None, session_id: str = "") -> dict:
    """Map an optional request body onto tmux.spawn's optional arguments.

    `session_id` lets a resume or takeover inherit the agent the session was
    first spawned with, unless the caller names a different one.
    """
    payload = payload or {}
    kwargs = {}
    if session_id and not payload.get("agent"):
        inherited = remembered_agent(session_id)
        if inherited:
            kwargs["agent"] = inherited
    if payload.get("trust_all"):
        kwargs["trust_all"] = True
    elif "trust_tools" in payload:
        kwargs["trust_tools"] = payload["trust_tools"] or None
    # Stored defaults fill in what the caller left out. Read once, and only when
    # something is actually missing, so the common fully-specified dispatch does
    # not pay for a file read.
    stored = _load_settings() if not (payload.get("model") and payload.get("effort")) else {}
    model = payload.get("model") or stored.get("dispatch-model") or ""
    effort = payload.get("effort") or stored.get("dispatch-effort") or ""
    engine = payload.get("engine") or stored.get("dispatch-engine") or ""
    if model:
        kwargs["model"] = model
    if effort:
        kwargs["effort"] = effort
    if engine and engine in ("v1", "v2", "v3"):
        kwargs["engine"] = engine
    if payload.get("agent"):
        kwargs["agent"] = str(payload["agent"])
    # Only wait for the hook's answer if the agent actually carries it. Without
    # this the grace period would be spent on every spawn, waiting for something
    # that is never going to arrive.
    kwargs["expect_hook"] = agent_has_spawn_hook(
        kwargs.get("agent", ""), payload.get("cwd", ""))
    # Shell prelude for a new session. This is arbitrary command execution by
    # design — the same trust boundary as the agent itself, which can already run
    # commands — so it stays behind the API token like everything else.
    if payload.get("pre_command"):
        kwargs["pre_command"] = str(payload["pre_command"])
    return kwargs


def _as_string(value: str) -> str:
    """Escape a Python string for embedding in an AppleScript literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _launch_in_terminal(terminal: str, command: str, cwd: str) -> dict:
    """Open `command` in a new window/tab of the chosen terminal app."""
    if terminal in ("terminal", "iterm"):
        literal = _as_string(command)
        if terminal == "terminal":
            script = (
                'tell application "Terminal"\n'
                "  activate\n"
                f'  do script "{literal}"\n'
                "end tell"
            )
        else:
            script = (
                'tell application "iTerm"\n'
                "  activate\n"
                "  set w to (create window with default profile)\n"
                f'  tell current session of w to write text "{literal}"\n'
                "end tell"
            )
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=20
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip() or "osascript failed"}
        return {"ok": True, "ran_command": True}

    if terminal == "ghostty":
        # On macOS the Ghostty binary cannot launch the GUI directly; `open -na`
        # with --args is the documented route.
        result = subprocess.run(
            ["open", "-na", "Ghostty.app", "--args", "-e", command],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip() or "open failed"}
        return {"ok": True, "ran_command": True}

    if terminal == "warp":
        subprocess.run(
            ["open", f"warp://action/new_tab?path={quote(cwd)}"],
            capture_output=True, timeout=20,
        )
        try:
            subprocess.run(["pbcopy"], input=command, text=True, timeout=5)
            copied = True
        except (OSError, subprocess.TimeoutExpired):
            copied = False
        return {"ok": True, "ran_command": False, "clipboard": copied}

    return {"ok": False, "error": f"Unknown terminal: {terminal}"}


@app.post("/api/sessions/{session_id}/handoff")
def handoff_session(session_id: str, payload: dict | None = None):
    """Hand a managed session back to a real terminal.

    Quits our tmux session cleanly first, then opens the terminal running
    `cd <cwd> && kiro-cli chat --resume-id <id>`. The quit has to come first:
    two kiro-cli processes on one session id would fight over its files.

    Afterwards the session is no longer ours, so it shows up as foreign.
    """
    payload = payload or {}
    terminal = payload.get("terminal", "terminal")
    if terminal not in TERMINALS:
        return {"error": f"terminal must be one of {sorted(TERMINALS)}"}

    meta = read_metadata(session_id)
    if not meta:
        return {"error": "Session not found"}
    cwd = meta.get("cwd", "")
    if not cwd or not Path(cwd).is_dir():
        return {"error": f"Session directory not available: {cwd or '(none)'}"}

    # Read the agent while we still own the session — killing it drops the
    # record that remembers which agent it was started under.
    agent = remembered_agent(session_id)

    quit_mode = "not-managed"
    if tmux.is_managed(session_id):
        result = tmux.kill(session_id, graceful=True)
        if not result.get("ok"):
            return {"error": result.get("error", "Could not release the tmux session")}
        quit_mode = result.get("mode", "kill")

    # Wait for the lock to clear so the resumed process starts unobstructed.
    lock_path = SESSIONS_DIR / f"{session_id}.lock"
    deadline = time.time() + 5
    while lock_path.exists() and time.time() < deadline:
        time.sleep(0.1)

    # Carry the agent across, quoted — it lands in a shell command line.
    agent_flag = f" --agent {shlex.quote(agent)}" if agent else ""
    command = (f"cd {shlex.quote(cwd)} && kiro-cli chat"
               f"{agent_flag} --resume-id {shlex.quote(session_id)}")
    launched = _launch_in_terminal(terminal, command, cwd)
    if not launched.get("ok"):
        return {"error": launched.get("error", "Could not open the terminal"),
                "quit_mode": quit_mode, "command": command}
    return {
        "ok": True, "terminal": terminal, "quit_mode": quit_mode,
        "ran_command": launched.get("ran_command", False),
        "clipboard": launched.get("clipboard", False),
        "command": command,
    }


def finder_folder() -> str:
    """POSIX path of the frontmost Finder window, or "" if there isn't one.

    Convenience only — used to default a new session's directory. Unlike the
    Warp control path this replaced, nothing depends on it: every failure mode
    (Finder not running, no window open, automation permission denied,
    osascript missing) falls through to "" and the caller uses $HOME.
    """
    script = '''
    tell application "System Events"
        if not (exists process "Finder") then return ""
    end tell
    tell application "Finder"
        if (count of Finder windows) is 0 then return ""
        return POSIX path of (target of front Finder window as alias)
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    path = result.stdout.strip().rstrip("/")
    return path if path and Path(path).is_dir() else ""


@app.get("/api/cwd-suggestion")
def cwd_suggestion():
    """Directory a new session would use if the user picks none.

    Controlled by the 'dispatch-cwd-mode' setting:
      'auto'   (default) — frontmost Finder window
      'last'   — cwd of the most recently active or modified session
      'fixed'  — the path stored in 'dispatch-cwd-fixed'
    """
    settings = _load_settings()
    mode = settings.get("dispatch-cwd-mode", "auto")

    if mode == "fixed":
        fixed = settings.get("dispatch-cwd-fixed", "")
        if fixed and Path(fixed).is_dir():
            return {"path": fixed, "source": "fixed"}
        # Fixed path missing or gone — fall through to auto
        mode = "auto"

    if mode == "last":
        # Most recently modified session .json that has a real cwd
        json_files = sorted(
            SESSIONS_DIR.glob("*.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        for jf in json_files[:20]:
            try:
                meta = json.loads(jf.read_text())
                raw_cwd = meta.get("cwd", "")
                if raw_cwd and Path(raw_cwd).is_dir() and raw_cwd != str(Path.home()):
                    return {"path": raw_cwd, "source": "last"}
            except Exception:
                continue
        # No usable last session — fall through to auto
        mode = "auto"

    # auto (default)
    path = finder_folder()
    return {
        "path": path or str(Path.home()),
        "source": "finder" if path else "home",
    }


def default_agent_name(cwd: str = "") -> str:
    """The agent kiro-cli would use if `--agent` were not passed.

    kiro-cli checks the workspace for a local kiro_default before falling back
    to the global settings default — mirror that resolution here so the
    Launcher's dropdown shows the right label.
    """
    if cwd:
        local_default = Path(cwd).expanduser() / WORKSPACE_AGENTS_SUBDIR / "kiro_default.json"
        if local_default.exists():
            return "kiro_default"
    try:
        settings = json.loads(KIRO_CLI_SETTINGS.read_text())
    except (json.JSONDecodeError, OSError):
        return ""
    value = settings.get(DEFAULT_AGENT_KEY, "")
    return value if isinstance(value, str) else ""


def _read_agent_config(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def list_agents(cwd: str = "") -> list[dict]:
    """Agents that `--agent` will accept, discovered from disk.

    Global agents live in ~/.kiro/agents; a directory can also carry its own in
    .kiro/agents, and a workspace agent of the same name wins — so the same
    shadowing kiro-cli applies is applied here. Built-ins have no file at all.

    `hooks_possible` says whether the agent could carry Quarterdeck's hooks. A built-in
    cannot, because there is nothing on disk to add them to; see
    docs/ROADMAP.md section 4.
    """
    found: dict[str, dict] = {}
    for name in BUILTIN_AGENTS:
        found[name] = {
            "name": name, "description": "", "source": "builtin",
            "path": "", "hooks_possible": False, "has_hooks": False,
        }

    directories = [(AGENTS_DIR, "global")]
    if cwd:
        workspace_dir = Path(cwd).expanduser() / WORKSPACE_AGENTS_SUBDIR
        # Workspace second: it shadows a global agent of the same name.
        directories.append((workspace_dir, "workspace"))

    for directory, source in directories:
        if not directory.is_dir():
            continue
        for config_path in sorted(directory.glob("*.json")):
            config = _read_agent_config(config_path)
            if config is None:
                continue
            name = config.get("name") or config_path.stem
            if not isinstance(name, str) or not name:
                continue
            hooks = config.get("hooks")
            found[name] = {
                "name": name,
                "description": (config.get("description") or "")[:200],
                "source": source,
                "path": str(config_path),
                "hooks_possible": True,
                "has_hooks": bool(isinstance(hooks, dict) and hooks),
                "deck_hook": _has_deck_hook(config),
                # Per hook, not only "all three present". A config written before
                # a hook was added carries some and not others, and "installed:
                # no" with no way to see which part is missing is the kind of
                # answer that sends people to read JSON by hand.
                "deck_hooks": _deck_hooks_present(config),
                # Present but running an older command. Reported separately
                # because the fix is different: missing needs an install,
                # stale needs a re-install, and "installed" for both is a lie.
                "deck_hooks_stale": _deck_hooks_stale(config),
            }

    agents = sorted(found.values(), key=lambda a: (a["source"] == "builtin", a["name"].lower()))
    default = default_agent_name(cwd)
    for agent in agents:
        agent["is_default"] = agent["name"] == default
    return agents


@app.get("/api/agents")
def get_agents(cwd: str = ""):
    """Selectable agents, and which one a spawn without `--agent` would use."""
    return {"agents": list_agents(cwd), "default": default_agent_name(cwd)}


# --- the agentSpawn hook, installed into the user's agent configs ---
#
# These are the user's files, and some belong to other tools entirely (the
# default agent here is a bridge for CMUX). So: add one entry, never replace the
# array; back the file up before the first write; make install idempotent; and
# make uninstall remove exactly what was added and nothing else.

# Which hooks Quarterdeck installs, as (event, command, marker, timeout_ms).
#
# preToolUse is here despite blocking on a human, because it is opt-in per
# session: without a gate file for the session it returns at once, so installing
# it costs a hooked session nothing until someone asks for gating. Its timeout
# is its own — the 5s the other two use would kill it while the phone was still
# in a pocket.
DECK_HOOKS = (
    ("agentSpawn", tmux.SPAWN_HOOK_COMMAND, tmux.HOOK_MARKER, 5000),
    ("stop", tmux.STOP_HOOK_COMMAND, tmux.STOP_HOOK_MARKER, 5000),
    ("preToolUse", tmux.PRETOOL_HOOK_COMMAND, tmux.PRETOOL_HOOK_MARKER,
     tmux.PRETOOL_HOOK_TIMEOUT_MS),
    # postToolUse carries `tool_response` as well as the input — measured against
    # kiro-cli 2.14.2, since the docs do not say and the roadmap's table had no
    # row for this event. That makes the audit trail a side effect of a hook
    # already being installed rather than a feature of its own.
    ("postToolUse", audit.HOOK_COMMAND, audit.HOOK_MARKER, audit.HOOK_TIMEOUT_MS),
)
DECK_MARKERS = {marker for _, _, marker, _ in DECK_HOOKS}

# What each one buys, in the terms the settings pane has to explain it in.
HOOK_PURPOSE = {
    "agentSpawn": "kiro-cli reports the session id it chose, so Quarterdeck can check "
                  "the one it inferred",
    "stop": "an exact end-of-turn, instead of guessing from file timestamps",
    "preToolUse": "lets a session be gated: every tool call held until you "
                  "allow it. Does nothing until you switch gating on for a "
                  "session",
    "postToolUse": "records every tool call and how it went, so there is an "
                   "answer to what a session did while you were not watching",
}


def _hooks_for(config: dict, event: str) -> list:
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return []
    entries = hooks.get(event)
    return entries if isinstance(entries, list) else []


def _deck_entry(config: dict, event: str, marker: str) -> dict | None:
    """Quarterdeck's own entry for one event, if this config carries one."""
    return next(
        (e for e in _hooks_for(config, event)
         if isinstance(e, dict) and e.get("deck") == marker),
        None,
    )


def _deck_hooks_present(config: dict) -> list[str]:
    """Events this config carries a Quarterdeck entry for, current or not."""
    return [event for event, _, marker, _ in DECK_HOOKS
            if _deck_entry(config, event, marker) is not None]


def _deck_hooks_stale(config: dict) -> list[str]:
    """Events whose Quarterdeck entry is present but runs an out-of-date command.

    Matching on the marker alone is not enough to call a hook installed. When a
    hook's command changes — as the approval hook's poll interval just did — the
    old entry still carries the marker, so the pane would report it installed
    while the session ran the previous version. That is the failure this repo
    keeps hitting from the other side: the roadmap ticks something off and the
    machine does not do it. `_install_into` already patches a drifted command in
    place, so a stale entry only needs saying out loud to be fixable.
    """
    stale = []
    for event, command, marker, timeout_ms in DECK_HOOKS:
        entry = _deck_entry(config, event, marker)
        if entry is None:
            continue
        if entry.get("command") != command or entry.get("timeout_ms") != timeout_ms:
            stale.append(event)
    return stale


def _has_deck_hook(config: dict) -> bool:
    """True once every hook Quarterdeck installs is present *and current*.

    A partial install is not installed, or a later addition would never be
    applied to older configs — and neither is an install running last version's
    command.
    """
    return (len(_deck_hooks_present(config)) == len(DECK_HOOKS)
            and not _deck_hooks_stale(config))


def agent_has_spawn_hook(agent: str = "", cwd: str = "") -> bool:
    """Whether the agent a spawn will use carries Quarterdeck's spawn hook.

    An empty name means kiro-cli's default agent, which is what a spawn without
    `--agent` gets.
    """
    wanted = agent or default_agent_name()
    if not wanted:
        return False
    return any(a["name"] == wanted and a.get("deck_hook") for a in list_agents(cwd))


def agent_has_pretool_hook(session_id: str) -> bool:
    """Whether this session runs under an agent carrying the approval hook.

    Quarterdeck installs all three hooks together, so this is the same question as the
    spawn hook — asked about the agent the session was started with. A session
    Quarterdeck did not start has no remembered agent, so the default agent is the best
    available guess, and a wrong guess here only mislabels a hint in the UI.
    """
    return agent_has_spawn_hook(remembered_agent(session_id))


def _agent_config_paths(cwd: str = "") -> list[Path]:
    return [Path(a["path"]) for a in list_agents(cwd) if a.get("path")]


def _install_into(path: Path) -> str:
    """Add or update the hook in one agent config. Returns what happened."""
    config = _read_agent_config(path)
    if config is None:
        return "unreadable"
    backup = path.with_suffix(path.suffix + ".deck-backup")
    if not backup.exists():
        try:
            backup.write_text(path.read_text())
        except OSError:
            return "backup-failed"
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return "unexpected-hooks-shape"
    changed = False
    for event, command, marker, timeout_ms in DECK_HOOKS:
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            return "unexpected-hooks-shape"
        existing = next(
            (i for i, e in enumerate(entries)
             if isinstance(e, dict) and e.get("deck") == marker),
            None,
        )
        if existing is None:
            entries.append({"command": command, "deck": marker,
                            "timeout_ms": timeout_ms})
            changed = True
        elif (entries[existing].get("command") != command
              or entries[existing].get("timeout_ms") != timeout_ms):
            # Command or timeout was updated — patch in place so re-install
            # refreshes it rather than leaving an older entry that still matches
            # by marker and so is never touched again.
            entries[existing]["command"] = command
            entries[existing]["timeout_ms"] = timeout_ms
            changed = True
    if not changed:
        return "already-present"
    try:
        _atomic_write_json(path, config)
    except OSError:
        return "write-failed"
    return "installed"


def _uninstall_from(path: Path) -> str:
    config = _read_agent_config(path)
    if config is None:
        return "unreadable"
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return "not-present"
    # Remove by marker, over every event Quarterdeck has ever installed into — not only
    # the ones it installs today, or an older config would keep a stale entry.
    removed_any = False
    for event in {e for e, _, _, _ in DECK_HOOKS} | set(hooks):
        entries = _hooks_for(config, event)
        kept = [e for e in entries
                if not (isinstance(e, dict) and e.get("deck") in DECK_MARKERS)]
        if len(kept) == len(entries):
            continue
        removed_any = True
        if kept:
            hooks[event] = kept
        else:
            # Leave no empty scaffolding behind in someone else's file.
            hooks.pop(event, None)
    if not removed_any:
        return "not-present"
    if not hooks:
        config.pop("hooks", None)
    try:
        _atomic_write_json(path, config)
    except OSError:
        return "write-failed"
    # The file is back to the state the backup holds, so keeping it is clutter in
    # a directory that belongs to kiro-cli.
    backup = path.with_suffix(path.suffix + ".deck-backup")
    try:
        backup.unlink()
    except OSError:
        pass
    return "removed"


@app.get("/api/hooks/status")
def hooks_status(cwd: str = ""):
    """Where the spawn hook is installed, and whether it is being used.

    `correlated_via` on each managed session records which route found its id,
    so this can report whether the hook actually works here rather than only
    whether it is configured.
    """
    agents = list_agents(cwd)
    installed = [a["name"] for a in agents if a.get("deck_hook")]
    eligible = [a["name"] for a in agents if a["hooks_possible"]]
    routes: dict[str, int] = {}
    for record in tmux.managed_sessions().values():
        routes[record.get("correlated_via") or "unknown"] = (
            routes.get(record.get("correlated_via") or "unknown", 0) + 1)
    return {
        "installed": installed,
        "eligible": eligible,
        "missing": [n for n in eligible if n not in installed],
        # Carrying a Quarterdeck entry whose command has since changed. These need the
        # same re-install as a missing one, but they look installed from the
        # outside, so they have to be named or nobody will run it.
        "stale": [a["name"] for a in agents if a.get("deck_hooks_stale")],
        # What each hook is for, and how far it has actually got — the pane
        # cannot explain "0 of 15" without this, and the approval gate in the
        # detail panel does nothing until `preToolUse` is in the agent's config.
        "hooks": [
            {"event": event,
             "purpose": HOOK_PURPOSE.get(event, ""),
             "installed": [a["name"] for a in agents
                           if event in (a.get("deck_hooks") or [])],
             "stale": [a["name"] for a in agents
                       if event in (a.get("deck_hooks_stale") or [])]}
            for event, _, _, _ in DECK_HOOKS
        ],
        # Built-ins ship inside kiro-cli and have no file to add hooks to, so a
        # session started on one always falls back to the process-tree walk.
        "cannot_hook": [a["name"] for a in agents if not a["hooks_possible"]],
        "correlated_via": routes,
        "command": tmux.SPAWN_HOOK_COMMAND,
    }


@app.post("/api/hooks/install")
def hooks_install(payload: dict | None = None, request: Request = None):  # type: ignore[assignment]
    """Add the spawn hook to agent configs. Local callers only.

    Writing to the user's kiro-cli configuration is a local, GUI-adjacent act
    with no meaning from another device, so it is loopback-only like the folder
    endpoints.
    """
    if request is not None and not require_local(request):
        return {"error": "local only"}
    payload = payload or {}
    cwd = payload.get("cwd", "")
    only = payload.get("agents")
    results = {}
    for agent in list_agents(cwd):
        if not agent.get("path"):
            continue
        if only and agent["name"] not in only:
            continue
        results[agent["name"]] = _install_into(Path(agent["path"]))
    return {"ok": True, "results": results,
            "installed": sorted(n for n, r in results.items()
                                if r in ("installed", "already-present"))}


@app.post("/api/hooks/uninstall")
def hooks_uninstall(payload: dict | None = None, request: Request = None):  # type: ignore[assignment]
    """Remove Quarterdeck's spawn hook, leaving everything else in the file alone."""
    if request is not None and not require_local(request):
        return {"error": "local only"}
    payload = payload or {}
    results = {}
    for path in _agent_config_paths(payload.get("cwd", "")):
        results[path.stem] = _uninstall_from(path)
    return {"ok": True, "results": results}


# ── Denied command patterns ──────────────────────────────────────────────────

from . import deny as _deny_mod  # noqa: E402


@app.get("/api/deny-patterns")
def list_deny_patterns():
    return {"patterns": _deny_mod.list_patterns()}


@app.post("/api/deny-patterns")
def add_deny_pattern(payload: dict, request: Request):
    if not require_local(request):
        return {"error": "local only"}
    tool = payload.get("tool", "execute_bash")
    pattern = payload.get("pattern", "")
    note = payload.get("note", "")
    if not pattern:
        return {"error": "pattern required"}
    try:
        import re; re.compile(pattern)
    except re.error as e:
        return {"error": f"invalid regex: {e}"}
    return {"pattern": _deny_mod.add_pattern(tool, pattern, note)}


@app.delete("/api/deny-patterns/{pattern_id}")
def delete_deny_pattern(pattern_id: str, request: Request):
    if not require_local(request):
        return {"error": "local only"}
    ok = _deny_mod.remove_pattern(pattern_id)
    return {"ok": ok}


@app.get("/api/sessions/{session_id}/gate")
def get_gate(session_id: str):
    """Whether this session's tool calls are held for a human decision."""
    return {"enabled": tmux.gate_enabled(session_id),
            # A gate on a session whose agent carries no preToolUse hook is a
            # switch wired to nothing, so say so rather than let the UI imply
            # protection that is not there.
            "hooked": agent_has_pretool_hook(session_id)}


@app.post("/api/sessions/{session_id}/gate")
def set_gate(session_id: str, payload: dict | None = None):
    """Turn approval gating on or off for one session."""
    payload = payload or {}
    enabled = bool(payload.get("enabled", False))
    if not tmux.set_gate(session_id, enabled):
        return {"error": "Invalid session id"}
    if not enabled:
        # Anything already held was held under the old setting. Releasing the
        # gate without answering them would leave those calls waiting out the
        # full timeout and then being denied, which reads as the toggle breaking
        # the session.
        for a in tmux.pending_approvals():
            if a["session_id"] == session_id:
                tmux.respond_approval(session_id, a["request_id"], allow=True)
                # Allowed because gating was switched off, not because anyone
                # looked at this call. Recorded as such.
                audit.append("decision", session=session_id,
                             request=a["request_id"], allow=True,
                             how="gate-off", tool=a.get("tool_name"),
                             input=a.get("tool_input"))
    return {"ok": True, "enabled": enabled,
            "hooked": agent_has_pretool_hook(session_id)}


# ── Per-session trust TTL ────────────────────────────────────────────────────

def _trust_path(session_id: str) -> Path:
    d = STATE_DIR / "trust"
    d.mkdir(parents=True, exist_ok=True)
    return d / session_id


def _trust_until(session_id: str) -> float:
    """Return the trust expiry timestamp, or 0 if not trusted."""
    try:
        return float(_trust_path(session_id).read_text().strip())
    except (OSError, ValueError):
        return 0


def _is_trusted(session_id: str) -> bool:
    return time.time() < _trust_until(session_id)


@app.post("/api/sessions/{session_id}/trust")
def trust_session(session_id: str, payload: dict | None = None):
    """Grant temporary trust: auto-allow all tool calls for N minutes."""
    payload = payload or {}
    minutes = float(payload.get("minutes", 30))
    minutes = max(1, min(minutes, 480))  # 1 min – 8 hrs
    expires = time.time() + minutes * 60
    _trust_path(session_id).write_text(str(expires))
    # Auto-allow any currently pending approvals for this session.
    for a in tmux.pending_approvals():
        if a["session_id"] == session_id:
            tmux.respond_approval(session_id, a["request_id"], allow=True)
    return {"ok": True, "trust_until": expires, "minutes": minutes}


@app.delete("/api/sessions/{session_id}/trust")
def revoke_trust(session_id: str):
    """Revoke trust TTL for a session."""
    try:
        _trust_path(session_id).unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True}


@app.get("/api/sessions/{session_id}/trust")
def get_trust(session_id: str):
    until = _trust_until(session_id)
    remaining = max(0, until - time.time())
    return {"trusted": remaining > 0, "trust_until": until if remaining > 0 else None,
            "remaining_seconds": int(remaining)}


@app.get("/api/approvals")
def list_approvals():
    """Tool calls currently waiting for a human decision."""
    # Sweep expired files on every read so stale banners clear within one poll cycle.
    tmux.sweep_approvals(ttl=tmux.APPROVAL_TIMEOUT)
    return {"approvals": tmux.pending_approvals()}


@app.post("/api/approvals/dismiss-all")
def dismiss_all_approvals(request: Request = None):  # type: ignore[assignment]
    """Deny and remove all pending approvals — clears the banner immediately."""
    approvals = tmux.pending_approvals()
    for a in approvals:
        tmux.respond_approval(a["session_id"], a["request_id"], allow=False)
        # `how` separates this from a per-call answer. One click denying nine
        # tool calls is a different act from nine decisions, and an audit that
        # renders them identically is misleading in the direction that matters.
        audit.append("decision", session=a["session_id"],
                     request=a["request_id"], allow=False, how="dismiss-all",
                     actor=audit.actor_of(request) if request is not None else None,
                     tool=a.get("tool_name"), input=a.get("tool_input"))
    # Give hook processes a moment to pick up the deny signal, then sweep.
    time.sleep(0.2)
    tmux.sweep_approvals(ttl=0)
    return {"ok": True, "dismissed": len(approvals)}


def _held_call(session_id: str, request_id: str) -> dict:
    """The pending request's tool and input, for the decision record.

    Read *before* the answer is sent: answering retires the request, and with it
    the only record of which tool was being held. The whole claim of the approval
    gate is that a person decided — this is what makes the claim checkable.
    """
    for a in tmux.pending_approvals():
        if a["session_id"] == session_id and a["request_id"] == request_id:
            return {"tool": a.get("tool_name", ""), "input": a.get("tool_input")}
    return {}


@app.post("/api/approvals/{request_id}/allow")
def allow_tool(request_id: str, payload: dict | None = None,
               request: Request = None):  # type: ignore[assignment]
    """Allow a pending preToolUse hook call."""
    payload = payload or {}
    session_id = payload.get("session_id", "")
    if not session_id:
        return {"error": "session_id required"}
    held = _held_call(session_id, request_id)
    ok = tmux.respond_approval(session_id, request_id, allow=True)
    if ok:
        audit.append("decision", session=session_id, request=request_id,
                     allow=True, how="api", actor=audit.actor_of(request),
                     tool=held.get("tool"), input=held.get("input"))
    return {"ok": ok} if ok else {"error": "No such pending approval"}


@app.post("/api/approvals/{request_id}/deny")
def deny_tool(request_id: str, payload: dict | None = None,
              request: Request = None):  # type: ignore[assignment]
    """Deny a pending preToolUse hook call (kiro-cli will not run the tool)."""
    payload = payload or {}
    session_id = payload.get("session_id", "")
    if not session_id:
        return {"error": "session_id required"}
    held = _held_call(session_id, request_id)
    ok = tmux.respond_approval(session_id, request_id, allow=False)
    if ok:
        audit.append("decision", session=session_id, request=request_id,
                     allow=False, how="api", actor=audit.actor_of(request),
                     tool=held.get("tool"), input=held.get("input"))
    return {"ok": ok} if ok else {"error": "No such pending approval"}


# --- task stack ---

@app.get("/api/stacks")
def get_all_stacks():
    """Return all sessions that have pending stack items, with their items and session metadata."""
    results = []
    for stack_file in STACKS_DIR.glob("*.json"):
        session_id = stack_file.stem
        try:
            items = tmux.stack_get(session_id)
        except Exception:
            continue
        if not items:
            continue
        # Get session metadata from the sessions list cache (title, cwd, status)
        meta_file = SESSIONS_DIR / f"{session_id}.json"
        title = session_id[:8]
        cwd = ""
        status = "unknown"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text())
                title = meta.get("title") or meta.get("name") or title
                cwd = meta.get("cwd") or ""
                status = meta.get("status") or "unknown"
            except Exception:
                pass
        results.append({
            "session_id": session_id,
            "title": title,
            "cwd": cwd,
            "status": status,
            "items": items,
            "count": len(items),
        })
    results.sort(key=lambda x: x["count"], reverse=True)
    return {"sessions": results, "total_items": sum(r["count"] for r in results)}


@app.get("/api/sessions/{session_id}/stack")
def get_stack(session_id: str):
    """Return the task queue for a session."""
    return {"items": tmux.stack_get(session_id)}


@app.post("/api/sessions/{session_id}/stack")
def add_to_stack(session_id: str, payload: dict):
    """Append an item to the session's task queue."""
    text = str(payload.get("text", "")).strip()
    if not text:
        return {"error": "text required"}
    item = tmux.stack_add(session_id, text)
    return {"ok": True, "item": item, "items": tmux.stack_get(session_id)}


@app.delete("/api/sessions/{session_id}/stack/{item_id}")
def delete_stack_item(session_id: str, item_id: str):
    """Remove one item from the task queue."""
    ok = tmux.stack_delete(session_id, item_id)
    return {"ok": ok, "items": tmux.stack_get(session_id)}


@app.patch("/api/sessions/{session_id}/stack/{item_id}")
def update_stack_item(session_id: str, item_id: str, payload: dict):
    """Edit the text of one stack item."""
    text = (payload.get("text") or "").strip()
    if not text:
        return {"error": "text required"}
    items = tmux.stack_update(session_id, item_id, text)
    if items is None:
        return {"error": "item not found"}
    return {"ok": True, "items": items}


@app.post("/api/sessions/{session_id}/stack/reorder")
def reorder_stack(session_id: str, payload: dict):
    """Reorder the stack to match the provided id sequence."""
    ids = payload.get("ids", [])
    if not isinstance(ids, list):
        return {"error": "ids must be a list"}
    items = tmux.stack_reorder(session_id, ids)
    return {"ok": True, "items": items}


@app.post("/api/sessions/{session_id}/stack/send-next")
def send_next_stack_item(session_id: str):
    """Pop the first item from the stack and send it to the session."""
    meta = read_metadata(session_id)
    if not meta:
        return {"error": "Session not found"}
    if not tmux.is_managed(session_id):
        return {"error": "Session is not managed"}

    lock_data = read_lock(session_id)
    status = detect_status(session_id, lock_data, tmux.capture(session_id))
    if status == "awaiting-approval":
        return {"error": "Session is awaiting approval — not safe to send"}

    item = tmux.stack_pop(session_id)
    if not item:
        return {"error": "Stack is empty"}

    result = tmux.send_text(session_id, item["text"])
    if not result.get("ok"):
        # Put it back at the front so it is not lost
        items = tmux.stack_get(session_id)
        tmux.stack_save(session_id, [item] + items)
        return {"error": result.get("error", "send failed")}

    return {"ok": True, "sent": item, "remaining": tmux.stack_get(session_id)}


@app.post("/api/sessions/{session_id}/stack/auto-advance")
def set_auto_advance(session_id: str, payload: dict):
    """Enable or disable auto-advance for a session's task stack."""
    enabled = bool(payload.get("enabled", False))
    settings = _load_settings()
    key = f"stack-auto:{session_id}"
    if enabled:
        settings[key] = True
    else:
        settings.pop(key, None)
    _save_settings(settings)
    return {"ok": True, "enabled": enabled}


@app.get("/api/sessions/{session_id}/stack/auto-advance")
def get_auto_advance(session_id: str):
    """Return whether auto-advance is on for this session."""
    settings = _load_settings()
    return {"enabled": bool(settings.get(f"stack-auto:{session_id}", False))}


# ---------------------------------------------------------------------------
# Slash command queue endpoints
# ---------------------------------------------------------------------------

@app.get("/api/sessions/{session_id}/slash-queue")
def get_slash_queue(session_id: str):
    """Return the pending slash commands for this session."""
    return {"items": sq_list(session_id)}


@app.post("/api/sessions/{session_id}/slash-queue")
def push_slash_queue(session_id: str, payload: dict):
    """Enqueue a slash command (or any text) to send after the current turn ends."""
    text = (payload.get("text") or "").strip()
    if not text:
        return {"error": "text required"}
    item = sq_push(session_id, text)
    return {"ok": True, "item": item, "queue": sq_list(session_id)}


@app.delete("/api/sessions/{session_id}/slash-queue/{item_id}")
def delete_slash_queue_item(session_id: str, item_id: str):
    """Remove one queued item by id."""
    removed = sq_remove(session_id, item_id)
    return {"ok": removed, "queue": sq_list(session_id)}


@app.get("/api/options")
def get_options(cwd: str = "", session_id: str = ""):
    """Model, effort, agent, engine, and quick-command choices the UI offers.

    When session_id is given, resolve the session's recorded profile and return
    its memoised model list (written at save/switch time). Falls back to the
    global live list when no cached list exists. Returns a 'source' field so the
    UI knows where the list came from:
      'profile-cache' — session's profile's cached entitlements (most accurate)
      'live'          — currently active profile's live entitlements
      'fallback'      — kiro-cli unreachable; hardcoded fallback list
    """
    models_source = "live"
    models_list = list(available_models())

    if session_id:
        # Try to serve the session-scoped list.
        try:
            o = ownership.get_ownership(session_id) or {}
            session_profile = o.get("kiro_profile", "")
            if session_profile:
                meta_path = _profile_meta_path(session_profile)
                if meta_path.exists():
                    meta_data = json.loads(meta_path.read_text())
                    cached = meta_data.get("models")
                    if cached and isinstance(cached, list) and len(cached) > 0:
                        models_list = cached
                        models_source = "profile-cache"
        except Exception:
            pass  # fall back to live list

    # Detect fallback (config.MODELS is the hardcoded list; if live == MODELS,
    # we cannot tell if it's a real match or a --list-models failure, but we
    # can at least label it so the UI can show provenance).
    from .config import MODELS as _MODELS
    if tuple(models_list) == _MODELS:
        models_source = "fallback"
    elif models_source == "live":
        models_source = "live"

    return {"models": models_list, "efforts": list(EFFORTS),
            "engines": ["v1", "v2", "v3"],
            "commands": [dict(c) for c in QUICK_COMMANDS],
            "terminals": [{"id": k, **v} for k, v in TERMINALS.items()],
            "agents": list_agents(cwd), "default_agent": default_agent_name(cwd),
            "models_source": models_source}


@app.post("/api/open-folder")
def open_folder(payload: dict, request: Request):
    """Open a folder in Finder. Local callers only."""
    if not require_local(request):
        return {"error": "local only"}
    raw_path = payload.get("path", "").strip()
    if not raw_path:
        return {"error": "No path"}
    # Callers send the raw cwd, never the shortened cwd_display. This used to
    # try to reverse shorten_path()'s "/…/" abbreviation, which could not work:
    # a shortened path also begins with "~/", so that branch matched first and
    # left the ellipsis in place. Every folder under the abbreviated prefix
    # failed to open.
    if "…" in raw_path:
        return {"error": "Path is a display string, not a real path"}
    path = Path(raw_path).expanduser()
    if not path.is_dir():
        return {"error": "Not a directory"}
    subprocess.run(["open", str(path)], capture_output=True)
    return {"ok": True}


@app.post("/api/pick-folder")
def pick_folder(request: Request):
    """Open macOS native folder picker dialog. Local callers only."""
    if not require_local(request):
        return {"path": None, "error": "local only"}
    # Use a simple choose folder dialog without requiring System Events activation.
    # The with timeout block ensures it doesn't hang if the dialog is dismissed.
    script = 'POSIX path of (choose folder with prompt "Select folder")'
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=120, text=True
        )
        path = result.stdout.strip().rstrip("/")
        if result.returncode == 0 and path:
            return {"path": path}
        # User cancelled (returncode 1) — not an error
        return {"path": None}
    except subprocess.TimeoutExpired:
        return {"path": None, "error": "dialog timed out"}
    except Exception as e:
        return {"path": None, "error": str(e)}


@app.post("/api/sessions/{session_id}/kill")
def kill_session(session_id: str, force: bool = False):
    """End a session.

    Managed sessions get a clean `/quit` first so their conversation survives
    and the session stays resumable; pass force=true to skip straight to
    killing the tmux session. Foreign sessions are signalled by pid.
    """
    # Detach any ACP observer side-channel regardless of session type.
    acp_observer.detach(session_id)

    if tmux.is_managed(session_id):
        if force:
            result = tmux.kill(session_id, graceful=False)
            if not result.get("ok"):
                return {"error": result.get("error", "kill failed")}
            return {"ok": True, "mode": result.get("mode", "kill")}
        # A clean /quit can take several seconds to land. Doing it inline made
        # the UI hang on every close, so it runs in the background and the
        # caller returns at once; /api/sessions reflects the result when done.
        threading.Thread(
            target=tmux.kill, args=(session_id,), kwargs={"graceful": True}, daemon=True
        ).start()
        return {"ok": True, "mode": "quitting", "pending": True}

    lock_data = read_lock(session_id)
    if lock_data is None:
        return {"error": "Session not active"}
    pid = lock_data.get("pid")
    if not pid:
        return {"error": "No PID found"}
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError as e:
        return {"error": str(e)}
    # Clear any stale record left by a pane that died unnoticed.
    tmux.kill(session_id)
    return {"ok": True, "mode": "signal", "pid": pid}


@app.post("/api/sessions/{session_id}/dismiss")
def dismiss_session(session_id: str):
    """Archive a crew session by moving its JSONL to the archive subfolder.

    Crew sessions cannot be killed (no tmux/pid), but they can be hidden from
    the active list by moving them to ~/.kiro/crew/sessions/archive/.
    """
    if not _is_crew_session(session_id):
        return {"error": "Only crew sessions can be dismissed this way"}
    src = CREW_SESSIONS_DIR / f"{session_id}.jsonl"
    if not src.exists():
        return {"error": "Session file not found"}
    archive_dir = CREW_SESSIONS_DIR / "archive"
    archive_dir.mkdir(exist_ok=True)
    dst = archive_dir / f"{session_id}.jsonl"
    try:
        src.rename(dst)
    except OSError as e:
        return {"error": str(e)}
    # Record duration data for dismissed crew sessions — additive, never raises.
    threading.Thread(target=duration.write_record, args=(session_id,), daemon=True).start()
    return {"ok": True}


SESSION_FILE_EXTENSIONS = (".json", ".jsonl", ".lock", ".history")


def _session_is_active(session_id: str) -> bool:
    """Conservatively decide whether deleting session files is unsafe."""
    lock_path = SESSIONS_DIR / f"{session_id}.lock"
    if not lock_path.exists():
        return False
    lock_data = read_lock(session_id)
    if not lock_data:
        return True
    pid = lock_data.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return True
    if not is_process_alive(pid):
        return False
    # PID is alive — verify it's actually a kiro/tmux process, not an
    # unrelated process that inherited a reused PID.
    try:
        import subprocess as _sp
        r = _sp.run(["ps", "-p", str(pid), "-o", "command="],
                    capture_output=True, text=True, timeout=2)
        cmd = r.stdout.strip().lower()
        if not cmd:
            return False  # gone between checks
        # Only allow deletion when the process is positively identified as
        # something that cannot be a kiro session: GUI apps, system daemons, etc.
        # Never skip Python/tmux/shell — kiro-cli runs under those.
        definitely_not_kiro = any(
            x in cmd for x in ("safari", "chrome", "firefox", "slack", "zoom",
                               "xcode", "finder", "/applications/")
        )
        if definitely_not_kiro:
            return False
    except Exception:
        pass  # conservative: assume active
    return True


def _delete_session_files(session_id: str) -> list[str]:
    """Delete the known files for one already-verified archived session."""
    deleted = []
    for ext in SESSION_FILE_EXTENSIONS:
        path = SESSIONS_DIR / f"{session_id}{ext}"
        if path.exists():
            path.unlink()
            deleted.append(ext)
    return deleted


def _path_is_within(raw_path: str, root: Path) -> bool:
    """True when raw_path is root or a descendant, respecting path boundaries."""
    try:
        candidate = Path(raw_path).expanduser().resolve(strict=False)
        candidate.relative_to(root)
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _invalidate_projects_cache() -> None:
    """Drop the project scan cache after session metadata changes."""
    cache = globals().get("_projects_cache")
    if isinstance(cache, dict):
        cache["data"] = None
        cache["ts"] = 0.0


@app.post("/api/sessions/{session_id}/delete")
def delete_session(session_id: str, payload: dict = None):
    """Delete a session's files, killing it first if it is still running.

    Previously callers had to end the session manually before deleting. That
    made the delete button silently fail for idle managed sessions: the process
    is alive (waiting at the prompt), _session_is_active() returns True, and
    the backend refused with 'session_active'. Now the endpoint kills the
    session synchronously before deleting when it is still active.
    """
    if payload is None:
        payload = {}

    # V3 session: delete the directory
    if v3mod.is_v3_session(session_id):
        d = v3mod.session_dir(session_id)
        if not d or not d.exists():
            return {"ok": True, "deleted": []}
        import shutil as _shutil
        _shutil.rmtree(str(d))
        _remove_favourites({session_id})
        _invalidate_projects_cache()
        return {"ok": True, "deleted": [str(d)]}

    if _session_is_active(session_id):
        # Kill the session so its files become deletable.
        acp_observer.detach(session_id)
        if tmux.is_managed(session_id):
            tmux.kill(session_id, graceful=False)
        else:
            lock_data = read_lock(session_id)
            pid = (lock_data or {}).get("pid")
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
        # Give the process a moment to release its lock file.
        import time as _time
        for _ in range(10):
            if not _session_is_active(session_id):
                break
            _time.sleep(0.3)

    deleted = _delete_session_files(session_id)
    _remove_favourites({session_id})
    tmux.clear_gate(session_id)
    _invalidate_projects_cache()
    return {"ok": True, "deleted": deleted}


def _resolve_project_root(payload: dict) -> tuple[Path | None, dict | None]:
    """Validate a caller's project path. Returns (root, error) — one of each."""
    cwd = payload.get("cwd", "").strip()
    if not cwd:
        return None, {"error": "No cwd provided"}
    root = Path(cwd).expanduser()
    if not root.is_absolute():
        return None, {"error": "cwd must be an absolute path"}
    root = root.resolve(strict=False)
    if root == Path(root.anchor) or root == Path.home().resolve():
        return None, {"error": "Refusing to delete sessions for a broad system path"}
    return root, None


def _match_project_sessions(project_root: Path) -> tuple[list[str], list[str]]:
    """Sessions at or below project_root, and which of those are still running.

    Matching is by path containment, so a session in a subdirectory of the
    project counts as part of it. That is usually what a caller means, but note
    that extract_project() names a project after the first meaningful path
    segment, so a nested directory can be listed as its own project while still
    living inside this one — deleting the outer project takes the inner project's
    sessions with it. The preview endpoint exists so a caller can see the real
    set before agreeing to it.

    Sessions without a `<id>.json` are invisible here: metadata is where the cwd
    is recorded, so there is nothing to match them against.
    """
    matches = []
    for json_file in SESSIONS_DIR.glob("*.json"):
        session_id = json_file.stem
        meta = read_metadata(session_id)
        raw_cwd = meta.get("cwd", "") if meta else ""
        if raw_cwd and _path_is_within(raw_cwd, project_root):
            matches.append(session_id)
    active = [session_id for session_id in matches if _session_is_active(session_id)]
    return matches, active


@app.post("/api/projects/delete-preview")
def preview_project_deletion(payload: dict):
    """Report what deleting this project would remove, without removing it.

    The count a confirmation dialog shows has to come from the code that does
    the deleting, or it is a guess that happens to agree most of the time.
    """
    project_root, error = _resolve_project_root(payload)
    if error:
        return error
    assert project_root is not None
    matches, active = _match_project_sessions(project_root)
    return {
        "ok": True,
        "session_count": len(matches),
        "session_ids": matches,
        "active_sessions": active,
    }


@app.post("/api/projects/delete")
def delete_project_sessions(payload: dict):
    """Delete every session at or below a project path.

    See _match_project_sessions() for what "below" includes.
    """
    project_root, error = _resolve_project_root(payload)
    if error:
        return error
    assert project_root is not None
    matches, active = _match_project_sessions(project_root)

    # Preflight the full set. A project delete should never become a partial
    # delete because one of its sessions happened to be running.
    if active:
        return {
            "error": "Project has active sessions; end them before deleting",
            "code": "project_has_active_sessions",
            "active_sessions": active,
        }

    for session_id in matches:
        _delete_session_files(session_id)
    _remove_favourites(set(matches))
    _invalidate_projects_cache()
    return {
        "ok": True,
        "deleted_sessions": len(matches),
        "session_ids": matches,
    }


@app.post("/api/sessions/{session_id}/rename")
def rename_session(session_id: str, payload: dict):
    """Rename a session's title.

    Writes to ``~/.osa-kiro/names.json`` — a Quarterdeck-owned sidecar that
    kiro-cli never touches.  kiro-cli replaces the whole session JSON on every
    agent turn (new inode), so any field written into that file is lost
    immediately.  The sidecar survives because it is a completely separate file.
    """
    new_title = payload.get("title", "").strip()
    if not new_title:
        return {"error": "No title provided"}
    if read_metadata(session_id) is None:
        return {"error": "Session not found"}
    try:
        _set_deck_name(session_id, new_title)
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/dispatch")
def dispatch_task(payload: dict):
    """Start a new kiro-cli session under tmux with an initial task."""
    task = payload.get("task", "")
    # No directory given: use the configured cwd mode (auto/last/fixed).
    cwd = payload.get("cwd") or cwd_suggestion()["path"] or str(Path.home())
    if not task.strip():
        return {"error": "No task provided"}

    # Prepend paste attachment references to the task (dispatch sessions read
    # files freely — no gating at session start).
    attachments = payload.get("attachments") or []
    ref_lines = []
    for att in attachments:
        att_sid = att.get("session_id") or "_unassigned"
        att_name = att.get("name", "")
        if not att_name:
            continue
        try:
            ref = paste_store.reference_line(att_sid, att_name,
                                             att.get("lines", 0),
                                             att.get("size_display", ""))
            ref_lines.append(ref)
        except (ValueError, Exception):
            pass
    if ref_lines:
        task = "\n".join(ref_lines) + "\n" + task

    # Return as soon as tmux has the process: waiting for kiro-cli to write its
    # session id took seconds and made the UI feel dead. Correlation continues
    # on a background thread, and the pending entry shows up in /api/sessions
    # immediately so the card appears at once.
    result = tmux.spawn(cwd, task=" ".join(task.split()), wait=False,
                        **_spawn_kwargs(payload))
    if not result.get("ok"):
        return {"error": result.get("error", "spawn failed")}

    nonce = result.get("nonce", "")
    session_id = result.get("session_id", "")
    # Gating asked for at dispatch is applied before correlation finishes, keyed
    # by the nonce the hook can see in the environment. Waiting for the id would
    # miss the session's first tool calls, which are the ones worth holding.
    if payload.get("gate"):
        if session_id:
            tmux.set_gate(session_id, True)
        elif nonce:
            tmux.set_pending_gate(nonce, True)
    if nonce:
        threading.Thread(
            target=tmux.resolve_pending, args=(nonce,), daemon=True
        ).start()

    # Tag the new session with the currently active profile name so the card
    # can show which account it runs under. Written asynchronously: correlation
    # may not have resolved the session_id yet, so we wait for it in a thread.
    active_profile = _active_profile_name()
    if active_profile:
        # Snapshot existing managed keys before this spawn completes correlation,
        # so the thread can identify the new session_id by set difference.
        managed_before = set(tmux.load_state()["managed"])
        # Capture the ARN at dispatch time so we have something more stable than
        # the profile name (names are user-editable; ARNs are not).
        active_profile_arn = ""
        try:
            meta_path = _profile_meta_path(active_profile)
            if meta_path.exists():
                active_profile_arn = json.loads(meta_path.read_text()).get("profile_arn", "")
        except Exception:
            pass

        def _tag_profile(sid: str, nonce_: str, profile: str, profile_arn: str, before: set) -> None:
            if not sid:
                # Wait for nonce→session correlation. The resolver moves the
                # pending entry to managed once it finds the session_id.
                # Polling for the nonce to leave pending state is reliable;
                # managed_sessions() has no nonce field to match against.
                deadline = time.time() + 30
                while time.time() < deadline:
                    state = tmux.load_state()
                    if nonce_ not in state["pending"]:
                        new_ids = set(state["managed"]) - before
                        if new_ids:
                            sid = next(iter(new_ids))
                        break
                    time.sleep(0.5)
            if sid:
                o = ownership.get_ownership(sid) or {}
                o["kiro_profile"] = profile
                o["kiro_profile_arn"] = profile_arn  # stable identifier; name may change
                o["spawned_at"] = time.time()  # used to mark badge unverified after a switch
                ownership.write_sidecar(sid, o)
        threading.Thread(
            target=_tag_profile, args=(session_id, nonce, active_profile, active_profile_arn, managed_before),
            daemon=True,
        ).start()

    # For V3 sessions, start an ACP observation side-channel. Uses the same
    # nonce→session_id wait pattern as _tag_profile above. A failed attach is
    # non-fatal — tmux is still the source of truth; ACP is best-effort events.
    engine = (payload.get("engine") or "").strip()
    if engine == "v3":
        def _attach_observer(sid: str, nonce_: str, cwd_: str) -> None:
            if not sid:
                deadline = time.time() + 30
                while time.time() < deadline:
                    state = tmux.load_state()
                    if nonce_ not in state["pending"]:
                        managed_ids = set(state["managed"])
                        if managed_ids:
                            sid = next(iter(managed_ids))
                        break
                    time.sleep(0.5)
            if sid:
                acp_observer.attach(sid, cwd=cwd_)
        threading.Thread(
            target=_attach_observer, args=(session_id, nonce, cwd),
            daemon=True, name=f"acp-obs-{nonce or session_id[:8]}",
        ).start()

    return {
        "ok": True, "pending": not session_id, "nonce": nonce,
        "id": session_id or None,
        "attach": tmux.attach_command(session_id) if session_id else "",
        "cwd": cwd,
        "message": f"Dispatched: {task[:50]}",
    }


def _active_profile_name() -> str:
    """Return the name of the saved profile that matches the current auth, or ''.

    Matches by the CodeWhisperer profile ARN in the state table first — this
    is the authoritative signal when both profiles share the same SSO identity
    (same OAuth tokens, different subscription tier). Token fingerprint is the
    fallback for profiles saved before ARN tracking existed.
    """
    if not _KIRO_AUTH_DB.exists():
        return ""
    try:
        import sqlite3 as _sqlite3
        # Primary: match by the active CodeWhisperer profile ARN in state table
        con = _sqlite3.connect(str(_KIRO_AUTH_DB), timeout=5)
        try:
            row = con.execute(
                "SELECT value FROM state WHERE key = 'api.codewhisperer.profile'"
            ).fetchone()
        finally:
            con.close()
        if row and row[0]:
            try:
                state_arn = json.loads(row[0]).get("arn", "")
            except Exception:
                state_arn = ""
            if state_arn:
                for meta_path in sorted(_PROFILES_DIR.glob("*.meta.json")):
                    name = meta_path.stem.replace(".meta", "")
                    if name == "_previous":
                        continue
                    try:
                        meta = json.loads(meta_path.read_text())
                        if meta.get("profile_arn", "") == state_arn:
                            return name
                    except Exception:
                        continue

        # Fallback: token fingerprint match (profiles without ARN metadata)
        current_rows = _dump_auth_rows()
        current_fp = _token_fingerprint(current_rows)
        if not current_fp:
            return ""
        for data_path in sorted(_PROFILES_DIR.glob("*.jsonl")):
            name = data_path.stem
            if name == "_previous":
                continue
            try:
                saved_rows = [json.loads(line) for line in data_path.read_text().splitlines() if line.strip()]
                if _token_fingerprint(saved_rows) == current_fp:
                    return name
            except Exception:
                continue
    except Exception:
        pass
    return ""


@app.post("/api/sessions/restart-visible")
def restart_visible_sessions(payload: dict):
    """Kill and resume all sessions whose ids are passed in the payload."""
    session_ids: list[str] = payload.get("ids", [])
    if not session_ids:
        return {"error": "No session ids provided"}
    results = {}
    for session_id in session_ids:
        try:
            meta = read_metadata(session_id)
            if not meta:
                results[session_id] = "not found"
                continue
            cwd = meta.get("cwd", "")
            if not cwd:
                results[session_id] = "no cwd"
                continue
            # Kill if running
            lock_data = read_lock(session_id)
            pid = lock_data.get("pid") if lock_data else None
            if pid and is_process_alive(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                    deadline = time.time() + 5
                    while is_process_alive(pid) and time.time() < deadline:
                        time.sleep(0.1)
                except Exception:
                    pass
            # Wait for kiro-cli to release its lock before killing tmux.
            # The lock disappearing is the signal that the process has exited
            # cleanly; without this wait the resumed process can start and
            # find a stale lock that makes it treat the session as already
            # running. Mirrors the same pattern in takeover_session.
            lock_path = SESSIONS_DIR / f"{session_id}.lock"
            lock_deadline = time.time() + 5
            while lock_path.exists() and time.time() < lock_deadline:
                time.sleep(0.1)
            if lock_path.exists():
                try:
                    lock_path.unlink()
                except OSError:
                    pass
            # Kill the tmux session itself so spawn's session_exists check
            # doesn't see a stale session and refuse with "already managed".
            # remain-on-exit keeps the pane alive after the process dies, so
            # the tmux session outlives the kill above.
            tmux_session_name = tmux.tmux_name(session_id)
            if tmux.session_exists(tmux_session_name):
                tmux._tmux("kill-session", "-t", tmux_session_name, check=False)
                # Wait for the tmux session to actually disappear — the kill is
                # async and spawn's session_exists check races it without this.
                dead_deadline = time.time() + 3
                while tmux.session_exists(tmux_session_name) and time.time() < dead_deadline:
                    time.sleep(0.05)
            # Also remove from managed.json so spawn() doesn't get confused
            # by a stale "already managed" entry during the brief window
            # between kill and the new session appearing.
            state = tmux.load_state()
            if session_id in state["managed"]:
                state["managed"].pop(session_id, None)
                tmux.save_state(state)
            # Resume under tmux
            result = tmux.spawn(cwd, resume_id=session_id)
            results[session_id] = "ok" if result.get("ok") else result.get("error", "failed")
        except Exception as e:
            results[session_id] = str(e)
    ok_count = sum(1 for v in results.values() if v == "ok")
    return {"ok": True, "restarted": ok_count, "results": results}


@app.post("/api/kiro/login")
def kiro_login(payload: dict):
    """Trigger kiro-cli login in a terminal window."""
    license_type = payload.get("license", "free")  # free | pro
    identity_provider = payload.get("identity_provider", "")
    region = payload.get("region", "")

    cmd_parts = ["kiro-cli", "login", "--license", license_type]
    if identity_provider:
        cmd_parts += ["--identity-provider", identity_provider]
    if region:
        cmd_parts += ["--region", region]

    command = " ".join(shlex.quote(p) for p in cmd_parts)
    # Use configured terminal from settings, fall back to "terminal"
    terminal = _load_settings().get("terminal", "terminal")
    if terminal not in TERMINALS:
        terminal = "terminal"
    result = _launch_in_terminal(terminal, command, str(Path.home()))
    return result


@app.post("/api/kiro/logout")
def kiro_logout():
    """Run kiro-cli logout."""
    try:
        result = subprocess.run(
            ["kiro-cli", "logout"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip() or "logout failed"}
        return {"ok": True, "message": result.stdout.strip() or "Logged out"}
    except Exception as e:
        return {"error": str(e)}


# --- Concierge assistant ---

@app.post("/api/assist")
def assist(payload: dict):
    """Send a natural-language query to the concierge kiro-cli session.

    The concierge parses intent, queries Quarterdeck's own API, and returns a
    structured JSON response the command bar can render.
    """
    query_text = payload.get("query", "").strip()
    if not query_text:
        return {"type": "error", "title": "Empty query", "narrative": "Ask something.", "items": [], "actions": []}
    return concierge.query(query_text)


@app.get("/api/assist/status")
def assist_status():
    """Health check for the concierge session."""
    return concierge.status()


# --- Side chat ---

@app.post("/api/sessions/{session_id}/side-chat/open")
def side_chat_open(session_id: str):
    """Open a side chat for a session, injecting its recent transcript as frozen context."""
    # Build context from recent transcript
    transcript = read_transcript(session_id, after=-1, limit=40)
    context_lines = []
    for msg in transcript.get("messages", []):
        role = msg.get("role", "")
        text = msg.get("text", "") or ""
        if role and text:
            context_lines.append(f"[{role.upper()}]: {text[:1000]}")
    context_text = "\n\n".join(context_lines)
    if not context_text:
        # Fall back to last_output
        context_text = get_last_output(session_id) or "(no context available)"
    return side_chat.open_session(session_id, context_text)


@app.post("/api/sessions/{session_id}/side-chat/send")
def side_chat_send(session_id: str, payload: dict):
    """Send a message to the side chat."""
    text = payload.get("text", "").strip()
    if not text:
        return {"error": "empty message"}
    return side_chat.send(session_id, text)


@app.get("/api/sessions/{session_id}/side-chat/poll")
def side_chat_poll(session_id: str):
    """Poll the side chat pane state."""
    return side_chat.poll(session_id)


@app.post("/api/sessions/{session_id}/side-chat/close")
def side_chat_close(session_id: str):
    """Close (kill) the side chat for a session."""
    return side_chat.close(session_id)


@app.post("/api/sessions/{session_id}/side-chat/fork")
def side_chat_fork(session_id: str):
    """Fork the side chat into a standalone kiro-cli session.

    Task is passed as a CLI arg (the only supported input method).
    After spawn resolves we pin the display name via _set_deck_name so the
    transcript blob never leaks into the title shown in Quarterdeck.
    """
    meta = read_metadata(session_id)
    cwd = (meta or {}).get("cwd") or str(Path.home())
    # Use meta_title() — checks names.json (user renames) before kiro's own title
    base_name = meta_title(meta or {}) or (meta or {}).get("name") or "side-chat"
    import re as _re
    base_name = _re.sub(r'-\d+$', '', base_name).strip() or "side-chat"

    existing_titles = set()
    try:
        for s in list_sessions().get("sessions", []):
            t = s.get("title") or s.get("name") or ""
            existing_titles.add(t.strip())
    except Exception:
        pass

    fork_name = base_name
    n = 1
    while fork_name in existing_titles:
        fork_name = f"{base_name}-{n}"
        n += 1

    transcript = side_chat.get_transcript(session_id)
    if not transcript:
        return {"error": "No side chat transcript available"}

    # Task text: short directive first so kiro starts immediately, then context.
    # No .split() — preserve the structure so kiro reads it as a coherent brief.
    task = (
        f"You are continuing a side chat that was promoted to a full session. "
        f"Resume the work below — do NOT start over or re-investigate.\n\n"
        f"=== Conversation so far ===\n"
        f"{transcript[-4000:]}\n"
        f"=== End ===\n\n"
        f"Continue from where the conversation left off."
    )

    # wait=True so we get the session_id back and can pin the display name.
    result = tmux.spawn(cwd, task=task, wait=True)
    if result.get("ok") and result.get("session_id"):
        _set_deck_name(result["session_id"], fork_name)
        side_chat.close(session_id)
    elif result.get("ok"):
        # pending (hook-based correlation) — best effort close
        side_chat.close(session_id)
    return result


# --- Screenshots folder ---

@app.get("/api/screenshots/status")
def screenshots_status(request: Request):
    """Current screenshot watcher status."""
    if not require_local(request):
        return {"error": "local only"}
    return screenshots.status()


@app.post("/api/screenshots/configure")
def screenshots_configure(request: Request, payload: dict):
    """Set or clear the watched folder path. Starts/stops the watcher."""
    if not require_local(request):
        return {"error": "local only"}
    path = payload.get("path", "").strip()
    with _settings_lock:
        settings = _load_settings()
        if path:
            settings["screenshots_folder"] = path
        else:
            settings.pop("screenshots_folder", None)
        _save_settings(settings)
    if path:
        return screenshots.start(path)
    else:
        screenshots.stop()
        return {"ok": True, "watching": False}


@app.get("/api/screenshots/pending")
def screenshots_pending():
    """Return and clear new screenshots since last check."""
    return {"items": screenshots.pending()}


@app.get("/api/screenshots/recent")
def screenshots_recent():
    """Return last 10 screenshots (newest last), never clears."""
    return {"items": screenshots.recent()}


@app.get("/api/screenshots/recent-files")
def screenshots_recent_files(minutes: int = 5):
    """Scan watched folder for image files modified in the last N minutes."""
    return {"items": screenshots.recent_files(minutes)}


@app.get("/api/screenshots/file")
def screenshots_file(path: str):
    """Serve a screenshot file by absolute path (read-only)."""
    from fastapi.responses import FileResponse
    import mimetypes
    p = Path(path).expanduser().resolve()
    if not p.exists() or not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    # Only serve image files from the configured screenshots folder
    cfg_path = screenshots.configured_path()
    if not cfg_path:
        return JSONResponse({"error": "no screenshots folder configured"}, status_code=403)
    cfg_resolved = Path(cfg_path).expanduser().resolve()
    try:
        p.relative_to(cfg_resolved)
    except ValueError:
        return JSONResponse({"error": "path outside screenshots folder"}, status_code=403)
    mime, _ = mimetypes.guess_type(str(p))
    return FileResponse(str(p), media_type=mime or "image/png")


# --- A plain shell ---
#
# Each session is spawned as one command, so there was nowhere to run an
# interactive command that is not kiro-cli — `kiro login` and `kiro logout`
# above all. See backend/shell.py for why this is a singleton and why it is no
# wider than what /dispatch and `pre_command` already allow. `/input` shares the
# input rate-limit bucket; see auth._INPUT_PATH.

@app.get("/api/shell")
def shell_status():
    return shell.status()


@app.post("/api/shell/open")
def shell_open(payload: dict | None = None):
    return shell.open_shell((payload or {}).get("cwd", "~"))


@app.get("/api/shell/pane")
def shell_pane(lines: int = CAPTURE_LINES):
    lines = max(1, min(lines, MAX_CAPTURE_LINES))
    return {"pane": shell.capture(lines), **shell.status()}


@app.post("/api/shell/input")
def shell_input(payload: dict):
    text = payload.get("text", "")
    if not isinstance(text, str):
        return {"ok": False, "error": "text must be a string"}
    return shell.send_text(text, submit=payload.get("submit", True) is not False)


@app.post("/api/shell/key")
def shell_key(payload: dict):
    key = payload.get("key", "")
    return shell.send_key(key if isinstance(key, str) else "")


@app.post("/api/shell/resize")
def shell_resize(payload: dict):
    cols, rows = payload.get("cols"), payload.get("rows")
    if not isinstance(cols, int) or not isinstance(rows, int) \
       or isinstance(cols, bool) or isinstance(rows, bool) \
       or cols < 1 or rows < 1:
        return {"ok": False, "error": "cols and rows must be positive integers"}
    return shell.resize(min(cols, 500), min(rows, 200))


@app.post("/api/shell/close")
def shell_close():
    return shell.close()


@app.get("/api/assist/activity")
def assist_activity():
    """Get the concierge's current pane activity for live status display."""
    if not concierge.is_alive():
        return {"activity": "Starting assistant…", "tools": []}
    
    pane = concierge._capture(25)
    
    # Extract API calls the concierge is making to Quarterdeck
    lines = pane.strip().split('\n')
    api_calls = []
    is_thinking = False
    
    for line in lines:
        line = line.strip()
        # Skip empty lines and JSON output
        if not line or line.startswith('{') or line.startswith('"') or line.startswith('['):
            continue
        
        # Look for curl calls to Quarterdeck API
        if ('● Shell' in line or '● shell' in line) and 'curl' in line and f'127.0.0.1:{PORT}' in line:
            match = re.search(r'/api/([a-z_/-]+)', line)
            if match:
                endpoint = match.group(1).split('?')[0].split('"')[0]
                api_calls.append(f"/api/{endpoint}")
        
        # Check for thinking indicator
        if '◔' in line:
            is_thinking = True
    
    # Deduplicate
    seen = set()
    unique_calls = []
    for call in api_calls:
        if call not in seen:
            seen.add(call)
            unique_calls.append(call)
    
    # Build response
    if unique_calls:
        activity = f"Querying {unique_calls[-1]}"
        tools = [{"type": "api", "detail": c} for c in unique_calls[-3:]]
    elif is_thinking:
        activity = "Thinking…"
        tools = []
    else:
        activity = "Processing…"
        tools = []
    
    return {"activity": activity, "tools": tools}


# --- audit trail ---

@app.get("/api/audit")
def get_audit(limit: int = 200, kind: str = "", session: str = ""):
    """Recent audit records, newest first.

    Readable remotely on purpose: the phone is where approvals are answered, so
    it is also where "what did I just allow" gets asked. The records carry no
    secret — the token is redacted at write time, not at read time, so there is
    nothing here that a client holding a valid token should not see.
    """
    limit = max(1, min(int(limit or 200), 2000))
    return {"records": audit.read(limit=limit, kind=kind, session=session),
            **audit.stats()}


@app.post("/api/audit/enabled")
def set_audit_enabled(payload: dict | None = None):
    """Turn recording on or off, in the setting and in the hook's flag file."""
    payload = payload or {}
    on = bool(payload.get("enabled", True))
    # Through the one serialised read-modify-write path, like every other
    # setting: two clients toggling at once must not lose one of the changes.
    try:
        with _settings_lock:
            settings = _load_settings()
            settings[audit.SETTINGS_KEY] = on
            _save_settings(settings)
    except OSError:
        return {"error": "Could not save that setting"}
    # The hook reads a file, not the settings, so the two have to be moved
    # together or the shell hook keeps writing after the switch says it stopped.
    audit.set_enabled(on)
    return {"ok": True, **audit.stats()}


@app.post("/api/assist/restart")
def assist_restart():
    """Kill and respawn the concierge session."""
    concierge.kill()
    ok = concierge.ensure_alive()
    return {"ok": ok, **concierge.status()}


@app.post("/api/assist/stop")
def assist_stop():
    """Kill the concierge session without respawning. Disabling is a settings key."""
    concierge.kill()
    return {"ok": True, **concierge.status()}


# --- Snapshot persistence ---
# Both live in ~/.osa-kiro, imported rather than rebuilt here: computing the path
# from `__file__` put them inside the .app bundle, where a reinstall deletes them
# and a read-only bundle refuses the write. See config for the whole story.

_snapshots_lock = threading.RLock()
_settings_lock = threading.RLock()
_favourites_lock = threading.RLock()


def _load_snapshots() -> list:
    """Return snapshot collections in the legacy flat shape for the frontend.

    Shape: [{id, name, date, time, sessions:[{id, name, title, cwd}]}]
    Each snapshot collection (source=snapshot) becomes one entry.
    """
    colls = _collections.load_collections()
    result = []
    for c in colls:
        if c.get("source") != "snapshot":
            continue
        # Parse date/time from created_at for display
        ts = c.get("created_at", "")
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(ts).astimezone()
            date_str = dt.strftime("%b %-d")
            time_str = dt.strftime("%I:%M %p").lstrip("0")
        except Exception:
            date_str = ""
            time_str = ""
        sessions = [
            {
                "id": m.get("session_id"),
                "name": Path(m.get("cwd") or "").name or m.get("session_id", "")[:8],
                "title": m.get("title") or "",
                "cwd": m.get("cwd") or "",
            }
            for m in c.get("members", [])
            if m.get("session_id")
        ]
        result.append({
            "id": c["id"],
            "name": c.get("name", ""),
            "date": date_str,
            "time": time_str,
            "sessions": sessions,
        })
    # Most recent first
    result.sort(key=lambda x: x.get("id", ""), reverse=True)
    return result


def _save_snapshots(data: list):
    # No-op: snapshots are now created directly as collections via /api/collections.
    # Kept for backward compat — the frontend's saveSnapshots effect still fires
    # but nothing needs to be written (collections.json is the source of truth).
    pass


@app.get("/api/snapshots")
def get_snapshots():
    return {"snapshots": _load_snapshots()}


@app.post("/api/snapshots")
def save_snapshots(payload: dict):
    snapshots = payload.get("snapshots", [])
    _save_snapshots(snapshots)
    return {"ok": True, "count": len(snapshots)}


def _load_settings() -> dict:
    # Reads SETTINGS_FILE through this module's own name rather than calling
    # config.read_settings(), so tests that repoint the file still repoint it.
    # concierge.py uses the config helper because it cannot import this module.
    with _settings_lock:
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except (json.JSONDecodeError, OSError, ValueError):
            return {}


def _save_settings(data: dict):
    with _settings_lock:
        _atomic_write_json(SETTINGS_FILE, data)


@app.get("/api/settings")
def get_settings():
    return _load_settings()


@app.post("/api/settings")
def save_settings(payload: dict):
    with _settings_lock:
        settings = _load_settings()
        settings.update(payload)
        _save_settings(settings)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Client preferences — UI state that must survive WKWebView process restarts.
# WKWebView batches localStorage writes and may drop them on fast close.
# This file-backed store is written synchronously on every update.
# ---------------------------------------------------------------------------
from .config import CLIENT_PREFS_FILE as _CLIENT_PREFS_FILE
_prefs_lock = threading.Lock()

def _load_prefs() -> dict:
    try:
        return json.loads(_CLIENT_PREFS_FILE.read_text())
    except Exception:
        return {}

@app.get("/api/prefs")
def get_prefs():
    return _load_prefs()

@app.post("/api/prefs")
async def set_prefs(request: Request):
    """Merge patch: POST {"key": value} to set individual keys."""
    body = await request.json()
    with _prefs_lock:
        prefs = _load_prefs()
        prefs.update(body)
        _atomic_write_json(_CLIENT_PREFS_FILE, prefs)
    return {"ok": True}


# --- Kiro CLI profile switching ---
# Profiles are snapshots of the auth_kv table in kiro-cli's SQLite DB.
# This lets users save multiple logins (Identity Center accounts, Builder ID)
# and switch between them without re-authenticating each time.

import sqlite3 as _sqlite3

_KIRO_AUTH_DB = Path.home() / "Library" / "Application Support" / "kiro-cli" / "data.sqlite3"
_PROFILES_DIR = Path.home() / ".kiro" / "profiles"
_PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def _profile_meta_path(name: str) -> Path:
    return _PROFILES_DIR / f"{name}.meta.json"


def _profile_data_path(name: str) -> Path:
    return _PROFILES_DIR / f"{name}.jsonl"


def _dump_auth_rows() -> list[dict]:
    """Read all rows from auth_kv as a list of {key, value} dicts."""
    if not _KIRO_AUTH_DB.exists():
        return []
    con = _sqlite3.connect(str(_KIRO_AUTH_DB), timeout=5)
    try:
        rows = con.execute("SELECT key, value FROM auth_kv").fetchall()
        return [{"key": k, "value": v} for k, v in rows]
    finally:
        con.close()


def _restore_auth_rows(rows: list[dict]) -> None:
    """Replace auth_kv content with the given rows."""
    con = _sqlite3.connect(str(_KIRO_AUTH_DB), timeout=5)
    try:
        con.execute("DELETE FROM auth_kv")
        for row in rows:
            con.execute("INSERT INTO auth_kv (key, value) VALUES (?, ?)", (row["key"], row["value"]))
        con.commit()
    finally:
        con.close()


def _token_fingerprint(rows: list[dict]) -> str:
    """Return a stable fingerprint for a set of auth rows.

    Uses a hash of the refresh_token (stable across access_token rotations)
    from the kirocli:odic:token key. Falls back to hashing the full access_token.
    This is unique per Identity Center account/role even when the start_url is identical.
    """
    import hashlib
    keys = {r["key"]: r["value"] for r in rows}
    token_key = "kirocli:odic:token"
    if token_key not in keys:
        return ""
    try:
        tok = json.loads(keys[token_key])
        # refresh_token survives access_token rotation — prefer it
        secret = tok.get("refresh_token") or tok.get("access_token", "")
        if not secret:
            return ""
        # Use first 16 chars of sha256 — enough to distinguish, not a full credential
        return hashlib.sha256(secret.encode()).hexdigest()[:16]
    except Exception:
        return ""


def _current_profile_identity() -> dict:
    """Run kiro-cli whoami and parse the output."""
    try:
        result = subprocess.run(
            ["kiro-cli", "whoami"],
            capture_output=True, text=True, timeout=10,
        )
        lines = result.stdout.strip().splitlines()
        email = ""
        provider = lines[0] if lines else "unknown"
        profile_arn = ""
        for i, line in enumerate(lines):
            if line.lower().startswith("email:"):
                email = line.split(":", 1)[1].strip()
            if line.startswith("arn:"):
                profile_arn = line.strip()
        return {"email": email, "provider": provider, "profile_arn": profile_arn}
    except Exception:
        return {"email": "unknown", "provider": "unknown", "profile_arn": ""}


@app.get("/api/profiles")
def list_profiles():
    """List all saved profiles with metadata."""
    profiles = []
    for meta_path in sorted(_PROFILES_DIR.glob("*.meta.json")):
        name = meta_path.stem.removesuffix(".meta")
        data_path = _profile_data_path(name)
        if not data_path.exists():
            continue
        if name == "_previous":
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            meta = {}
        profiles.append({
            "name": name,
            "email": meta.get("email", "?"),
            "provider": meta.get("provider", "?"),
            "profile_arn": meta.get("profile_arn", ""),
            "saved_at": meta.get("saved_at", ""),
        })
    return {"profiles": profiles}


@app.get("/api/profiles/current")
def current_profile():
    """Return the currently active identity."""
    info = _current_profile_identity()
    # Match active profile by ARN first (authoritative for same-SSO profiles),
    # fall back to token fingerprint for older profiles without ARN metadata.
    active_name = _active_profile_name()
    return {
        "email": info["email"],
        "provider": info["provider"],
        "profile_arn": info.get("profile_arn", ""),
        "active_profile": active_name or None,
    }


@app.post("/api/profiles/save")
def save_profile(payload: dict):
    """Save the current auth state as a named profile."""
    name = payload.get("name", "").strip()
    if not name or name == "_previous":
        return {"error": "Invalid profile name"}
    if "/" in name or "\\" in name:
        return {"error": "Profile name cannot contain slashes"}
    rows = _dump_auth_rows()
    if not rows:
        return {"error": "No auth data found — are you logged in?"}
    # Write data
    data_path = _profile_data_path(name)
    data_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    data_path.chmod(0o600)  # contains live OAuth tokens — owner-only
    # Write metadata — include fingerprint, profile_arn, and the full state
    # profile entry so switch_profile can restore the exact ARN kiro-cli needs.
    info = _current_profile_identity()
    state_profile = ""
    if _KIRO_AUTH_DB.exists():
        try:
            import sqlite3 as _sqlite3
            con = _sqlite3.connect(str(_KIRO_AUTH_DB), timeout=5)
            row = con.execute("SELECT value FROM state WHERE key='api.codewhisperer.profile'").fetchone()
            if row:
                state_profile = row[0]
            con.close()
        except Exception:
            pass
    # Capture the model entitlement list at save time — the only moment when
    # this profile is definitely active and its entitlements are available.
    try:
        current_models = list(available_models(force=True))
    except Exception:
        current_models = []
    meta = {
        "email": info["email"],
        "provider": info["provider"],
        "profile_arn": info.get("profile_arn", ""),
        "state_profile": state_profile,
        "token_fingerprint": _token_fingerprint(rows),
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "models": current_models,
        "models_refreshed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _profile_meta_path(name).write_text(json.dumps(meta))
    return {"ok": True, "email": info["email"]}


@app.post("/api/profiles/switch")
def switch_profile(payload: dict):
    """Switch to a saved profile."""
    name = payload.get("name", "").strip()
    if not name:
        return {"error": "Profile name is required"}
    data_path = _profile_data_path(name)
    if not data_path.exists():
        return {"error": f"Profile '{name}' not found"}
    # Auto-save current as _previous
    current_rows = _dump_auth_rows()
    if current_rows:
        prev_path = _profile_data_path("_previous")
        prev_path.write_text("\n".join(json.dumps(r) for r in current_rows) + "\n")
        prev_path.chmod(0o600)  # contains live OAuth tokens — owner-only
    # Restore target auth tokens
    try:
        rows = [json.loads(line) for line in data_path.read_text().splitlines() if line.strip()]
        _restore_auth_rows(rows)
    except Exception as e:
        return {"error": f"Failed to switch: {e}"}
    meta_path = _profile_meta_path(name)
    email = "?"
    profile_arn = ""
    state_profile = ""
    if meta_path.exists():
        try:
            meta_data = json.loads(meta_path.read_text())
            email = meta_data.get("email", "?")
            profile_arn = meta_data.get("profile_arn", "")
            state_profile = meta_data.get("state_profile", "")
        except Exception:
            pass
    # Also update the state table so kiro-cli uses the correct profile ARN.
    # kiro-cli reads api.codewhisperer.profile from the state table to determine
    # which CodeWhisperer profile (and therefore which account/subscription) to
    # use — swapping the OAuth tokens alone is not enough.
    if _KIRO_AUTH_DB.exists():
        try:
            import sqlite3 as _sqlite3
            # Use the full saved state_profile if we have it; fall back to
            # reconstructing from the profile_arn in the meta file.
            if not state_profile and profile_arn:
                state_profile = json.dumps({"arn": profile_arn, "profile_name": "QDevProfile-eu-central-1"})
            if state_profile:
                con = _sqlite3.connect(str(_KIRO_AUTH_DB), timeout=5)
                con.execute(
                    "INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)",
                    ("api.codewhisperer.profile", state_profile),
                )
                con.commit()
                con.close()
        except Exception:
            pass  # Non-fatal: token switch still happened
    # Invalidate the cached active profile so the new name shows immediately
    global _active_profile_cache, _last_profile_switch_at
    _active_profile_cache = (0.0, "")
    _last_profile_switch_at = time.time()
    # Refresh model list synchronously — different profiles have different
    # entitlements and the frontend fetches /api/options immediately after switch.
    # Takes ~1-2s but avoids the race where the old list is returned.
    # Also persist the list in meta.json so /api/options?session_id=... can serve
    # the session-scoped list even after a subsequent switch (Tier 2 memoisation).
    try:
        from .config import available_models as _am
        refreshed_models = list(_am(force=True))
    except Exception:
        refreshed_models = []
    if refreshed_models:
        try:
            meta_path = _profile_meta_path(name)
            existing_meta: dict = {}
            if meta_path.exists():
                try:
                    existing_meta = json.loads(meta_path.read_text())
                except Exception:
                    pass
            existing_meta["models"] = refreshed_models
            existing_meta["models_refreshed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            meta_path.write_text(json.dumps(existing_meta))
        except Exception:
            pass
    return {"ok": True, "name": name, "email": email, "profile_arn": profile_arn}


@app.post("/api/profiles/delete")
def delete_profile(payload: dict):
    """Delete a saved profile."""
    name = payload.get("name", "").strip()
    if not name or name == "_previous":
        return {"error": "Invalid profile name"}
    data_path = _profile_data_path(name)
    meta_path = _profile_meta_path(name)
    if not data_path.exists():
        return {"error": f"Profile '{name}' not found"}
    data_path.unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)
    return {"ok": True}


# --- Remote access ---

# Holds the subprocess handle when we spawned the remote listener ourselves.
_remote_process: subprocess.Popen | None = None
_remote_lock = threading.Lock()
_remote_server: None = None  # kept for compat; proxy mode doesn't use it
_remote_thread: threading.Thread | None = None


def _start_proxy(ts_ip: str) -> None:
    """TCP proxy: listen on ts_ip:REMOTE_PORT, forward to localhost:PORT.

    Used instead of a second uvicorn in the frozen app — sharing a FastAPI
    app object across two event loops causes deadlocks under load.
    """
    import socket, select, threading as _t

    proxy_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    proxy_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        proxy_sock.bind((ts_ip, REMOTE_PORT))
    except OSError:
        return  # already bound
    proxy_sock.listen(32)
    proxy_sock.settimeout(1.0)

    def _pipe(src, dst):
        try:
            while True:
                r, _, _ = select.select([src], [], [], 0.5)
                if r:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
        except Exception:
            pass
        finally:
            for s in (src, dst):
                try: s.close()
                except Exception: pass

    def _handle(client):
        try:
            backend = socket.create_connection(("127.0.0.1", PORT), timeout=5)
            _t.Thread(target=_pipe, args=(client, backend), daemon=True).start()
            _t.Thread(target=_pipe, args=(backend, client), daemon=True).start()
        except Exception:
            try: client.close()
            except Exception: pass

    while True:
        try:
            client, _ = proxy_sock.accept()
            _t.Thread(target=_handle, args=(client,), daemon=True).start()
        except socket.timeout:
            if getattr(threading.current_thread(), '_stop_proxy', False):
                break
        except Exception:
            break
    try: proxy_sock.close()
    except Exception: pass


LAUNCHAGENT_LABEL = "com.osa-kiro.remote"
LAUNCHAGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHAGENT_LABEL}.plist"


def _tailscale_ip() -> str:
    # The packaged app runs with a minimal PATH that may not include
    # /usr/local/bin. Try known locations before giving up.
    candidates = [
        "tailscale",
        "/usr/local/bin/tailscale",
        "/Applications/Tailscale.app/Contents/MacOS/tailscale",
    ]
    for cmd in candidates:
        try:
            r = subprocess.run([cmd, "ip", "-4"], capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                ip = r.stdout.strip().split("\n")[0]
                if ip:
                    return ip
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return ""


def _uvicorn_cmd() -> list[str]:
    """Return a command prefix that can run uvicorn as a subprocess.

    In the packaged .app there is no `uvicorn` on PATH — it is bundled as a
    Python module, not a CLI. We need to find a Python binary that has uvicorn
    importable and use `python -m uvicorn` instead. Candidates in preference
    order:
      1. The Python that is running us right now (sys.executable) — works in
         dev where sys.executable is the venv python.
      2. The Python framework binary shipped inside the .app bundle.
      3. `uvicorn` on PATH as a last resort (works in dev if the venv is active).
    """
    import sys
    import shutil

    # In a PyInstaller bundle sys.executable is the Quarterdeck binary itself,
    # which cannot run -m uvicorn. Detect that case via sys.frozen.
    if not getattr(sys, "frozen", False):
        # Dev environment: sys.executable is the venv python.
        return [sys.executable, "-m", "uvicorn"]

    # Bundled app: look for the Python framework binary next to the bundle.
    bundle_dir = Path(sys.executable).parent.parent  # .../Quarterdeck.app/Contents
    candidates = [
        bundle_dir / "Frameworks" / "python3.14",
        bundle_dir / "Resources" / "python3.14",
    ]
    for p in candidates:
        if p.is_file() and os.access(p, os.X_OK):
            return [str(p), "-m", "uvicorn"]

    # Last resort: hope uvicorn is on PATH (works if launched from a shell).
    uvicorn_bin = shutil.which("uvicorn")
    if uvicorn_bin:
        return [uvicorn_bin]
    return ["uvicorn"]  # will raise FileNotFoundError if missing — caller handles it


def _remote_running() -> bool:
    global _remote_process, _remote_thread
    with _remote_lock:
        if _remote_thread and _remote_thread.is_alive():
            return True
        if _remote_process and _remote_process.poll() is None:
            return True
    # Detect any listener actually bound to the Tailscale address on our port.
    # This is the authoritative check — a process is running only if the socket
    # exists. _launchagent_loaded() alone is not sufficient: the agent may be
    # installed but the socket not yet bound (e.g. after a Stop that unloaded it).
    ts_ip = _tailscale_ip()
    if ts_ip:
        try:
            r = subprocess.run(
                ["lsof", "-i", f"TCP@{ts_ip}:{REMOTE_PORT}", "-sTCP:LISTEN", "-t"],
                capture_output=True, text=True, timeout=3,
            )
            if r.stdout.strip():
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return False


def _launchagent_loaded() -> bool:
    try:
        r = subprocess.run(["launchctl", "list", LAUNCHAGENT_LABEL],
                           capture_output=True, text=True, timeout=3)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _battery_state() -> dict:
    """Whether the Mac is on battery and battery percentage.

    On battery, caffeinate's sleep assertion is ignored and the Mac will sleep
    with the lid closed — making it unreachable. Worth showing in the UI.
    """
    try:
        r = subprocess.run(
            ["pmset", "-g", "batt"], capture_output=True, text=True, timeout=3)
        if r.returncode != 0:
            return {}
        output = r.stdout
        on_battery = "'Battery Power'" in output
        # Parse percentage: "99%; charging" or "99%; discharging"
        import re as _re
        match = _re.search(r"(\d+)%", output)
        percent = int(match.group(1)) if match else None
        return {"on_battery": on_battery, "percent": percent}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}


@app.get("/api/remote/status")
def remote_status(request: Request):
    """Tailscale address, token presence, running state, LaunchAgent state."""
    if not require_local(request):
        return {"error": "local only"}
    ts_ip = _tailscale_ip()
    token = auth.read_token()
    port = REMOTE_PORT
    # Battery state: on battery means sleep assertion is ignored and the Mac
    # will sleep with the lid closed, making it unreachable.
    battery = _battery_state()
    remote_info = auth.last_remote_request_info()
    return {
        "tailscale_ip": ts_ip,
        "tailscale_up": bool(ts_ip),
        "token_set": bool(token),
        "token_masked": (token[:4] + "…" + token[-4:]) if len(token) >= 8 else ("set" if token else ""),
        "running": _remote_running(),
        "launchagent_installed": LAUNCHAGENT_PLIST.exists(),
        "launchagent_loaded": _launchagent_loaded(),
        "url": f"http://{ts_ip}:{port}/app/" if ts_ip else "",
        "port": port,
        "on_battery": battery.get("on_battery"),
        "battery_percent": battery.get("percent"),
        "last_remote_at": remote_info["last_remote_at"],
        "last_remote_ip": remote_info["last_remote_ip"],
    }


@app.post("/api/remote/start")
def remote_start(request: Request):
    """Spawn a remote uvicorn listener on the Tailscale address."""
    global _remote_process, _remote_server, _remote_thread
    if not require_local(request):
        return {"error": "local only"}
    ts_ip = _tailscale_ip()
    if not ts_ip:
        return {"error": "Tailscale is not up — no IPv4 address found"}
    token = auth.read_token()
    if not token:
        token = auth.generate_token()
        auth.write_token(token)
    with _remote_lock:
        # Already running in-thread?
        if _remote_thread and _remote_thread.is_alive():
            return {"ok": True, "already_running": True, "url": f"http://{ts_ip}:{REMOTE_PORT}/app/"}
        # Already running as subprocess?
        if _remote_process and _remote_process.poll() is None:
            return {"ok": True, "already_running": True, "url": f"http://{ts_ip}:{REMOTE_PORT}/app/"}

        import sys
        env = {**os.environ, "OSA_KIRO_TOKEN": token}

        if getattr(sys, "frozen", False):
            # Packaged app: use the TCP proxy (see _start_proxy above).
            _remote_server = None
            _remote_thread = threading.Thread(
                target=_start_proxy, args=(ts_ip,), daemon=True, name="remote-proxy")
            _remote_thread.start()
        else:
            # Dev/repo install: spawn a subprocess so caffeinate keeps the Mac awake.
            _remote_process = subprocess.Popen(
                ["caffeinate", "-si",
                 *_uvicorn_cmd(), "backend.api:app",
                 "--host", ts_ip, "--port", str(REMOTE_PORT)],
                cwd=str(Path(__file__).parent.parent),
                env=env,
            )
    s = _load_settings(); s["remote-autostart"] = True; _save_settings(s)
    return {"ok": True, "url": f"http://{ts_ip}:{REMOTE_PORT}/app/", "token_masked": token[:4] + "…" + token[-4:]}


@app.post("/api/remote/stop")
def remote_stop(request: Request):
    """Stop the remote listener — whether started by the app or externally."""
    global _remote_process, _remote_server, _remote_thread
    if not require_local(request):
        return {"error": "local only"}

    # Persist intent BEFORE any kill so a crash mid-stop cannot leave
    # remote-autostart=True and have the lifespan hook restart the listener.
    s = _load_settings(); s["remote-autostart"] = False; _save_settings(s)

    stopped = False

    # Unload the LaunchAgent first so launchd does not respawn the listener
    # the moment we kill it. Silent if not installed.
    if _launchagent_loaded():
        try:
            subprocess.run(
                ["launchctl", "unload", str(LAUNCHAGENT_PLIST)],
                capture_output=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    with _remote_lock:
        # Stop thread-based proxy (packaged app).
        if _remote_thread and _remote_thread.is_alive():
            _remote_thread._stop_proxy = True  # type: ignore[attr-defined]
            _remote_thread = None
            stopped = True
        # Stop thread-based uvicorn server (legacy path).
        if _remote_server:
            _remote_server.should_exit = True
            _remote_server = None
            _remote_thread = None
            stopped = True
        # Kill subprocess (dev/repo caffeinate+uvicorn).
        if _remote_process:
            _remote_process.terminate()
            _remote_process = None
            stopped = True

    # Kill any *other* process bound to the Tailscale address on our port.
    # Exclude our own PID — the proxy socket is owned by this process and
    # lsof would otherwise return our own PID, causing a self-SIGTERM.
    our_pid = os.getpid()
    ts_ip = _tailscale_ip()
    if ts_ip:
        try:
            r = subprocess.run(
                ["lsof", "-ti", f"TCP@{ts_ip}:{REMOTE_PORT}", "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=3,
            )
            for pid_str in r.stdout.strip().split():
                try:
                    pid = int(pid_str)
                    if pid == our_pid:
                        continue
                    os.kill(pid, 15)  # SIGTERM
                    stopped = True
                except (ValueError, OSError):
                    pass
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return {"ok": True, "stopped": stopped}


@app.get("/api/remote/token")
def get_remote_token(request: Request):
    """Return token info + QR code data URI for the phone setup screen."""
    if not require_local(request):
        return {"error": "local only"}
    token = auth.read_token()
    if not token:
        return {"token_set": False, "qr": None}
    ts_ip = _tailscale_ip()
    url = f"http://{ts_ip}:{REMOTE_PORT}/app/" if ts_ip else ""
    # Auto-login URL: scanning this QR logs the phone in immediately, no manual
    # entry. It carries a single-use code rather than the token — a URL is
    # written to the phone's history and this server's access log, and the token
    # is long-lived and shared, so it must not be the thing in the query string.
    # The code expires in two minutes, so the QR is only good while it is on
    # screen; reopening this panel mints a fresh one.
    login_url = ""
    if ts_ip:
        code = auth.mint_exchange_code()
        login_url = f"http://{ts_ip}:{REMOTE_PORT}/login?c={code}&next=/app/"
    qr_data = _make_qr_svg(login_url) if login_url else ""
    return {
        "token_set": True,
        "token_masked": token[:4] + "…" + token[-4:],
        "token": token,
        "url": url,
        "login_url": login_url,
        "qr_svg": qr_data,
    }


@app.post("/api/remote/rotate")
def rotate_token(request: Request):
    """Generate a new token. All existing sessions are logged out."""
    if not require_local(request):
        return {"error": "local only"}
    new_token = auth.generate_token()
    auth.write_token(new_token)
    return {"ok": True, "token_masked": new_token[:4] + "…" + new_token[-4:]}


@app.post("/api/remote/launchagent/install")
def launchagent_install(request: Request):
    """Write a LaunchAgent plist so remote serving survives a reboot."""
    if not require_local(request):
        return {"error": "local only"}
    import sys
    if getattr(sys, "frozen", False):
        # In the packaged app there is no separate uvicorn binary to run via
        # LaunchAgent. Remote serving auto-restarts via the remote-autostart
        # setting instead. The LaunchAgent approach only works for source installs.
        return {"error": "LaunchAgent not supported in the packaged app — remote access auto-restarts with Quarterdeck instead. Start Quarterdeck at login via System Settings → General → Login Items."}
    ts_ip = _tailscale_ip()
    if not ts_ip:
        return {"error": "Tailscale is not up"}
    project_dir = str(Path(__file__).parent.parent)
    python = str(Path(project_dir) / "venv" / "bin" / "python3")
    if not Path(python).exists():
        python = "python3"
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LAUNCHAGENT_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>caffeinate</string><string>-si</string>
    <string>uvicorn</string><string>backend.api:app</string>
    <string>--host</string><string>{ts_ip}</string>
    <string>--port</string><string>{REMOTE_PORT}</string>
  </array>
  <key>WorkingDirectory</key><string>{project_dir}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:{Path(project_dir) / "venv" / "bin"}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{Path.home()}/.osa-kiro/remote.log</string>
  <key>StandardErrorPath</key><string>{Path.home()}/.osa-kiro/remote.log</string>
</dict>
</plist>"""
    LAUNCHAGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHAGENT_PLIST.write_text(plist)
    subprocess.run(["launchctl", "load", str(LAUNCHAGENT_PLIST)], capture_output=True)
    return {"ok": True, "path": str(LAUNCHAGENT_PLIST)}


@app.post("/api/remote/launchagent/uninstall")
def launchagent_uninstall(request: Request):
    """Stop and remove the LaunchAgent."""
    if not require_local(request):
        return {"error": "local only"}
    subprocess.run(["launchctl", "unload", str(LAUNCHAGENT_PLIST)], capture_output=True)
    try:
        LAUNCHAGENT_PLIST.unlink()
    except FileNotFoundError:
        pass
    return {"ok": True}


def _make_qr_svg(text: str) -> str:
    """Generate a minimal QR code SVG without external dependencies.

    Uses the `qrcode` library if available, otherwise returns an empty string.
    The endpoint still works without it — the UI hides the QR element.
    """
    try:
        import qrcode  # type: ignore
        import qrcode.image.svg  # type: ignore
        import io
        factory = qrcode.image.svg.SvgPathImage
        img = qrcode.make(text, image_factory=factory, box_size=6)
        buf = io.BytesIO()
        img.save(buf)
        return buf.getvalue().decode("utf-8")
    except Exception:
        return ""


# --- Per-device tokens ---

from . import devices as _devices


@app.get("/api/devices")
def list_devices(request: Request):
    """List all registered device tokens (masked)."""
    if not require_local(request):
        return {"error": "local only"}
    return {"devices": _devices.list_devices()}


@app.post("/api/devices")
def create_device(request: Request, payload: dict):
    """Create a new device token. Returns the full token once."""
    if not require_local(request):
        return {"error": "local only"}
    name = payload.get("name", "unnamed")
    result = _devices.create_device(name)
    if "error" in result:
        return result
    return {"ok": True, **result}


@app.post("/api/devices/{device_id}/revoke")
def revoke_device(device_id: str, request: Request):
    """Revoke a device token by its id."""
    if not require_local(request):
        return {"error": "local only"}
    return _devices.revoke_device(device_id)


@app.post("/api/devices/{device_id}/rename")
def rename_device(device_id: str, request: Request, payload: dict):
    """Rename a device."""
    if not require_local(request):
        return {"error": "local only"}
    name = payload.get("name", "")
    if not name.strip():
        return {"error": "Name cannot be empty"}
    return _devices.rename_device(device_id, name)


# --- Favourites ---
# Backed by the "Favourites" collection (source=favourites) in collections.json.
# The legacy favourites.json is read once by _migrate_legacy() in collections.py
# on first load, then left in place as a backup.

def _load_favourites() -> list:
    """Return favourites as the legacy flat list [{id, title, cwd, ...}]."""
    coll = _collections.get_favourites_collection()
    if coll is None:
        return []
    return [
        {
            "id": m.get("session_id"),
            "title": m.get("title") or "",
            "cwd": m.get("cwd") or "",
            "name": Path(m.get("cwd") or "").name,
        }
        for m in coll.get("members", [])
        if m.get("session_id")
    ]


def _save_favourites(data: list):
    """Persist a flat favourites list back into the Favourites collection."""
    coll = _collections.ensure_favourites_collection()
    coll_id = coll["id"]
    members = [
        {
            "session_id": f.get("id"),
            "cwd": f.get("cwd"),
            "title": f.get("title"),
            "agent": None,
            "model": None,
            "prompt": None,
        }
        for f in data
        if f.get("id")
    ]
    with _collections._lock:
        collections = _collections.load_collections()
        for c in collections:
            if c["id"] == coll_id:
                c["members"] = members
                c["updated_at"] = _collections._now_iso()
                _collections.save_collections(collections)
                break


def _remove_favourites(session_ids: set[str]) -> None:
    """Remove several sessions without exposing a read-modify-write race."""
    if not session_ids:
        return
    with _favourites_lock:
        favs = [
            favourite for favourite in _load_favourites()
            if favourite.get("id") not in session_ids
        ]
        _save_favourites(favs)


@app.get("/api/favourites")
def get_favourites():
    # Derive the display path on read rather than migrating the file: entries
    # written before cwd_display existed would otherwise render as full paths.
    return {
        "favourites": [
            {**fav, "cwd_display": shorten_path(fav.get("cwd", "") or "")}
            for fav in _load_favourites()
        ]
    }


@app.post("/api/favourites")
def save_favourites(payload: dict):
    _save_favourites(payload.get("favourites", []))
    return {"ok": True}


@app.post("/api/favourites/add")
def add_favourite(payload: dict):
    session_id = payload.get("id", "")
    if not session_id:
        return {"error": "No id"}
    meta = read_metadata(session_id)
    if not meta:
        return {"error": "Session not found"}
    with _favourites_lock:
        favs = _load_favourites()
        # Don't duplicate
        if any(f.get("id") == session_id for f in favs):
            return {"ok": True, "message": "Already favourited"}
        favs.append({
            "id": session_id,
            "title": clean_title(meta.get("title", "Untitled"), session_id),
            "cwd": meta.get("cwd", ""),
            "cwd_display": shorten_path(meta.get("cwd", "")),
            "name": Path(meta.get("cwd", "")).name,
        })
        _save_favourites(favs)
    return {"ok": True}


@app.post("/api/favourites/remove")
def remove_favourite(payload: dict):
    session_id = payload.get("id", "")
    _remove_favourites({session_id})
    return {"ok": True}


@app.post("/api/favourites/purge-stale")
def purge_stale_favourites():
    """Remove favourites whose cwd no longer exists on disk."""
    with _favourites_lock:
        favs = _load_favourites()
        stale = {f["id"] for f in favs if f.get("cwd") and not Path(f["cwd"]).exists()}
        if stale:
            _remove_favourites(stale)
    return {"ok": True, "removed": len(stale)}


# --- Collections ---

from . import collections as _collections


def _enrich_members(collection: dict) -> dict:
    """Annotate each member with availability status."""
    c = dict(collection)
    c["members"] = list(c.get("members", []))
    for m in c["members"]:
        sid = m.get("session_id")
        if not sid:
            m["availability"] = "recipe"
        elif read_metadata(sid):
            lock = read_lock(sid)
            m["availability"] = "active" if (lock and is_process_alive(lock.get("pid", 0))) else "done"
        else:
            m["availability"] = "missing"
    return c


def _remove_from_all_collections(session_ids: set) -> None:
    """Remove deleted sessions from every collection they appear in."""
    if not session_ids:
        return
    collections = _collections.load_collections()
    changed = False
    for c in collections:
        before = len(c.get("members", []))
        c["members"] = [
            m for m in c.get("members", [])
            if m.get("session_id") not in session_ids
        ]
        if len(c["members"]) < before:
            c["updated_at"] = _collections._now_iso()
            changed = True
    if changed:
        _collections.save_collections(collections)


@app.get("/api/collections")
def list_collections():
    """List all collections (no availability annotation — use /enriched for that)."""
    return {"collections": _collections.load_collections()}


@app.get("/api/collections/enriched")
def list_collections_enriched():
    """List all collections with member availability annotations."""
    colls = _collections.load_collections()
    return {"collections": [_enrich_members(c) for c in colls]}


@app.get("/api/collections/{collection_id}")
def get_one_collection(collection_id: str):
    """Get a single collection by ID."""
    c = _collections.get_collection(collection_id)
    if not c:
        return {"error": "Collection not found"}
    return {"collection": c}


@app.get("/api/collections/{collection_id}/enriched")
def get_collection_enriched(collection_id: str):
    """Get a single collection with member availability annotations."""
    c = _collections.get_collection(collection_id)
    if not c:
        return {"error": "Collection not found"}
    return {"collection": _enrich_members(c)}


@app.post("/api/collections")
def create_collection(payload: dict):
    """Create a new collection."""
    name = payload.get("name", "").strip()
    if not name:
        return {"error": "Name is required"}
    source = payload.get("source", "manual")
    members = payload.get("members", [])
    meta = payload.get("meta")
    c = _collections.create_collection(name, source=source, members=members, meta=meta)
    return {"ok": True, "collection": c}


@app.post("/api/collections/{collection_id}/rename")
def rename_collection(collection_id: str, payload: dict):
    name = payload.get("name", "").strip()
    if not name:
        return {"error": "Name is required"}
    c = _collections.rename_collection(collection_id, name)
    if not c:
        return {"error": "Collection not found"}
    return {"ok": True, "collection": c}


@app.delete("/api/collections/{collection_id}")
def delete_collection(collection_id: str):
    if _collections.delete_collection(collection_id):
        return {"ok": True}
    return {"error": "Collection not found"}


@app.post("/api/collections/{collection_id}/members")
def add_collection_member(collection_id: str, payload: dict):
    """Add a member (session or recipe) to a collection."""
    member = {
        "session_id": payload.get("session_id"),
        "cwd": payload.get("cwd"),
        "title": payload.get("title"),
        "agent": payload.get("agent"),
        "model": payload.get("model"),
        "prompt": payload.get("prompt"),
    }
    c = _collections.add_member(collection_id, member)
    if not c:
        return {"error": "Collection not found"}
    return {"ok": True, "collection": c}


@app.post("/api/collections/{collection_id}/members/remove")
def remove_collection_member(collection_id: str, payload: dict):
    """Remove a member by session_id."""
    session_id = payload.get("session_id", "")
    if not session_id:
        return {"error": "session_id is required"}
    c = _collections.remove_member(collection_id, session_id)
    if not c:
        return {"error": "Collection not found"}
    return {"ok": True, "collection": c}


@app.post("/api/collections/{collection_id}/reorder")
def reorder_collection(collection_id: str, payload: dict):
    ids = payload.get("session_ids") or payload.get("ids", [])
    if not ids:
        return {"error": "session_ids is required"}
    c = _collections.reorder_members(collection_id, ids)
    if not c:
        return {"error": "Collection not found"}
    return {"ok": True, "collection": c}


@app.post("/api/collections/{collection_id}/start")
def start_collection(collection_id: str):
    """Spawn sessions for members that aren't running."""
    c = _collections.get_collection(collection_id)
    if not c:
        return {"error": "Collection not found"}
    started = []
    failed = []
    for m in c.get("members", []):
        sid = m.get("session_id")
        cwd = m.get("cwd")
        # Skip already-running sessions
        if sid:
            lock = read_lock(sid)
            if lock and is_process_alive(lock.get("pid", 0)):
                continue
        # Need a cwd to spawn
        if not cwd:
            if sid:
                meta = read_metadata(sid)
                cwd = meta.get("cwd") if meta else None
            if not cwd:
                failed.append({"session_id": sid, "reason": "no cwd"})
                continue
        # Spawn or resume
        kwargs = {}
        if m.get("agent"):
            kwargs["agent"] = m["agent"]
        if m.get("model"):
            kwargs["model"] = m["model"]
        if sid:
            result = tmux.spawn(cwd, resume_id=sid, **kwargs)
        else:
            result = tmux.spawn(cwd, **kwargs)
        if result.get("ok"):
            started.append({"session_id": sid or result.get("id"), "cwd": cwd})
        else:
            failed.append({"session_id": sid, "reason": result.get("error", "spawn failed")})
    return {"ok": True, "started": started, "failed": failed}


# --- Archive search ---
# --- Stats ---
@app.get("/api/stats")
def get_stats(period: str = "all", date_from: str = "", date_to: str = ""):
    """Compute session statistics from all session files."""
    from collections import Counter
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    # Determine date range
    range_start = None
    range_end = None
    if period == "7d":
        range_start = now - timedelta(days=7)
    elif period == "30d":
        range_start = now - timedelta(days=30)
    elif period == "90d":
        range_start = now - timedelta(days=90)
    elif period == "custom" and date_from:
        try:
            range_start = datetime.fromisoformat(date_from + "T00:00:00+00:00")
        except:
            pass
        if date_to:
            try:
                range_end = datetime.fromisoformat(date_to + "T23:59:59+00:00")
            except:
                pass

    if not SESSIONS_DIR.exists():
        return {"total_sessions": 0}

    json_files = list(SESSIONS_DIR.glob("*.json"))
    total = 0
    home = str(Path.home())

    projects_sessions = Counter()
    projects_messages = Counter()
    projects_cwd = {}  # project_name → full cwd path
    months = Counter()
    weekdays = Counter()
    tools = Counter()
    message_count = 0
    session_durations = []
    longest_sessions = []
    empty_sessions = []
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def extract_project(cwd):
        if "/PROJECTS/" in cwd:
            parts = cwd.split("/")
            idx = parts.index("PROJECTS")
            for i in range(idx + 1, min(idx + 4, len(parts))):
                if parts[i] not in ("PERSONAL", "PORSCHE", "ACTUAL", "RESEARCH", ""):
                    return parts[i], "/".join(parts[:i+1])
        elif "Obsidian" in cwd:
            return "Obsidian Vault", cwd
        elif cwd:
            return Path(cwd).name, cwd
        return None, None

    for jf in json_files:
        try:
            meta = json.loads(jf.read_text())
            cwd = meta.get("cwd") or ""
            created = meta.get("created_at") or ""
            updated = meta.get("updated_at") or ""
            title = clean_title(meta_title(meta) or "Untitled", jf.stem) or "Untitled"
            session_id = jf.stem

            # Date range filter
            if range_start or range_end:
                if not created:
                    continue
                try:
                    dt_created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if range_start and dt_created < range_start:
                        continue
                    if range_end and dt_created > range_end:
                        continue
                except:
                    continue

            total += 1
            proj_name, proj_path = extract_project(cwd)
            if proj_name:
                projects_sessions[proj_name] += 1
                if proj_path and proj_name not in projects_cwd:
                    projects_cwd[proj_name] = proj_path

            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    months[dt.strftime("%Y-%m")] += 1
                    weekdays[day_names[dt.weekday()]] += 1
                except:
                    pass

            # Duration
            dur_min = 0
            if created and updated:
                try:
                    dt1 = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    dt2 = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    dur_min = (dt2 - dt1).total_seconds() / 60
                    if 0 < dur_min < 1440:
                        session_durations.append(dur_min)
                        longest_sessions.append({"id": session_id, "title": title[:60], "cwd": cwd, "cwd_display": shorten_path(cwd), "duration_min": round(dur_min)})
                except:
                    pass

            # Detect empty sessions (no jsonl or tiny jsonl)
            jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
            if not jsonl_path.exists() or jsonl_path.stat().st_size < 100:
                empty_sessions.append({"id": session_id, "title": title[:60], "cwd": cwd, "cwd_display": shorten_path(cwd), "created_at": created[:10] if created else ""})
        except:
            continue

    # Sort longest sessions
    longest_sessions.sort(key=lambda s: s["duration_min"], reverse=True)
    longest_sessions = longest_sessions[:10]

    # Count messages per project from JSONL files
    monthly_messages = Counter()
    jsonl_files = sorted(SESSIONS_DIR.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:100]
    for jf in jsonl_files:
        try:
            if jf.stat().st_size > 2_000_000:
                continue
            session_id = jf.stem
            meta = read_metadata(session_id)
            if not meta:
                continue
            cwd = meta.get("cwd", "") or ""
            created = meta.get("created_at", "") or ""

            # Apply same date filter to message counting
            if range_start or range_end:
                if not created:
                    continue
                try:
                    dt_created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if range_start and dt_created < range_start:
                        continue
                    if range_end and dt_created > range_end:
                        continue
                except:
                    continue

            session_month = created[:7] if created else ""
            proj_name, _ = extract_project(cwd)
            msg_in_session = 0
            user_msg_in_session = 0
            with open(jf) as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        kind = entry.get("kind", "")
                        if kind == "AssistantMessage":
                            message_count += 1
                            msg_in_session += 1
                            for block in entry.get("data", {}).get("content", []):
                                if isinstance(block, dict) and block.get("kind") == "toolUse":
                                    tools[block.get("data", {}).get("name", "")] += 1
                        elif kind == "Prompt":
                            user_msg_in_session += 1
                    except:
                        continue
            if proj_name:
                projects_messages[proj_name] += msg_in_session
            if session_month:
                monthly_messages[session_month] += msg_in_session
        except:
            continue

    avg_duration = sum(session_durations) / len(session_durations) if session_durations else 0

    # Build top projects with messages and paths
    top_projects = []
    for name, msg_count in projects_messages.most_common(10):
        top_projects.append({
            "name": name,
            "messages": msg_count,
            "sessions": projects_sessions.get(name, 0),
            "cwd": projects_cwd.get(name, ""),
            "cwd_display": shorten_path(projects_cwd.get(name, "")),
        })

    return {
        "total_sessions": total,
        "avg_duration_min": round(avg_duration, 1),
        "messages_sampled": message_count,
        "top_projects": top_projects,
        "monthly_activity": sorted(months.items())[-6:],
        "monthly_messages": sorted(monthly_messages.items())[-6:],
        "weekday_activity": [[d, weekdays.get(d, 0)] for d in day_names],
        "top_tools": tools.most_common(10),
        "longest_sessions": longest_sessions,
        "empty_sessions": empty_sessions[:20],
    }


# --- Cleanup / Zombie detection ---
@app.get("/api/cleanup/preview")
def preview_cleanup():
    """
    Identify zombie sessions:
    - 0-1 turns AND <5 min duration → auto-archive candidates
    - Inactive >24h (has lock but process dead, or idle >24h) → prompt to close
    Returns lists for preview before deletion.
    """
    from datetime import datetime, timezone, timedelta

    if not SESSIONS_DIR.exists():
        return {"zombies": [], "stale": [], "summary": {}}

    now = datetime.now(timezone.utc)
    zombies = []  # 0-1 turns, <5 min
    stale = []    # inactive >24h

    # Retention setting: sessions older than this many days are stale
    settings = _load_settings()
    retention_days = settings.get("retention-days")
    retention_hours = retention_days * 24 if isinstance(retention_days, (int, float)) and retention_days > 0 else None

    for json_file in SESSIONS_DIR.glob("*.json"):
        session_id = json_file.stem
        meta = read_metadata(session_id)
        if not meta:
            continue

        created = meta.get("created_at", "")
        updated = meta.get("updated_at", "")
        title = clean_title(meta.get("title", "Untitled"), session_id)
        cwd = meta.get("cwd", "")

        # Calculate duration
        dur_min = 0
        if created and updated:
            try:
                dt1 = datetime.fromisoformat(created.replace("Z", "+00:00"))
                dt2 = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                dur_min = (dt2 - dt1).total_seconds() / 60
            except:
                pass

        # Count turns from JSONL
        jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
        turn_count = 0
        if jsonl_path.exists():
            try:
                with open(jsonl_path) as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            if entry.get("kind") == "Prompt":
                                turn_count += 1
                        except:
                            continue
            except:
                pass

        # Check if active (has live process)
        lock_data = read_lock(session_id)
        is_active = lock_data and is_process_alive(lock_data.get("pid", 0))

        # Skip active sessions
        if is_active:
            # But check if idle >24h (or retention threshold, whichever is shorter)
            stale_threshold = min(24, retention_hours) if retention_hours else 24
            if updated:
                try:
                    dt_updated = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    hours_idle = (now - dt_updated).total_seconds() / 3600
                    if hours_idle > stale_threshold:
                        stale.append({
                            "id": session_id,
                            "title": title[:60],
                            "cwd": cwd,
                            "cwd_display": shorten_path(cwd),
                            "turns": turn_count,
                            "duration_min": round(dur_min),
                            "hours_idle": round(hours_idle),
                            "reason": "idle_24h",
                        })
                except:
                    pass
            continue

        # Zombie detection: 0-1 turns AND <5 min duration
        if turn_count <= 1 and dur_min < 5:
            zombies.append({
                "id": session_id,
                "title": title[:60],
                "cwd": cwd,
                "cwd_display": shorten_path(cwd),
                "turns": turn_count,
                "duration_min": round(dur_min, 1),
                "created_at": created[:10] if created else "",
                "reason": "one_shot",
            })
        # Retention check: sessions older than retention_days
        elif retention_hours and updated:
            try:
                dt_updated = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                hours_old = (now - dt_updated).total_seconds() / 3600
                if hours_old > retention_hours:
                    stale.append({
                        "id": session_id,
                        "title": title[:60],
                        "cwd": cwd,
                        "cwd_display": shorten_path(cwd),
                        "turns": turn_count,
                        "duration_min": round(dur_min),
                        "hours_idle": round(hours_old),
                        "reason": f"older_than_{retention_days}d",
                    })
            except:
                pass

    paste_bytes = paste_store.storage_bytes()
    return {
        "zombies": zombies,
        "stale": stale,
        "paste_bytes": paste_bytes,
        "paste_size_display": paste_store._fmt_bytes(paste_bytes),
        "summary": {
            "zombie_count": len(zombies),
            "stale_count": len(stale),
            "total_cleanable": len(zombies) + len(stale),
        }
    }


@app.post("/api/cleanup/apply")
def apply_cleanup(payload: dict):
    """
    Delete specified sessions.
    payload: { "session_ids": ["id1", "id2", ...] }
    """
    session_ids = payload.get("session_ids", [])
    deleted = []
    failed = []

    for session_id in session_ids:
        try:
            # V3 session: delete the sess_<uuid>/ directory
            if v3mod.is_v3_session(session_id):
                d = v3mod.session_dir(session_id)
                if d and d.exists():
                    import shutil as _shutil
                    _shutil.rmtree(str(d))
                    deleted.append(session_id)
                else:
                    failed.append({"id": session_id, "reason": "not_found"})
                continue

            # Don't delete active sessions
            lock_data = read_lock(session_id)
            if lock_data and is_process_alive(lock_data.get("pid", 0)):
                failed.append({"id": session_id, "reason": "still_active"})
                continue

            # Delete all session files
            for ext in (".json", ".jsonl", ".lock", ".history"):
                p = SESSIONS_DIR / f"{session_id}{ext}"
                if p.exists():
                    p.unlink()
            deleted.append(session_id)
        except Exception as e:
            failed.append({"id": session_id, "reason": str(e)})

    _remove_favourites(set(deleted))
    if deleted:
        _invalidate_projects_cache()
    # Sweep expired paste files alongside session cleanup
    paste_store.sweep()
    return {"deleted": deleted, "failed": failed, "count": len(deleted)}


# ── Paste store ────────────────────────────────────────────────────────────

@app.post("/api/pastes")
def create_paste(payload: dict):
    """Save a pasted document and return its metadata.

    Body: {text: str, session_id?: str, name?: str}
    Returns: {id, name, session_id, path, lines, bytes, size_display, preview}
    """
    text = payload.get("text", "")
    if not text.strip():
        return {"error": "No text provided"}
    session_id = payload.get("session_id") or None
    name = payload.get("name") or None
    try:
        meta = paste_store.save(session_id, text, name=name)
        return meta
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/pastes/{session_id}/{name}")
def get_paste(session_id: str, name: str):
    """Fetch the full text of a paste file."""
    try:
        text = paste_store.read(session_id, name)
        return {"text": text}
    except FileNotFoundError:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Paste not found")
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/pastes/{session_id}/{name}")
def delete_paste(session_id: str, name: str):
    """Delete a paste file."""
    try:
        paste_store.delete(session_id, name)
        return {"ok": True}
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))


# --- Projects view ---
_projects_cache: dict = {"data": None, "ts": 0.0}
PROJECTS_TTL = 60.0  # seconds; the scan walks every session file, so avoid redoing it per poll


@app.get("/api/projects")
def get_projects(refresh: bool = False):
    """
    Get sessions grouped by project with:
    - Hot projects (most recent activity, most turns)
    - Abandoned threads (started but no activity in 7+ days)
    """
    from datetime import datetime, timezone, timedelta
    from collections import defaultdict

    cached = _projects_cache["data"]
    if cached is not None and not refresh and (time.time() - _projects_cache["ts"]) < PROJECTS_TTL:
        return {**cached, "cached": True,
                "age_seconds": round(time.time() - _projects_cache["ts"], 1)}

    if not SESSIONS_DIR.exists():
        return {"projects": [], "hot": [], "abandoned": []}

    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    # Project data structure
    projects = defaultdict(lambda: {
        "sessions": [],
        "total_turns": 0,
        "total_messages": 0,
        "last_activity": "",
        "cwd": "",
    })

    abandoned = []

    def extract_project(cwd):
        """Extract project name from cwd path."""
        if not cwd:
            return None, None
        if "/PROJECTS/" in cwd:
            parts = cwd.split("/")
            idx = parts.index("PROJECTS")
            for i in range(idx + 1, min(idx + 4, len(parts))):
                if parts[i] not in ("PERSONAL", "PORSCHE", "ACTUAL", "RESEARCH", ""):
                    return parts[i], "/".join(parts[:i+1])
        elif "Obsidian" in cwd:
            return "Obsidian Vault", cwd
        elif cwd:
            return Path(cwd).name, cwd
        return None, None

    # Process all sessions
    for json_file in SESSIONS_DIR.glob("*.json"):
        session_id = json_file.stem
        meta = read_metadata(session_id)
        if not meta:
            continue

        cwd = meta.get("cwd", "") or ""
        created = meta.get("created_at", "") or ""
        updated = meta.get("updated_at", "") or ""
        title = clean_title(meta_title(meta) or "Untitled", session_id) or "Untitled"

        proj_name, proj_path = extract_project(cwd)
        if not proj_name:
            continue

        # Count turns and messages from JSONL
        jsonl_path = SESSIONS_DIR / f"{session_id}.jsonl"
        turn_count = 0
        msg_count = 0
        if jsonl_path.exists():
            try:
                with open(jsonl_path) as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            kind = entry.get("kind", "")
                            if kind == "Prompt":
                                turn_count += 1
                            elif kind == "AssistantMessage":
                                msg_count += 1
                        except:
                            continue
            except:
                pass

        # Check if active
        lock_data = read_lock(session_id)
        is_active = lock_data and is_process_alive(lock_data.get("pid", 0))
        status = detect_status(session_id, lock_data) if is_active else "done"

        session_data = {
            "id": session_id,
            "title": title[:80],
            "cwd": cwd,
            "cwd_display": shorten_path(cwd),
            "created_at": created,
            "updated_at": updated,
            "turns": turn_count,
            "messages": msg_count,
            "status": status,
            "is_active": is_active,
        }

        # Add to project
        projects[proj_name]["sessions"].append(session_data)
        projects[proj_name]["total_turns"] += turn_count
        projects[proj_name]["total_messages"] += msg_count
        if not projects[proj_name]["cwd"]:
            projects[proj_name]["cwd"] = proj_path or ""

        # Track last activity
        if updated and (not projects[proj_name]["last_activity"] or updated > projects[proj_name]["last_activity"]):
            projects[proj_name]["last_activity"] = updated

        # Check for abandoned threads (7+ days inactive, had meaningful activity)
        if turn_count >= 2 and updated:
            try:
                dt_updated = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                if dt_updated < seven_days_ago and not is_active:
                    days_inactive = (now - dt_updated).days
                    abandoned.append({
                        "id": session_id,
                        "title": title[:60],
                        "project": proj_name,
                        "cwd": cwd,
                        "cwd_display": shorten_path(cwd),
                        "turns": turn_count,
                        "days_inactive": days_inactive,
                        "updated_at": updated,
                    })
            except:
                pass

    # Build final project list
    project_list = []
    for name, data in projects.items():
        # Sort sessions by updated_at descending
        data["sessions"].sort(key=lambda s: s.get("updated_at", "") or "", reverse=True)
        # Calculate active session count
        active_count = sum(1 for s in data["sessions"] if s["is_active"])

        project_list.append({
            "name": name,
            "cwd": data["cwd"],
            "cwd_display": shorten_path(data["cwd"]),
            "session_count": len(data["sessions"]),
            "active_count": active_count,
            "total_turns": data["total_turns"],
            "total_messages": data["total_messages"],
            "last_activity": data["last_activity"],
            "sessions": data["sessions"][:20],  # Limit to 20 most recent
        })

    # Sort projects by last_activity descending
    project_list.sort(key=lambda p: p.get("last_activity", "") or "", reverse=True)

    # Hot projects: most recent activity + high turn count
    # Score = recency_weight * turns_weight
    hot_projects = []
    for p in project_list[:20]:  # Consider top 20 by recency
        if p["last_activity"]:
            try:
                dt = datetime.fromisoformat(p["last_activity"].replace("Z", "+00:00"))
                hours_ago = (now - dt).total_seconds() / 3600
                # Recency score: decay over 7 days
                recency_score = max(0, 1 - (hours_ago / (7 * 24)))
                # Activity score: log scale of turns
                activity_score = min(1, p["total_turns"] / 100) if p["total_turns"] > 0 else 0
                # Combined score
                hot_score = (recency_score * 0.6) + (activity_score * 0.4)
                if hot_score > 0.1:
                    hot_projects.append({
                        "name": p["name"],
                        "cwd": p["cwd"],
                        "session_count": p["session_count"],
                        "active_count": p["active_count"],
                        "total_turns": p["total_turns"],
                        "last_activity": p["last_activity"],
                        "hot_score": round(hot_score, 2),
                    })
            except:
                pass

    # Sort hot by score
    hot_projects.sort(key=lambda p: p["hot_score"], reverse=True)

    # Sort abandoned by days_inactive descending
    abandoned.sort(key=lambda a: a["days_inactive"], reverse=True)

    payload = {
        "projects": project_list,
        "hot": hot_projects[:5],
        "abandoned": abandoned[:20],
    }
    _projects_cache["data"] = payload
    _projects_cache["ts"] = time.time()
    return {**payload, "cached": False, "age_seconds": 0}


# --- Archive search ---
@app.get("/api/archive")
def search_archive(q: str = "", limit: int = 50):
    """Search all sessions (active + done) by title/cwd."""
    if not SESSIONS_DIR.exists():
        return {"sessions": [], "total": 0}

    json_files = sorted(
        SESSIONS_DIR.glob("*.json"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    q_lower = q.lower()
    favs = {f["id"] for f in _load_favourites()}

    # Build set of active session IDs to exclude
    active_ids = set()
    for lock_file in SESSIONS_DIR.glob("*.lock"):
        ld = read_lock(lock_file.stem)
        if ld and is_process_alive(ld.get("pid", 0)):
            active_ids.add(lock_file.stem)

    results = []
    total_matches = 0
    
    for json_file in json_files:
        session_id = json_file.stem
        if session_id in active_ids:
            continue
        meta = read_metadata(session_id)
        if not meta:
            continue
        title = clean_title(meta_title(meta) or "Untitled", session_id) or "Untitled"
        cwd = meta.get("cwd") or ""
        # Hide KiroCrew background/infrastructure sessions
        if any(cwd.startswith(p) for p in HIDDEN_CWD_PREFIXES):
            continue
        # Filter by search query
        if q_lower and q_lower not in title.lower() and q_lower not in cwd.lower():
            continue
        total_matches += 1
        if len(results) < limit:
            results.append({
                "id": session_id,
                "title": (title[:80] + "…" if len(title) > 80 else title),
                "cwd": cwd,
                "cwd_display": shorten_path(cwd),
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
                "is_favourite": session_id in favs,
            })

    # Include V3 sessions in archive
    v3_sessions_all = sorted(
        v3mod.all_v3_sessions(),
        key=lambda t: (t[1] / "session.json").stat().st_mtime if (t[1] / "session.json").exists() else 0,
        reverse=True,
    )
    for v3_id, _ in v3_sessions_all:
        meta = v3mod.read_metadata(v3_id)
        if not meta:
            continue
        title = meta.get("title") or "Untitled"
        cwd = meta.get("cwd") or ""
        if any(cwd.startswith(p) for p in HIDDEN_CWD_PREFIXES):
            continue
        if q_lower and q_lower not in title.lower() and q_lower not in cwd.lower():
            continue
        total_matches += 1
        if len(results) < limit:
            results.append({
                "id": v3_id,
                "title": (title[:80] + "…" if len(title) > 80 else title),
                "cwd": cwd,
                "cwd_display": shorten_path(cwd),
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
                "is_favourite": v3_id in favs,
                "format": "v3",
            })

    results.sort(key=lambda s: s.get("updated_at", "") or "", reverse=True)
    return {"sessions": results[:limit], "total": total_matches}


# Serve frontend static files in production

# ---------------------------------------------------------------------------
# Self-update
# ---------------------------------------------------------------------------

def _find_repo_root() -> Path:
    """Walk up from __file__ to find the git repo root.
    Falls back to a sibling of the app bundle for packaged builds."""
    candidate = Path(__file__).resolve()
    for _ in range(8):
        candidate = candidate.parent
        if (candidate / ".git").exists():
            return candidate
    # Packaged app: source repo lives alongside the bundle by convention.
    # Try ~/Documents/PROJECTS/PERSONAL/osa-kiro as a known fallback.
    fallback = Path.home() / "Documents" / "PROJECTS" / "PERSONAL" / "osa-kiro"
    if (fallback / ".git").exists():
        return fallback
    return Path(__file__).parent.parent

_REPO_ROOT = _find_repo_root()


@app.get("/api/update/check")
def update_check():
    """Compare running commit against latest remote commit.

    Returns current/latest sha, whether an update is available, and whether
    the working tree is clean. Returns no_remote=true if no remote is configured
    (e.g. local-only repo) so the UI can explain the situation.
    """
    def _run(*args):
        r = subprocess.run(list(args), capture_output=True, text=True, cwd=str(_REPO_ROOT))
        return r.stdout.strip(), r.returncode

    current, _ = _run("git", "rev-parse", "HEAD")
    short, _ = _run("git", "rev-parse", "--short", "HEAD")

    # Check remote exists
    remote_url, rc = _run("git", "remote", "get-url", "origin")
    if rc != 0:
        return {"current": current, "short": short, "no_remote": True, "up_to_date": True}

    # Fetch latest remote HEAD without pulling
    latest, rc = _run("git", "ls-remote", "origin", "HEAD")
    if rc != 0:
        return {"current": current, "short": short, "error": "Could not reach remote", "up_to_date": True}

    latest_sha = latest.split()[0] if latest else current

    # Check working tree cleanliness
    dirty_out, _ = _run("git", "status", "--porcelain")
    clean = dirty_out == ""

    return {
        "current": current,
        "short": short,
        "latest": latest_sha,
        "latest_short": latest_sha[:7],
        "up_to_date": current == latest_sha,
        "clean": clean,
        "no_remote": False,
    }


@app.post("/api/update/apply")
def update_apply():
    """Pull, install dependencies, and rebuild frontend. Streams progress lines.

    Requires a clean working tree. After success the caller should trigger
    an app restart (the UI will handle this via a reload prompt).
    """
    def _stream():
        def _run_step(label, *args, cwd=None):
            yield f"▶ {label}\n"
            proc = subprocess.Popen(
                list(args),
                cwd=str(cwd or _REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in proc.stdout:
                yield line
            proc.wait()
            if proc.returncode != 0:
                yield f"✗ {label} failed (exit {proc.returncode})\n"
                raise RuntimeError(label)
            yield f"✓ {label}\n"

        try:
            # Guard: require clean working tree
            check = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, cwd=str(_REPO_ROOT)
            )
            if check.stdout.strip():
                yield "✗ Working tree is not clean — stash or commit changes before updating.\n"
                return

            yield from _run_step("git pull", "git", "pull", "--ff-only")

            venv_python = _REPO_ROOT / "venv" / "bin" / "python"
            pip = str(venv_python) if venv_python.exists() else "python3"
            yield from _run_step(
                "pip install -r requirements.txt",
                pip, "-m", "pip", "install", "-r", str(_REPO_ROOT / "requirements.txt"), "-q"
            )

            yield from _run_step(
                "npm run build",
                "npm", "run", "build",
                cwd=_REPO_ROOT / "frontend",
            )
            yield "✓ Update complete. Restarting Quarterdeck…\n"
            yield "__DONE__\n"

            # Replace the running process with a fresh one so the new code
            # takes effect immediately. Done in a short-delay thread so the
            # streaming response has time to flush before the process exits.
            import threading as _threading
            def _restart():
                import time as _time
                _time.sleep(1.5)
                import os as _os
                _os.execv(sys.executable, [sys.executable] + sys.argv)
            _threading.Thread(target=_restart, daemon=True).start()

        except RuntimeError:
            yield "__ERROR__\n"

    return StreamingResponse(_stream(), media_type="text/plain")


frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"

# ---------------------------------------------------------------------------
# Dock badge
# ---------------------------------------------------------------------------

@app.post("/api/badge")
async def set_dock_badge(req: Request):
    """Set the macOS dock badge to the given count (0 clears it)."""
    body = await req.json()
    count = int(body.get("count", 0))
    import subprocess, sys
    if sys.platform == "darwin":
        label = str(count) if count > 0 else ""
        script = (
            f'tell application "Quarterdeck" to set the badge of the dock tile to "{label}"'
            if label else
            'tell application "Quarterdeck" to set the badge of the dock tile to ""'
        )
        subprocess.Popen(["osascript", "-e", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True}


import mimetypes as _mimetypes
from starlette.responses import Response as _StarletteResponse

@app.get("/app/{path:path}")
def serve_frontend(path: str):
    """Serve frontend assets with no-store so WKWebView always fetches fresh CSS/JS."""
    base = Path(__file__).parent.parent / "frontend" / "dist"
    file = base / path
    if not base.exists():
        return {"error": "frontend not built"}
    if not file.exists() or not file.is_file():
        file = base / "index.html"
    mime, _ = _mimetypes.guess_type(str(file))
    content = file.read_bytes()
    return _StarletteResponse(
        content=content,
        media_type=mime or "application/octet-stream",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/")
def root():
    """Redirect to app."""
    if frontend_dist.exists():
        return FileResponse(
            frontend_dist / "index.html",
            headers={"Cache-Control": "no-store"},
        )
    return {"status": "Quarterdeck API running", "docs": "/docs"}
