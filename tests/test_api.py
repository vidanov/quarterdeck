"""Tests for osa-kiro backend API."""
import json
import os
import threading
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

# Setup path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from backend import api, auth, config, concierge
from backend.api import app, SESSIONS_DIR

# TestClient reports a client host of "testclient", which the auth middleware
# treats as remote. Everything here exercises the local path, so say so.
client = TestClient(app, client=("127.0.0.1", 45678))


class TestSessionListing:
    def test_sessions_endpoint_returns_list(self):
        r = client.get("/api/sessions")
        assert r.status_code == 200
        data = r.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)

    def test_sessions_have_required_fields(self):
        r = client.get("/api/sessions")
        data = r.json()
        for s in data["sessions"][:3]:
            assert "id" in s
            assert "title" in s
            assert "status" in s
            assert "name" in s
            assert s["control"] in ("managed", "foreign", "archived", "starting")
            assert "cwd" in s

    def test_session_statuses_are_valid(self):
        r = client.get("/api/sessions")
        valid = {"thinking", "running", "idle", "awaiting-approval", "done", "error"}
        for s in r.json()["sessions"]:
            assert s["status"] in valid


class TestSessionDetail:
    def test_detail_returns_output(self):
        r = client.get("/api/sessions")
        sessions = r.json()["sessions"]
        if not sessions:
            pytest.skip("No sessions available")
        sid = sessions[0]["id"]
        r = client.get(f"/api/sessions/{sid}")
        assert r.status_code == 200
        data = r.json()
        assert "title" in data
        assert "output" in data
        assert "last_output" in data
        assert "status" in data

    def test_detail_title_longer_than_listing(self):
        r = client.get("/api/sessions")
        sessions = r.json()["sessions"]
        if not sessions:
            pytest.skip("No sessions available")
        sid = sessions[0]["id"]
        list_title = sessions[0]["title"]
        detail = client.get(f"/api/sessions/{sid}").json()
        # Detail title should be >= listing title length
        assert len(detail["title"]) >= len(list_title[:200])

    def test_detail_nonexistent_session(self):
        r = client.get("/api/sessions/nonexistent-id-12345")
        assert r.status_code == 200
        assert "error" in r.json()


class TestTranscript:
    SID = "11111111-2222-3333-4444-555555555555"

    @staticmethod
    def _entry(kind, text="", *, message_id="", tools=()):
        """Build a minimal JSONL entry. Pass tools=["tool_name"] for tool-use entries."""
        content = []
        if text:
            content.append({"kind": "text", "data": text})
        for tool_name in tools:
            content.append({"kind": "toolUse", "data": {"name": tool_name, "input": {}}})
        return {
            "kind": kind,
            "data": {
                "content": content,
                "message_id": message_id,
                "meta": {"timestamp": "2026-07-27T12:00:00Z"},
            },
        }

    def _write(self, root: Path, entries: list[dict]):
        (root / f"{self.SID}.json").write_text(json.dumps({
            "session_id": self.SID, "title": "Transcript test", "cwd": "/tmp",
        }))
        (root / f"{self.SID}.jsonl").write_text(
            "".join(json.dumps(entry) + "\n" for entry in entries)
        )

    def test_endpoint_returns_stable_line_sequences(self, tmp_path):
        self._write(tmp_path, [
            self._entry("Prompt", "first", message_id="p1"),
            self._entry("AssistantMessage", "answer", message_id="a1"),
        ])
        with patch.object(api, "SESSIONS_DIR", tmp_path):
            data = client.get(f"/api/sessions/{self.SID}/messages").json()
        assert [m["seq"] for m in data["messages"]] == [0, 1]
        assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
        assert data["messages"][0]["is_turn"] is True
        assert data["messages"][1]["message_id"] == "a1"

    def test_after_cursor_pages_forward_not_from_the_file_tail(self, tmp_path):
        self._write(tmp_path, [
            self._entry("Prompt", f"turn {i}") for i in range(6)
        ])
        with patch.object(api, "SESSIONS_DIR", tmp_path):
            data = api.read_transcript(self.SID, after=1, limit=2)
        assert [m["seq"] for m in data["messages"]] == [2, 3]
        assert data["more_before"] is False
        assert data["more_after"] is True

    def test_no_cursor_returns_the_latest_page(self, tmp_path):
        self._write(tmp_path, [
            self._entry("Prompt", f"turn {i}") for i in range(5)
        ])
        with patch.object(api, "SESSIONS_DIR", tmp_path):
            data = api.read_transcript(self.SID, limit=2)
        assert [m["seq"] for m in data["messages"]] == [3, 4]
        assert data["more_before"] is True
        assert data["more_after"] is False

    def test_last_message_cache_invalidates_when_the_file_grows(self, tmp_path):
        path = tmp_path / f"{self.SID}.jsonl"
        self._write(tmp_path, [self._entry("AssistantMessage", "old answer")])
        with patch.object(api, "SESSIONS_DIR", tmp_path):
            api._last_message_cache.clear()
            assert api.last_message(self.SID) == "old answer"
            with path.open("a") as f:
                f.write(json.dumps(
                    self._entry("AssistantMessage", "new answer")
                ) + "\n")
            assert api.last_message(self.SID) == "new answer"

    def test_entry_with_text_and_tools_returns_both(self, tmp_path):
        """An AssistantMessage that has prose AND tool calls must expose both.

        This is the regression test for the transcript truncation bug: the text
        field (the leading prose) was present in the JSONL but dropped on the
        way out because _block_text() was not extracting it alongside toolUse
        blocks. The fix is in _block_text / _block_tools; this test pins it.
        """
        entry = self._entry("AssistantMessage", "Let me check that for you.",
                            tools=["fs_read"])
        self._write(tmp_path, [entry])
        with patch.object(api, "SESSIONS_DIR", tmp_path):
            data = client.get(f"/api/sessions/{self.SID}/messages").json()
        msg = data["messages"][0]
        assert msg["text"] == "Let me check that for you.", (
            "prose preceding a tool call must be returned in the text field")
        assert any(t.get("name") == "fs_read" for t in msg["tools"]), (
            "tool names must be returned alongside the text")

    def test_entry_text_is_capped_at_MESSAGE_TEXT_MAX(self, tmp_path):
        """Text longer than MESSAGE_TEXT_MAX is truncated and flagged."""
        long_text = "x" * (api.MESSAGE_TEXT_MAX + 1)
        self._write(tmp_path, [self._entry("AssistantMessage", long_text)])
        with patch.object(api, "SESSIONS_DIR", tmp_path):
            data = client.get(f"/api/sessions/{self.SID}/messages").json()
        msg = data["messages"][0]
        assert len(msg["text"]) == api.MESSAGE_TEXT_MAX
        assert msg["truncated"] is True


class TestControlState:
    def test_every_session_has_a_control_state(self):
        r = client.get("/api/sessions")
        for s in r.json()["sessions"]:
            assert s["control"] in ("managed", "foreign", "archived", "starting", "crew")

    def test_archived_sessions_have_no_attach_command(self):
        r = client.get("/api/sessions")
        for s in r.json()["sessions"]:
            if s["control"] == "archived":
                assert s["attach"] == ""

    def test_managed_sessions_expose_attach_command(self):
        r = client.get("/api/sessions")
        for s in r.json()["sessions"]:
            if s["control"] == "managed":
                assert s["attach"].startswith("tmux attach -t kiro-")

    def test_detail_reports_control_state(self):
        sessions = client.get("/api/sessions").json()["sessions"]
        if not sessions:
            pytest.skip("No sessions available")
        d = client.get(f"/api/sessions/{sessions[0]['id']}").json()
        assert d["control"] in ("managed", "foreign", "archived", "starting")


class TestManagedEndpoint:
    def test_managed_reports_tmux_availability(self):
        data = client.get("/api/managed").json()
        assert isinstance(data["tmux_available"], bool)
        assert isinstance(data["sessions"], dict)
        assert isinstance(data["pending"], dict)


class TestInputGuards:
    """Input must be refused for anything we do not own the tmux session of."""

    def test_input_rejects_unmanaged_session(self):
        r = client.post("/api/sessions/nonexistent-id-12345/input",
                        json={"text": "hello"})
        assert "not managed" in r.json()["error"]

    def test_input_rejects_empty_text(self):
        r = client.post("/api/sessions/nonexistent-id-12345/input",
                        json={"text": "   "})
        assert "No text" in r.json()["error"]

    def test_send_is_an_alias_for_input(self):
        r = client.post("/api/sessions/nonexistent-id-12345/send",
                        json={"task": "hello"})
        assert "not managed" in r.json()["error"]

    def test_respond_rejects_unknown_choice(self):
        # Ownership is checked before the payload, so an unmanaged session is
        # refused for that reason first.
        r = client.post("/api/sessions/nonexistent-id-12345/respond",
                        json={"choice": "rm -rf /"})
        assert "not managed" in r.json()["error"]

    def test_respond_choice_map_covers_the_menu(self):
        from backend.api import PROMPT_CHOICES, RESPOND_KEYS
        assert PROMPT_CHOICES["allow"] == ["Enter"]
        assert PROMPT_CHOICES["trust"] == ["Down", "Enter"]
        assert PROMPT_CHOICES["deny"] == ["Down", "Down", "Enter"]
        for keys in PROMPT_CHOICES.values():
            assert all(k in RESPOND_KEYS for k in keys)


