"""Exercise real macOS terminal ownership, resizing, and job control."""
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.pty_shell import PtySession


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS terminal launcher")
@pytest.mark.parametrize("app_entry", [False, True])
def test_shell_controlling_terminal_resize_and_interrupt(tmp_path, monkeypatch, app_entry):
    if app_entry:
        # Exercise the entry point used by the bundled executable without
        # rebuilding or launching the installed GUI app.
        popen = subprocess.Popen

        def launch(argv, **kwargs):
            entry = Path(__file__).resolve().parents[1] / "app.py"
            return popen([argv[0], str(entry), "--pty-exec", *argv[2:]], **kwargs)

        monkeypatch.setattr("backend.pty_shell.subprocess.Popen", launch)
    # Avoid the developer's startup hooks and history in this integration test.
    shell = tmp_path / "shell"
    shell.write_text('#!/bin/sh\nexec /bin/zsh -f\n')
    shell.chmod(0o700)
    monkeypatch.setenv("SHELL", str(shell))
    sess = PtySession("terminal-test", str(tmp_path), cols=100, rows=30)

    def expect(marker):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with sess._lock:
                output = bytes(sess._ring).decode(errors="replace")
            if marker in output:
                return
            time.sleep(0.02)
        pytest.fail(f"Missing {marker!r} in terminal output: {output!r}")

    try:
        assert sess.start()
        expect("\x1b[?2004h")  # zsh has enabled input at its first prompt
        # A terminal foreground-group query fails when stdio is merely wired
        # to a PTY without assigning it as the controlling terminal.
        probe = "import os; print('TTY' + '_OK' if os.tcgetpgrp(0) == os.getpgrp() else 'BAD_GROUP')"
        sess.write(f"{shlex.quote(sys.executable)} -c {shlex.quote(probe)}\n".encode())
        expect("TTY_OK")
        sess.write(b"stty size\n")
        expect("30 100")
        sess.resize(132, 42)
        sess.write(b"stty size\n")
        expect("42 132")
        sess.write(b"sleep 30\n")
        time.sleep(0.2)
        sess.write(b"\x03")
        sess.write(b"printf 'INTERRUPT_%s\\n' OK\n")
        expect("INTERRUPT_OK")
        sess.write(b"exit\n")
        assert sess.proc.wait(timeout=5) == 0
    finally:
        sess.close()
