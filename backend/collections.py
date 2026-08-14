"""Collections — unified replacement for snapshots, favourites, and project groups.

A collection is an ordered set of members with a name and a source. Members
can reference existing sessions (by id) or be recipes for spawning new ones.

Storage: ~/.osa-kiro/collections.json — a single file with all collections.
Migration from snapshots.json and favourites.json happens on first load.
"""
import json
import os
import threading
import time
import uuid
from pathlib import Path

from .config import STATE_DIR, SNAPSHOTS_FILE, FAVOURITES_FILE, COLLECTIONS_FILE

_lock = threading.RLock()


def _atomic_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Data shape ---
# Collection: {
#   "id": str (uuid4),
#   "name": str,
#   "source": "manual" | "snapshot" | "favourites" | "cwd",
#   "created_at": str (ISO),
#   "updated_at": str (ISO),
#   "members": [Member],
#   "meta": {}  # source-specific metadata (e.g. snapshot time, cwd path)
# }
#
# Member: {
#   "session_id": str | None,  # existing session, if any
#   "cwd": str | None,
#   "title": str | None,
#   "agent": str | None,
#   "model": str | None,
#   "prompt": str | None,
# }


def _migrate_legacy() -> list[dict]:
    """Convert snapshots.json and favourites.json into collections. Once."""
    collections = []

    # Migrate favourites → one collection named "Favourites"
    if FAVOURITES_FILE.exists():
        try:
            favs = json.loads(FAVOURITES_FILE.read_text())
            if favs:
                members = []
                for f in favs:
                    members.append({
                        "session_id": f.get("id"),
                        "cwd": f.get("cwd"),
                        "title": f.get("title"),
                        "agent": None,
                        "model": None,
                        "prompt": None,
                    })
                collections.append({
                    "id": str(uuid.uuid4()),
                    "name": "Favourites",
                    "source": "favourites",
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                    "members": members,
                    "meta": {"migrated_from": "favourites.json"},
                })
        except (json.JSONDecodeError, OSError):
            pass

    # Migrate snapshots → one collection per snapshot
    if SNAPSHOTS_FILE.exists():
        try:
            snaps = json.loads(SNAPSHOTS_FILE.read_text())
            for snap in snaps:
                members = []
                for s in snap.get("sessions", []):
                    members.append({
                        "session_id": s.get("id"),
                        "cwd": s.get("cwd"),
                        "title": s.get("title") or s.get("name"),
                        "agent": None,
                        "model": None,
                        "prompt": None,
                    })
                name = f"Snapshot {snap.get('date', '')} {snap.get('time', '')}".strip()
                collections.append({
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "source": "snapshot",
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                    "members": members,
                    "meta": {
                        "migrated_from": "snapshots.json",
                        "original_id": snap.get("id"),
                        "date": snap.get("date"),
                        "time": snap.get("time"),
                    },
                })
        except (json.JSONDecodeError, OSError):
            pass

    return collections


def load_collections() -> list[dict]:
    """Load all collections from disk, migrating legacy files on first call."""
    with _lock:
        if COLLECTIONS_FILE.exists():
            try:
                return json.loads(COLLECTIONS_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        # First time: migrate from legacy
        collections = _migrate_legacy()
        if collections:
            _atomic_write(COLLECTIONS_FILE, collections)
        return collections


def save_collections(collections: list[dict]) -> None:
    with _lock:
        _atomic_write(COLLECTIONS_FILE, collections)


def get_collection(collection_id: str) -> dict | None:
    for c in load_collections():
        if c["id"] == collection_id:
            return c
    return None


def create_collection(name: str, source: str = "manual", members: list | None = None,
                      meta: dict | None = None) -> dict:
    now = _now_iso()
    collection = {
        "id": str(uuid.uuid4()),
        "name": name,
        "source": source,
        "created_at": now,
        "updated_at": now,
        "members": members or [],
        "meta": meta or {},
    }
    with _lock:
        collections = load_collections()
        collections.append(collection)
        save_collections(collections)
    return collection


def rename_collection(collection_id: str, name: str) -> dict | None:
    with _lock:
        collections = load_collections()
        for c in collections:
            if c["id"] == collection_id:
                c["name"] = name
                c["updated_at"] = _now_iso()
                save_collections(collections)
                return c
    return None


def delete_collection(collection_id: str) -> bool:
    with _lock:
        collections = load_collections()
        before = len(collections)
        collections = [c for c in collections if c["id"] != collection_id]
        if len(collections) < before:
            save_collections(collections)
            return True
    return False


def add_member(collection_id: str, member: dict) -> dict | None:
    """Add a member to a collection. Returns updated collection or None."""
    with _lock:
        collections = load_collections()
        for c in collections:
            if c["id"] == collection_id:
                c["members"].append(member)
                c["updated_at"] = _now_iso()
                save_collections(collections)
                return c
    return None


def remove_member(collection_id: str, session_id: str) -> dict | None:
    """Remove a member by session_id. Returns updated collection or None."""
    with _lock:
        collections = load_collections()
        for c in collections:
            if c["id"] == collection_id:
                before = len(c["members"])
                c["members"] = [
                    m for m in c["members"]
                    if m.get("session_id") != session_id
                ]
                if len(c["members"]) < before:
                    c["updated_at"] = _now_iso()
                    save_collections(collections)
                return c
    return None


def reorder_members(collection_id: str, session_ids: list[str]) -> dict | None:
    """Reorder members to match the given session_id order.

    IDs not in the list are appended at the end. IDs in the list but not in
    the collection are ignored.
    """
    with _lock:
        collections = load_collections()
        for c in collections:
            if c["id"] == collection_id:
                by_id = {m.get("session_id"): m for m in c["members"]}
                ordered = []
                seen = set()
                for sid in session_ids:
                    if sid in by_id and sid not in seen:
                        ordered.append(by_id[sid])
                        seen.add(sid)
                # Append any not mentioned in the order
                for m in c["members"]:
                    if m.get("session_id") not in seen:
                        ordered.append(m)
                c["members"] = ordered
                c["updated_at"] = _now_iso()
                save_collections(collections)
                return c
    return None


def has_member(collection_id: str, session_id: str) -> bool:
    """Check if a session is in a collection."""
    c = get_collection(collection_id)
    if not c:
        return False
    return any(m.get("session_id") == session_id for m in c["members"])


def get_favourites_collection() -> dict | None:
    """Return the designated favourites collection (source=favourites), or None."""
    for c in load_collections():
        if c.get("source") == "favourites":
            return c
    return None


def ensure_favourites_collection() -> dict:
    """Return the favourites collection, creating it if absent."""
    c = get_favourites_collection()
    if c:
        return c
    return create_collection("Favourites", source="favourites")