class TestPromptDetection:
    """Approval is read off the pane, never guessed from phrasing."""

    REAL_PROMPT = (
        "  run the shell command: echo hello\n"
        "↓ Shell echo hello\n"
        "────────\n"
        " shell requires approval\n"
        " ❯ Yes, single permission\n"
        "   Trust, always allow in this session\n"
        "   No (Tab to edit)\n"
        "────────\n"
        " esc to close · ↑↓ to navigate · ↵ to select · Tab to edit\n"
    )
    IDLE_PANE = (
        "  40\n────────\n"
        "cmux · claude-opus-4.6 · High · ◔ 4%      /private/tmp/x\n"
        " ask a question or describe a task ↵\n"
        "                        /copy to clipboard\n"
    )

    def test_real_prompt_detected(self):
        from backend.api import pane_awaiting_approval
        assert pane_awaiting_approval(self.REAL_PROMPT)

    def test_idle_pane_not_a_prompt(self):
        from backend.api import pane_awaiting_approval
        assert not pane_awaiting_approval(self.IDLE_PANE)

    def test_empty_pane_not_a_prompt(self):
        from backend.api import pane_awaiting_approval
        assert not pane_awaiting_approval("")

    def test_agent_question_is_not_an_approval_request(self):
        # The old heuristic flagged anything ending in "?" — this is the
        # regression that caused false "Awaiting" badges.
        from backend.api import pane_awaiting_approval
        pane = self.IDLE_PANE.replace("  40", "  What's on your mind today?")
        assert not pane_awaiting_approval(pane)

    # Verbatim from a finished session, tip line and all. kiro-cli prints this
    # above the composer, so "Kiro is working" appears on screen while nothing
    # is working — which is exactly what a substring search cannot tell apart.
    IDLE_PANE_WITH_TIP = (
        "  Tip: Type while Kiro is working to steer it mid-turn\n"
        "  (Ctrl+S switches to queue mode); Ctrl+X opens the tray to\n"
        "  edit a queued message.\n"
        "────────\n"
        "  Reply with the single word: ok\n"
        "\n"
        "  ok\n"
        "────────\n"
        "cmux · claude-sonnet-4.6 · High · ◔ 5%\n"
        "~/Documents/PROJECTS/PERSONAL/osa-kiro · (pty-api)\n"
        "\n"
        " ask a question or describe a task ↵\n"
        "                        /copy to clipboard\n"
    )
    WORKING_PANE = (
        "  reading backend/api.py\n"
        "────────\n"
        "cmux · claude-sonnet-4.6 · High · ◔ 5%\n"
        " Kiro is working · Type to steer · Ctrl+S to queue\n"
    )

    def test_a_tip_mentioning_working_does_not_make_a_finished_session_thinking(self):
        from backend.api import pane_status
        assert pane_status(self.IDLE_PANE_WITH_TIP) == "idle"

    def test_a_working_footer_is_still_read_as_working(self):
        from backend.api import pane_status
        assert pane_status(self.WORKING_PANE) == "thinking"

    def test_the_conversation_above_the_footer_is_not_status(self):
        # An agent that quotes the footer, or a transcript scrolled to the wrong
        # place, must not decide what the session is doing.
        from backend.api import pane_status
        pane = "  I ran `Kiro is working` in the shell\n" + self.IDLE_PANE
        assert pane_status(pane) == "idle"

    def test_status_without_pane_is_never_awaiting(self):
        from backend.api import detect_status
        status = detect_status("nonexistent-id-12345", {"pid": os.getpid()})
        assert status != "awaiting-approval"

    def test_respond_rejects_unmanaged_session(self):
        r = client.post("/api/sessions/nonexistent-id-12345/respond",
                        json={"choice": "y"})
        assert "not managed" in r.json()["error"]

    def test_pane_is_empty_for_unmanaged_session(self):
        data = client.get("/api/sessions/nonexistent-id-12345/pane").json()
        assert data["managed"] is False
        assert data["pane"] == ""


class TestTakeoverGuards:
    def test_takeover_nonexistent_session(self):
        r = client.post("/api/sessions/nonexistent-id-12345/takeover")
        assert "error" in r.json()

    def test_resume_nonexistent_session(self):
        r = client.post("/api/sessions/nonexistent-id-12345/resume")
        assert "error" in r.json()

    def test_resume_refuses_a_running_session(self):
        sessions = client.get("/api/sessions").json()["sessions"]
        running = [s for s in sessions if s["control"] in ("managed", "foreign")]
        if not running:
            pytest.skip("No running sessions")
        r = client.post(f"/api/sessions/{running[0]['id']}/resume")
        assert "already running" in r.json().get("error", "")


class TestDispatchGuards:
    def test_dispatch_rejects_empty_task(self):
        r = client.post("/api/dispatch", json={"task": "  "})
        assert "error" in r.json()

    def test_dispatch_rejects_missing_directory(self):
        r = client.post("/api/dispatch",
                        json={"task": "hi", "cwd": "/no/such/dir/anywhere"})
        assert "not found" in r.json()["error"]


class TestCleanTitle:
    def test_normal_title_unchanged(self):
        from backend.api import clean_title
        result = clean_title("fix the bug in auth module", "nonexistent-id")
        assert result == "fix the bug in auth module"

    def test_objective_title_extracts_goal(self):
        from backend.api import clean_title
        # This requires a real JSONL file, so skip if not present
        import backend.api as api
        sessions_dir = api.SESSIONS_DIR
        # Find a goal session
        for jf in sessions_dir.glob("*.jsonl"):
            first_line = jf.open().readline()
            try:
                entry = json.loads(first_line)
                text = entry.get("data", {}).get("content", [{}])[0].get("data", "")
                if "goal for you to achieve" in text:
                    result = clean_title("## Objective (iteration 1 of 5)", jf.stem)
                    assert not result.startswith("## Objective")
                    assert len(result) > 10
                    return
            except:
                continue
        pytest.skip("No goal sessions found")


class TestOpenFolder:
    @patch("subprocess.run")
    def test_open_folder_expands_tilde(self, mock_run):
        home = str(Path.home())
        r = client.post("/api/open-folder", json={"path": "~/Documents"})
        if Path(f"{home}/Documents").is_dir():
            assert r.json().get("ok") is True
            mock_run.assert_called_once()

    def test_open_folder_rejects_empty(self):
        r = client.post("/api/open-folder", json={"path": ""})
        assert "error" in r.json()


class TestKill:
    def test_kill_nonexistent_session(self):
        r = client.post("/api/sessions/nonexistent-id-12345/kill")
        assert "error" in r.json()


