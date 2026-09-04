"""Tests for link-drop detection and reanimate (backend/api.py).

The pane fixtures mirror real captures: a kiro-cli session whose agent link
dropped between turns (43be4af1, 2026-09-04) and one that dropped mid-tool-use
(4ed031ea, same day). Both are the shape this feature exists for.
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import backend.api as api_mod
from backend.api import (
    _trim_dangling_tool_use,
    pane_link_dropped,
    pane_status,
    pane_unsent_prompt,
)

# ── fixtures ──────────────────────────────────────────────────────────────────

# Dropped after a user prompt: the prompt is the only thing that needs carrying.
PANE_DROPPED_AFTER_PROMPT = """\
  All done. PR #53 opened.

──────────────────────────────────────────────────────────────
  open the draft for me, #3872 is popular what is about? spend your comments smart

● Agent connection closed unexpectedly
──────────────────────────────────────────────────────────────
cmux · auto · 13%                    ~/kiro-workspace/scratch/ai-ready-repo · (main)
 ask a question or describe a task
                                                          /copy to clipboard
"""

# Dropped mid-tool-use: nothing above the banner belongs to the user.
PANE_DROPPED_MID_TOOL = """\
● Read /Users/a/Obsidian Vault/Kiro/hot.md
● Glob "Daily/2026-09/*.md"
    ╰ path=/Users/a/Obsidian Vault
● Agent connection closed unexpectedly
──────────────────────────────────────────────────────────────
cmux · auto · 8%                     ~/kiro-workspace/scratch · (main)
 ask a question or describe a task
"""

PANE_IDLE = """\
● All done.
──────────────────────────────────────────────────────────────
cmux · auto · 8%                     ~/scratch · (main)
 ask a question or describe a task
"""

# The banner is in scrollback but the agent has spoken since — recovered.
PANE_RECOVERED = """\
  open the draft for me
● Agent connection closed unexpectedly
● Reading the draft now.
──────────────────────────────────────────────────────────────
cmux · auto · 14%                    ~/scratch · (main)
 ask a question or describe a task
"""

# Dropped mid-answer: the last unprefixed block is the agent's own prose, not
# a prompt. Real capture from 43be4af1 after a partial recovery — an
# indentation-only reading fed this back to the agent as a question.
PANE_DROPPED_MID_ANSWER = """\
● Shell cd ~/kiro-workspace/scratch/ai-ready-repo
  python3 -c "
  print('POST', p['ref'], '| votes:', p.get('votes'))
  "
  No new replies since I posted mine 20 minutes ago — still 4 comments (2 theirs, 2 mine), 10 votes. Let me check the
  broader board front page
● Agent connection closed unexpectedly
──────────────────────────────────────────────────────────────
cmux · auto · 13%                    ~/kiro-workspace/scratch/ai-ready-repo · (docs/gate3)
 ask a question or describe a task
"""

# A resumed session whose agent backend never answers. Different banner, same
# state: live composer in front of an agent that cannot take work. Real capture
# from 43be4af1 after a successful --resume-id.
PANE_AGENT_NOT_RESPONDING = """\
● Cancelled
──────────────────────────────────────────────────────────────
  ping
● Agent not responding. The backend may be misconfigured or unresponsive. Press Ctrl+C to cancel.
──────────────────────────────────────────────────────────────
cmux · auto · 8%                     ~/kiro-workspace/scratch/ai-ready-repo · (docs/gate3)
 ask a question or describe a task
"""

PANE_WORKING = """\
● Reading files
──────────────────────────────────────────────────────────────
cmux · auto · 9%                     ~/scratch · (main)
 Kiro is working · Type to steer
