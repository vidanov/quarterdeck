"""Per-device token management for Quarterdeck.

Replaces the single shared secret with named device tokens, each independently
revocable. A lost phone becomes a two-click problem: revoke the phone's token
and the Mac, the existing sessions, and any other device keep working.

Tokens are stored in a JSON file at `~/.osa-kiro/devices.json` with 0600
permissions. Each entry records:
- name: human label ("phone", "laptop", "ipad")
- token: the secret (64 hex chars)
- created_at: ISO timestamp
- last_used_at: ISO timestamp or null
- last_ip: last socket peer that authenticated with this token

The legacy single-token path (`~/.osa-kiro/token`) remains supported: if it
exists and no device tokens are configured, the old token works. Once any
device token is created, the legacy token is ignored for remote auth (but kept
on disk for rollback).
"""
import hmac
import json
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import STATE_DIR

DEVICES_FILE = STATE_DIR / "devices.json"
_lock = threading.Lock()
MAX_DEVICES = 20


def _read() -> list[dict]:
    with _lock:
        if not DEVICES_FILE.exists():
            return []
        try:
            data = json.loads(DEVICES_FILE.read_text())
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _write(devices: list[dict]) -> None:
    with _lock:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = DEVICES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(devices, indent=2) + "\n")
        tmp.chmod(0o600)
        tmp.replace(DEVICES_FILE)


def list_devices() -> list[dict]:
    """All devices, tokens masked for display."""
    devices = _read()
    return [
        {
            "id": d.get("id", ""),
            "name": d.get("name", "unnamed"),
            "created_at": d.get("created_at"),
            "last_used_at": d.get("last_used_at"),
            "last_ip": d.get("last_ip"),
            "token_prefix": d.get("token", "")[:8],
        }
        for d in devices
    ]


def create_device(name: str) -> dict:
    """Create a new device token. Returns the full token (shown once)."""
    devices = _read()
    if len(devices) >= MAX_DEVICES:
        return {"error": f"Maximum {MAX_DEVICES} devices reached. Revoke one first."}

    token = secrets.token_hex(32)
    device_id = secrets.token_hex(8)
    now = datetime.now(timezone.utc).isoformat()

    entry = {
        "id": device_id,
        "name": name.strip()[:40] or "unnamed",
        "token": token,
        "created_at": now,
        "last_used_at": None,
        "last_ip": None,
    }
    devices.append(entry)
    _write(devices)
    return {"id": device_id, "name": entry["name"], "token": token}


def revoke_device(device_id: str) -> dict:
    """Revoke a device by its id. Returns ok or error."""
    devices = _read()
    before = len(devices)
    devices = [d for d in devices if d.get("id") != device_id]
    if len(devices) == before:
        return {"error": "Device not found"}
    _write(devices)
    return {"ok": True, "remaining": len(devices)}


def rename_device(device_id: str, name: str) -> dict:
    """Rename a device."""
    devices = _read()
    for d in devices:
        if d.get("id") == device_id:
            d["name"] = name.strip()[:40] or "unnamed"
            _write(devices)
            return {"ok": True}
    return {"error": "Device not found"}


def has_device_tokens() -> bool:
    """True if at least one device token is configured."""
    return len(_read()) > 0


def validate_token(presented: str, client_ip: str = "") -> str | None:
    """Check presented token against all device tokens.

    Returns the device name if valid, None otherwise. Updates last_used
    metadata on match.
    """
    if not presented:
        return None
    devices = _read()
    for d in devices:
        stored = d.get("token", "")
        if stored and hmac.compare_digest(presented, stored):
            # Update usage metadata
            d["last_used_at"] = datetime.now(timezone.utc).isoformat()
            if client_ip:
                d["last_ip"] = client_ip
            _write(devices)
            return d.get("name", "unnamed")
    return None


def get_token_for_cookie() -> str | None:
    """Return the first device token for cookie-based auth (QR login).

    When using device tokens, the QR code exchange still needs to set a cookie.
    We use the first device token as the cookie value. This means any device
    token validates the cookie — which is fine, because the cookie is just
    "this browser proved it had a token once."
    """
    devices = _read()
    if devices:
        return devices[0].get("token")
    return None
