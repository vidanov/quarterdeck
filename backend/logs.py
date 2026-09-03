"""Rotation for the log files launchd writes on our behalf.

The remote proxy runs as a LaunchAgent with `StandardOutPath` and
`StandardErrorPath` both pointing at ~/.osa-kiro/remote.log. launchd appends to
that file and never rotates it, and the agent is declared `KeepAlive`, so a
uvicorn that cannot start is restarted forever with a fresh traceback each time.
The file found on this machine was 133MB of exactly that — a crash loop ending
in `ModuleNotFoundError: No module named 'watchdog'`, appended since August.

Rotation here is a truncate, not a rename, and that is the whole point of the
module: launchd holds an open descriptor for as long as the agent runs. Renaming
the file leaves launchd writing to the old inode — the "rotated" copy keeps
growing, the new file stays empty, and nothing looks wrong until the disk is
gone. Truncating in place keeps the inode, and because the descriptor is opened
append-only the next write lands at offset zero.

The tail is kept in a sibling `.1` first, because the reason to read this file
at all is to find out why the agent would not start.
"""
import os
from pathlib import Path


def rotate_if_big(path: Path, max_bytes: int, tail_bytes: int = 256 * 1024) -> bool:
    """Truncate `path` if it exceeds `max_bytes`, keeping its tail in `<path>.1`.

    Returns True if it rotated. Never raises: this runs on a poll path and on
    startup, where a log that cannot be rotated is not worth failing over.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= max_bytes:
        return False

    keep = min(tail_bytes, size)
    try:
        with path.open("rb") as f:
            f.seek(size - keep)
            tail = f.read()
    except OSError:
        tail = b""

    if tail:
        # Write the tail via a temp file so a crash mid-rotation cannot leave a
        # half-written `.1` in place of the previous one.
        previous = path.with_name(path.name + ".1")
        tmp = path.with_name(path.name + ".1.tmp")
        try:
            tmp.write_bytes(tail)
            tmp.replace(previous)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass

    try:
        # os.truncate, not unlink/rename — see the module docstring.
        os.truncate(path, 0)
    except OSError:
        return False
    return True
