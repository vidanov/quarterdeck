"""Tests for the size-capped cache the per-session memoisation uses.

The bug it exists for: `_tail_cache`, `_last_message_cache`, `_capture_cache`
and `_get_pane_cache` were plain dicts holding up to 64KB of text per session,
keyed by session id, with no eviction. A backend that had looked at the whole
archive held one entry per session for the life of the process.
"""
import threading
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from backend.cache import LruCache


class TestLruCache:
    def test_stores_and_reads_back(self):
        c = LruCache(maxsize=4)
        c["a"] = 1
        assert c.get("a") == 1
        assert c["a"] == 1
        assert "a" in c
        assert len(c) == 1

    def test_missing_key_returns_the_default(self):
        c = LruCache(maxsize=4)
        assert c.get("nope") is None
        assert c.get("nope", "fallback") == "fallback"
        assert "nope" not in c

    def test_evicts_the_least_recently_used(self):
        c = LruCache(maxsize=3)
        for key in ("a", "b", "c"):
            c[key] = key
        c["d"] = "d"
        assert "a" not in c
        assert len(c) == 3

    def test_a_read_counts_as_a_use(self):
        """The poll hits a few live sessions repeatedly while the archive is
        read once — those live entries must be the ones that survive."""
        c = LruCache(maxsize=3)
        for key in ("a", "b", "c"):
            c[key] = key
        c.get("a")          # a is now the most recently used
        c["d"] = "d"        # evicts b, not a
        assert "a" in c
        assert "b" not in c

    def test_a_write_to_an_existing_key_refreshes_it(self):
        c = LruCache(maxsize=2)
        c["a"] = 1
        c["b"] = 2
        c["a"] = 3
        c["c"] = 4
        assert c.get("a") == 3
        assert "b" not in c

    def test_pop_removes_and_returns(self):
        c = LruCache(maxsize=2)
        c["a"] = 1
        assert c.pop("a") == 1
        assert c.pop("a", "gone") == "gone"
        assert len(c) == 0

    def test_clear_empties_it(self):
        c = LruCache(maxsize=2)
        c["a"] = 1
        c.clear()
        assert len(c) == 0

    def test_size_never_exceeds_the_cap_under_load(self):
        """The cap is the whole point: this is the assertion that it holds."""
        c = LruCache(maxsize=10)
        for i in range(1000):
            c[str(i)] = "x" * 100
        assert len(c) == 10

    def test_a_zero_cap_is_refused(self):
        with pytest.raises(ValueError):
            LruCache(maxsize=0)

    def test_concurrent_writers_do_not_corrupt_it(self):
        """Written from the background refresh thread, read from request
        threads — so the eviction bookkeeping runs under a lock."""
        c = LruCache(maxsize=50)

        def hammer(offset):
            for i in range(500):
                c[f"{offset}-{i}"] = i
                c.get(f"{offset}-{i // 2}")

        threads = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(c) == 50


class TestCallSitesAreCapped:
    """A regression guard: these four were the leak, so they must stay capped."""

    def test_every_per_session_cache_is_an_lru(self):
        from backend import api, shell, tmux_manager
        assert isinstance(api._tail_cache, LruCache)
        assert isinstance(api._last_message_cache, LruCache)
        assert isinstance(tmux_manager._capture_cache, LruCache)
        assert isinstance(shell._get_pane_cache, LruCache)

    def test_the_tail_cache_stays_bounded_when_the_archive_is_read(self, tmp_path):
        from backend import api
        api._tail_cache.clear()
        session_dir = tmp_path
        for i in range(200):
            (session_dir / f"sess{i}.jsonl").write_text('{"kind":"Prompt"}\n')
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
                api, "SESSIONS_DIR", session_dir):
            for i in range(200):
                api.tail_jsonl(f"sess{i}", 5)
        assert len(api._tail_cache) <= api._tail_cache.maxsize
