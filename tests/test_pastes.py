"""Tests for backend/pastes.py — paste store round-trip, guards, sweep."""
import time
import pytest
from backend import pastes
from backend.config import PASTE_MIN_CHARS, PASTE_MIN_LINES


# ── fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def patch_pastes_dir(tmp_path, monkeypatch):
    """Redirect PASTES_DIR to a temp directory for every test."""
    fake_dir = tmp_path / "pastes"
    monkeypatch.setattr("backend.pastes.PASTES_DIR", fake_dir)
    monkeypatch.setattr("backend.config.PASTES_DIR", fake_dir)
    return fake_dir


# ── should_collapse ────────────────────────────────────────────────────────

def test_should_collapse_by_chars():
    assert pastes.should_collapse("x" * PASTE_MIN_CHARS)


def test_should_collapse_by_lines():
    text = "\n".join(["line"] * PASTE_MIN_LINES)
    assert pastes.should_collapse(text)


def test_should_not_collapse_short():
    assert not pastes.should_collapse("short text")


# ── save / read round-trip ─────────────────────────────────────────────────

def test_save_read_roundtrip():
    text = "Hello\nWorld\nThis is a test paste.\n" * 5
    meta = pastes.save("sess-abc", text)
    assert meta["lines"] == text.count("\n") + 1
    assert meta["bytes"] == len(text.encode())
    assert meta["preview"].startswith("Hello")
    assert len(meta["preview"].splitlines()) <= 15

    result = pastes.read("sess-abc", meta["name"])
    assert result == text


def test_save_unassigned():
    meta = pastes.save(None, "content\n" * 5)
    assert meta["session_id"] == "_unassigned"
    result = pastes.read(None, meta["name"])
    assert result == "content\n" * 5


def test_save_preview_truncation():
    lines = [f"line {i}" for i in range(30)]
    text = "\n".join(lines)
    meta = pastes.save("s1", text)
    preview_lines = meta["preview"].splitlines()
    assert len(preview_lines) == 15
    assert preview_lines[0] == "line 0"
    assert preview_lines[-1] == "line 14"


def test_save_slug_sanitises_name():
    meta = pastes.save("s1", "# Hello World\nmore text", name="some/path/../file name")
    # Name must be a plain filename with no path separators
    assert "/" not in meta["name"]
    assert ".." not in meta["name"]


def test_save_no_collision():
    text = "a\n" * 5
    m1 = pastes.save("s1", text)
    # Force same timestamp by monkeypatching datetime — instead just save twice fast
    m2 = pastes.save("s1", text)
    # Both files should exist (names differ because counter or sub-second differs)
    from backend.pastes import _bucket
    files = list(_bucket("s1").glob("*.md"))
    assert len(files) == 2


# ── delete ─────────────────────────────────────────────────────────────────

def test_delete_removes_file():
    meta = pastes.save("s1", "content\n" * 5)
    pastes.delete("s1", meta["name"])
    with pytest.raises(FileNotFoundError):
        pastes.read("s1", meta["name"])


def test_delete_missing_is_noop():
    pastes.delete("s1", "nonexistent-20260815-000000-x.md")  # must not raise


# ── traversal guard ────────────────────────────────────────────────────────

def test_traversal_rejected_dotdot():
    with pytest.raises(ValueError, match="Traversal"):
        pastes.read("s1", "../../etc/passwd")


def test_traversal_rejected_abs_path():
    with pytest.raises(ValueError):
        pastes.read("s1", "/etc/passwd")


# ── sweep ──────────────────────────────────────────────────────────────────

def test_sweep_honours_age_cutoff(monkeypatch, tmp_path):
    fake_dir = tmp_path / "pastes"
    monkeypatch.setattr("backend.pastes.PASTES_DIR", fake_dir)

    meta_new = pastes.save("s1", "new content\n" * 5)
    meta_old = pastes.save("s1", "old content\n" * 5)

    # Back-date the old file's mtime by 40 days
    old_path = fake_dir / "s1" / meta_old["name"]
    old_mtime = time.time() - 40 * 86400
    import os
    os.utime(old_path, (old_mtime, old_mtime))

    removed = pastes.sweep(days=30)
    assert removed == 1

    # New file still readable
    assert pastes.read("s1", meta_new["name"]) == "new content\n" * 5
    # Old file gone
    with pytest.raises(FileNotFoundError):
        pastes.read("s1", meta_old["name"])


def test_sweep_empty_dir_cleaned(tmp_path, monkeypatch):
    fake_dir = tmp_path / "pastes"
    monkeypatch.setattr("backend.pastes.PASTES_DIR", fake_dir)

    meta = pastes.save("s1", "old\n" * 5)
    old_path = fake_dir / "s1" / meta["name"]
    import os
    old_mtime = time.time() - 40 * 86400
    os.utime(old_path, (old_mtime, old_mtime))

    pastes.sweep(days=30)
    # Empty bucket dir should be removed
    assert not (fake_dir / "s1").exists()


# ── reference_line ─────────────────────────────────────────────────────────

def test_reference_line_format():
    meta = pastes.save("s1", "text\n" * 5)
    ref = pastes.reference_line("s1", meta["name"], meta["lines"], meta["size_display"])
    assert ref.startswith("[pasted document:")
    assert "lines" in ref
    assert ".md" in ref
    assert "\n" not in ref  # must be one line


# ── storage_bytes ──────────────────────────────────────────────────────────

def test_storage_bytes():
    assert pastes.storage_bytes() == 0
    pastes.save("s1", "x" * 500)
    assert pastes.storage_bytes() > 0
