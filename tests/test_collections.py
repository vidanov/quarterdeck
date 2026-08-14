"""Tests for backend/collections.py"""
import json
import pytest
from pathlib import Path


@pytest.fixture()
def tmp_state(tmp_path, monkeypatch):
    """Redirect all collections paths to a temp directory."""
    import backend.collections as coll_mod
    import backend.config as cfg

    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cfg, "COLLECTIONS_FILE", tmp_path / "collections.json")
    monkeypatch.setattr(cfg, "SNAPSHOTS_FILE", tmp_path / "snapshots.json")
    monkeypatch.setattr(cfg, "FAVOURITES_FILE", tmp_path / "favourites.json")
    monkeypatch.setattr(coll_mod, "COLLECTIONS_FILE", tmp_path / "collections.json")
    monkeypatch.setattr(coll_mod, "SNAPSHOTS_FILE", tmp_path / "snapshots.json")
    monkeypatch.setattr(coll_mod, "FAVOURITES_FILE", tmp_path / "favourites.json")
    # Reset the in-module lock so each test gets a fresh one
    import threading
    monkeypatch.setattr(coll_mod, "_lock", threading.RLock())
    return tmp_path


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------

def test_empty_on_first_load(tmp_state):
    from backend.collections import load_collections
    assert load_collections() == []


def test_create_and_list(tmp_state):
    from backend.collections import create_collection, load_collections
    c = create_collection("My Collection")
    assert c["name"] == "My Collection"
    assert c["source"] == "manual"
    assert c["members"] == []
    assert "id" in c
    assert "created_at" in c

    all_c = load_collections()
    assert len(all_c) == 1
    assert all_c[0]["id"] == c["id"]


def test_create_with_members(tmp_state):
    from backend.collections import create_collection
    members = [{"session_id": "abc", "cwd": "/tmp", "title": "Test"}]
    c = create_collection("With Members", members=members)
    assert len(c["members"]) == 1
    assert c["members"][0]["session_id"] == "abc"


def test_get_collection(tmp_state):
    from backend.collections import create_collection, get_collection
    c = create_collection("Get Me")
    found = get_collection(c["id"])
    assert found is not None
    assert found["id"] == c["id"]


def test_get_missing_returns_none(tmp_state):
    from backend.collections import get_collection
    assert get_collection("nonexistent-id") is None


def test_rename_collection(tmp_state):
    from backend.collections import create_collection, rename_collection
    c = create_collection("Old Name")
    updated = rename_collection(c["id"], "New Name")
    assert updated is not None
    assert updated["name"] == "New Name"
    assert updated["updated_at"] >= c["created_at"]


def test_rename_missing_returns_none(tmp_state):
    from backend.collections import rename_collection
    assert rename_collection("missing", "X") is None


def test_delete_collection(tmp_state):
    from backend.collections import create_collection, delete_collection, load_collections
    c = create_collection("To Delete")
    assert delete_collection(c["id"]) is True
    assert load_collections() == []


def test_delete_missing_returns_false(tmp_state):
    from backend.collections import delete_collection
    assert delete_collection("nonexistent") is False


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

def test_add_member(tmp_state):
    from backend.collections import create_collection, add_member, get_collection
    c = create_collection("Members Test")
    member = {"session_id": "s1", "cwd": "/foo", "title": "Session 1", "agent": None, "model": None, "prompt": None}
    updated = add_member(c["id"], member)
    assert updated is not None
    assert len(updated["members"]) == 1
    assert updated["members"][0]["session_id"] == "s1"


def test_add_member_to_missing_collection(tmp_state):
    from backend.collections import add_member
    assert add_member("missing", {"session_id": "x"}) is None


def test_remove_member(tmp_state):
    from backend.collections import create_collection, add_member, remove_member
    c = create_collection("Remove Test")
    add_member(c["id"], {"session_id": "s1", "cwd": "/foo"})
    add_member(c["id"], {"session_id": "s2", "cwd": "/bar"})
    updated = remove_member(c["id"], "s1")
    assert updated is not None
    assert len(updated["members"]) == 1
    assert updated["members"][0]["session_id"] == "s2"


