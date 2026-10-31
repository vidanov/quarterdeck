"""Side-chat keys and keeping side chats (incl. the ⌘J quick chat) out of the list."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend import side_chat


def test_send_key_refuses_keys_outside_the_toolbar():
    assert side_chat.send_key("x", "rm -rf /")["error"] == "unsupported key"
    assert side_chat.send_key("x", "y")["error"] == "unsupported key"


def test_known_ids_learns_live_side_chats_and_remembers_them(tmp_path, monkeypatch):
    monkeypatch.setattr(side_chat, "SIDE_DIR", tmp_path)
    monkeypatch.setattr(side_chat, "_IDS_FILE", tmp_path / "session-ids.json")
    monkeypatch.setattr(side_chat, "_correlated", {})
    monkeypatch.setattr(side_chat.tmux, "list_tmux_sessions",
                        lambda: ["deck-side-_quick", "kiro-abc"])
    monkeypatch.setattr(side_chat.tmux, "pane_pid", lambda name: 42)
    monkeypatch.setattr(side_chat.tmux, "_correlate", lambda pid, claimed: "sid-quick")

    assert side_chat.known_session_ids() == {"sid-quick"}
    assert json.loads((tmp_path / "session-ids.json").read_text()) == ["sid-quick"]

    # Gone from tmux, still hidden: a finished side chat must not reach the archive.
    monkeypatch.setattr(side_chat.tmux, "list_tmux_sessions", lambda: [])
    assert side_chat.known_session_ids() == {"sid-quick"}