class TestSafeDeletion:
    @staticmethod
    def _write_session(sessions_dir: Path, session_id: str, cwd: Path):
        (sessions_dir / f"{session_id}.json").write_text(
            json.dumps({"cwd": str(cwd), "title": session_id})
        )
        (sessions_dir / f"{session_id}.jsonl").write_text(
            json.dumps({"kind": "Prompt"}) + "\n"
        )

    def test_running_session_is_refused_and_left_intact(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        self._write_session(sessions_dir, "live", tmp_path / "project")
        (sessions_dir / "live.lock").write_text(json.dumps({"pid": os.getpid()}))
        favourites = tmp_path / "favourites.json"
        favourites.write_text(json.dumps([{"id": "live"}]))

        with patch.object(api, "SESSIONS_DIR", sessions_dir), \
             patch.object(api, "FAVOURITES_FILE", favourites):
            result = api.delete_session("live")

        assert result["code"] == "session_active"
        assert (sessions_dir / "live.json").exists()
        assert (sessions_dir / "live.jsonl").exists()
        assert (sessions_dir / "live.lock").exists()
        assert json.loads(favourites.read_text()) == [{"id": "live"}]

    def test_archived_session_files_and_favourite_are_deleted(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        self._write_session(sessions_dir, "old", tmp_path / "project")
        (sessions_dir / "old.history").write_text("history")

        removed = []
        def capture_remove(ids): removed.extend(ids)

        with patch.object(api, "SESSIONS_DIR", sessions_dir), \
             patch.object(api, "_remove_favourites", side_effect=capture_remove):
            result = api.delete_session("old")

        assert result["ok"] is True
        assert set(result["deleted"]) == {".json", ".jsonl", ".history"}
        assert not list(sessions_dir.glob("old.*"))
        # Favourites cleanup is now delegated to _remove_favourites (collections-backed)
        assert "old" in removed

    def test_project_delete_uses_raw_paths_and_path_boundaries(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        project = tmp_path / "project"
        nested = project / "packages" / "web"
        similarly_named = tmp_path / "project-extra"
        for path in (project, nested, similarly_named):
            path.mkdir(parents=True, exist_ok=True)

        self._write_session(sessions_dir, "root", project)
        self._write_session(sessions_dir, "nested", nested)
        self._write_session(sessions_dir, "keep", similarly_named)

        removed = []
        def capture_remove(ids): removed.extend(ids)

        with patch.object(api, "SESSIONS_DIR", sessions_dir), \
             patch.object(api, "_remove_favourites", side_effect=capture_remove), \
             patch.object(api, "list_sessions",
                          side_effect=AssertionError("display listing must not be used")):
            result = api.delete_project_sessions({"cwd": str(project)})

        assert result["ok"] is True
        assert result["deleted_sessions"] == 2
        assert set(result["session_ids"]) == {"root", "nested"}
        assert not (sessions_dir / "root.json").exists()
        assert not (sessions_dir / "nested.json").exists()
        assert (sessions_dir / "keep.json").exists()
        # Favourites cleanup is now delegated to _remove_favourites (collections-backed)
        assert set(removed) == {"root", "nested"}

    def test_project_delete_is_all_or_nothing_when_one_session_is_active(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        self._write_session(sessions_dir, "archived", project)
        self._write_session(sessions_dir, "live", project / "nested")
        (sessions_dir / "live.lock").write_text(json.dumps({"pid": os.getpid()}))

        with patch.object(api, "SESSIONS_DIR", sessions_dir), \
             patch.object(api, "FAVOURITES_FILE", tmp_path / "favourites.json"):
            result = api.delete_project_sessions({"cwd": str(project)})

        assert result["code"] == "project_has_active_sessions"
        assert result["active_sessions"] == ["live"]
        assert (sessions_dir / "archived.json").exists()
        assert (sessions_dir / "live.json").exists()


class TestAgentSelection:
    """Agents are the user's own files, so they are discovered, not hardcoded."""

    @staticmethod
    def _write_agent(directory: Path, name: str, **extra):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.json").write_text(json.dumps({"name": name, **extra}))

    def test_lists_global_agents_and_flags_the_default(self, tmp_path):
        agents_dir = tmp_path / "agents"
        self._write_agent(agents_dir, "aws-agent", description="AWS things")
        self._write_agent(agents_dir, "hooky", hooks={"agentSpawn": [{"command": "true"}]})
        settings = tmp_path / "cli.json"
        settings.write_text(json.dumps({"chat.defaultAgent": "hooky"}))

        with patch.object(api, "AGENTS_DIR", agents_dir), \
             patch.object(api, "KIRO_CLI_SETTINGS", settings):
            agents = api.list_agents()

        by_name = {a["name"]: a for a in agents}
        assert by_name["aws-agent"]["description"] == "AWS things"
        assert by_name["aws-agent"]["has_hooks"] is False
        assert by_name["hooky"]["has_hooks"] is True
        assert by_name["hooky"]["is_default"] is True
        assert by_name["aws-agent"]["is_default"] is False

    def test_workspace_agent_shadows_a_global_one_of_the_same_name(self, tmp_path):
        agents_dir = tmp_path / "global"
        self._write_agent(agents_dir, "shared", description="global version")
        workspace = tmp_path / "project"
        self._write_agent(workspace / ".kiro" / "agents", "shared",
                          description="workspace version")

        with patch.object(api, "AGENTS_DIR", agents_dir), \
             patch.object(api, "KIRO_CLI_SETTINGS", tmp_path / "missing.json"):
            agents = api.list_agents(str(workspace))

        shared = next(a for a in agents if a["name"] == "shared")
        assert shared["source"] == "workspace"
        assert shared["description"] == "workspace version"

    def test_builtins_are_offered_but_cannot_carry_hooks(self, tmp_path):
        with patch.object(api, "AGENTS_DIR", tmp_path / "none"), \
             patch.object(api, "KIRO_CLI_SETTINGS", tmp_path / "missing.json"):
            agents = api.list_agents()

        builtins = [a for a in agents if a["source"] == "builtin"]
        assert {a["name"] for a in builtins} == set(api.BUILTIN_AGENTS)
        assert all(a["hooks_possible"] is False for a in builtins)

    def test_a_malformed_agent_config_is_skipped_not_fatal(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "broken.json").write_text("{not json")
        self._write_agent(agents_dir, "fine")

        with patch.object(api, "AGENTS_DIR", agents_dir), \
             patch.object(api, "KIRO_CLI_SETTINGS", tmp_path / "missing.json"):
            names = {a["name"] for a in api.list_agents()}

        assert "fine" in names
        assert "broken" not in names

    @pytest.mark.parametrize("bad", [
        "../escape", "a b", "-rf", "--trust-all-tools", ".hidden",
        "x" * 65, "semi;colon", "$(whoami)", "quote'd",
    ])
    def test_spawn_refuses_an_agent_name_that_is_not_a_plain_name(self, bad):
        # The name becomes an argv entry inside a detached pane, where a failure
        # would be invisible — and a leading dash would be read as flags.
        # tmux is stubbed: a name that slipped through would otherwise start a
        # real session and hang until the correlation timeout.
        with patch.object(api.tmux, "_tmux",
                          side_effect=AssertionError("must be rejected before tmux runs")):
            result = api.tmux.spawn("/tmp", agent=bad)
        assert result["ok"] is False
        assert "agent" in result["error"].lower()

    @pytest.mark.parametrize("good", ["cmux", "aws-agent", "kiro_default", "a.b_c-1"])
    def test_accepts_the_name_shapes_kiro_cli_itself_uses(self, good):
        assert api.tmux._agent_name_ok(good) is True

    def test_resume_inherits_the_agent_the_session_started_with(self):
        # kiro-cli does not record the agent, so Quarterdeck's own record is the only
        # source — without this a resumed session silently changes agent.
        with patch.object(api.tmux, "managed_sessions",
                          return_value={"s1": {"agent": "frontend"}}):
            assert api._spawn_kwargs(None, "s1")["agent"] == "frontend"
            assert api._spawn_kwargs({"agent": "editor"}, "s1")["agent"] == "editor"
            assert "agent" not in api._spawn_kwargs(None, "unknown-session")


class TestSpawnHook:
    """kiro-cli reporting its own session id, instead of Quarterdeck inferring it."""

    def test_hook_drop_is_only_trusted_once_the_session_exists_on_disk(self, tmp_path):
        spawns, sessions = tmp_path / "spawns", tmp_path / "sessions"
        spawns.mkdir(), sessions.mkdir()
        nonce, sid = "abc123def456", "11111111-2222-3333-4444-555555555555"
        (spawns / nonce).write_text(sid)

        with patch.object(api.tmux, "SPAWNS_DIR", spawns), \
             patch.object(api.tmux, "SESSIONS_DIR", sessions):
            # A --no-interactive run reports an id that never persists, so an id
            # without files behind it is not usable.
            assert api.tmux.hook_reported_session(nonce) == ""
            (sessions / f"{sid}.json").write_text("{}")
            assert api.tmux.hook_reported_session(nonce) == sid

    @pytest.mark.parametrize("junk", ["", "not a uuid", "../../etc/passwd", "x" * 100])
    def test_a_junk_hook_drop_is_ignored(self, tmp_path, junk):
        spawns, sessions = tmp_path / "spawns", tmp_path / "sessions"
        spawns.mkdir(), sessions.mkdir()
        (spawns / "abc123def456").write_text(junk)
        with patch.object(api.tmux, "SPAWNS_DIR", spawns), \
             patch.object(api.tmux, "SESSIONS_DIR", sessions):
            assert api.tmux.hook_reported_session("abc123def456") == ""

    @pytest.mark.parametrize("nonce", ["../escape", "not-hex", "", "a/b"])
    def test_a_nonce_that_is_not_ours_never_becomes_a_path(self, tmp_path, nonce):
        with patch.object(api.tmux, "SPAWNS_DIR", tmp_path):
            assert api.tmux.hook_reported_session(nonce) == ""

    def test_sweep_removes_only_stale_drops(self, tmp_path):
        spawns = tmp_path / "spawns"
        spawns.mkdir()
        (spawns / "aaaaaaaaaaaa").write_text("old")
        (spawns / "bbbbbbbbbbbb").write_text("fresh")
        os.utime(spawns / "aaaaaaaaaaaa", (0, 0))
        with patch.object(api.tmux, "SPAWNS_DIR", spawns):
            assert api.tmux.sweep_hook_reports(ttl=60) == 1
        assert not (spawns / "aaaaaaaaaaaa").exists()
        assert (spawns / "bbbbbbbbbbbb").exists()

    def test_install_adds_one_entry_and_keeps_the_hooks_already_there(self, tmp_path):
        # The default agent on a real machine belongs to another tool and is a
        # pure hook bridge; replacing its array would break it.
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        theirs = {"command": "their-bridge session-start"}
        (agents_dir / "cmux.json").write_text(json.dumps(
            {"name": "cmux", "hooks": {"agentSpawn": [theirs], "stop": [theirs]}}))

        with patch.object(api, "AGENTS_DIR", agents_dir), \
             patch.object(api, "KIRO_CLI_SETTINGS", tmp_path / "none.json"):
            first = api.hooks_install({})
            second = api.hooks_install({})  # idempotent

        assert first["results"]["cmux"] == "installed"
        assert second["results"]["cmux"] == "already-present"
        config = json.loads((agents_dir / "cmux.json").read_text())
        # Quarterdeck installs into agentSpawn and stop; both must be additive, and the
        # other tool's entry must still be there in each.
        for event in ("agentSpawn", "stop"):
            entries = config["hooks"][event]
            assert theirs in entries, f"their {event} hook must survive"
            assert len(entries) == 2, f"{event} gained exactly one entry"
        assert (agents_dir / "cmux.json.deck-backup").exists()

    def test_an_event_deck_does_not_touch_is_left_alone(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        theirs = {"command": "their-bridge"}
        (agents_dir / "cmux.json").write_text(json.dumps(
            {"name": "cmux", "hooks": {"preToolUse": [theirs], "postToolUse": [theirs]}}))

        with patch.object(api, "AGENTS_DIR", agents_dir), \
             patch.object(api, "KIRO_CLI_SETTINGS", tmp_path / "none.json"):
            api.hooks_install({})
            api.hooks_uninstall({})

        config = json.loads((agents_dir / "cmux.json").read_text())
        assert config["hooks"]["preToolUse"] == [theirs]
        assert config["hooks"]["postToolUse"] == [theirs]

    def test_uninstall_removes_ours_and_leaves_theirs(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        theirs = {"command": "their-bridge session-start"}
        (agents_dir / "cmux.json").write_text(json.dumps(
            {"name": "cmux", "hooks": {"agentSpawn": [theirs]}}))

        with patch.object(api, "AGENTS_DIR", agents_dir), \
             patch.object(api, "KIRO_CLI_SETTINGS", tmp_path / "none.json"):
            api.hooks_install({})
            api.hooks_uninstall({})

        config = json.loads((agents_dir / "cmux.json").read_text())
        assert config["hooks"]["agentSpawn"] == [theirs]

    def test_uninstall_leaves_no_empty_scaffolding_in_someone_elses_file(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "plain.json").write_text(json.dumps({"name": "plain"}))

        with patch.object(api, "AGENTS_DIR", agents_dir), \
             patch.object(api, "KIRO_CLI_SETTINGS", tmp_path / "none.json"):
            api.hooks_install({})
            api.hooks_uninstall({})

        assert json.loads((agents_dir / "plain.json").read_text()) == {"name": "plain"}

    def test_a_turn_mark_settles_idle_without_the_ten_second_guess(self, tmp_path):
        # A session with no pane used to be called "thinking" for ten seconds
        # after any file write. A stop hook says the turn is over outright.
        sessions, turns = tmp_path / "sessions", tmp_path / "turns"
        sessions.mkdir(), turns.mkdir()
        sid = "11111111-2222-3333-4444-555555555555"
        (sessions / f"{sid}.jsonl").write_text(json.dumps({"kind": "ToolResults"}) + "\n")
        lock = {"pid": os.getpid()}

        with patch.object(api, "SESSIONS_DIR", sessions), \
             patch.object(api.tmux, "TURNS_DIR", turns):
            # Mid-turn: fresh files, last entry is a tool result.
            assert api.detect_status(sid, lock) == "thinking"
            # The hook fires after those writes.
            (turns / sid).touch()
            assert api.detect_status(sid, lock) == "idle"
            # A later write means a new turn started; the stale mark must not
            # keep claiming the session is finished.
            time.sleep(0.01)
            (sessions / f"{sid}.jsonl").write_text(json.dumps({"kind": "Prompt"}) + "\n")
            assert api.detect_status(sid, lock) == "thinking"

    def test_turn_marks_are_swept_when_stale(self, tmp_path):
        turns = tmp_path / "turns"
        turns.mkdir()
        (turns / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee").touch()
        (turns / "ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee").touch()
        os.utime(turns / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", (0, 0))
        with patch.object(api.tmux, "TURNS_DIR", turns):
            assert api.tmux.sweep_turn_marks(ttl=60) == 1


class TestApprovalGating:
    """preToolUse holds tool calls — but only for sessions that opted in.

    The hook is installed into every agent config, so the only thing standing
    between an ordinary session and a blocked tool call is the gate file. These
    tests run the real hook command in a shell rather than trusting the string.
    """

    SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def _run_hook(self, home, nonce=""):
        env = {**os.environ, "HOME": str(home), "KIRO_SESSION_ID": self.SID}
        if nonce:
            env["DECK_NONCE"] = nonce
        return subprocess.Popen(
            ["sh", "-c", api.tmux.PRETOOL_HOOK_COMMAND],
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)

    def test_an_ungated_session_is_not_held(self, tmp_path):
        proc = self._run_hook(tmp_path)
        out, _ = proc.communicate('{"tool_name":"execute_bash"}', timeout=10)
        assert proc.returncode == 0, "an ungated tool call must run"
        assert out.strip() == "{}"
        assert not (tmp_path / ".osa-kiro" / "approvals").exists(), \
            "no request should be raised for a session nobody gated"

    @pytest.mark.parametrize("allow,expected_code", [(True, 0), (False, 2)])
    def test_a_gated_call_waits_for_the_answer_it_is_given(self, tmp_path, allow,
                                                           expected_code):
        gates = tmp_path / ".osa-kiro" / "gates"
        gates.mkdir(parents=True)
        (gates / self.SID).touch()
        approvals = tmp_path / ".osa-kiro" / "approvals"

        proc = self._run_hook(tmp_path)
        proc.stdin.write('{"tool_name":"execute_bash","tool_input":{"command":"ls"}}')
        proc.stdin.close()

        with patch.object(api.tmux, "APPROVALS_DIR", approvals):
            deadline = time.time() + 10
            pending = []
            while time.time() < deadline and not pending:
                pending = api.tmux.pending_approvals()
                if not pending:
                    time.sleep(0.1)
            assert pending, "the hook must raise a request Quarterdeck can see"
            assert pending[0]["session_id"] == self.SID
            assert pending[0]["tool_name"] == "execute_bash"
            assert pending[0]["tool_input"] == {"command": "ls"}
            api.tmux.respond_approval(self.SID, pending[0]["request_id"], allow=allow)

        proc.wait(timeout=10)
        # 2 is what kiro-cli reads as "do not run this tool".
        assert proc.returncode == expected_code

    def test_a_spawn_is_gated_by_nonce_before_its_id_is_known(self, tmp_path):
        gates = tmp_path / ".osa-kiro" / "gates"
        gates.mkdir(parents=True)
        (gates / "n-abc123def456").touch()
        proc = self._run_hook(tmp_path, nonce="abc123def456")
        proc.stdin.write('{"tool_name":"fs_write"}')
        proc.stdin.close()
        approvals = tmp_path / ".osa-kiro" / "approvals"
        deadline = time.time() + 10
        while time.time() < deadline and not list(approvals.glob("*")):
            time.sleep(0.1)
        assert list(approvals.glob("*")), "a nonce gate must hold the call too"
        proc.kill()
        proc.wait(timeout=5)

    def test_the_gate_moves_from_the_nonce_to_the_id_once_correlated(self, tmp_path):
        with patch.object(api.tmux, "GATES_DIR", tmp_path):
            api.tmux.set_pending_gate("abc123def456", True)
            api.tmux.adopt_pending_gate("abc123def456", self.SID)
            assert api.tmux.gate_enabled(self.SID)
            # The session keeps that nonce in its environment for as long as it
            # runs, so leaving the file would re-gate a session after the user
            # switched gating off.
            assert not (tmp_path / "n-abc123def456").exists()

    def test_only_uncorrelated_nonce_gates_expire(self, tmp_path):
        with patch.object(api.tmux, "GATES_DIR", tmp_path):
            api.tmux.set_gate(self.SID, True)
            api.tmux.set_pending_gate("abc123def456", True)
            os.utime(tmp_path / "n-abc123def456", (0, 0))
            os.utime(tmp_path / self.SID, (0, 0))
            assert api.tmux.sweep_gates(ttl=60) == 1
            assert api.tmux.gate_enabled(self.SID), \
                "a gate on a real session is a decision, not a leftover"

    def test_the_endpoint_reports_and_flips_the_gate(self, tmp_path):
        with patch.object(api.tmux, "GATES_DIR", tmp_path), \
             patch.object(api, "agent_has_pretool_hook", return_value=True):
            assert client.get(f"/api/sessions/{self.SID}/gate").json()["enabled"] is False
            r = client.post(f"/api/sessions/{self.SID}/gate", json={"enabled": True})
            assert r.json() == {"ok": True, "enabled": True, "hooked": True}
            assert client.get(f"/api/sessions/{self.SID}/gate").json()["enabled"] is True
            client.post(f"/api/sessions/{self.SID}/gate", json={"enabled": False})
            assert client.get(f"/api/sessions/{self.SID}/gate").json()["enabled"] is False

    def test_a_gate_on_an_agent_without_the_hook_says_so(self, tmp_path):
        # A switch wired to nothing is worse than no switch: the UI would imply
        # the session is being held when it is running freely.
        with patch.object(api.tmux, "GATES_DIR", tmp_path), \
             patch.object(api, "agent_has_pretool_hook", return_value=False):
            r = client.post(f"/api/sessions/{self.SID}/gate", json={"enabled": True})
            assert r.json()["hooked"] is False

    def test_switching_gating_off_releases_what_is_already_held(self, tmp_path):
        # Otherwise the held calls sit out the full timeout and are then denied,
        # which reads as the toggle having broken the session.
        held = [{"session_id": self.SID, "request_id": "r1", "tool_name": "x",
                 "tool_input": {}, "age": 1.0},
                {"session_id": "other", "request_id": "r2", "tool_name": "x",
                 "tool_input": {}, "age": 1.0}]
        with patch.object(api.tmux, "GATES_DIR", tmp_path), \
             patch.object(api.tmux, "pending_approvals", return_value=held), \
             patch.object(api.tmux, "respond_approval") as respond:
            api.tmux.set_gate(self.SID, True)
            client.post(f"/api/sessions/{self.SID}/gate", json={"enabled": False})
        respond.assert_called_once_with(self.SID, "r1", allow=True)

    @pytest.mark.parametrize("bad", ["../escape", "", "a/b", "x" * 100])
    def test_a_gate_is_refused_for_anything_that_is_not_a_session_id(self, tmp_path, bad):
        with patch.object(api.tmux, "GATES_DIR", tmp_path):
            assert api.tmux.set_gate(bad, True) is False
            assert list(tmp_path.iterdir()) == []

    def test_status_says_which_hook_is_missing_not_only_that_one_is(self, tmp_path):
        # An agent counts as installed only when it carries all three, so adding
        # a hook flips every existing install to "not installed". Without a
        # per-hook breakdown that reads as everything having been lost — and the
        # gate switch is dead until preToolUse specifically is there.
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "cmux.json").write_text(json.dumps({
            "name": "cmux",
            "hooks": {"agentSpawn": [{"command": "x", "deck": api.tmux.HOOK_MARKER}]},
        }))
        with patch.object(api, "AGENTS_DIR", agents_dir), \
             patch.object(api, "KIRO_CLI_SETTINGS", tmp_path / "none.json"):
            status = api.hooks_status()
        by_event = {h["event"]: h for h in status["hooks"]}
        assert status["installed"] == [], "a partial install is not an install"
        assert by_event["agentSpawn"]["installed"] == ["cmux"]
        assert by_event["preToolUse"]["installed"] == []
        assert "gated" in by_event["preToolUse"]["purpose"]

    def test_answering_retires_the_request_at_once(self, tmp_path):
        # The hook polls, so it can be a moment behind in cleaning up its own
        # file — and while that file is there `pending_approvals()` keeps
        # reporting a call that has already been answered. The UI removes the row
        # on click and the next poll put it straight back, so answering looked
        # like it had not worked. Quarterdeck retires the request itself.
        approvals = tmp_path / "approvals"
        approvals.mkdir()
        (approvals / f"{self.SID}-r1").write_text(f"{self.SID}:r1:execute_bash:{{}}")
        with patch.object(api.tmux, "APPROVALS_DIR", approvals):
            assert len(api.tmux.pending_approvals()) == 1
            assert api.tmux.respond_approval(self.SID, "r1", allow=True) is True
            assert api.tmux.pending_approvals() == [], \
                "an answered call must stop being reported immediately"
            # The signal the hook is waiting on has to outlive the request file,
            # or the hook waits out the full timeout and denies.
            assert (approvals / f".{self.SID}-r1").exists()

    def test_a_hook_still_gets_its_answer_after_the_request_is_retired(self, tmp_path):
        # The real shell hook, to prove retiring the request file out from under
        # it does not strand it: it waits on the signal, not on its own file.
        gates = tmp_path / ".osa-kiro" / "gates"
        gates.mkdir(parents=True)
        (gates / self.SID).touch()
        approvals = tmp_path / ".osa-kiro" / "approvals"

        proc = self._run_hook(tmp_path)
        proc.stdin.write('{"tool_name":"execute_bash","tool_input":{"command":"ls"}}')
        proc.stdin.close()
        with patch.object(api.tmux, "APPROVALS_DIR", approvals):
            deadline = time.time() + 10
            pending = []
            while time.time() < deadline and not pending:
                pending = api.tmux.pending_approvals()
                if not pending:
                    time.sleep(0.1)
            assert pending
            api.tmux.respond_approval(self.SID, pending[0]["request_id"], allow=True)
        proc.wait(timeout=10)
        assert proc.returncode == 0

    def test_a_hook_running_last_versions_command_is_not_installed(self, tmp_path):
        # The dangerous shape: the entry carries Quarterdeck's marker, so matching on the
        # marker alone reports it installed while the agent runs the old command.
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "cmux.json").write_text(json.dumps({
            "name": "cmux",
            "hooks": {
                event: [{"command": "echo stale", "deck": marker,
                         "timeout_ms": timeout}]
                for event, _, marker, timeout in api.DECK_HOOKS
            },
        }))
        with patch.object(api, "AGENTS_DIR", agents_dir), \
             patch.object(api, "KIRO_CLI_SETTINGS", tmp_path / "none.json"):
            status = api.hooks_status()
            assert status["installed"] == [], "a stale command is not installed"
            assert status["stale"] == ["cmux"], "and it has to be named to be fixed"
            # Install is what fixes it, and it patches in place rather than
            # appending a second entry beside the old one.
            api.hooks_install({})
            assert api.hooks_status()["stale"] == []
        entries = json.loads((agents_dir / "cmux.json").read_text())["hooks"]["preToolUse"]
        assert len(entries) == 1
        assert entries[0]["command"] == api.tmux.PRETOOL_HOOK_COMMAND

    def test_the_approval_hook_is_installed_with_a_timeout_it_can_survive(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "cmux.json").write_text(json.dumps({"name": "cmux"}))
        with patch.object(api, "AGENTS_DIR", agents_dir), \
             patch.object(api, "KIRO_CLI_SETTINGS", tmp_path / "none.json"):
            api.hooks_install({})
        entries = json.loads((agents_dir / "cmux.json").read_text())["hooks"]["preToolUse"]
        assert len(entries) == 1
        # 5s — the timeout the other two hooks carry — would kill this one long
        # before anyone reached their phone.
        assert entries[0]["timeout_ms"] == api.tmux.PRETOOL_HOOK_TIMEOUT_MS
        assert entries[0]["timeout_ms"] > api.tmux.APPROVAL_TIMEOUT * 1000


class TestQRExchangeCode:
    """The QR must not carry the token itself — see auth.mint_exchange_code."""

    def test_a_code_is_good_once(self):
        code = auth.mint_exchange_code()
        assert auth.redeem_exchange_code(code) is True
        assert auth.redeem_exchange_code(code) is False

    def test_an_expired_code_is_refused(self, tmp_path):
        codes = tmp_path / "codes"
        with patch.object(auth, "CODES_DIR", codes):
            code = auth.mint_exchange_code()
            old = time.time() - auth.CODE_TTL - 1
            os.utime(codes / code, (old, old))
            assert auth.redeem_exchange_code(code) is False

    def test_nonsense_is_refused(self):
        assert auth.redeem_exchange_code("") is False
        assert auth.redeem_exchange_code("not-a-code") is False

    def test_a_code_shaped_like_a_path_never_reaches_the_filesystem(self, tmp_path):
        # The code becomes a filename, and it arrives in a query string.
        with patch.object(auth, "CODES_DIR", tmp_path / "codes"):
            for hostile in ("../token", "..", "a/b" * 6, "." * 20):
                assert auth.redeem_exchange_code(hostile) is False

    def test_minting_sweeps_codes_nobody_redeemed(self, tmp_path):
        codes = tmp_path / "codes"
        codes.mkdir()
        stale = codes / ("s" * 22)
        stale.touch()
        old = time.time() - auth.CODE_TTL - 1
        os.utime(stale, (old, old))
        with patch.object(auth, "CODES_DIR", codes):
            auth.mint_exchange_code()
        assert not stale.exists()

    def test_a_code_minted_here_is_redeemable_by_the_remote_listener(self, tmp_path):
        """The bug this shape exists to fix, tested across a real process boundary.

        The QR is rendered by the desktop app's backend; the phone talks to the
        separate uvicorn on the Tailscale address. Held in memory, the code was
        minted in one process and looked for in another, so the scan always
        landed on the token form — and every single-process test passed anyway.
        """
        home = tmp_path / "home"
        home.mkdir()
        with patch.object(auth, "CODES_DIR", home / ".osa-kiro" / "codes"):
            code = auth.mint_exchange_code()

        # A second process, given the same HOME, therefore the same state dir.
        redeem = (
            "import sys; sys.path.insert(0, %r);"
            "from backend import auth;"
            "print(auth.redeem_exchange_code(%r))"
            % (str(Path(__file__).parent.parent), code)
        )
        env = {**os.environ, "HOME": str(home)}
        first = subprocess.run([sys.executable, "-c", redeem], capture_output=True,
                               text=True, env=env, timeout=60)
        assert first.stdout.strip() == "True", first.stderr
        # And burning it there burns it everywhere — one scan, one login.
        second = subprocess.run([sys.executable, "-c", redeem], capture_output=True,
                                text=True, env=env, timeout=60)
        assert second.stdout.strip() == "False", second.stderr

    def test_the_qr_url_does_not_contain_the_token(self, tmp_path):
        token = "a" * 64
        with patch.object(auth, "TOKEN_FILE", tmp_path / "token"), \
             patch.object(api, "_tailscale_ip", lambda: "100.64.0.1"):
            auth.write_token(token)
            body = client.get("/api/remote/token").json()
        assert token not in body["login_url"]
        assert token not in body["qr_svg"]
        assert "c=" in body["login_url"]

    def test_no_remote_url_means_no_qr_and_no_exchange_code(self, tmp_path):
        token = "a" * 64
        with patch.object(auth, "TOKEN_FILE", tmp_path / "token"), \
             patch.object(api, "_tailscale_ip", return_value=None), \
             patch.object(auth, "mint_exchange_code") as mint, \
             patch.object(api, "_make_qr_svg") as make_qr:
            auth.write_token(token)
            body = client.get("/api/remote/token").json()
        assert body["login_url"] == ""
        assert body["qr_svg"] == ""
        mint.assert_not_called()
        make_qr.assert_not_called()

    def test_login_accepts_the_code_and_sets_the_cookie(self, tmp_path):
        token = "b" * 64
        with patch.object(auth, "TOKEN_FILE", tmp_path / "token"):
            auth.write_token(token)
            code = auth.mint_exchange_code()
            r = client.get(f"/login?c={code}&next=/app/", follow_redirects=False)
            assert r.status_code == 303
            assert auth.COOKIE_NAME in r.cookies
            # Burned: the same scan cannot be replayed off someone's history.
            # Cookies cleared first, or the replay would be waved through as an
            # already-logged-in client and prove nothing about the code.
            client.cookies.clear()
            again = client.get(f"/login?c={code}&next=/app/", follow_redirects=False)
            assert again.status_code == 200  # the login form, not a redirect
        client.cookies.clear()

    def test_the_raw_token_is_no_longer_a_login_url(self, tmp_path):
        # The whole point: ?t=<token> used to work, which is what put the secret
        # into logs and history in the first place.
        token = "c" * 64
        with patch.object(auth, "TOKEN_FILE", tmp_path / "token"):
            auth.write_token(token)
            r = client.get(f"/login?t={token}&next=/app/", follow_redirects=False)
        assert r.status_code == 200  # form, not a 303


class TestModelList:
    """The offered models come from kiro-cli, not from a constant that drifts."""

    LISTING = json.dumps({"models": [
        {"model_name": "auto"}, {"model_name": "claude-sonnet-4.6"},
    ], "default_model": "auto"})

    def _run(self, **kw):
        return MagicMock(returncode=kw.get("rc", 0), stdout=kw.get("out", self.LISTING))

    def test_it_asks_kiro_cli_rather_than_the_hardcoded_tuple(self):
        config.available_models(force=True)  # prime, then clear
        with patch.object(config, "_models_cache", None), \
             patch("subprocess.run", return_value=self._run()):
            assert config.available_models(force=True) == ("auto", "claude-sonnet-4.6")

    def test_a_missing_kiro_cli_falls_back_instead_of_offering_nothing(self):
        # An empty dropdown is worse than a slightly stale one: dispatch would
        # have no model to send and validation would reject every choice.
        with patch.object(config, "_models_cache", None), \
             patch("subprocess.run", side_effect=FileNotFoundError):
            assert config.available_models(force=True) == config.MODELS

    def test_garbage_output_falls_back_too(self):
        with patch.object(config, "_models_cache", None), \
             patch("subprocess.run", return_value=self._run(out="not json")):
            assert config.available_models(force=True) == config.MODELS

    def test_dispatch_rejects_a_model_kiro_cli_does_not_offer(self):
        with patch.object(config, "_models_cache", (time.time(), ("auto",))):
            result = api.tmux.spawn("hi", "/tmp", model="claude-opus-4.6")
        assert result["ok"] is False
        assert "claude-opus-4.6" in result["error"]


class TestConciergeModel:
    """The picker has to change what actually spawns, or it is lying."""

    def test_it_reads_the_shared_setting(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"concierge_model": "claude-haiku-4.5"}))
        with patch.object(config, "SETTINGS_FILE", settings), \
             patch.object(config, "_models_cache",
                          (time.time(), ("auto", "claude-haiku-4.5"))):
            assert concierge.configured_model() == "claude-haiku-4.5"

    def test_a_model_no_longer_offered_falls_back_to_auto(self, tmp_path):
        # Otherwise the name reaches --model and kiro-cli dies inside a detached
        # pane, where the only visible symptom is a concierge that never starts.
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"concierge_model": "claude-opus-4.6"}))
        with patch.object(config, "SETTINGS_FILE", settings), \
             patch.object(config, "_models_cache", (time.time(), ("auto",))):
            assert concierge.configured_model() == "auto"

    def test_status_separates_configured_from_running(self):
        with patch.object(concierge, "_running_model", "claude-haiku-4.5"), \
             patch.object(concierge, "is_alive", lambda: True), \
             patch.object(concierge, "configured_model", lambda: "auto"):
            s = concierge.status()
        assert s["model"] == "auto"
        assert s["running_model"] == "claude-haiku-4.5"


class TestAutoAdvance:
    """The stop hook is only useful if something acts on it."""

    SID = "22222222-3333-4444-5555-666666666666"

    def _turns(self, tmp_path):
        turns = tmp_path / "turns"
        turns.mkdir()
        (turns / self.SID).touch()
        return turns

    def test_a_stop_mark_sends_the_next_queued_item(self, tmp_path):
        turns = self._turns(tmp_path)
        sent = []
        with patch.object(api.tmux, "TURNS_DIR", turns), \
             patch.object(api, "_load_settings", lambda: {f"stack-auto:{self.SID}": True}), \
             patch.object(api, "read_lock", lambda _: {"pid": os.getpid()}), \
             patch.object(api, "detect_status", lambda *a, **k: "idle"), \
             patch.object(api.tmux, "capture", lambda *a, **k: ""), \
             patch.object(api.tmux, "is_managed", lambda _: True), \
             patch.object(api.tmux, "stack_pop", lambda _: {"id": "x", "text": "next thing"}), \
             patch.object(api.tmux, "send_text", lambda sid, text: sent.append((sid, text))):
            last_seen = {}
            api._check_auto_advance(last_seen)
            assert sent == [(self.SID, "next thing")]
            # One stop event sends one item: a second pass over the same mark
            # must not fire again, or a single turn would drain the queue.
            api._check_auto_advance(last_seen)
            assert len(sent) == 1

    @pytest.mark.parametrize("status", ["thinking", "awaiting-approval"])
    def test_it_refuses_to_type_into_a_busy_session(self, tmp_path, status):
        turns = self._turns(tmp_path)
        sent = []
        with patch.object(api.tmux, "TURNS_DIR", turns), \
             patch.object(api, "_load_settings", lambda: {f"stack-auto:{self.SID}": True}), \
             patch.object(api, "read_lock", lambda _: {"pid": os.getpid()}), \
             patch.object(api, "detect_status", lambda *a, **k: status), \
             patch.object(api.tmux, "capture", lambda *a, **k: ""), \
             patch.object(api.tmux, "send_text", lambda sid, text: sent.append(text)):
            api._check_auto_advance({})
        assert sent == []

    def test_it_runs_clean_on_a_real_module(self, tmp_path):
        # Regression: `re` was imported inside another function, so this raised
        # NameError on every tick — and the loop's bare `except` ate it, so
        # auto-advance was dead and silent. Exercise the real module namespace.
        turns = self._turns(tmp_path)
        with patch.object(api.tmux, "TURNS_DIR", turns), \
             patch.object(api, "_load_settings", lambda: {}):
            api._check_auto_advance({})   # must not raise

    def test_status_separates_installed_from_cannot_be_installed(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "one.json").write_text(json.dumps({"name": "one"}))
        (agents_dir / "two.json").write_text(json.dumps({"name": "two"}))

        with patch.object(api, "AGENTS_DIR", agents_dir), \
             patch.object(api, "KIRO_CLI_SETTINGS", tmp_path / "none.json"), \
             patch.object(api.tmux, "managed_sessions", return_value={}):
            api.hooks_install({"agents": ["one"]})
            status = api.hooks_status()

        assert status["installed"] == ["one"]
        assert status["missing"] == ["two"]
        # Built-ins have no file, so they can never carry the hook.
        assert set(status["cannot_hook"]) == set(api.BUILTIN_AGENTS)


class TestProjectDeletePreview:
    """The confirmation count has to come from the code that does the deleting."""

    def test_preview_reports_the_same_set_delete_would_remove(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        project = tmp_path / "project"
        # A session in a subdirectory counts as part of the project, which is
        # exactly the case a caller cannot infer from the project card's count.
        TestSafeDeletion._write_session(sessions_dir, "top", project)
        TestSafeDeletion._write_session(sessions_dir, "nested", project / "inner")
        TestSafeDeletion._write_session(sessions_dir, "elsewhere", tmp_path / "other")

        with patch.object(api, "SESSIONS_DIR", sessions_dir):
            preview = api.preview_project_deletion({"cwd": str(project)})

        assert preview["session_count"] == 2
        assert set(preview["session_ids"]) == {"top", "nested"}
        assert preview["active_sessions"] == []

        with patch.object(api, "SESSIONS_DIR", sessions_dir), \
             patch.object(api, "FAVOURITES_FILE", tmp_path / "favourites.json"):
            deleted = api.delete_project_sessions({"cwd": str(project)})

        assert set(deleted["session_ids"]) == set(preview["session_ids"])

    def test_preview_deletes_nothing(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        project = tmp_path / "project"
        TestSafeDeletion._write_session(sessions_dir, "keep", project)

        with patch.object(api, "SESSIONS_DIR", sessions_dir):
            api.preview_project_deletion({"cwd": str(project)})

        assert (sessions_dir / "keep.json").exists()
        assert (sessions_dir / "keep.jsonl").exists()

    def test_preview_names_running_sessions_before_the_confirm(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        project = tmp_path / "project"
        TestSafeDeletion._write_session(sessions_dir, "live", project)
        (sessions_dir / "live.lock").write_text(json.dumps({"pid": os.getpid()}))

        with patch.object(api, "SESSIONS_DIR", sessions_dir):
            preview = api.preview_project_deletion({"cwd": str(project)})

        assert preview["active_sessions"] == ["live"]

    @pytest.mark.parametrize("cwd", ["", "relative/path", str(Path.home()), "/"])
    def test_preview_rejects_what_delete_rejects(self, cwd):
        assert "error" in api.preview_project_deletion({"cwd": cwd})
        assert "error" in api.delete_project_sessions({"cwd": cwd})


class TestPathsAreNotDisplayStrings:
    """Paths cross the API twice: `cwd` is real, `cwd_display` is for reading.

    Sending the display form back is what broke Open in Finder for every folder
    under the abbreviated prefix, so these tests check the contract itself —
    they feed one endpoint exactly what another endpoint handed out.
    """

    def test_listing_emits_a_usable_path_and_a_separate_display_path(self, tmp_path):
        # A path the abbreviator actually rewrites, so the two fields differ.
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        cwd = Path.home() / "Documents" / "PROJECTS" / "PERSONAL" / "widget"
        TestSafeDeletion._write_session(sessions_dir, "listed", cwd)

        with patch.object(api, "SESSIONS_DIR", sessions_dir):
            sessions = api.list_sessions()["sessions"]

        listed = next(s for s in sessions if s["id"] == "listed")
        assert listed["cwd"] == str(cwd), "cwd must stay usable"
        assert "…" not in listed["cwd"]
        assert listed["cwd_display"] == "~/…/PERSONAL/widget"

    def test_project_delete_accepts_the_path_the_projects_endpoint_returned(self, tmp_path):
        # The round trip that matters: read a project, then act on it.
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        project = tmp_path / "PROJECTS" / "widget"
        project.mkdir(parents=True)
        TestSafeDeletion._write_session(sessions_dir, "one", project)

        with patch.object(api, "SESSIONS_DIR", sessions_dir):
            listed = api.get_projects(refresh=True)
        projects = [p for p in listed["projects"] if p["name"] == "widget"]
        assert projects, "the project must be listed before it can be deleted"
        advertised_cwd = projects[0]["cwd"]

        with patch.object(api, "SESSIONS_DIR", sessions_dir), \
             patch.object(api, "FAVOURITES_FILE", tmp_path / "favourites.json"):
            result = api.delete_project_sessions({"cwd": advertised_cwd})

        assert result.get("ok") is True, result
        assert result["deleted_sessions"] == 1

    def test_open_folder_refuses_a_display_string_instead_of_failing_obscurely(self, tmp_path):
        target = tmp_path / "Documents" / "PROJECTS" / "PERSONAL" / "widget"
        target.mkdir(parents=True)
        request = MagicMock()

        with patch.object(api, "require_local", return_value=True), \
             patch.object(api.subprocess, "run") as run:
            shortened = api.shorten_path(str(target))
            # shorten_path only abbreviates paths under the real home, so build
            # the display form directly when tmp_path is elsewhere.
            display = shortened if "…" in shortened else "~/…/PERSONAL/widget"
            refused = api.open_folder({"path": display}, request)
            opened = api.open_folder({"path": str(target)}, request)

        assert "error" in refused
        assert run.call_count == 1, "only the real path should reach `open`"
        assert opened == {"ok": True}


class TestSettingsLocation:
    """Where settings live, which is the whole of "settings survive a restart"."""

    def test_settings_do_not_live_beside_the_code(self):
        # `Path(__file__).parent.parent` is the repo root in a checkout and the
        # *inside of the .app bundle* once PyInstaller has packaged it, so every
        # preference was written somewhere a reinstall overwrites — and that a
        # read-only bundle refuses outright.
        code_dir = Path(config.__file__).resolve().parent.parent
        assert code_dir not in config.SETTINGS_FILE.resolve().parents
        assert config.SETTINGS_FILE.parent == config.STATE_DIR

    @pytest.mark.parametrize("name", ["SNAPSHOTS_FILE", "FAVOURITES_FILE"])
    def test_snapshots_and_favourites_do_not_live_beside_the_code_either(self, name):
        # The same bug, unfixed for these two long after settings were moved: a
        # reinstall replaced the bundle and took the user's snapshots and
        # favourites with it. A stale snapshots.json surviving inside an
        # installed bundle is what exposed it.
        code_dir = Path(config.__file__).resolve().parent.parent
        path = getattr(config, name)
        assert code_dir not in path.resolve().parents
        assert path.parent == config.STATE_DIR
        # api must not rebuild its own path from __file__ — that is where the
        # bug lived, and two names for one file is how it would come back.
        assert getattr(api, name) == path

    def test_preferences_written_before_the_move_are_kept(self, tmp_path):
        legacy = tmp_path / "old" / "settings.json"
        legacy.parent.mkdir()
        legacy.write_text(json.dumps({"dispatch-model": "claude-opus-4.5"}))
        new = tmp_path / "state" / "settings.json"
        with patch.object(config, "LEGACY_SETTINGS_FILE", legacy), \
             patch.object(config, "SETTINGS_FILE", new), \
             patch.object(config, "LEGACY_SNAPSHOTS_FILE", tmp_path / "none-1.json"), \
             patch.object(config, "LEGACY_FAVOURITES_FILE", tmp_path / "none-2.json"), \
             patch.object(config, "STATE_DIR", new.parent):
            assert config.migrate_settings() == ["settings.json"]
            assert config.read_settings() == {"dispatch-model": "claude-opus-4.5"}
            # Second run must not clobber what has been written since.
            new.write_text(json.dumps({"dispatch-model": "claude-sonnet-4.6"}))
            assert config.migrate_settings() == []
            assert config.read_settings()["dispatch-model"] == "claude-sonnet-4.6"

    def test_snapshots_and_favourites_written_before_the_move_are_kept(self, tmp_path):
        # The migration a user actually needs: these two were being written into
        # the bundle until now, so an existing install has real data there.
        old, state = tmp_path / "old", tmp_path / "state"
        old.mkdir()
        (old / "snapshots.json").write_text(json.dumps([{"id": 1, "sessions": []}]))
        (old / "favourites.json").write_text(json.dumps([{"id": "abc", "title": "kept"}]))
        with patch.object(config, "LEGACY_SETTINGS_FILE", old / "settings.json"), \
             patch.object(config, "SETTINGS_FILE", state / "settings.json"), \
             patch.object(config, "LEGACY_SNAPSHOTS_FILE", old / "snapshots.json"), \
             patch.object(config, "SNAPSHOTS_FILE", state / "snapshots.json"), \
             patch.object(config, "LEGACY_FAVOURITES_FILE", old / "favourites.json"), \
             patch.object(config, "FAVOURITES_FILE", state / "favourites.json"), \
             patch.object(config, "STATE_DIR", state):
            assert config.migrate_settings() == ["snapshots.json", "favourites.json"]
            assert json.loads((state / "favourites.json").read_text())[0]["title"] == "kept"
            # Copied, not moved: if the move was wrong the original is still there.
            assert (old / "favourites.json").exists()
            assert config.migrate_settings() == []


class TestStatePersistence:
    @pytest.mark.parametrize(
        ("path_name", "save_name", "payload"),
        [
            ("settings.json", "_save_settings", {"theme": "dark"}),
            # snapshots and favourites are now collections-backed (no direct file write)
        ],
    )
    def test_state_saves_are_atomic(self, tmp_path, path_name, save_name, payload):
        path = tmp_path / path_name
        attribute = {
            "_save_settings": "SETTINGS_FILE",
        }[save_name]

        with patch.object(api, attribute, path):
            getattr(api, save_name)(payload)

        assert json.loads(path.read_text()) == payload
        assert not list(tmp_path.glob(".*.tmp"))

    def test_concurrent_settings_updates_do_not_lose_a_change(self, tmp_path):
        settings = tmp_path / "settings.json"
        real_load = api._load_settings
        barrier = threading.Barrier(2)

        def slow_load():
            data = real_load()
            time.sleep(0.05)
            return data

        def update(payload):
            barrier.wait()
            return api.save_settings(payload)

        with patch.object(api, "SETTINGS_FILE", settings), \
             patch.object(api, "_load_settings", side_effect=slow_load), \
             ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(update, {"theme": "dark"}),
                pool.submit(update, {"detail_tab": "activity"}),
            ]
            for future in futures:
                assert future.result()["ok"] is True

        assert json.loads(settings.read_text()) == {
            "theme": "dark",
            "detail_tab": "activity",
        }

    def test_concurrent_favourite_adds_do_not_lose_a_session(self, tmp_path):
        # Favourites are now collections-backed. Verify concurrent adds don't lose
        # an entry by patching _load_favourites/_save_favourites (the lock layer).
        saved_state = []
        real_load = api._load_favourites
        barrier = threading.Barrier(2)

        def slow_load():
            data = list(saved_state)  # current saved state
            time.sleep(0.05)
            return data

        def capture_save(data):
            saved_state.clear()
            saved_state.extend(data)

        def add(session_id):
            barrier.wait()
            return api.add_favourite({"id": session_id})

        def metadata(session_id):
            return {"title": session_id, "cwd": str(tmp_path / session_id)}

        with patch.object(api, "_load_favourites", side_effect=slow_load), \
             patch.object(api, "_save_favourites", side_effect=capture_save), \
             patch.object(api, "read_metadata", side_effect=metadata), \
             ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(add, "one"), pool.submit(add, "two")]
            for future in futures:
                assert future.result()["ok"] is True

        saved_ids = {item["id"] for item in saved_state}
        assert saved_ids == {"one", "two"}


class TestBranch:
    def test_branch_nonexistent_session(self):
        r = client.post("/api/sessions/nonexistent-id-12345/branch")
        assert "error" in r.json()


class TestPendingCollapse:
    """One spawn must produce one card.

    Between spawn and correlation the session exists on disk with a live lock
    while its nonce is still pending, so the listing used to show it twice: once
    as a `starting` placeholder and once as a `foreign` session it did not
    realise it owned.
    """

    def _pending_state(self, nonce="n1"):
        return {"managed": {}, "pending": {
            nonce: {"tmux": f"osa-pending-{nonce}", "cwd": "/tmp", "task": "spawned task"}}}

    def test_uncorrelated_pending_shows_a_placeholder_with_a_nonce(self):
        with patch("backend.api.tmux.load_state", return_value=self._pending_state()), \
             patch("backend.api.tmux.pending_owners", return_value={}), \
             patch("backend.api.tmux.reap_pendings", return_value=[]):
            sessions = client.get("/api/sessions").json()["sessions"]
        placeholders = [s for s in sessions if s.get("nonce")]
        assert len(placeholders) == 1
        assert placeholders[0]["control"] == "starting"
        assert placeholders[0]["nonce"] == "n1"

    def test_correlated_pending_drops_its_placeholder(self):
        """Its real session is in the list already, so the placeholder is noise."""
        with patch("backend.api.tmux.load_state", return_value=self._pending_state()), \
             patch("backend.api.tmux.pending_owners", return_value={"n1": "some-session"}), \
             patch("backend.api.tmux.reap_pendings", return_value=[]):
            sessions = client.get("/api/sessions").json()["sessions"]
        assert [s for s in sessions if s.get("nonce")] == []

    def test_correlated_session_reports_starting_not_foreign(self):
        """`foreign` would offer a takeover that kills a process mid-adoption."""
        live = client.get("/api/sessions").json()["sessions"]
        candidates = [s for s in live if s["control"] in ("managed", "foreign")]
        if not candidates:
            pytest.skip("No live sessions to reclassify")
        target = candidates[0]["id"]
        with patch("backend.api.tmux.pending_owners", return_value={"n1": target}), \
             patch("backend.api.tmux.managed_sessions", return_value={}), \
             patch("backend.api.tmux.reap_pendings", return_value=[]):
            sessions = client.get("/api/sessions").json()["sessions"]
        match = [s for s in sessions if s["id"] == target]
        assert match and match[0]["control"] == "starting"

    def test_listing_reaps_dead_pendings(self):
        """Reaping used to happen only on startup, so a pending left behind by a
        dead backend produced a card nothing could remove."""
        with patch("backend.api.tmux.reap_pendings") as reap, \
             patch("backend.api.tmux.pending_owners", return_value={}):
            client.get("/api/sessions")
        reap.assert_called_once()


class TestCancelPendingEndpoint:
    def test_unknown_nonce_is_rejected(self):
        r = client.post("/api/pending/does-not-exist/cancel")
        assert r.status_code == 200
        assert r.json()["ok"] is False

    def test_cancel_delegates_to_the_manager(self):
        with patch("backend.api.tmux.cancel_pending",
                   return_value={"ok": True, "killed_tmux": True}) as cancel:
            r = client.post("/api/pending/n1/cancel")
        cancel.assert_called_once_with("n1")
        assert r.json()["killed_tmux"] is True


class TestResizeEndpoint:
    """Geometry comes from a browser, so it is validated before reaching tmux."""

    def test_rejects_non_numeric(self):
        r = client.post("/api/sessions/x/resize", json={"cols": "wide", "rows": 20})
        assert r.json()["ok"] is False

    def test_rejects_zero_and_negative(self):
        for payload in ({"cols": 0, "rows": 20}, {"cols": 100, "rows": -5}):
            assert client.post("/api/sessions/x/resize", json=payload).json()["ok"] is False

    def test_unmanaged_session_is_refused(self):
        r = client.post("/api/sessions/nonexistent-id-12345/resize",
                        json={"cols": 100, "rows": 30})
        assert r.json()["ok"] is False
        assert "not managed" in r.json()["error"]

    def test_valid_geometry_reaches_the_manager(self):
        with patch("backend.api.tmux.resize",
                   return_value={"ok": True, "cols": 100, "rows": 30}) as resize:
            r = client.post("/api/sessions/abc/resize", json={"cols": 100, "rows": 30})
        resize.assert_called_once_with("abc", 100, 30)
        assert r.json()["ok"] is True

    def test_pane_reports_current_geometry(self):
        with patch("backend.api.tmux.is_managed", return_value=True), \
             patch("backend.api.tmux.capture", return_value="hello"), \
             patch("backend.api.tmux.geometry", return_value=(120, 36)):
            data = client.get("/api/sessions/abc/pane").json()
        assert (data["cols"], data["rows"]) == (120, 36)


class TestShell:
    """The plain shell: the one place an interactive command can be run.

    Sessions are spawned as a single command, so `kiro-cli login` — which asks
    questions and waits — had nowhere to go. These tests hold down the parts that
    are security-relevant rather than the tmux plumbing, which is exercised by
    running a real shell in the last test.
    """

    def test_a_key_outside_the_allowlist_is_refused(self):
        # The key name is passed to `tmux send-keys`, so the set is enumerated
        # rather than passed through.
        r = client.post("/api/shell/key", json={"key": "C-x"})
        assert r.json()["ok"] is False
        assert "not allowed" in r.json()["error"]

    def test_a_missing_directory_is_refused_before_anything_starts(self, tmp_path):
        r = client.post("/api/shell/open", json={"cwd": str(tmp_path / "nope")})
        assert r.json()["ok"] is False
        assert "not found" in r.json()["error"]

    def test_input_is_refused_when_no_shell_is_running(self):
        with patch.object(api.shell.tmux, "session_exists", lambda name: False):
            assert client.post("/api/shell/input", json={"text": "ls"}).json()["ok"] is False
            assert client.post("/api/shell/key", json={"key": "Enter"}).json()["ok"] is False

    def test_geometry_is_validated(self):
        for payload in ({"cols": "wide", "rows": 20}, {"cols": 0, "rows": 20},
                        {"cols": 100, "rows": -1}, {"cols": True, "rows": 20}):
            assert client.post("/api/shell/resize", json=payload).json()["ok"] is False

    def test_multi_line_input_becomes_one_command(self):
        # Each newline is a submit at a shell prompt, so pasted text would run
        # its second line as its own command — running half of something the
        # user has not finished reading.
        sent = []
        with patch.object(api.shell.tmux, "session_exists", lambda name: True), \
             patch.object(api.shell.tmux, "pane_dead", lambda name: False), \
             patch.object(api.shell.tmux, "_tmux",
                          lambda *a, **k: sent.append(a) or ""):
            api.shell.send_text("echo one\necho two")
        literal = [a for a in sent if "-l" in a]
        assert literal and literal[0][-1] == "echo one echo two"

    def test_the_shell_input_path_shares_the_input_rate_limit(self):
        # Typing into a login shell is the most direct command execution in the
        # API. Left out of this regex it would be the one unmetered way in.
        assert auth._INPUT_PATH.fullmatch("/api/shell/input")
        assert auth._INPUT_PATH.fullmatch("/api/shell/key")
        assert auth._INPUT_PATH.fullmatch("/api/sessions/abc/input")
        assert not auth._INPUT_PATH.fullmatch("/api/shell/open")

    def test_the_tmux_name_is_not_adoptable_as_a_kiro_session(self):
        # `reconcile()` adopts stray `kiro-*` sessions as managed kiro sessions.
        # A shell is not one, and must not be swept up as one.
        assert not api.shell.SHELL_TMUX_NAME.startswith(config.TMUX_PREFIX)

    @pytest.mark.skipif(not api.tmux.tmux_available(), reason="tmux not installed")
    def test_a_real_shell_runs_a_command_and_closes(self):
        # Never touch a shell somebody else opened. The shell is a singleton, so
        # an earlier version of this test adopted the running one, typed into
        # whatever had the pane — a kiro chat TUI, as it happened, which ate the
        # probe as chat input — and then killed it in cleanup. A test that can
        # destroy the user's session is worse than no test.
        if api.shell.is_alive():
            pytest.skip("a shell is already running; refusing to drive it")
        opened = api.shell.open_shell("~")
        assert opened["ok"] is True, opened
        try:
            assert api.shell.send_text("echo quarterdeck-shell-probe")["ok"] is True
            deadline = time.time() + 15
            pane = ""
            while time.time() < deadline:
                pane = api.shell.capture(40)
                if "quarterdeck-shell-probe" in pane and pane.count("probe") > 1:
                    break
                time.sleep(0.3)
            # Twice: once as the echoed command line, once as its output. One
            # occurrence only would mean the command was typed but never ran.
            assert pane.count("quarterdeck-shell-probe") >= 2, pane[-400:]
        finally:
            assert api.shell.close()["closed"] is True
        assert api.shell.is_alive() is False
