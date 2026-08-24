"""Folder-bound script store and runner for Quarterdeck.

Scripts are named shell commands bound to a project folder.  They run
as direct subprocess.Popen children (no tmux, no kiro-cli), and their
output is captured in an in-memory ring buffer that the output endpoint
tails.

Storage: ~/.osa-kiro/scripts/<cwd-hash>.json

Each entry::

    {
        "id":          "uuid4",
        "name":        "Build",
        "command":     "npm --prefix frontend run build",
        "cwd":         "/abs/path",
        "description": "optional one-liner",
        "confirm":     false,
        "created_at":  "ISO-Z",
        "updated_at":  "ISO-Z"
    }

Public API
----------
list_scripts(cwd)           → list[dict]
add_script(cwd, **fields)   → dict
update_script(id, cwd, **) → dict | None
delete_script(id, cwd)      → bool
run_script(id, cwd)         → RunHandle | None
kill_script(id)             → bool
get_output(id)              → dict  {lines, exit_code, running}
detect_imports(cwd)         → list[dict]  (from Makefile / package.json)
"""
from __future__ import annotations

import hashlib
import json
import os
import select
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from backend.config import STATE_DIR

_SCRIPTS_DIR = STATE_DIR / "scripts"
_MAX_OUTPUT_BYTES = 256 * 1024   # 256 KB ring per run
_MAX_OUTPUT_LINES = 2000


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _cwd_hash(cwd: str) -> str:
    return hashlib.sha256(str(Path(cwd).resolve()).encode()).hexdigest()[:16]


def _store_path(cwd: str) -> Path:
    _SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    return _SCRIPTS_DIR / f"{_cwd_hash(cwd)}.json"


def _load(cwd: str) -> list[dict]:
    p = _store_path(cwd)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(cwd: str, entries: list[dict]) -> None:
    _store_path(cwd).write_text(json.dumps(entries, indent=2))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_scripts(cwd: str) -> list[dict]:
    return _load(cwd)


def add_script(cwd: str, name: str, command: str,
               description: str = "", confirm: bool = False) -> dict:
    name = name.strip()
    command = command.strip()
    if not name or not command:
        raise ValueError("name and command required")
    entries = _load(cwd)
    now = _now()
    entry: dict = {
        "id": str(uuid.uuid4()),
        "name": name,
        "command": command,
        "cwd": cwd,
        "description": description.strip(),
        "confirm": bool(confirm),
        "created_at": now,
        "updated_at": now,
    }
    entries.append(entry)
    _save(cwd, entries)
    return entry


def update_script(script_id: str, cwd: str, **fields) -> dict | None:
    entries = _load(cwd)
    for e in entries:
        if e.get("id") == script_id:
            for k, v in fields.items():
                if k in ("name", "command", "description", "confirm"):
                    e[k] = v
            e["updated_at"] = _now()
            _save(cwd, entries)
            return e
    return None


def delete_script(script_id: str, cwd: str) -> bool:
    entries = _load(cwd)
    before = len(entries)
    entries = [e for e in entries if e.get("id") != script_id]
    if len(entries) == before:
        return False
    _save(cwd, entries)
    # Kill any running instance
    kill_script(script_id)
    return True


def get_script(script_id: str, cwd: str) -> dict | None:
    for e in _load(cwd):
        if e.get("id") == script_id:
            return e
    return None


# ---------------------------------------------------------------------------
# Runner — one active run per script id (last-writer-wins)
# ---------------------------------------------------------------------------

class _Run:
    def __init__(self, script_id: str, command: str, cwd: str):
        self.script_id = script_id
        self.run_id = str(uuid.uuid4())[:8]
        self.command = command
        self.cwd = cwd
        self.lines: list[str] = []
        self.exit_code: int | None = None
        self.running = True
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self) -> None:
        try:
            env = os.environ.copy()
            env.setdefault("TERM", "xterm-256color")
            self._proc = subprocess.Popen(
                self.command,
                shell=True,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
                bufsize=1,
            )
            assert self._proc.stdout is not None
            for raw_line in self._proc.stdout:
                line = raw_line.rstrip("\n")
                with self._lock:
                    self.lines.append(line)
                    if len(self.lines) > _MAX_OUTPUT_LINES:
                        self.lines = self.lines[-_MAX_OUTPUT_LINES:]
            self._proc.wait()
            with self._lock:
                self.exit_code = self._proc.returncode
                self.running = False
        except Exception as exc:
            with self._lock:
                self.lines.append(f"[error] {exc}")
                self.exit_code = 1
                self.running = False

    def kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                time.sleep(0.3)
                if self._proc.poll() is None:
                    self._proc.kill()
            except OSError:
                pass
        with self._lock:
            self.running = False

    def snapshot(self, after: int = 0) -> dict:
        with self._lock:
            lines = self.lines[after:]
            return {
                "run_id": self.run_id,
                "lines": lines,
                "total": len(self.lines),
                "exit_code": self.exit_code,
                "running": self.running,
            }


# Active runs: script_id → _Run
_active: dict[str, _Run] = {}
_active_lock = threading.Lock()


def run_script(script_id: str, cwd: str) -> dict | None:
    """Start running a script. Replaces any existing run for this id."""
    entry = get_script(script_id, cwd)
    if not entry:
        return None
    run = _Run(script_id, entry["command"], cwd)
    with _active_lock:
        old = _active.get(script_id)
        if old:
            old.kill()
        _active[script_id] = run
    run.start()
    return {"ok": True, "run_id": run.run_id, "script_id": script_id}


def kill_script(script_id: str) -> bool:
    with _active_lock:
        run = _active.pop(script_id, None)
    if run:
        run.kill()
        return True
    return False


def get_output(script_id: str, after: int = 0) -> dict:
    with _active_lock:
        run = _active.get(script_id)
    if not run:
        return {"run_id": None, "lines": [], "total": 0, "exit_code": None, "running": False}
    return run.snapshot(after)


# ---------------------------------------------------------------------------
# Import detection
# ---------------------------------------------------------------------------

def detect_imports(cwd: str) -> list[dict]:
    """Detect runnable targets from Makefile and package.json in *cwd*."""
    results: list[dict] = []
    root = Path(cwd)

    # package.json scripts
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            for name, cmd in (data.get("scripts") or {}).items():
                results.append({
                    "name": name,
                    "command": f"npm run {name}",
                    "description": cmd[:80] if cmd else "",
                    "source": "package.json",
                })
        except Exception:
            pass

    # Makefile targets (non-pattern, non-private)
    makefile = root / "Makefile"
    if not makefile.exists():
        makefile = root / "makefile"
    if makefile.exists():
        try:
            for line in makefile.read_text(errors="replace").splitlines():
                if not line or line[0] in ("\t", " ", "#", "."):
                    continue
                if ":" in line and not line.startswith("\t"):
                    target = line.split(":")[0].strip()
                    if target and not target.startswith(".") and " " not in target:
                        results.append({
                            "name": target,
                            "command": f"make {target}",
                            "description": "",
                            "source": "Makefile",
                        })
        except Exception:
            pass

    # Deduplicate by name, keeping first seen
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in results:
        if r["name"] not in seen:
            seen.add(r["name"])
            deduped.append(r)
    return deduped
