"""Standalone ACP query script — run as subprocess to avoid blocking FastAPI.

One-shot query pattern: spawn kiro-cli acp → initialize → session/new →
session/prompt → collect response → exit.

Usage: python acp_query.py          (prompt read from stdin)
Output: the model's text response on stdout, nothing on stderr.
Exit 0 on success, 1 on failure.

This module is intentionally kept as a thin wrapper around ACPSession so
there is one ACP implementation, not two.  The subprocess boundary (api.py
calling this script) is kept because kiro-cli acp must not block FastAPI's
thread pool.
"""
import sys
from pathlib import Path

# When run directly (subprocess from api.py), add parent dir to path so that
# backend.acp_session is importable regardless of working directory.
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.acp_session import ACPSession  # noqa: E402

TIMEOUT = 40.0


def acp_query(prompt: str) -> str:
    """Run a single prompt through a fresh ACP session and return the text."""
    session = ACPSession(engine="v2", model="auto",
                         extra_args=["--trust-all-tools"], timeout=TIMEOUT)
    session.start()
    try:
        session.initialize(client_name="quarterdeck-summary")
        sid = session.new_session(cwd=str(Path.home()))
        return session.collect_response(sid, prompt, timeout=TIMEOUT)
    except Exception:
        return ""
    finally:
        session.stop()


if __name__ == "__main__":
    prompt = sys.stdin.read().strip()
    if not prompt:
        sys.exit(1)
    result = acp_query(prompt)
    if result:
        print(result)
        sys.exit(0)
    sys.exit(1)
