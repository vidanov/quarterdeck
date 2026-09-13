"""Recovery must recognize the composer observed on the affected live session."""
import pytest


@pytest.mark.parametrize("marker", ["", "› ", "›  "])
def test_recovery_delivers_to_ready_composer(monkeypatch, marker):
    from backend import api

    pane = (
        "cmux · auto · ◔ 5%\n\n"
        f"{marker}ask a question or describe a task ↵\n"
        " /copy to clipboard\n" + "\n" * 30
    )
    sent = []
    monkeypatch.setattr(api.tmux, "capture", lambda _: pane)
    monkeypatch.setattr(api.tmux, "send_text", lambda sid, text: sent.append((sid, text)))
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    assert api._deliver_carried_prompt("recovery-test", "ping", timeout=0.1)
    assert sent == [("recovery-test", "ping")]


def test_dropped_link_with_composer_is_not_ready():
    from backend.api import pane_status

    pane = (
        "● Agent connection closed unexpectedly\n"
        "────────────────────────────────────\n"
        "cmux · auto · ◔ 5%\n"
        "›  ask a question or describe a task ↵\n"
    )
    assert pane_status(pane) == "stalled"
