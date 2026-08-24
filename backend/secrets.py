"""Per-project secrets store for Quarterdeck.

Each secret is bound to a project folder (identified by a hash of its
canonical cwd path).  Secret *values* are stored in the macOS login keychain
under the service "com.vidanov.quarterdeck.secrets".  The JSON metadata file
at ~/.osa-kiro/secrets/<cwd-hash>.json stores only names and timestamps —
never values — so the file is safe to back up and safe to lose.

Public API
----------
list_secrets(cwd)           → [{"name": ..., "created_at": ...}]
set_secret(cwd, name, val)  → {"name": ..., "created_at": ...}
delete_secret(cwd, name)    → bool
get_env(cwd)                → {"NAME": "value", ...}  (for tmux injection)
"""

import hashlib
import json
import subprocess
import time
from pathlib import Path

from .config import STATE_DIR

_SECRETS_DIR = STATE_DIR / "secrets"
_KC_SERVICE = "com.vidanov.quarterdeck.secrets"

# Fallback file key when keychain is unavailable (CI / headless). Values stored
# XOR-obfuscated — not real encryption, but not plaintext either.
_FALLBACK_KEY_FILE = STATE_DIR / "secrets.key"


# ── helpers ──────────────────────────────────────────────────────────────────

def _cwd_hash(cwd: str) -> str:
    return hashlib.sha256(str(Path(cwd).resolve()).encode()).hexdigest()[:16]


def _meta_path(cwd: str) -> Path:
    _SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    return _SECRETS_DIR / f"{_cwd_hash(cwd)}.json"


def _load_meta(cwd: str) -> list[dict]:
    p = _meta_path(cwd)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_meta(cwd: str, entries: list[dict]) -> None:
    _meta_path(cwd).write_text(json.dumps(entries, indent=2))


def _kc_account(cwd: str, name: str) -> str:
    return f"{_cwd_hash(cwd)}.{name}"


# ── keychain wrappers ─────────────────────────────────────────────────────────

def _kc_read(account: str) -> str:
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", _KC_SERVICE, "-a", account, "-w"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def _kc_write(account: str, value: str) -> bool:
    try:
        r = subprocess.run(
            ["security", "add-generic-password", "-s", _KC_SERVICE, "-a", account,
             "-w", value, "-U"],
            capture_output=True, text=True, timeout=3,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _kc_delete(account: str) -> None:
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-s", _KC_SERVICE, "-a", account],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


# ── fallback obfuscation (headless/CI only) ───────────────────────────────────

def _fallback_key() -> bytes:
    if _FALLBACK_KEY_FILE.exists():
        try:
            return bytes.fromhex(_FALLBACK_KEY_FILE.read_text().strip())
        except Exception:
            pass
    key = __import__("os").urandom(32)
    _FALLBACK_KEY_FILE.write_text(key.hex())
    try:
        _FALLBACK_KEY_FILE.chmod(0o600)
    except OSError:
        pass
    return key


def _fallback_write(account: str, value: str) -> None:
    key = _fallback_key()
    enc = bytes(b ^ key[i % len(key)] for i, b in enumerate(value.encode()))
    fb_dir = _SECRETS_DIR / ".fallback"
    fb_dir.mkdir(parents=True, exist_ok=True)
    (fb_dir / account).write_bytes(enc)


def _fallback_read(account: str) -> str:
    fb_path = _SECRETS_DIR / ".fallback" / account
    if not fb_path.exists():
        return ""
    try:
        key = _fallback_key()
        enc = fb_path.read_bytes()
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(enc)).decode()
    except Exception:
        return ""


def _fallback_delete(account: str) -> None:
    fb_path = _SECRETS_DIR / ".fallback" / account
    try:
        fb_path.unlink(missing_ok=True)
    except OSError:
        pass


# ── public API ────────────────────────────────────────────────────────────────

def list_secrets(cwd: str) -> list[dict]:
    """Return secret metadata (name + created_at) for a project. No values."""
    return _load_meta(cwd)


def set_secret(cwd: str, name: str, value: str) -> dict:
    """Store a secret value. Returns the metadata entry (no value)."""
    name = name.strip().upper().replace(" ", "_")
    if not name:
        raise ValueError("name required")

    account = _kc_account(cwd, name)
    stored = _kc_write(account, value)
    if not stored:
        # Keychain unavailable — use fallback obfuscation
        _fallback_write(account, value)

    entries = _load_meta(cwd)
    # Update or insert
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for e in entries:
        if e.get("name") == name:
            e["updated_at"] = now
            _save_meta(cwd, entries)
            return {"name": name, "created_at": e.get("created_at", now), "updated_at": now}

    entry = {"name": name, "created_at": now}
    entries.append(entry)
    _save_meta(cwd, entries)
    return entry


def delete_secret(cwd: str, name: str) -> bool:
    """Remove a secret. Returns True if it existed."""
    name = name.strip().upper()
    entries = _load_meta(cwd)
    before = len(entries)
    entries = [e for e in entries if e.get("name") != name]
    if len(entries) == before:
        return False
    _save_meta(cwd, entries)
    account = _kc_account(cwd, name)
    _kc_delete(account)
    _fallback_delete(account)
    return True


def get_env(cwd: str) -> dict[str, str]:
    """Return {NAME: value} for all secrets in a project. Used at spawn time."""
    result = {}
    for entry in _load_meta(cwd):
        name = entry.get("name", "")
        if not name:
            continue
        account = _kc_account(cwd, name)
        value = _kc_read(account) or _fallback_read(account)
        if value:
            result[name] = value
    return result


def has_secrets(cwd: str) -> bool:
    return bool(_load_meta(cwd))
