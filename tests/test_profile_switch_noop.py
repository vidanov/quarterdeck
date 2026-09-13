"""Profile switches are atomic, isolated, and never rewind live tokens on a no-op."""
import json
import sqlite3
from unittest.mock import Mock

import pytest
from backend import api, config

LIVE_ROWS = [{"key": "kirocli:odic:token", "value": json.dumps({"access_token": "fresh", "refresh_token": "same"})}]
OTHER_ROWS = [{"key": "kirocli:odic:token", "value": json.dumps({"access_token": "other", "refresh_token": "other"})}]


@pytest.fixture
def profile_store(tmp_path, monkeypatch):
    db = tmp_path / 'auth.sqlite3'
    monkeypatch.setattr(api, '_KIRO_AUTH_DB', db)
    monkeypatch.setattr(api, '_PROFILES_DIR', tmp_path)
    monkeypatch.setattr(api, '_active_profile_cache', (0, ''))
    monkeypatch.setattr(api, '_last_profile_switch_at', 0)
    monkeypatch.setattr(config, '_models_cache', None)
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE auth_kv (key TEXT PRIMARY KEY, value TEXT)')
        con.execute('CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)')
        con.executemany('INSERT INTO auth_kv VALUES (?,?)', [(r['key'], r['value']) for r in LIVE_ROWS])
        con.execute('INSERT INTO state VALUES (?,?)', ('api.codewhisperer.profile', json.dumps({'arn': 'arn:live'})))

    def save(name, rows=LIVE_ROWS, arn='arn:live'):
        (tmp_path / f'{name}.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
        (tmp_path / f'{name}.meta.json').write_text(json.dumps({
            'email': 'test@example.com', 'provider': 'test', 'profile_arn': arn,
            'state_profile': json.dumps({'arn': arn}), 'models': ['model-a'],
            'token_fingerprint': api._token_fingerprint(rows),
        }))
    return save, db, tmp_path


def read_db(db):
    with sqlite3.connect(db) as con:
        return con.execute('SELECT * FROM auth_kv').fetchall(), con.execute('SELECT * FROM state').fetchall()


def test_noop_preserves_fresh_access_token(profile_store):
    save, db, folder = profile_store
    old = [{**LIVE_ROWS[0], 'value': json.dumps({'access_token': 'old', 'refresh_token': 'same'})}]
    save('Live', old)
    before = read_db(db)
    result = api.switch_profile({'name': 'Live'})
    assert result['ok'] and result['unchanged']
    assert read_db(db) == before
    assert not (folder / '_previous.jsonl').exists()


def test_real_switch_updates_tokens_and_subscription(profile_store):
    save, db, folder = profile_store
    save('Other', OTHER_ROWS, 'arn:other')
    result = api.switch_profile({'name': 'Other'})
    assert result['ok'] and not result['unchanged']
    tokens, state = read_db(db)
    assert tokens == [(r['key'], r['value']) for r in OTHER_ROWS]
    assert json.loads(state[0][1])['arn'] == 'arn:other'
    assert (folder / '_previous.jsonl').exists()
    assert api.current_profile()['active_profile'] == 'Other'


def test_same_tokens_different_subscription_is_a_real_switch(profile_store):
    save, db, _ = profile_store
    save('Paid', LIVE_ROWS, 'arn:paid')
    before_tokens = read_db(db)[0]
    result = api.switch_profile({'name': 'Paid'})
    assert result['ok'] and not result['unchanged']
    assert read_db(db)[0] == before_tokens
    assert json.loads(read_db(db)[1][0][1])['arn'] == 'arn:paid'


def test_state_failure_rolls_back_tokens(profile_store):
    save, db, _ = profile_store
    save('Other', OTHER_ROWS, 'arn:other')
    with sqlite3.connect(db) as con:
        con.execute("CREATE TRIGGER reject_state BEFORE INSERT ON state BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    before = read_db(db)
    result = api.switch_profile({'name': 'Other'})
    assert 'error' in result and not result.get('ok')
    assert read_db(db) == before


def test_missing_subscription_does_not_change_login(profile_store):
    save, db, _ = profile_store
    save('Incomplete', OTHER_ROWS, '')
    before = read_db(db)
    result = api.switch_profile({'name': 'Incomplete'})
    assert 'error' in result and not result.get('ok')
    assert read_db(db) == before


def test_switch_does_not_wait_for_cli_or_model_lookup(profile_store, monkeypatch):
    save, _, _ = profile_store
    save('Other', OTHER_ROWS, 'arn:other')
    cli = Mock(side_effect=AssertionError('Switch must not run CLI'))
    monkeypatch.setattr(api.subprocess, 'run', cli)
    assert api.switch_profile({'name': 'Other'})['ok']
    assert api.current_profile()['active_profile'] == 'Other'
    assert config._models_cache[1] == ('model-a',)
    cli.assert_not_called()


@pytest.mark.parametrize('name', ['', '  ', '../outside', '_previous', ' _previous ', 'missing'])
def test_invalid_names_do_not_change_login(profile_store, name):
    _, db, _ = profile_store
    before = read_db(db)
    assert 'error' in api.switch_profile({'name': name})
    assert read_db(db) == before


def test_overlapping_switch_is_refused(profile_store):
    save, db, _ = profile_store
    save('Other', OTHER_ROWS, 'arn:other')
    before = read_db(db)
    with api._profile_switch_lock:
        assert 'error' in api.switch_profile({'name': 'Other'})
    assert read_db(db) == before
    assert api.switch_profile({'name': 'Other'})['ok']


def test_arn_only_snapshot_is_not_reconstructed_with_guessed_fields(profile_store):
    save, db, folder = profile_store
    save('Legacy', OTHER_ROWS, 'arn:other')
    meta = folder / 'Legacy.meta.json'
    data = json.loads(meta.read_text())
    data.pop('state_profile')
    meta.write_text(json.dumps(data))
    before = read_db(db)
    assert 'error' in api.switch_profile({'name': 'Legacy'})
    assert read_db(db) == before
