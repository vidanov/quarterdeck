"""Standalone ACP query script — run as subprocess to avoid blocking FastAPI.

Usage: python acp_query.py "prompt text"
Output: the model's text response on stdout, nothing on stderr.
Exit 0 on success, 1 on failure.
"""
import json
import subprocess
import sys
import threading
from pathlib import Path

KIRO_CLI = "kiro-cli"
TIMEOUT = 40.0


def acp_query(prompt: str) -> str:
    proc = subprocess.Popen(
        [KIRO_CLI, "acp", "--model=auto", "--trust-all-tools"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    chunks: list[str] = []
    done = threading.Event()

    def _send(obj: dict) -> None:
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def _reader() -> None:
        try:
            for raw in proc.stdout:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                result = msg.get("result")
                mid = msg.get("id")
                if mid == 1 and result is not None:
                    _send({"jsonrpc": "2.0", "method": "session/new",
                           "params": {"cwd": str(Path.home()), "mcpServers": []}, "id": 2})
                elif mid == 2 and result is not None:
                    sid = result.get("sessionId", "")
                    _send({"jsonrpc": "2.0", "method": "session/prompt",
                           "params": {"sessionId": sid,
                                      "prompt": [{"type": "text", "text": prompt}]}, "id": 3})
                elif mid == 3:
                    done.set()
                    break
                elif msg.get("method") == "session/update":
                    update = msg.get("params", {}).get("update", {})
                    if update.get("sessionUpdate") == "agent_message_chunk":
                        content = update.get("content", {})
                        if content.get("type") == "text":
                            chunks.append(content.get("text", ""))
        except Exception:
            pass
        finally:
            done.set()
            try:
                proc.stdin.close()
            except Exception:
                pass

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    _send({"jsonrpc": "2.0", "method": "initialize",
           "params": {"protocolVersion": 1, "clientCapabilities": {},
                      "clientInfo": {"name": "quarterdeck-summary", "version": "1.0"}}, "id": 1})
    done.wait(timeout=TIMEOUT)
    try:
        proc.kill()
    except Exception:
        pass
    return "".join(chunks).strip()


if __name__ == "__main__":
    # Read prompt from stdin (avoids argv length/encoding issues)
    prompt = sys.stdin.read().strip()
    if not prompt:
        sys.exit(1)
    result = acp_query(prompt)
    if result:
        print(result)
        sys.exit(0)
    sys.exit(1)