def test_remove_nonexistent_member(tmp_state):
    from backend.collections import create_collection, remove_member
    c = create_collection("Remove Test 2")
    # Removing a session not in the collection returns the collection unchanged
    updated = remove_member(c["id"], "not-there")
    assert updated is not None
    assert updated["members"] == []


def test_reorder_members(tmp_state):
    from backend.collections import create_collection, add_member, reorder_members
    c = create_collection("Reorder Test")
    for sid in ["s1", "s2", "s3"]:
        add_member(c["id"], {"session_id": sid})
    updated = reorder_members(c["id"], ["s3", "s1", "s2"])
    assert updated is not None
    assert [m["session_id"] for m in updated["members"]] == ["s3", "s1", "s2"]


def test_reorder_partial_list(tmp_state):
    """Members not in the order list are appended at the end."""
    from backend.collections import create_collection, add_member, reorder_members
    c = create_collection("Reorder Partial")
    for sid in ["s1", "s2", "s3"]:
        add_member(c["id"], {"session_id": sid})
    updated = reorder_members(c["id"], ["s2"])
    ids = [m["session_id"] for m in updated["members"]]
    assert ids[0] == "s2"
    assert set(ids) == {"s1", "s2", "s3"}


def test_has_member(tmp_state):
    from backend.collections import create_collection, add_member, has_member
    c = create_collection("HasMember")
    add_member(c["id"], {"session_id": "s1"})
    assert has_member(c["id"], "s1") is True
    assert has_member(c["id"], "s2") is False


# ---------------------------------------------------------------------------
# Favourites helpers
# ---------------------------------------------------------------------------

def test_ensure_favourites_collection_creates_if_absent(tmp_state):
    from backend.collections import ensure_favourites_collection, load_collections
    c = ensure_favourites_collection()
    assert c["source"] == "favourites"
    assert c["name"] == "Favourites"
    # Check it was persisted
    assert any(x["id"] == c["id"] for x in load_collections())


def test_ensure_favourites_collection_idempotent(tmp_state):
    from backend.collections import ensure_favourites_collection, load_collections
    c1 = ensure_favourites_collection()
    c2 = ensure_favourites_collection()
    assert c1["id"] == c2["id"]
    # Only one favourites collection
    favs = [x for x in load_collections() if x["source"] == "favourites"]
    assert len(favs) == 1


def test_get_favourites_collection_none_when_absent(tmp_state):
    from backend.collections import get_favourites_collection
    assert get_favourites_collection() is None


# ---------------------------------------------------------------------------
# Migration from legacy files
# ---------------------------------------------------------------------------

def test_migrate_from_favourites(tmp_state):
    # Write a legacy favourites.json
    favs = [
        {"id": "f1", "title": "Fav 1", "cwd": "/projects/a"},
        {"id": "f2", "title": "Fav 2", "cwd": "/projects/b"},
    ]
    (tmp_state / "favourites.json").write_text(json.dumps(favs))

    from backend.collections import load_collections
    collections = load_collections()
    fav_colls = [c for c in collections if c["source"] == "favourites"]
    assert len(fav_colls) == 1
    assert fav_colls[0]["name"] == "Favourites"
    assert len(fav_colls[0]["members"]) == 2
    assert fav_colls[0]["members"][0]["session_id"] == "f1"


def test_migrate_from_snapshots(tmp_state):
    snaps = [
        {
            "id": 1001,
            "date": "2026-07-28",
            "time": "10:00",
            "sessions": [{"id": "s1", "cwd": "/foo", "title": "Session 1"}],
        }
    ]
    (tmp_state / "snapshots.json").write_text(json.dumps(snaps))

    from backend.collections import load_collections
    collections = load_collections()
    snap_colls = [c for c in collections if c["source"] == "snapshot"]
    assert len(snap_colls) == 1
    assert "2026-07-28" in snap_colls[0]["name"]
    assert snap_colls[0]["members"][0]["session_id"] == "s1"


