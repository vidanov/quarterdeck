#!/usr/bin/env python3
"""Task 1 probe: can ACP observe a tmux-owned V3 session via session/load?

Records:
  1. Whether session/load succeeds against a live tmux-owned V3 session.
  2. Whether the ACP process writes to the same messages.jsonl.
  3. Whether the tmux session survives and stays interactive after load.
  4. Whether events for turns driven from the tmux side arrive over ACP.
  5. Whether the transcript is parseable after the probe.

Usage:
    python scripts/probe-acp-session-load.py

Creates a throwaway V3 session in /tmp/acp-probe-<ts>/, runs the probe,
records all findings to docs/probe-acp-session-load-<date>.md, and cleans up.

SAFETY: only operates on scratch sessions it creates. Never touches real sessions.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

KIRO_CLI = "kiro-cli"
V3_SESSIONS_BASE = Path.home() / ".kiro" / "sessions"
ROADMAP = Path(__file__).parent.parent / "docs" / "ROADMAP.md"
TIMEOUT_SPAWN = 20.0
TIMEOUT_ACP = 15.0
TIMEOUT_EVENTS = 10.0


# ── helpers ──────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def find_v3_session(scratch_dir: str) -> tuple[str, Path] | None:
    """Return (session_id, session_dir) for a V3 session under scratch_dir."""
    # macOS: /var is a symlink to /private/var; resolve both sides to compare
    scratch_real = str(Path(scratch_dir).resolve())
    base = V3_SESSIONS_BASE
    if not base.exists():
        return None
    for ws_dir in base.iterdir():
        if not ws_dir.is_dir() or len(ws_dir.name) not in (16, 8):
            continue
        for sess_dir in ws_dir.iterdir():
            if not sess_dir.name.startswith("sess_"):
                continue
            sj = sess_dir / "session.json"
            if not sj.exists():
                continue
            try:
                meta = json.loads(sj.read_text())
            except Exception:
                continue
            for wp in (meta.get("workspacePaths") or []):
                if str(Path(wp).resolve()) == scratch_real:
                    return (meta.get("id", ""), sess_dir)
    return None


def jsonl_line_count(path: Path) -> int:
    try:
        return sum(1 for _ in path.open())
    except Exception:
        return 0


def send(proc, obj: dict) -> None:
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


# ── ACP session/load client ───────────────────────────────────────────────

class ACPLoadProbe:
    """Minimal ACP client that connects via session/load instead of session/new."""

    def __init__(self, session_id: str, cwd: str):
        self.session_id = session_id
        self.cwd = cwd
        self.loaded = threading.Event()
        self.events: list[dict] = []
        self.load_result: dict | None = None
        self.load_error: str | None = None
        self._proc: subprocess.Popen | None = None

    def start(self) -> subprocess.Popen:
        self._proc = subprocess.Popen(
            [KIRO_CLI, "acp", "--agent-engine", "v3"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()
        # Step 1: initialize
        send(self._proc, {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {"name": "probe-acp-session-load", "version": "1.0"},
            },
            "id": 1,
        })
        return self._proc

    def _reader(self) -> None:
        try:
            for raw in self._proc.stdout:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                mid = msg.get("id")
                method = msg.get("method", "")
                result = msg.get("result")
                error = msg.get("error")

                # Record every notification
                if method:
                    self.events.append({"time": time.time(), "msg": msg})

                if mid == 1 and result is not None:
                    # initialize done → session/load
                    send(self._proc, {
                        "jsonrpc": "2.0",
                        "method": "session/load",
                        "params": {
                            "sessionId": self.session_id,
                            "cwd": self.cwd,
                            "mcpServers": [],
                        },
                        "id": 2,
                    })
                elif mid == 1 and error is not None:
                    self.load_error = f"initialize error: {error}"
                    self.loaded.set()

                elif mid == 2:
                    if error is not None:
                        self.load_error = f"session/load error: {error}"
                    else:
                        self.load_result = result
                    self.loaded.set()

        except Exception as e:
            self.load_error = str(e)
            self.loaded.set()

    def stop(self) -> None:
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass

    def wait_loaded(self, timeout: float) -> bool:
        return self.loaded.wait(timeout=timeout)

    def wait_events(self, timeout: float) -> None:
        time.sleep(timeout)


# ── main probe ────────────────────────────────────────────────────────────

def run_probe() -> dict:
    scratch = tempfile.mkdtemp(prefix="acp-probe-")
    log(f"Scratch directory: {scratch}")
    findings: dict = {
        "scratch": scratch,
        "session_id": None,
        "session_dir": None,
        "messages_jsonl_before": 0,
        "messages_jsonl_after_load": 0,
        "messages_jsonl_after_turn": 0,
        "load_succeeded": False,
        "load_error": None,
        "tmux_survives": None,
        "events_received": [],
        "turn_events_seen": False,
        "transcript_parseable": None,
        "acp_stderr": "",
    }

    tmux_name = f"acp-probe-{int(time.time())}"
    tmux_proc = None

    try:
        # ── Step 1: spawn a throwaway V3 session via tmux ─────────────────
        log("Spawning throwaway V3 session in tmux...")
        tmux_proc = subprocess.run([
            "tmux", "new-session", "-d", "-s", tmux_name,
            "-x", "220", "-y", "50", "-c", scratch,
            "--",
            KIRO_CLI, "chat", "--agent-engine", "v3",
            "--trust-all-tools", "Hello, this is a probe session. Say hi.",
        ], capture_output=True, text=True)

        if tmux_proc.returncode != 0:
            findings["load_error"] = f"tmux spawn failed: {tmux_proc.stderr}"
            return findings

        log("Waiting for V3 session to appear on disk...")
        deadline = time.time() + TIMEOUT_SPAWN
        while time.time() < deadline:
            result = find_v3_session(scratch)
            if result:
                findings["session_id"], sess_dir = result
                findings["session_dir"] = str(sess_dir)
                log(f"Found session: {findings['session_id']}")
                break
            time.sleep(0.5)

        if not findings["session_id"]:
            findings["load_error"] = f"V3 session not found in {V3_SESSIONS_BASE} after {TIMEOUT_SPAWN}s"
            return findings

        messages_jsonl = sess_dir / "messages.jsonl"
        # Wait for it to have some content
        deadline = time.time() + 10
        while time.time() < deadline and jsonl_line_count(messages_jsonl) == 0:
            time.sleep(0.3)

        findings["messages_jsonl_before"] = jsonl_line_count(messages_jsonl)
        log(f"messages.jsonl lines before probe: {findings['messages_jsonl_before']}")

        # ── Step 2: ACP session/load from a separate process ──────────────
        log("Starting ACP client with session/load...")
        probe = ACPLoadProbe(findings["session_id"], scratch)
        proc = probe.start()

        loaded = probe.wait_loaded(timeout=TIMEOUT_ACP)
        findings["load_succeeded"] = loaded and probe.load_result is not None
        findings["load_error"] = probe.load_error

        # Capture stderr for diagnosis
        try:
            stderr_out, _ = proc.communicate(timeout=0.1)
            findings["acp_stderr"] = stderr_out[:500] if stderr_out else ""
        except subprocess.TimeoutExpired:
            pass

        findings["messages_jsonl_after_load"] = jsonl_line_count(messages_jsonl)
        log(f"session/load succeeded: {findings['load_succeeded']}")
        log(f"messages.jsonl lines after load: {findings['messages_jsonl_after_load']}")

        # ── Step 3: check tmux session still alive ─────────────────────────
        check = subprocess.run(["tmux", "has-session", "-t", tmux_name],
                               capture_output=True)
        findings["tmux_survives"] = check.returncode == 0
        log(f"tmux session survives: {findings['tmux_survives']}")

        if findings["load_succeeded"]:
            # ── Step 4: drive a turn from tmux, watch for ACP events ──────
            log("Driving a turn from the tmux pane...")
            probe.events.clear()
            subprocess.run([
                "tmux", "send-keys", "-t", tmux_name,
                "What is 2+2?", "Enter",
            ])
            log(f"Waiting {TIMEOUT_EVENTS}s for ACP events...")
            probe.wait_events(TIMEOUT_EVENTS)
            findings["events_received"] = [
                e["msg"].get("method", "") for e in probe.events
            ]
            findings["turn_events_seen"] = bool(probe.events)
            findings["messages_jsonl_after_turn"] = jsonl_line_count(messages_jsonl)
            log(f"ACP events received: {findings['events_received']}")
            log(f"messages.jsonl lines after turn: {findings['messages_jsonl_after_turn']}")

        # ── Step 5: check transcript parseability ─────────────────────────
        try:
            lines = [json.loads(l) for l in messages_jsonl.open() if l.strip()]
            findings["transcript_parseable"] = len(lines) > 0
        except Exception as e:
            findings["transcript_parseable"] = False
            findings["transcript_parse_error"] = str(e)
        log(f"Transcript parseable: {findings['transcript_parseable']}")

        probe.stop()

    finally:
        # Clean up tmux session
        subprocess.run(["tmux", "kill-session", "-t", tmux_name],
                       capture_output=True)
        # Leave scratch + V3 session dir for a few minutes in case needed
        log(f"Probe complete. Scratch kept at {scratch} for inspection.")

    return findings


# ── format results ────────────────────────────────────────────────────────

def format_findings(f: dict) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    load_ok = f["load_succeeded"]
    tmux_ok = f["tmux_survives"]
    writes = f["messages_jsonl_after_load"] > f["messages_jsonl_before"]
    events = bool(f["events_received"])
    parseable = f["transcript_parseable"]

    verdict = "✅ clean" if (load_ok and tmux_ok and not writes) else "⚠ conflicted"

    lines = [
        f"**Probe: ACP session/load against tmux-owned V3 session — {date}**",
        "",
        f"Verdict: {verdict}",
        "",
        "| Question | Result |",
        "|---|---|",
        f"| session/load succeeds | {'yes' if load_ok else 'no'} |",
        f"| ACP writes to messages.jsonl | {'yes ⚠' if writes else 'no'} |",
        f"| tmux session survives load | {'yes' if tmux_ok else 'no — ⚠ ACP took ownership'} |",
        f"| Events arrive for tmux-driven turns | {'yes' if events else 'no / not tested (load failed)'} |",
        f"| Transcript parseable after probe | {'yes' if parseable else 'no'} |",
        "",
        "**Details:**",
        f"- Session id: `{f['session_id']}`",
        f"- messages.jsonl lines: before={f['messages_jsonl_before']}, "
        f"after load={f['messages_jsonl_after_load']}, "
        f"after turn={f['messages_jsonl_after_turn']}",
        f"- ACP events seen: {f['events_received'] or '(none)'}",
    ]
    if f.get("load_error"):
        lines += ["", f"- Error: `{f['load_error']}`"]
    if f.get("acp_stderr"):
        lines += ["", f"- ACP stderr: `{f['acp_stderr'][:200]}`"]

    lines += [
        "",
        "**Implication for Task 2:**",
    ]
    if load_ok and not writes:
        lines += [
            "session/load is a read-only observer — safe to proceed with `acp_session.py`.",
            "Tasks 3–7 follow the primary path in the flowchart.",
        ]
    elif load_ok and writes:
        lines += [
            "session/load writes to messages.jsonl. ACP is NOT a safe side-channel.",
            "12g must fall back to tmux send-keys. Tasks 3–6 take the fallback path.",
        ]
    else:
        lines += [
            "session/load failed or tmux session was killed.",
            "ACP cannot attach to a session it does not own.",
            "12g falls back to tmux send-keys for all V3 sessions.",
        ]

    return "\n".join(lines)


def inject_into_roadmap(probe_block: str) -> None:
    """Insert the probe results into ROADMAP.md section 13."""
    text = ROADMAP.read_text()
    marker = "**Probe results (2026-08-12, kiro-cli 2.16.2):**"
    if marker not in text:
        log("Could not find probe marker in ROADMAP.md — skipping injection")
        return
    insert_after = text.find(marker)
    # Find the end of the existing probe block (next blank line after a list item)
    block_end = text.find("\n\n### Work", insert_after)
    if block_end == -1:
        block_end = text.find("\n\n- [ ]", insert_after)
    if block_end == -1:
        log("Could not find probe block end — skipping injection")
        return
    new_block = f"\n\n**Probe results ({datetime.now().strftime('%Y-%m-%d')}, kiro-cli re-run):**\n\n{probe_block}"
    new_text = text[:block_end] + new_block + text[block_end:]
    ROADMAP.write_text(new_text)
    log("Injected probe results into ROADMAP.md section 13")


if __name__ == "__main__":
    log("=== ACP session/load probe ===")
    findings = run_probe()
    block = format_findings(findings)

    print("\n" + "=" * 60)
    print(block)
    print("=" * 60 + "\n")

    # Write standalone report
    date = datetime.now().strftime("%Y-%m-%d")
    report_path = Path(__file__).parent.parent / "docs" / f"probe-acp-session-load-{date}.md"
    report_path.write_text(f"# ACP session/load Probe — {date}\n\n{block}\n")
    log(f"Report written to {report_path}")

    # Inject into ROADMAP section 13
    inject_into_roadmap(block)

    # Exit code: 0 if safe to proceed with acp_session.py, 1 if fallback needed
    sys.exit(0 if findings["load_succeeded"] else 1)
