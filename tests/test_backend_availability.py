"""Regressions for the September 11 request pile-up and deletion failures."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from backend import api


@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(api, "_sessions_cache", {"data": None, "ts": 0, "dir": None})
    monkeypatch.setattr(api, "_sessions_scan_lock", threading.Lock())
    monkeypatch.setattr(api, "_load_settings", lambda: {})
    return api._sessions_cache


def test_slow_refresh_does_not_multiply_scans(cache, monkeypatch):
    old = {"sessions": [{"id": "old"}]}
    new = {"sessions": [{"id": "new"}]}
    cache.update(data=old, dir=str(api.SESSIONS_DIR))
    entered, release = threading.Event(), threading.Event()

    def slow_scan():
        entered.set()
        assert release.wait(5)
        return new

    scan = Mock(side_effect=slow_scan)
    monkeypatch.setattr(api, "_do_sessions_scan", scan)
    try:
        assert api.list_sessions(True) == old
        assert entered.wait(2)
        with ThreadPoolExecutor(max_workers=12) as pool:
            polls = [pool.submit(api.list_sessions, True) for _ in range(60)]
            assert all(f.result(timeout=2) == old for f in polls)
        # The scheduled refresher must share the same reservation as HTTP polls.
        assert api._refresh_sessions_cache() is None
        assert scan.call_count == 1
    finally:
        release.set()
        assert api._sessions_scan_lock.acquire(timeout=3)
        api._sessions_scan_lock.release()
    assert api.list_sessions(True) == new


def test_cold_polls_do_not_wait_or_return_an_empty_grid(cache, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow_scan():
        entered.set()
        assert release.wait(5)
        return {"sessions": [{"id": "first"}]}

    scan = Mock(side_effect=slow_scan)
    monkeypatch.setattr(api, "_do_sessions_scan", scan)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(api.list_sessions, True)
        try:
            assert entered.wait(2)
            for _ in range(20):
                response = api.list_sessions(True)
                assert response.status_code == 503
                assert response.headers["Retry-After"] == "2"
            assert scan.call_count == 1
        finally:
            release.set()
        assert first.result(timeout=3)["sessions"][0]["id"] == "first"


def test_failed_scan_releases_reservation_and_keeps_snapshot(cache, monkeypatch):
    old = {"sessions": [{"id": "old"}]}
    cache.update(data=old, dir=str(api.SESSIONS_DIR))
    monkeypatch.setattr(api, "_do_sessions_scan", Mock(side_effect=RuntimeError("tmux timeout")))
    with pytest.raises(RuntimeError):
        api._refresh_sessions_cache()
    assert cache["data"] == old
    monkeypatch.setattr(api, "_do_sessions_scan", lambda: {"sessions": []})
    assert api._refresh_sessions_cache() == {"sessions": []}


def test_invalidation_during_scan_discards_result(cache, monkeypatch):
    def scan():
        api._invalidate_sessions_cache()
        return {"sessions": [{"id": "deleted"}]}

    monkeypatch.setattr(api, "_do_sessions_scan", scan)
    assert api._refresh_sessions_cache() is None
    assert cache["data"] is None


def test_cache_never_serves_a_different_directory(cache, monkeypatch):
    cache.update(data={"sessions": [{"id": "wrong"}]}, dir="/elsewhere")
    monkeypatch.setattr(api, "_do_sessions_scan", lambda: {"sessions": [{"id": "right"}]})
    assert api.list_sessions(True)["sessions"][0]["id"] == "right"


def test_file_deleted_during_listing_is_skipped(cache, monkeypatch):
    vanished = Mock()
    vanished.stat.side_effect = FileNotFoundError()
    kept = Mock()
    kept.stat.return_value = SimpleNamespace(st_mtime=3)
    monkeypatch.setattr(Path, "glob", lambda *_: [vanished, kept])
    monkeypatch.setattr(api, "_json_files_cache", {"files": [], "ts": 0, "dir": None})
    assert api._sorted_json_files() == [kept]


def test_build_check_cannot_block_other_requests(tmp_path, monkeypatch):
    state = tmp_path / ".osa-kiro"
    state.mkdir()
    (state / "build-stamp.json").write_text('{"git_sha": "abc"}')
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("DECK_DEV", "1")
    entered, release = threading.Event(), threading.Event()

    def slow_git(*args, **kwargs):
        assert kwargs["timeout"] == 3
        entered.set()
        release.wait(3)
        return "abc"

    monkeypatch.setattr(api.subprocess, "check_output", slow_git)

    async def scenario():
        transport = httpx.ASGITransport(app=api.app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            build = asyncio.create_task(client.get("/api/health/build"))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                response = await asyncio.wait_for(client.get("/api/dev/token"), 1)
                assert response.status_code == 200
                assert not build.done(), "Git blocked the event loop until completion"
            finally:
                release.set()
                await build

    asyncio.run(scenario())