def test_migrate_only_once(tmp_state):
    """After migration creates collections.json, re-loading should not re-migrate."""
    favs = [{"id": "f1", "title": "T", "cwd": "/x"}]
    (tmp_state / "favourites.json").write_text(json.dumps(favs))

    from backend.collections import load_collections
    c1 = load_collections()
    c2 = load_collections()
    # Same result both times, not doubled
    fav_colls = [c for c in c2 if c["source"] == "favourites"]
    assert len(fav_colls) == 1


def test_migrate_empty_files(tmp_state):
    """Empty legacy files should not create collections."""
    (tmp_state / "favourites.json").write_text("[]")
    (tmp_state / "snapshots.json").write_text("[]")

    from backend.collections import load_collections
    assert load_collections() == []


def test_migrate_corrupt_files(tmp_state):
    """Corrupt legacy files should not crash migration."""
    (tmp_state / "favourites.json").write_text("not json")

    from backend.collections import load_collections
    assert load_collections() == []


# ---------------------------------------------------------------------------
# Persistence — atomic write
# ---------------------------------------------------------------------------

def test_persistence_across_loads(tmp_state):
    from backend import collections as coll_mod
    coll_mod.create_collection("Persist Test")
    # Load from disk
    data = json.loads((tmp_state / "collections.json").read_text())
    assert len(data) == 1
    assert data[0]["name"] == "Persist Test"


def test_multiple_collections_preserved(tmp_state):
    from backend.collections import create_collection, load_collections
    create_collection("A")
    create_collection("B")
    create_collection("C")
    names = [c["name"] for c in load_collections()]
    assert names == ["A", "B", "C"]

# ---------------------------------------------------------------------------
# Tests for enriched endpoints (member availability)
# ---------------------------------------------------------------------------

