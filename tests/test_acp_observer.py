"""Tests for ACP observer lifecycle — the leak, and the bug hiding behind it.

`detach()` runs on the paths where Quarterdeck ends a session itself: kill,
handoff, takeover. Sessions also end on their own — the agent exits, kiro-cli
crashes, someone runs `tmux kill-session` in a terminal — and every one of those
used to leave an entry in the registry holding a live ACP subprocess tree
(kiro-cli-chat → bun → tui.js). Ten sessions had turned into fifty-odd kiro-cli
processes that way.

The same entry made `attach()` lie: the id was present, so it returned True
without starting anything, and that session's V3 streaming stayed dead for as
long as the backend lived.
"""
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend import acp_observer as obs


class FakeSession:
    """Stands in for ACPSession: alive flag plus a recorded stop()."""

    def __init__(self, alive=True):
        self.is_alive = alive
        self.stopped = False

    def stop(self):
        self.stopped = True
        self.is_alive = False


def _entry(alive=True):
    # Duck-typed stand-in: prune() only ever reads .is_alive and calls .stop().
    return obs._Entry(sess=FakeSession(alive),  # type: ignore[arg-type]
                      store=obs._EventStore(), acp_sid="acp-1")


class TestPrune:
    def setup_method(self):
        obs._registry.clear()

    teardown_method = setup_method

    def test_a_dead_observer_is_collected(self):
        dead = _entry(alive=False)
        obs._registry["s1"] = dead
        assert obs.prune() == ["s1"]
        assert obs.attached_count() == 0
        assert dead.sess.stopped is True  # type: ignore[attr-defined]

    def test_a_live_observer_is_left_alone(self):
        live = _entry(alive=True)
        obs._registry["s1"] = live
        assert obs.prune() == []
        assert obs.attached_count() == 1
        assert live.sess.stopped is False  # type: ignore[attr-defined]

    def test_an_observer_for_a_vanished_session_is_collected(self):
        """Its subprocess is still alive; the session it was watching is not."""
        obs._registry["s1"] = _entry(alive=True)
        obs._registry["s2"] = _entry(alive=True)
        assert obs.prune(live_session_ids={"s1"}) == ["s2"]
        assert obs.attached_count() == 1

    def test_no_argument_means_liveness_only(self):
        """Without the live set, a session Quarterdeck cannot see must not be
        assumed gone — ACP-only sessions have no tmux session to check."""
        obs._registry["s1"] = _entry(alive=True)
        assert obs.prune() == []
        assert obs.attached_count() == 1

    def test_a_session_whose_liveness_check_raises_is_collected(self):
        class Exploding:
            stopped = False

            @property
            def is_alive(self):
                raise RuntimeError("pipe is gone")

            def stop(self):
                Exploding.stopped = True

        obs._registry["s1"] = obs._Entry(sess=Exploding(),  # type: ignore[arg-type]
                                         store=obs._EventStore(), acp_sid="a")
        assert obs.prune() == ["s1"]
        assert obs.attached_count() == 0


class TestAttachReplacesDeadEntries:
    def setup_method(self):
        obs._registry.clear()

    teardown_method = setup_method

    def test_a_live_entry_short_circuits(self):
        obs._registry["s1"] = _entry(alive=True)
        with patch.object(obs, "ACPSession") as factory:
            assert obs.attach("s1") is True
        factory.assert_not_called()

    def test_a_dead_entry_is_dropped_and_a_new_attach_attempted(self):
        dead = _entry(alive=False)
        obs._registry["s1"] = dead
        # Make the replacement attempt fail fast — what matters here is that
        # the corpse was detached and a new session was actually tried.
        with patch.object(obs, "ACPSession") as factory:
            factory.return_value.start.side_effect = RuntimeError("no binary")
            assert obs.attach("s1") is False
        factory.assert_called_once()
        assert dead.sess.stopped is True  # type: ignore[attr-defined]
        assert "s1" not in obs._registry
