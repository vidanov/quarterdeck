"""A private kiro-cli login for Quarterdeck's own background processes.

kiro-cli keeps its login in one shared SQLite DB under $HOME. Quarterdeck's
own long-lived kiro processes (the summary worker) used to share it with the
user's sessions, so a profile switch pulled the rug from under them and their
token refreshes wrote the old login back over the new one.

With the ``internal-profile`` setting naming a saved profile, those processes
run with HOME pointed at ``~/.osa-kiro/internal-kiro``. kiro-cli then reads and
refreshes a private DB seeded from that profile's snapshot, and the user's
profile switches never touch it. Without the setting, internal processes keep
using the shared login.
"""
import json
import logging
import os
import sqlite3
import subprocess
import threading
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

SETTING_KEY = "internal-profile"
HOME_DIR = config.STATE_DIR / "internal-kiro"
PROFILES_DIR = Path.home() / ".kiro" / "profiles"
_STATE_KEY = "api.codewhisperer.profile"

_seed_lock = threading.Lock()


def _db_path() -> Path:
    return HOME_DIR / "Library" / "Application Support" / "kiro-cli" / "data.sqlite3"


def _marker_path() -> Path:
    return HOME_DIR / ".seeded.json"


def profile_name() -> str:
    """The saved profile internal processes should use, or '' to share the login."""
    name = config.read_settings().get(SETTING_KEY) or ""
    if not isinstance(name, str) or "/" in name or "\\" in name or name == "_previous":
        return ""
    return name.strip()


def _load_profile(name: str) -> tuple[list[dict], dict] | None:
    try:
        rows = [json.loads(line) for line in (PROFILES_DIR / f"{name}.jsonl").read_text().splitlines()
                if line.strip()]
        meta = json.loads((PROFILES_DIR / f"{name}.meta.json").read_text())
    except (OSError, ValueError):
        return None
    if not rows or not meta.get("state_profile"):
        return None
    return rows, meta


def login_key() -> tuple[str, str] | None:
    """Identity of the configured internal login, or None when none is configured.

    Changes when the setting names another profile or that profile is saved
    again, which is exactly when a running worker must be replaced.
    """
    name = profile_name()
    if not name:
        return None
    loaded = _load_profile(name)
    if not loaded:
        return None
    return (f"internal:{name}", loaded[1].get("token_fingerprint", ""))


def _env() -> dict:
    return {**os.environ, "HOME": str(HOME_DIR)}


def _link_binaries() -> None:
    """kiro-cli launches its chat binary from $HOME/.local/bin."""
    link = HOME_DIR / ".local" / "bin"
    if link.is_symlink() or link.exists():
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(Path.home() / ".local" / "bin")


def _seed(name: str, rows: list[dict], meta: dict) -> bool:
    """Write the profile's login into the private DB. True on success."""
    db = _db_path()
    if not db.exists():
        HOME_DIR.mkdir(parents=True, exist_ok=True)
        HOME_DIR.chmod(0o700)  # will hold live OAuth tokens
        # Let kiro-cli create its own schema rather than guessing it.
        try:
            subprocess.run(["kiro-cli", "whoami"], env=_env(), capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if not db.exists():
            return False
    con = sqlite3.connect(str(db), timeout=5)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM auth_kv")
        con.executemany("INSERT INTO auth_kv (key, value) VALUES (?, ?)",
                        [(r["key"], r["value"]) for r in rows])
        con.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)",
                    (_STATE_KEY, meta["state_profile"]))
        con.commit()
    except sqlite3.Error:
        con.rollback()
        return False
    finally:
        con.close()
    _marker_path().write_text(json.dumps({"name": name, "fingerprint": meta.get("token_fingerprint", "")}))
    return True


def env() -> dict | None:
    """Environment for an internal kiro-cli process, or None to use the shared login.

    Seeds the private DB the first time and again whenever the configured
    profile changes or is re-saved. Between seeds the private DB is left alone
    so kiro-cli's own token refreshes there persist.
    """
    name = profile_name()
    if not name:
        return None
    loaded = _load_profile(name)
    if not loaded:
        log.warning("internal_auth: profile %r not found, using the shared login", name)
        return None
    rows, meta = loaded
    with _seed_lock:
        try:
            marker = json.loads(_marker_path().read_text())
        except (OSError, ValueError):
            marker = {}
        current = {"name": name, "fingerprint": meta.get("token_fingerprint", "")}
        if marker != current or not _db_path().exists():
            if not _seed(name, rows, meta):
                log.warning("internal_auth: could not seed private login, using the shared login")
                return None
        try:
            _link_binaries()
        except OSError:
            log.warning("internal_auth: could not link kiro-cli binaries, using the shared login")
            return None
    return _env()