class TestEnrichedEndpoints:
    """Tests for /api/collections/enriched and member availability logic."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        import backend.collections as coll_mod
        import backend.config as cfg
        import threading

        monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)
        monkeypatch.setattr(cfg, "COLLECTIONS_FILE", tmp_path / "collections.json")
        monkeypatch.setattr(cfg, "SNAPSHOTS_FILE", tmp_path / "snapshots.json")
        monkeypatch.setattr(cfg, "FAVOURITES_FILE", tmp_path / "favourites.json")
        monkeypatch.setattr(coll_mod, "COLLECTIONS_FILE", tmp_path / "collections.json")
        monkeypatch.setattr(coll_mod, "SNAPSHOTS_FILE", tmp_path / "snapshots.json")
        monkeypatch.setattr(coll_mod, "FAVOURITES_FILE", tmp_path / "favourites.json")
        monkeypatch.setattr(coll_mod, "_lock", threading.RLock())
        self.tmp_path = tmp_path

    def _client(self):
        from fastapi.testclient import TestClient
        import backend.api as api_mod
        # Fake loopback so the auth middleware lets requests through
        return TestClient(api_mod.app, client=("127.0.0.1", 45678))

    def test_enriched_list_empty(self):
        client = self._client()
        r = client.get("/api/collections/enriched")
        assert r.status_code == 200
        assert r.json()["collections"] == []

    def test_enriched_member_recipe_when_no_session_id(self):
        from backend import collections as coll_mod
        c = coll_mod.create_collection("Recipe")
        coll_mod.add_member(c["id"], {"session_id": None, "cwd": "/foo", "title": "Recipe member"})
        client = self._client()
        r = client.get("/api/collections/enriched")
        assert r.status_code == 200
        members = r.json()["collections"][0]["members"]
        assert members[0]["availability"] == "recipe"

    def test_enriched_member_missing_when_session_not_on_disk(self):
        from backend import collections as coll_mod
        c = coll_mod.create_collection("Missing test")
        coll_mod.add_member(c["id"], {"session_id": "nonexistent-id", "cwd": "/foo"})
        client = self._client()
        r = client.get("/api/collections/enriched")
        members = r.json()["collections"][0]["members"]
        assert members[0]["availability"] == "missing"

    def test_enriched_single_collection(self):
        from backend import collections as coll_mod
        c = coll_mod.create_collection("Single")
        coll_mod.add_member(c["id"], {"session_id": None, "cwd": "/bar"})
        client = self._client()
        r = client.get(f"/api/collections/{c['id']}/enriched")
        assert r.status_code == 200
        data = r.json()["collection"]
        assert data["id"] == c["id"]
        assert data["members"][0]["availability"] == "recipe"

    def test_enriched_missing_collection_returns_error(self):
        client = self._client()
        r = client.get("/api/collections/does-not-exist/enriched")
        assert r.status_code == 200
        assert "error" in r.json()


# ---------------------------------------------------------------------------
# Tests for _remove_from_all_collections (called on session delete)
# ---------------------------------------------------------------------------

class TestCollectionCleanupOnDelete:
    """Verify that deleting a session removes it from all collections."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        import backend.collections as coll_mod
        import backend.config as cfg
        import threading

        monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)
        monkeypatch.setattr(cfg, "COLLECTIONS_FILE", tmp_path / "collections.json")
        monkeypatch.setattr(cfg, "SNAPSHOTS_FILE", tmp_path / "snapshots.json")
        monkeypatch.setattr(cfg, "FAVOURITES_FILE", tmp_path / "favourites.json")
        monkeypatch.setattr(coll_mod, "COLLECTIONS_FILE", tmp_path / "collections.json")
        monkeypatch.setattr(coll_mod, "SNAPSHOTS_FILE", tmp_path / "snapshots.json")
        monkeypatch.setattr(coll_mod, "FAVOURITES_FILE", tmp_path / "favourites.json")
        monkeypatch.setattr(coll_mod, "_lock", threading.RLock())
        self.tmp_path = tmp_path

    def _get_remove_fn(self):
        """Import _remove_from_all_collections fresh from api module."""
        from backend.api import _remove_from_all_collections
        return _remove_from_all_collections

    def test_removes_from_single_collection(self):
        from backend import collections as coll_mod
        c = coll_mod.create_collection("Test")
        coll_mod.add_member(c["id"], {"session_id": "s1", "cwd": "/foo"})
        coll_mod.add_member(c["id"], {"session_id": "s2", "cwd": "/bar"})

        fn = self._get_remove_fn()
        fn({"s1"})

        updated = coll_mod.get_collection(c["id"])
        assert len(updated["members"]) == 1
        assert updated["members"][0]["session_id"] == "s2"

    def test_removes_from_multiple_collections(self):
        from backend import collections as coll_mod
        c1 = coll_mod.create_collection("A")
        c2 = coll_mod.create_collection("B")
        coll_mod.add_member(c1["id"], {"session_id": "s1"})
        coll_mod.add_member(c2["id"], {"session_id": "s1"})
        coll_mod.add_member(c2["id"], {"session_id": "s2"})

        fn = self._get_remove_fn()
        fn({"s1"})

        assert coll_mod.get_collection(c1["id"])["members"] == []
        assert len(coll_mod.get_collection(c2["id"])["members"]) == 1

    def test_batch_remove(self):
        from backend import collections as coll_mod
        c = coll_mod.create_collection("Batch")
        for sid in ["s1", "s2", "s3"]:
            coll_mod.add_member(c["id"], {"session_id": sid})

        fn = self._get_remove_fn()
        fn({"s1", "s2"})

        updated = coll_mod.get_collection(c["id"])
        assert len(updated["members"]) == 1
        assert updated["members"][0]["session_id"] == "s3"

    def test_noop_on_empty_set(self):
        from backend import collections as coll_mod
        c = coll_mod.create_collection("Noop")
        coll_mod.add_member(c["id"], {"session_id": "s1"})

        fn = self._get_remove_fn()
        fn(set())  # empty set — nothing should change

        assert len(coll_mod.get_collection(c["id"])["members"]) == 1

    def test_updates_updated_at_timestamp(self):
        from backend import collections as coll_mod
        c = coll_mod.create_collection("Timestamp")
        coll_mod.add_member(c["id"], {"session_id": "s1"})
        before_ts = coll_mod.get_collection(c["id"])["updated_at"]

        import time; time.sleep(0.01)  # ensure clock advances

        fn = self._get_remove_fn()
        fn({"s1"})

        after_ts = coll_mod.get_collection(c["id"])["updated_at"]
        assert after_ts >= before_ts


