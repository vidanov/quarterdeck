"""Tests for LaunchAgent log rotation.

The file this exists for reached 133MB: launchd appends to the remote proxy's
StandardOutPath forever, and the agent is KeepAlive, so a uvicorn that cannot
import its dependencies is restarted and writes another traceback, endlessly.

The inode assertion is the important one. launchd holds the descriptor open
while the agent runs, so a rename would leave it writing to the old inode — the
rotated copy would keep growing and the new file would stay empty, which looks
like rotation working right up to the point the disk fills.
"""
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.logs import rotate_if_big


class TestRotateIfBig:
    def test_a_small_file_is_left_alone(self, tmp_path):
        log = tmp_path / "remote.log"
        log.write_bytes(b"x" * 100)
        assert rotate_if_big(log, max_bytes=1000) is False
        assert log.read_bytes() == b"x" * 100
        assert not (tmp_path / "remote.log.1").exists()

    def test_a_file_at_the_cap_is_left_alone(self, tmp_path):
        log = tmp_path / "remote.log"
        log.write_bytes(b"x" * 1000)
        assert rotate_if_big(log, max_bytes=1000) is False
        assert log.stat().st_size == 1000

    def test_an_oversized_file_is_emptied(self, tmp_path):
        log = tmp_path / "remote.log"
        log.write_bytes(b"x" * 5000)
        assert rotate_if_big(log, max_bytes=1000, tail_bytes=100) is True
        assert log.stat().st_size == 0

    def test_the_inode_survives_rotation(self, tmp_path):
        """A rename here would silently break a running launchd agent."""
        log = tmp_path / "remote.log"
        log.write_bytes(b"x" * 5000)
        before = log.stat().st_ino
        rotate_if_big(log, max_bytes=1000, tail_bytes=100)
        assert log.stat().st_ino == before

    def test_an_open_appending_writer_keeps_working(self, tmp_path):
        """What launchd does: hold the fd, append. Writes must land in the
        truncated file, not extend a rotated-away copy."""
        log = tmp_path / "remote.log"
        log.write_bytes(b"old" * 2000)
        with log.open("ab") as writer:
            rotate_if_big(log, max_bytes=1000, tail_bytes=64)
            writer.write(b"after\n")
            writer.flush()
        assert log.read_bytes() == b"after\n"

    def test_the_tail_is_kept_for_diagnosis(self, tmp_path):
        log = tmp_path / "remote.log"
        log.write_bytes(b"junk" * 1000 + b"ModuleNotFoundError: watchdog\n")
        rotate_if_big(log, max_bytes=100, tail_bytes=64)
        kept = (tmp_path / "remote.log.1").read_bytes()
        assert kept.endswith(b"ModuleNotFoundError: watchdog\n")
        assert len(kept) == 64

    def test_a_second_rotation_replaces_the_previous_tail(self, tmp_path):
        log = tmp_path / "remote.log"
        log.write_bytes(b"first" * 500)
        rotate_if_big(log, max_bytes=100, tail_bytes=32)
        log.write_bytes(b"second" * 500)
        rotate_if_big(log, max_bytes=100, tail_bytes=32)
        assert b"second" in (tmp_path / "remote.log.1").read_bytes()
        assert not (tmp_path / "remote.log.1.tmp").exists()

    def test_a_missing_file_is_not_created(self, tmp_path):
        log = tmp_path / "absent.log"
        assert rotate_if_big(log, max_bytes=10) is False
        assert not log.exists()

    def test_a_directory_in_the_way_is_survived(self, tmp_path):
        """Never raise: this runs on the poll path and at startup."""
        log = tmp_path / "remote.log"
        log.mkdir()
        assert rotate_if_big(log, max_bytes=0) is False


class TestWiring:
    def test_the_agent_log_path_and_cap_come_from_config(self):
        from backend import config
        assert config.REMOTE_LOG.name == "remote.log"
        assert config.REMOTE_LOG_MAX_BYTES == 8 * 1024 * 1024

    def test_the_plist_points_at_the_configured_path(self):
        """The plist used to spell the path out twice, next to a cap that knew
        nothing about it."""
        source = (Path(__file__).parent.parent / "backend" / "api.py").read_text()
        assert "<key>StandardOutPath</key><string>{REMOTE_LOG}</string>" in source
        assert "<key>StandardErrorPath</key><string>{REMOTE_LOG}</string>" in source
        assert ".osa-kiro/remote.log</string>" not in source
