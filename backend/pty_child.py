"""Assign the inherited PTY as controlling terminal before executing a shell.

Popen(start_new_session=True) has already called setsid. Connecting the slave
to stdio alone does not acquire a controlling terminal; Kiro's startup wrapper
then fails with EPERM. Run this in a fresh child rather than a Python
preexec_fn, which can deadlock after forking a threaded backend.
"""
import fcntl
import os
import sys
import termios


def exec_shell(argv: list[str]) -> None:
    if not argv:
        raise SystemExit("Missing shell command")
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.execvpe(argv[0], argv, os.environ)


if __name__ == "__main__":
    exec_shell(sys.argv[1:])