# ---------------------------------------------------------------------------
# API endpoint tests for collections CRUD
# ---------------------------------------------------------------------------

class TestCollectionsAPI:
    """End-to-end API tests for the collections endpoints."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        import backend.collections as coll_mod
        import backend.config as cfg
        import threading
        from fastapi.testclient import TestClient
        import backend.api as api_mod

        monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)
        monkeypatch.setattr(cfg, "COLLECTIONS_FILE", tmp_path / "collections.json")
        monkeypatch.setattr(cfg, "SNAPSHOTS_FILE", tmp_path / "snapshots.json")
        monkeypatch.setattr(cfg, "FAVOURITES_FILE", tmp_path / "favourites.json")
        monkeypatch.setattr(coll_mod, "COLLECTIONS_FILE", tmp_path / "collections.json")
        monkeypatch.setattr(coll_mod, "SNAPSHOTS_FILE", tmp_path / "snapshots.json")
        monkeypatch.setattr(coll_mod, "FAVOURITES_FILE", tmp_path / "favourites.json")
        monkeypatch.setattr(coll_mod, "_lock", threading.RLock())

        self.client = TestClient(api_mod.app, client=("127.0.0.1", 45678))

    def test_create_and_list(self):
        r = self.client.post("/api/collections", json={"name": "My List"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["collection"]["name"] == "My List"
        assert data["collection"]["source"] == "manual"

        r2 = self.client.get("/api/collections")
        assert r2.status_code == 200
        assert len(r2.json()["collections"]) == 1

    def test_create_requires_name(self):
        r = self.client.post("/api/collections", json={"name": ""})
        assert r.status_code == 200
        assert "error" in r.json()

    def test_get_one(self):
        r = self.client.post("/api/collections", json={"name": "Get One"})
        cid = r.json()["collection"]["id"]

        r2 = self.client.get(f"/api/collections/{cid}")
        assert r2.status_code == 200
        assert r2.json()["collection"]["id"] == cid

    def test_get_missing_returns_error(self):
        r = self.client.get("/api/collections/nonexistent")
        assert r.status_code == 200
        assert "error" in r.json()

    def test_rename(self):
        r = self.client.post("/api/collections", json={"name": "Old"})
        cid = r.json()["collection"]["id"]

        r2 = self.client.post(f"/api/collections/{cid}/rename", json={"name": "New"})
        assert r2.status_code == 200
        assert r2.json()["collection"]["name"] == "New"

    def test_rename_requires_name(self):
        r = self.client.post("/api/collections", json={"name": "X"})
        cid = r.json()["collection"]["id"]
        r2 = self.client.post(f"/api/collections/{cid}/rename", json={"name": ""})
        assert "error" in r2.json()

    def test_delete(self):
        r = self.client.post("/api/collections", json={"name": "Delete Me"})
        cid = r.json()["collection"]["id"]

        r2 = self.client.delete(f"/api/collections/{cid}")
        assert r2.status_code == 200
        assert r2.json()["ok"] is True

        r3 = self.client.get("/api/collections")
        assert len(r3.json()["collections"]) == 0

    def test_delete_missing(self):
        r = self.client.delete("/api/collections/nonexistent")
        assert r.status_code == 200
        assert "error" in r.json()

    def test_add_member(self):
        r = self.client.post("/api/collections", json={"name": "Members"})
        cid = r.json()["collection"]["id"]

        r2 = self.client.post(f"/api/collections/{cid}/members",
                               json={"session_id": "s1", "cwd": "/foo", "title": "Test"})
        assert r2.status_code == 200
        assert r2.json()["ok"] is True
        assert len(r2.json()["collection"]["members"]) == 1

    def test_remove_member(self):
        r = self.client.post("/api/collections", json={"name": "Members"})
        cid = r.json()["collection"]["id"]
        self.client.post(f"/api/collections/{cid}/members",
                         json={"session_id": "s1", "cwd": "/foo"})
        self.client.post(f"/api/collections/{cid}/members",
                         json={"session_id": "s2", "cwd": "/bar"})

        r2 = self.client.post(f"/api/collections/{cid}/members/remove",
                               json={"session_id": "s1"})
        assert r2.status_code == 200
        assert r2.json()["ok"] is True
        remaining = r2.json()["collection"]["members"]
        assert len(remaining) == 1
        assert remaining[0]["session_id"] == "s2"

    def test_remove_member_requires_session_id(self):
        r = self.client.post("/api/collections", json={"name": "X"})
        cid = r.json()["collection"]["id"]
        r2 = self.client.post(f"/api/collections/{cid}/members/remove", json={})
        assert "error" in r2.json()

    def test_reorder_members(self):
        r = self.client.post("/api/collections", json={"name": "Reorder"})
        cid = r.json()["collection"]["id"]
        for sid in ["s1", "s2", "s3"]:
            self.client.post(f"/api/collections/{cid}/members",
                             json={"session_id": sid})

        r2 = self.client.post(f"/api/collections/{cid}/reorder",
                               json={"session_ids": ["s3", "s1", "s2"]})
        assert r2.status_code == 200
        ids = [m["session_id"] for m in r2.json()["collection"]["members"]]
        assert ids == ["s3", "s1", "s2"]

    def test_reorder_requires_session_ids(self):
        r = self.client.post("/api/collections", json={"name": "X"})
        cid = r.json()["collection"]["id"]
        r2 = self.client.post(f"/api/collections/{cid}/reorder", json={})
        assert "error" in r2.json()

    def test_enriched_list(self):
        r = self.client.post("/api/collections", json={"name": "Enriched"})
        cid = r.json()["collection"]["id"]
        self.client.post(f"/api/collections/{cid}/members",
                         json={"session_id": None, "cwd": "/recipe"})

        r2 = self.client.get("/api/collections/enriched")
        assert r2.status_code == 200
        data = r2.json()
        assert "collections" in data
        colls = data["collections"]
        assert len(colls) == 1
        assert colls[0]["members"][0]["availability"] == "recipe"

    def test_enriched_single(self):
        r = self.client.post("/api/collections", json={"name": "Single Enriched"})
        cid = r.json()["collection"]["id"]
        self.client.post(f"/api/collections/{cid}/members",
                         json={"session_id": "missing-id"})

        r2 = self.client.get(f"/api/collections/{cid}/enriched")
        assert r2.status_code == 200
        c = r2.json()["collection"]
        assert c["id"] == cid
        assert c["members"][0]["availability"] == "missing"

    def test_create_with_source_and_members(self):
        members = [{"session_id": "s1", "cwd": "/x", "title": "T"}]
        r = self.client.post("/api/collections",
                             json={"name": "Snap", "source": "snapshot", "members": members})
        assert r.status_code == 200
        c = r.json()["collection"]
        assert c["source"] == "snapshot"
        assert len(c["members"]) == 1
