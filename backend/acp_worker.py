"""Persistent ACP worker — one kiro-cli acp subprocess, one session, reused forever.

Replaces the one-shot acp_query.py subprocess pattern that created a new
kiro-cli session (and a new ~/.kiro/sessions/cli/ entry) for every summary
request.  With this module, all summary queries share a single session,
eliminating the "808 Summarise what this..." archive pollution.

Usage (from api.py)::

    from .acp_worker import query

    text = query(prompt_text, timeout=40.0)

The worker is lazy-started on first call and survives Quarterdeck's lifetime.
If the subprocess dies or the session becomes unresponsive, ``query()`` spawns
a fresh one automatically.

Thread safety: a single threading.Lock serialises queries so multiple
_generate_summary_async threads don't interleave prompts on the same session.
"""

import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_lock = threading.Lock()
_session: "ACPSession | None" = None  # type: ignore[name-defined]
_session_id: str = ""

# Keep the import lazy so this module can be imported before ACPSession's
# dependencies are resolved (frozen bundle path setup, etc.).
def _get_acp_session_class():
    try:
        from .acp_session import ACPSession
        return ACPSession
    except ImportError:
        # Running outside a package (e.g. tests)
        import sys
        _here = Path(__file__).parent
        if str(_here) not in sys.path:
            sys.path.insert(0, str(_here))
        from acp_session import ACPSession  # type: ignore
        return ACPSession


def _spawn() -> tuple["ACPSession", str]:  # type: ignore[name-defined]
    """Start a fresh ACP subprocess and create one session. Returns (session, sid)."""
    ACPSession = _get_acp_session_class()
    sess = ACPSession(engine="v2", model="auto",
                      extra_args=["--trust-all-tools"], timeout=40.0)
    sess.start()
    sess.initialize(client_name="quarterdeck-worker")
    sid = sess.new_session(cwd=str(Path.home()))
    log.debug("acp_worker: spawned new session %s", sid)
    return sess, sid


def _is_alive(sess) -> bool:
    try:
        return sess._proc is not None and sess._proc.poll() is None
    except Exception:
        return False


def query(prompt: str, timeout: float = 40.0) -> str:
    """Send *prompt* to the persistent ACP session and return the text response.

    Automatically restarts the worker if the subprocess has died.
    Serialises concurrent callers so prompts don't interleave.
    """
    global _session, _session_id

    with _lock:
        # (Re-)spawn if dead or not yet started
        if _session is None or not _is_alive(_session):
            try:
                _session, _session_id = _spawn()
            except Exception as exc:
                log.warning("acp_worker: spawn failed: %s", exc)
                return ""

        try:
            return _session.collect_response(_session_id, prompt, timeout=timeout)
        except Exception as exc:
            log.warning("acp_worker: query failed (%s), will respawn next call", exc)
            # Mark dead so next call respawns
            try:
                _session.stop()
            except Exception:
                pass
            _session = None
            _session_id = ""
            return ""


def shutdown() -> None:
    """Stop the worker subprocess on clean application exit."""
    global _session, _session_id
    with _lock:
        if _session is not None:
            try:
                _session.stop()
            except Exception:
                pass
            _session = None
            _session_id = ""
