"""The summary worker must not outlive a kiro-cli login change.

A kiro-cli process writes the login it started with back to the shared auth
DB on refresh, so a stale worker undid profile switches and left the old
account's token under the new subscription ("bearer token ... is invalid").
"""
from backend import acp_worker


class _FakeSession:
    def __init__(self):
        self.stopped = False

    def collect_response(self, sid, prompt, timeout):
        return "ok"

    def stop(self):
        self.stopped = True


def _setup(monkeypatch, logins):
    spawned = []

    def spawn():
        s = _FakeSession()
        spawned.append(s)
        return s, f"sid-{len(spawned)}"

    seq = iter(logins)
    monkeypatch.setattr(acp_worker, "_spawn", spawn)
    monkeypatch.setattr(acp_worker, "_current_login", lambda: next(seq))
    monkeypatch.setattr(acp_worker, "_is_alive", lambda s: not s.stopped)
    monkeypatch.setattr(acp_worker, "_session", None)
    monkeypatch.setattr(acp_worker, "_session_id", "")
    monkeypatch.setattr(acp_worker, "_session_login", None)
    return spawned


def test_same_login_reuses_worker(monkeypatch):
    a = ("https://a.awsapps.com/start", "arn:a")
    spawned = _setup(monkeypatch, [a, a, a])
    for _ in range(3):
        assert acp_worker.query("hi") == "ok"
    assert len(spawned) == 1


def test_login_change_respawns_worker(monkeypatch):
    a = ("https://a.awsapps.com/start", "arn:a")
    b = ("https://a.awsapps.com/start", "arn:b")
    spawned = _setup(monkeypatch, [a, b])
    acp_worker.query("hi")
    acp_worker.query("hi")
    assert len(spawned) == 2
    assert spawned[0].stopped


def test_unreadable_db_is_not_a_change(monkeypatch):
    a = ("https://a.awsapps.com/start", "arn:a")
    spawned = _setup(monkeypatch, [a, None])
    acp_worker.query("hi")
    acp_worker.query("hi")
    assert len(spawned) == 1


def test_restart_kills_worker_and_next_query_respawns(monkeypatch):
    a = ("https://a.awsapps.com/start", "arn:a")
    spawned = _setup(monkeypatch, [a, a])
    acp_worker.query("hi")
    acp_worker.restart()
    assert spawned[0].stopped
    acp_worker.query("hi")
    assert len(spawned) == 2
