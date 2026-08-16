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

# Support two execution contexts:
# 1. In-project dev: __file__ is backend/acp_query.py → parent.parent is repo root
#    → "from backend.acp_session import ACPSession" works.
# 2. Frozen app copy to /tmp: __file__ is /tmp/qd_acp_query.py → parent.parent is /
#    The backend package isn't there, so we also copy acp_session.py alongside
#    and try a direct import as fallback.
_here = Path(__file__).parent
_repo_root = _here.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

try:
    from backend.acp_session import ACPSession  # in-project / bundle  # noqa: E402
except ModuleNotFoundError:
    # Running from /tmp after copy — acp_session.py was copied alongside us
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from acp_session import ACPSession  # type: ignore  # noqa: E402

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
