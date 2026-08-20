"""PTY-backed shell sessions for xterm.js WebSocket streaming.

Each shell is a real subprocess running in a PTY.  The WebSocket endpoint
reads raw bytes from the PTY master and sends them to the browser; it also
forwards bytes from the browser to the PTY master.  xterm.js handles all
ANSI/VT100 rendering, cursor positioning, and colours natively.

Sessions are keyed by shell_id (same hash as shell.py uses for tmux sessions)
so the frontend can open an xterm for the same folder without caring about
the implementation.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import threading
import time
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# PTY session record
# ---------------------------------------------------------------------------

class PtySession:
    RING_BYTES = 65536  # keep last 64 KB for replay on reconnect

    def __init__(self, shell_id: str, cwd: str, cols: int = 220, rows: int = 50):
        self.shell_id = shell_id
        self.cwd = cwd
        self.cols = cols
        self.rows = rows
        self.master_fd: int = -1
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        # Callbacks registered by the active WebSocket connection
        self._on_data: Callable[[bytes], None] | None = None
        self._reader_thread: threading.Thread | None = None
        self._alive = False
        # Ring buffer — last N bytes of output, replayed on reconnect
        self._ring: bytearray = bytearray()

    def start(self) -> bool:
        """Spawn the login shell in a PTY. Returns True on success."""
        shell = os.environ.get("SHELL") or "/bin/zsh"
        target = str(Path(self.cwd).expanduser().resolve())
        if not Path(target).is_dir():
            target = str(Path.home())

        master, slave = pty.openpty()
        self._set_pty_size(master, self.cols, self.rows)

        try:
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLORTERM"] = "truecolor"
            self.proc = subprocess.Popen(
                [shell, "-l"],
                stdin=slave, stdout=slave, stderr=slave,
                cwd=target,
                env=env,
                close_fds=True,
                start_new_session=True,
            )
        except Exception:
            os.close(master)
            os.close(slave)
            return False

        os.close(slave)
        # Non-blocking reads from the master
        flags = fcntl.fcntl(master, fcntl.F_GETFL)
        fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        self.master_fd = master
        self._alive = True
        self._start_reader()
        return True

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        if self.master_fd >= 0:
            self._set_pty_size(self.master_fd, cols, rows)

    def write(self, data: bytes) -> None:
        if self._alive and self.master_fd >= 0:
            try:
                os.write(self.master_fd, data)
            except OSError:
                self._alive = False

    def set_on_data(self, cb: Callable[[bytes], None] | None) -> None:
        with self._lock:
            self._on_data = cb
            # Replay buffered output so the new client sees the current screen state
            if cb and self._ring:
                replay = bytes(self._ring)
            else:
                replay = None
        if replay:
            try:
                cb(replay)
            except Exception:
                pass

    @property
    def alive(self) -> bool:
        if not self._alive:
            return False
        if self.proc and self.proc.poll() is not None:
            self._alive = False
            return False
        return True

    def close(self) -> None:
        self._alive = False
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        if self.master_fd >= 0:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = -1

    # ------------------------------------------------------------------
    # Internal

    def _start_reader(self) -> None:
        def _read_loop():
            while self._alive:
                try:
                    r, _, _ = select.select([self.master_fd], [], [], 0.1)
                    if not r:
                        if self.proc and self.proc.poll() is not None:
                            self._alive = False
                            break
                        continue
                    data = os.read(self.master_fd, 4096)
                    if not data:
                        self._alive = False
                        break
                    with self._lock:
                        # Append to ring buffer, trim to RING_BYTES
                        self._ring.extend(data)
                        if len(self._ring) > self.RING_BYTES:
                            del self._ring[:len(self._ring) - self.RING_BYTES]
                        cb = self._on_data
                    if cb:
                        try:
                            cb(data)
                        except Exception:
                            pass
                except OSError:
                    self._alive = False
                    break

        self._reader_thread = threading.Thread(target=_read_loop, daemon=True)
        self._reader_thread.start()

    @staticmethod
    def _set_pty_size(fd: int, cols: int, rows: int) -> None:
        try:
            size = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, size)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_sessions: dict[str, PtySession] = {}
_registry_lock = threading.Lock()


def _shell_id(cwd: str) -> str:
    resolved = str(Path(cwd).expanduser().resolve())
    return hashlib.sha1(resolved.encode()).hexdigest()[:8]


def get_or_create(cwd: str, cols: int = 220, rows: int = 50) -> PtySession:
    sid = _shell_id(cwd)
    with _registry_lock:
        sess = _sessions.get(sid)
        if sess and sess.alive:
            return sess
        # Stale or missing — create fresh
        if sess:
            sess.close()
        sess = PtySession(sid, cwd, cols, rows)
        if not sess.start():
            raise RuntimeError(f"Failed to start shell in {cwd}")
        _sessions[sid] = sess
    return sess


def get(shell_id: str) -> PtySession | None:
    with _registry_lock:
        return _sessions.get(shell_id)


def close(shell_id: str) -> bool:
    with _registry_lock:
        sess = _sessions.pop(shell_id, None)
    if sess:
        sess.close()
        return True
    return False


def list_all() -> list[dict]:
    with _registry_lock:
        items = list(_sessions.items())
    result = []
    home = str(Path.home())
    for sid, sess in items:
        result.append({
            "shell_id": sid,
            "alive": sess.alive,
            "cwd": sess.cwd,
            "cwd_short": sess.cwd.replace(home, "~"),
            "cols": sess.cols,
            "rows": sess.rows,
        })
    return result
