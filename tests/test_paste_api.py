"""Tests for paste API endpoints and attachment-aware send_input."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from backend import api, pastes as paste_store
from backend.api import app
from backend.config import GATES_DIR

client = TestClient(app, client=("127.0.0.1", 45678))


# ── POST /api/pastes ───────────────────────────────────────────────────────

class TestCreatePaste:
    def test_create_paste_returns_metadata(self):
        text = "# Header\n" + "line\n" * 25
        r = client.post("/api/pastes", json={"text": text, "session_id": "sess-1"})
        assert r.status_code == 200
        d = r.json()
        assert "name" in d
        assert d["lines"] > 0
        assert d["bytes"] > 0
        assert "preview" in d

    def test_create_paste_empty_text_error(self):
        r = client.post("/api/pastes", json={"text": "   "})
        assert r.status_code == 200
        assert "error" in r.json()

    def test_create_paste_unassigned_session(self):
        text = "content\n" * 5
        r = client.post("/api/pastes", json={"text": text})
        assert r.status_code == 200
        d = r.json()
        assert d["session_id"] == "_unassigned"


# ── GET /api/pastes/{session_id}/{name} ────────────────────────────────────

class TestGetPaste:
    def test_get_paste_returns_text(self):
        text = "Hello paste\n" * 10
        meta = client.post("/api/pastes", json={"text": text, "session_id": "s1"}).json()
        r = client.get(f"/api/pastes/s1/{meta['name']}")
        assert r.status_code == 200
        assert r.json()["text"] == text

    def test_get_paste_unknown_name_404(self):
        r = client.get("/api/pastes/s1/nonexistent-20260815-000000-x.md")
        assert r.status_code == 404

    def test_get_paste_traversal_rejected(self):
        # FastAPI normalises dotdot sequences in the URL path, so the request
        # arrives as /api/pastes/s1/etc/passwd — no file exists, returns 404.
        # The traversal guard in _resolve is the backstop for browser-supplied
        # names that bypass URL normalisation (e.g. JSON body fields).
        r = client.get("/api/pastes/s1/../../etc/passwd")
        assert r.status_code in (400, 404)  # either means content not served


# ── DELETE /api/pastes/{session_id}/{name} ─────────────────────────────────

class TestDeletePaste:
    def test_delete_paste_removes_file(self):
        text = "to delete\n" * 5
        meta = client.post("/api/pastes", json={"text": text, "session_id": "s2"}).json()
        r = client.delete(f"/api/pastes/s2/{meta['name']}")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        # File should be gone
        r2 = client.get(f"/api/pastes/s2/{meta['name']}")
        assert r2.status_code == 404


# ── send_input with attachments ────────────────────────────────────────────

class TestSendInputAttachments:
    def _make_managed(self, session_id: str):
        """Stub tmux.is_managed to return True and tmux.send_text to succeed."""
        return (
            patch.object(api.tmux, "is_managed", return_value=True),
            patch.object(api.tmux, "send_text", return_value={"ok": True}),
        )

    def test_attachment_reference_appears_in_prompt(self):
        text = "# Document\n" + "line\n" * 25
        meta = client.post("/api/pastes", json={"text": text, "session_id": "s3"}).json()

        sent_texts = []
        with patch.object(api.tmux, "is_managed", return_value=True), \
             patch.object(api.tmux, "send_text",
                         side_effect=lambda sid, t, **kw: sent_texts.append(t) or {"ok": True}), \
             patch.object(api.acp_observer, "is_attached", return_value=False), \
             patch("backend.api._can_read_files", return_value=True):
            r = client.post("/api/sessions/s3/input", json={
                "text": "please review",
                "attachments": [{"session_id": "s3", "name": meta["name"],
                                 "lines": meta["lines"], "size_display": meta["size_display"]}],
            })
        assert r.status_code == 200
        assert r.json().get("ok") is True
        assert sent_texts, "send_text was not called"
        prompt = sent_texts[0]
        assert "[pasted document:" in prompt
        assert meta["name"] in prompt
        assert "please review" in prompt

    def test_gated_session_inlines_content(self):
        text = "# Gated\n" + "line\n" * 25
        meta = client.post("/api/pastes", json={"text": text, "session_id": "sg"}).json()

        sent_texts = []
        with patch.object(api.tmux, "is_managed", return_value=True), \
             patch.object(api.tmux, "send_text",
                         side_effect=lambda sid, t, **kw: sent_texts.append(t) or {"ok": True}), \
             patch.object(api.acp_observer, "is_attached", return_value=False), \
             patch("backend.api._can_read_files", return_value=False):
            r = client.post("/api/sessions/sg/input", json={
                "text": "gated task",
                "attachments": [{"session_id": "sg", "name": meta["name"],
                                 "lines": meta["lines"], "size_display": meta["size_display"]}],
            })
        assert r.status_code == 200
        assert sent_texts, "send_text was not called"
        prompt = sent_texts[0]
        # Inline: should contain actual content, not a reference line
        assert "[pasted document:" not in prompt
        assert "# Gated" in prompt or "line" in prompt

    def test_unknown_attachment_skipped_gracefully(self):
        with patch.object(api.tmux, "is_managed", return_value=True), \
             patch.object(api.tmux, "send_text", return_value={"ok": True}), \
             patch.object(api.acp_observer, "is_attached", return_value=False):
            r = client.post("/api/sessions/sx/input", json={
                "text": "hello",
                "attachments": [{"session_id": "sx", "name": "nonexistent-20260815-x.md",
                                 "lines": 0, "size_display": "0 B"}],
            })
        assert r.status_code == 200
        assert r.json().get("ok") is True
