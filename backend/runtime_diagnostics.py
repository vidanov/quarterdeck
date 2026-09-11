"""Persistent native-app output and evidence for slow or stuck HTTP requests.

Never record request headers, query strings, or bodies. Streaming responses
stop counting as pending once their response headers have been sent.
"""
import faulthandler
import os
import signal
import sys
import threading
import time
from pathlib import Path

from .logs import rotate_if_big


class RequestDiagnostics:
    def __init__(self, output, dump, clock=time.monotonic):
        self.output, self.dump, self.clock = output, dump, clock
        self.lock = threading.Lock()
        self.pending = {}
        self.last_dump = float('-inf')

    def check(self):
        now = self.clock()
        with self.lock:
            slow = [now - started for started in self.pending.values()
                    if now - started >= 15]
        if slow and now - self.last_dump >= 60:
            self.last_dump = now
            self.output(f'backend stalled: {len(slow)} requests waiting for headers; '
                        f'oldest={max(slow):.1f}s; dumping Python threads')
            self.dump()

    def wrap(self, app):
        async def instrumented(scope, receive, send):
            if scope['type'] != 'http':
                return await app(scope, receive, send)
            key, started = object(), self.clock()
            with self.lock:
                self.pending[key] = started
            status = None

            def route_label():
                # A matched route template has no user-supplied query or path values.
                route = scope.get('route')
                return getattr(route, 'path', '<unmatched>')

            async def observed_send(message):
                nonlocal status
                if message['type'] == 'http.response.start':
                    status = message['status']
                    with self.lock:
                        self.pending.pop(key, None)
                    elapsed = self.clock() - started
                    if elapsed >= 2 or status >= 400:
                        self.output(f'HTTP {status} {route_label()} headers={elapsed:.2f}s')
                await send(message)

            try:
                return await app(scope, receive, observed_send)
            except Exception as exc:
                self.output(f'HTTP exception {route_label()} type={type(exc).__name__}')
                raise  # uvicorn records the traceback on stderr
            finally:
                with self.lock:
                    self.pending.pop(key, None)
        return instrumented


def install(directory: Path, name='runtime'):
    """Called only by the GUI entry point; redirect before starting threads."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / f'{name}.log'
    rotate_if_big(path, 8 * 1024 * 1024)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(fd, 0o600)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    if fd not in (1, 2):
        os.close(fd)
    # Windowed PyInstaller builds may set the Python streams to None.
    sys.stdout = os.fdopen(os.dup(1), 'w', buffering=1, encoding='utf-8', errors='replace')
    sys.stderr = os.fdopen(os.dup(2), 'w', buffering=1, encoding='utf-8', errors='replace')
    faulthandler.enable(file=sys.stderr, all_threads=True)
    if hasattr(signal, 'SIGUSR1'):
        faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)

    def output(message):
        print(f'{time.strftime("%Y-%m-%dT%H:%M:%S%z")} pid={os.getpid()} {message}',
              file=sys.stderr, flush=True)

    diagnostics = RequestDiagnostics(
        output, lambda: faulthandler.dump_traceback(file=sys.stderr, all_threads=True))
    stop = threading.Event()

    def monitor():
        while not stop.wait(5):
            rotate_if_big(path, 8 * 1024 * 1024)
            diagnostics.check()

    threading.Thread(target=monitor, name='runtime-diagnostics', daemon=True).start()
    output(f'Quarterdeck starting; Python={sys.version.split()[0]} log={path}')
    return diagnostics, stop
