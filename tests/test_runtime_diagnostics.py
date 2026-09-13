"""Diagnostics must survive Finder launch and identify hangs without logging tokens."""
import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from backend.runtime_diagnostics import RequestDiagnostics


def test_native_output_and_thread_dump_are_persisted(tmp_path):
    code = '''
import os, sys, faulthandler
from pathlib import Path
from backend.runtime_diagnostics import install
_, stop = install(Path(sys.argv[1]))
print('stdout-evidence', flush=True)
print('stderr-evidence', file=sys.stderr, flush=True)
os.write(2, b'native-evidence\\n')
faulthandler.dump_traceback(file=sys.stderr)
stop.set()
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], timeout=10)
    assert result.returncode == 0
    log = tmp_path / 'runtime.log'
    contents = log.read_text()
    for expected in ['Quarterdeck starting', 'stdout-evidence', 'stderr-evidence',
                     'native-evidence', 'Current thread']:
        assert expected in contents
    assert log.stat().st_mode & 0o777 == 0o600


def test_stuck_request_dump_is_rate_limited_and_stops_after_headers():
    now, messages, dumps = [0], [], []
    diagnostics = RequestDiagnostics(messages.append, lambda: dumps.append(True), lambda: now[0])
    diagnostics.pending['request'] = 0
    now[0] = 14
    diagnostics.check()
    assert not dumps
    now[0] = 15
    diagnostics.check()
    now[0] = 20
    diagnostics.check()
    assert len(dumps) == 1
    now[0] = 75
    diagnostics.check()
    assert len(dumps) == 2
    diagnostics.pending.clear()
    now[0] = 140
    diagnostics.check()
    assert len(dumps) == 2


def test_streaming_response_does_not_trigger_hang_and_query_is_not_logged():
    now, messages, dumps, sent = [0], [], [], []
    diagnostics = RequestDiagnostics(messages.append, lambda: dumps.append(True), lambda: now[0])

    async def app(scope, receive, send):
        scope['route'] = SimpleNamespace(path='/api/sessions/{session_id}/stream')
        now[0] = 3
        await send({'type': 'http.response.start', 'status': 200})
        now[0] = 100
        diagnostics.check()
        await send({'type': 'http.response.body', 'body': b'hello'})

    async def send(message):
        sent.append(message)

    scope = {'type': 'http', 'path': '/api/sessions/private-id/stream',
             'query_string': b't=secret-token', 'headers': [(b'authorization', b'secret')]}
    asyncio.run(diagnostics.wrap(app)(scope, None, send))
    assert len(sent) == 2
    assert not dumps
    assert not diagnostics.pending
    assert messages == ['HTTP 200 /api/sessions/{session_id}/stream headers=3.00s']


def test_exception_remains_visible_to_server_and_pending_is_removed():
    messages = []
    diagnostics = RequestDiagnostics(messages.append, lambda: None)

    async def app(scope, receive, send):
        raise ValueError('private-body-content')

    try:
        asyncio.run(diagnostics.wrap(app)({'type': 'http'}, None, None))
    except ValueError:
        pass
    else:
        raise AssertionError('Exception must propagate to the server')
    assert not diagnostics.pending
    assert messages == ['HTTP exception <unmatched> type=ValueError']
