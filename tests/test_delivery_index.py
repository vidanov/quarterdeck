import json
import threading
import time
from pathlib import Path
from unittest.mock import Mock

from backend import delivery, screenshots
from backend.delivery_index import DeliveryIndex


def rec(ts, method='static_inference', **extra):
    return {'session_id': 's1', 'ts': ts, 'method': method, **extra}


def test_history_is_incremental_and_latest_really_is_latest(tmp_path):
    path = tmp_path / '2026-09-11.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in [rec(1, agent='old'), rec(2, agent='new'), rec(3, 'probe_echo')])+'\n')
    index = DeliveryIndex(tmp_path)
    index.scan_file(path)
    assert index.get('s1')[0]['agent'] == 'new'
    assert len(index.get('s1')[1]) == 1
    offset = index.offsets[path.name][1]
    index.checkpoint()
    loaded = DeliveryIndex(tmp_path)
    loaded.load()
    assert loaded.offsets[path.name][1] == offset
    with path.open('a') as f:
        f.write('malformed\n'+json.dumps(rec(4, agent='latest'))+'\n')
    loaded.scan_file(path)
    assert loaded.get('s1')[0]['agent'] == 'latest'
    assert len(loaded.get('s1')[1]) == 1


def test_partial_line_is_retried(tmp_path):
    path = tmp_path / 'day.jsonl'
    line = json.dumps(rec(1))
    path.write_text(line[:20])
    index = DeliveryIndex(tmp_path)
    index.scan_file(path)
    assert index.get('s1')[0] == {}
    with path.open('a') as f:
        f.write(line[20:]+'\n')
    index.scan_file(path)
    assert index.get('s1')[0]['ts'] == 1


def test_live_record_wins_over_older_history_and_probe_replay_deduplicates(tmp_path):
    index = DeliveryIndex(tmp_path)
    index.add(rec(10, agent='live'))
    index.add(rec(1, agent='old'))
    probe = rec(2, 'probe_echo')
    index.add(probe)
    index.add(probe)
    assert index.get('s1')[0]['agent'] == 'live'
    assert len(index.get('s1')[1]) == 1


def test_request_does_not_read_any_archive(tmp_path, monkeypatch):
    index = DeliveryIndex(tmp_path)
    index.add(rec(1, agent='live'))
    monkeypatch.setattr(delivery, '_index', index)
    monkeypatch.setattr(delivery, '_delivery_dir', tmp_path)
    monkeypatch.setattr(Path, 'read_text', Mock(side_effect=AssertionError('Archive read on HTTP path')))
    assert delivery.get_session_delivery('s1')['agent'] == 'live'
    assert delivery.get_session_delivery('unknown')['history_loading']


def test_unchanged_polls_do_not_grow_log(tmp_path, monkeypatch):
    from backend.cache import LruCache
    monkeypatch.setattr(delivery, '_static_cache', LruCache(512))
    monkeypatch.setattr(delivery, '_delivery_dir', tmp_path)
    monkeypatch.setattr(delivery, '_index', DeliveryIndex(tmp_path))
    inference = {'expected': [], 'notes': ['same']}
    monkeypatch.setattr(delivery, '_steering_files_for_agent', lambda *a: inference)
    for i in range(10):
        # Advance beyond the inference TTL on each call: even recomputation
        # must not append unchanged static observations.
        monkeypatch.setattr(delivery.time, 'monotonic', lambda: i * 31)
        delivery.record_session_delivery('s1', 'agent', '/workspace')
    assert len(next(tmp_path.glob('*.jsonl')).read_text().splitlines()) == 1
    inference['notes'] = ['changed']
    monkeypatch.setattr(delivery.time, 'monotonic', lambda: 1000)
    delivery.record_session_delivery('s1', 'agent', '/workspace')
    assert len(next(tmp_path.glob('*.jsonl')).read_text().splitlines()) == 2


def test_empty_workspace_does_not_scan_current_directory():
    assert delivery._collect_always_files(None) == []


def test_screenshot_polls_do_not_wait_for_or_multiply_scans(monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(screenshots, '_files_cache', {'folder': '/shots', 'at': 0, 'items': []})
    monkeypatch.setattr(screenshots, '_files_scanning', False)
    monkeypatch.setattr(screenshots, 'configured_path', lambda: '/shots')
    def slow(_):
        entered.set()
        assert release.wait(3)
        finished.set()
        return [{'at': time.time(), 'name': 'new.png'}]
    scan = Mock(side_effect=slow)
    monkeypatch.setattr(screenshots, '_scan_recent_files', scan)
    try:
        assert screenshots.recent_files() == []
        assert entered.wait(1)
        for _ in range(50):
            assert screenshots.recent_files() == []
        assert scan.call_count == 1
    finally:
        release.set()
        assert finished.wait(2)
        # Wait for the short cache publication before patches are restored.
        for _ in range(100):
            with screenshots._files_lock:
                if not screenshots._files_scanning:
                    break
            time.sleep(.001)
    assert screenshots.recent_files()[0]['name'] == 'new.png'
