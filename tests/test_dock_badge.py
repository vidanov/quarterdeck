"""Badge storms must not start processes or flood the native main queue."""
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from backend import api
from backend.dock_badge import DockBadge


def test_install_clears_previous_launch_badge_without_frontend_request():
    badge, queued, applied = DockBadge(), [], []
    badge.install(queued.append, applied.append)
    assert len(queued) == 1
    queued.pop()()
    assert applied == ['']


def test_headless_badges_never_start_automation():
    badge = DockBadge()
    with patch.object(api, '_dock_badge', badge), patch('subprocess.run') as run:
        client = TestClient(api.app, client=('127.0.0.1', 1234))
        for count in [0, 1, 2, 0] * 20:
            assert client.post('/api/badge', json={'count': count}).json()['skipped'] == 'no native dock'
        run.assert_not_called()


def test_coalesces_changes_until_main_queue_runs():
    badge, queued, applied = DockBadge(), [], []
    badge.install(queued.append, applied.append)
    for label in ['1', '2', '', '3']:
        badge.set(label)
    assert len(queued) == 1
    assert applied == []
    queued.pop()()
    assert applied == ['3']
    badge.set('3')
    assert queued == []
    badge.set('')
    queued.pop()()
    assert applied == ['3', '']


def test_failure_disables_badges_without_retry_storm():
    badge, queued = DockBadge(), []
    apply = Mock(side_effect=RuntimeError('GUI unavailable'))
    badge.install(queued.append, apply)
    badge.set('1')
    queued.pop()()
    for label in ['2', '', '3']:
        assert 'skipped' in badge.set(label)
    assert queued == []
    apply.assert_called_once()


def test_invalid_badge_body_is_handled():
    client = TestClient(api.app, client=('127.0.0.1', 1234))
    assert 'error' in client.post('/api/badge', json=[]).json()


def test_change_during_apply_is_delivered_on_next_queue_turn():
    badge, queued, applied = DockBadge(), [], []

    def apply(label):
        applied.append(label)
        if label == '1':
            badge.set('2')
            badge.set('3')

    badge.install(queued.append, apply)
    badge.set('1')
    queued.pop(0)()
    assert len(queued) == 1
    queued.pop(0)()
    assert applied == ['1', '3']
    assert queued == []
