"""Tests for the app entry point — the parts that decide whether it starts."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import app


class TestPortClash:
    """macOS relaunches a bundle as a new process instead of reactivating the
    running one, so this path is hit by an ordinary double-click, not only by a
    misconfiguration. Exiting mutely there reads as "the app does not start"."""

    def test_a_second_launch_raises_the_window_already_open(self):
        with patch.object(app, "activate_running_instance", return_value=True), \
             patch.object(app, "show_alert") as alert:
            assert app.handle_port_clash("Quarterdeck 71838", 19418) == 0
        alert.assert_not_called()

    def test_a_stranger_on_the_port_is_reported_where_it_can_be_seen(self):
        # Not another Quarterdeck, so there is nothing to bring forward and the user
        # has to be told — stderr is invisible to a Finder launch.
        with patch.object(app, "activate_running_instance", return_value=False), \
             patch.object(app, "show_alert", return_value=True) as alert:
            assert app.handle_port_clash("some-other-server 42", 19418) == 1
        assert alert.call_count == 1
        title, message = alert.call_args[0]
        assert "some-other-server 42" in message
        assert "19418" in message

    def test_it_never_takes_over_a_port_it_did_not_claim(self):
        # The failure this guards against was silent and worse than a crash: the
        # window attached to whatever else was listening, so you read another
        # Quarterdeck's state and edited code that was not running.
        with patch.object(app, "activate_running_instance", return_value=False), \
             patch.object(app, "show_alert", return_value=False):
            assert app.handle_port_clash("Quarterdeck 1", 19418) != 0


class TestHolderOf:
    """The alert is only worth showing if it can name the holder."""

    def test_a_holder_on_a_non_loopback_address_is_named(self):
        # The bug this pins: `lsof -iTCP@127.0.0.1:<port>` matches only listeners
        # bound to that exact address, so a holder on 0.0.0.0 — or on the
        # Tailscale address, which is how remote serving runs — blocked the
        # loopback bind while being invisible to the lookup. The alert then read
        # "an unidentified process", which is the one thing it exists not to say.
        import socket
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            named = app.holder_of(port)
        finally:
            server.close()
        assert "unidentified" not in named, named
        assert str(port) in named, named

    def test_an_unheld_port_says_so_rather_than_inventing_a_holder(self):
        import socket
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
        probe.close()
        assert app.holder_of(free) == "an unidentified process"

    def test_a_missing_lsof_is_not_blamed_on_the_holder(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert "lsof" in app.holder_of(19418)


LSOF_HEADER = "COMMAND     PID       USER   FD   TYPE  DEVICE SIZE/OFF NODE NAME"
LSOF_TAILNET = ("Python    16327 testuser    7u  IPv4  0x1234      0t0  TCP "
                "100.111.220.56:19418 (LISTEN)")
LSOF_LOOPBACK = ("Quarterde  2035 testuser    9u  IPv4  0x5678      0t0  TCP "
                 "127.0.0.1:19418 (LISTEN)")


def _lsof(*lines):
    """Stand in for the lsof call, so the address column can be controlled."""
    from unittest.mock import MagicMock
    return patch("subprocess.run",
                 return_value=MagicMock(stdout="\n".join((LSOF_HEADER,) + lines)))


class TestWhoActuallyBlocked:
    """Listing every holder is right; naming an innocent one as the cause is not.

    Quarterdeck's own remote listener binds the tailnet address and outlives the
    GUI, so it sits on the port permanently without contending for it. Quitting
    the app and relaunching used to fail the loopback bind — the old socket was
    still going away — and the alert then pointed at the remote listener, which
    had nothing to do with it.
    """

    def test_only_loopback_and_wildcard_binds_can_block(self):
        assert app.blocks_loopback("127.0.0.1:19418")
        assert app.blocks_loopback("*:19418")
        assert app.blocks_loopback("0.0.0.0:19418")
        # Dual-stack on macOS, so it does take v4 connections with it.
        assert app.blocks_loopback("[::]:19418")
        # The remote listener, and anything else on a real interface.
        assert not app.blocks_loopback("100.111.220.56:19418")
        assert not app.blocks_loopback("192.168.1.20:19418")

    def test_a_bystander_is_not_offered_as_the_holder(self):
        with _lsof(LSOF_TAILNET):
            named = app.holder_of(19418)
        assert "16327" in named, named
        assert "not what blocked it" in named, named
        assert "Try again" in named, named

    def test_the_real_blocker_is_named_without_the_bystander(self):
        with _lsof(LSOF_TAILNET, LSOF_LOOPBACK):
            named = app.holder_of(19418)
        assert "2035" in named, named
        assert "16327" not in named, named


class TestClaimPort:
    """The probe has to agree with uvicorn about what counts as taken."""

    def test_a_free_port_is_claimed(self):
        import socket
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
        probe.close()
        assert app.claim_port(free) == ""

    def test_a_live_loopback_listener_is_still_a_clash(self):
        # SO_REUSEADDR lets the probe past a socket that is merely closing; it
        # does not let two live listeners share an address, which is the case
        # this guard is for.
        import socket
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            with patch.object(app, "PORT_CLAIM_TIMEOUT_S", 0):
                clash = app.claim_port(port)
        finally:
            server.close()
        assert clash != ""
        assert str(port) in clash, clash