"""


# ── detection ─────────────────────────────────────────────────────────────────

class TestPaneLinkDropped:
    def test_banner_as_last_transcript_line_is_a_drop(self):
        assert pane_link_dropped(PANE_DROPPED_AFTER_PROMPT) is True

    def test_drop_mid_tool_use_also_counts(self):
        assert pane_link_dropped(PANE_DROPPED_MID_TOOL) is True

    def test_idle_session_is_not_a_drop(self):
        assert pane_link_dropped(PANE_IDLE) is False

    def test_agent_output_below_the_banner_clears_the_state(self):
        """Scrollback keeps the banner; only the last transcript line decides."""
        assert pane_link_dropped(PANE_RECOVERED) is False

    def test_empty_pane_is_not_a_drop(self):
        assert pane_link_dropped("") is False

    def test_agent_not_responding_counts_as_the_same_state(self):
        """Resuming does not clear it, so it has to be visible as a stall.

        Without this the session fell through to the missing-lock branch and
        reported Done — running, unusable, and hidden from the grid.
        """
        assert pane_link_dropped(PANE_AGENT_NOT_RESPONDING) is True
        assert pane_status(PANE_AGENT_NOT_RESPONDING) == "stalled"


class TestPaneStatus:
    def test_dropped_link_reports_stalled_not_idle(self):
        """Both panes show the same composer line — the banner has to win."""
        assert pane_status(PANE_DROPPED_AFTER_PROMPT) == "stalled"

    def test_idle_still_reports_idle(self):
        assert pane_status(PANE_IDLE) == "idle"

    def test_working_still_reports_thinking(self):
        assert pane_status(PANE_WORKING) == "thinking"


class TestUnsentPrompt:
    def test_prompt_above_the_banner_is_recovered(self):
        assert pane_unsent_prompt(PANE_DROPPED_AFTER_PROMPT) == (
            "open the draft for me, #3872 is popular what is about? "
            "spend your comments smart"
        )

    def test_tool_output_is_not_mistaken_for_a_prompt(self):
        assert pane_unsent_prompt(PANE_DROPPED_MID_TOOL) == ""

    def test_no_prompt_when_the_link_is_up(self):
        assert pane_unsent_prompt(PANE_IDLE) == ""

    def test_prompt_above_a_not_responding_banner_is_recovered(self):
        assert pane_unsent_prompt(PANE_AGENT_NOT_RESPONDING) == "ping"

    def test_agent_prose_is_not_carried_as_a_prompt(self):
        """A drop mid-answer leaves the agent's own words above the banner.

        They are rendered exactly like a user prompt apart from the missing
        turn rule, so the rule is the only safe discriminator.
        """
        assert pane_link_dropped(PANE_DROPPED_MID_ANSWER) is True
        assert pane_unsent_prompt(PANE_DROPPED_MID_ANSWER) == ""


# ── jsonl repair ──────────────────────────────────────────────────────────────

CLEAN_TURN = {"kind": "AssistantMessage", "data": {"content": [{"text": "All done."}]}}
DANGLING_TURN = {
    "kind": "AssistantMessage",
    "data": {"content": [{"kind": "toolUse", "data": {"toolUseId": "tu_1", "name": "glob"}}]},
}
ANSWERED_TURN = {
    "kind": "AssistantMessage",
    "data": {
        "content": [{"kind": "toolUse", "data": {"toolUseId": "tu_2"}}],
        "toolUseResults": [{"toolUseId": "tu_2", "output": "ok"}],
    },
}

SESSION_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def session_dir(monkeypatch):
    """A throwaway SESSIONS_DIR plus a writer for its jsonl."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d)
        monkeypatch.setattr(api_mod, "SESSIONS_DIR", path)

        def write(entries: list[dict]) -> str:
            (path / f"{SESSION_ID}.jsonl").write_text(
                "".join(json.dumps(entry) + "\n" for entry in entries)
            )
            return SESSION_ID

        yield path, write


class TestTrimDanglingToolUse:
    def test_clean_trailing_turn_is_left_alone(self, session_dir):
        """43be4af1 ended on plain text — trimming it would lose a good turn."""
        path, write = session_dir
        result = _trim_dangling_tool_use(write([CLEAN_TURN, CLEAN_TURN]))
        assert result["trimmed"] is False
        assert len((path / f"{SESSION_ID}.jsonl").read_text().strip().splitlines()) == 2

    def test_unanswered_tool_use_is_dropped_with_a_backup(self, session_dir):
        path, write = session_dir
        result = _trim_dangling_tool_use(write([CLEAN_TURN, DANGLING_TURN]))
        assert result["trimmed"] is True
        assert (path / f"{SESSION_ID}.jsonl.bak").exists()
        kept = (path / f"{SESSION_ID}.jsonl").read_text().strip().splitlines()
        assert len(kept) == 1
        assert json.loads(kept[0]) == CLEAN_TURN

    def test_answered_tool_use_is_not_a_dangle(self, session_dir):
        _, write = session_dir
        assert _trim_dangling_tool_use(write([ANSWERED_TURN]))["trimmed"] is False

    def test_half_written_trailing_line_is_dropped(self, session_dir):
        path, write = session_dir
        write([CLEAN_TURN])
        with (path / f"{SESSION_ID}.jsonl").open("a") as f:
            f.write('{"kind": "AssistantMessage", "data": {"cont')
        assert _trim_dangling_tool_use(SESSION_ID)["trimmed"] is True

    def test_missing_jsonl_is_reported_not_raised(self, session_dir):
        assert _trim_dangling_tool_use("does-not-exist")["trimmed"] is False


# ── carried-prompt delivery ───────────────────────────────────────────────────

class TestDeliverCarriedPrompt:
    """A resume replays the JSONL before the composer exists.

    Sending the carried prompt too early loses it silently, which is the exact
    failure the carry is meant to prevent — so the wait is the feature.
    """

    def test_waits_for_the_composer_before_sending(self, monkeypatch):
        panes = iter([PANE_WORKING, PANE_WORKING, PANE_IDLE])
        sent = []
        monkeypatch.setattr(api_mod.tmux, "capture", lambda sid, *a, **k: next(panes))
        monkeypatch.setattr(api_mod.tmux, "send_text",
                            lambda sid, text, **k: sent.append((sid, text)))
        monkeypatch.setattr(api_mod, "REANIMATE_POLL_SECONDS", 0.0)

        assert api_mod._deliver_carried_prompt("sid", "open the draft") is True
        assert sent == [("sid", "open the draft")]

    def test_never_sends_into_a_still_stalled_session(self, monkeypatch):
        """A respawn that stalls again must not be handed the prompt."""
        sent = []
        monkeypatch.setattr(api_mod.tmux, "capture",
                            lambda sid, *a, **k: PANE_DROPPED_AFTER_PROMPT)
        monkeypatch.setattr(api_mod.tmux, "send_text",
                            lambda sid, text, **k: sent.append(text))
        monkeypatch.setattr(api_mod, "REANIMATE_POLL_SECONDS", 0.0)

        assert api_mod._deliver_carried_prompt("sid", "open the draft", timeout=0.05) is False
        assert sent == []
