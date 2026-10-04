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

A kiro-cli process keeps the login it started with and writes it back to the
shared auth DB when it refreshes. A worker that outlived a profile switch or a
new ``kiro-cli login`` therefore put the old account's token back under the new
account's subscription, and every session failed with "The bearer token
included in the request is invalid". So the worker is respawned whenever the
login in the DB is no longer the one it started with.

With the ``internal-profile`` setting, the worker runs on its own private
login instead (see internal_auth.py) and profile switches do not affect it.

Thread safety: a single threading.Lock serialises queries so multiple
_generate_summary_async threads don't interleave prompts on the same session.
"""

import json
import logging
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_lock = threading.Lock()
_session: "ACPSession | None" = None  # type: ignore[name-defined]
_session_id: str = ""
_session_login: tuple[str, str] | None = None

_KIRO_AUTH_DB = Path.home() / "Library" / "Application Support" / "kiro-cli" / "data.sqlite3"


def _internal_auth():
    try:
        from . import internal_auth
    except ImportError:
        import internal_auth  # type: ignore
    return internal_auth


def _current_login() -> tuple[str, str] | None:
    """Identity of the login the worker should run on — no secrets.

    The configured internal profile when there is one, else (SSO start_url,
    subscription ARN) in kiro-cli's shared auth DB. None when the DB cannot be
    read (it is often busy); callers treat that as "unknown", not as a change.
    """
    try:
        key = _internal_auth().login_key()
        if key:
            return key
    except Exception:
        pass
    try:
        con = sqlite3.connect(str(_KIRO_AUTH_DB), timeout=0.5)
        try:
            tok = con.execute(
                "SELECT value FROM auth_kv WHERE key = 'kirocli:odic:token'"
            ).fetchone()
            prof = con.execute(
                "SELECT value FROM state WHERE key = 'api.codewhisperer.profile'"
            ).fetchone()
        finally:
            con.close()
        start_url = json.loads(tok[0]).get("start_url", "") if tok and tok[0] else ""
        arn = json.loads(prof[0]).get("arn", "") if prof and prof[0] else ""
        return (start_url, arn)
    except Exception:
        return None


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
                      extra_args=["--trust-all-tools"], timeout=40.0,
                      env=_internal_auth().env())
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
    global _session, _session_id, _session_login

    with _lock:
        login = _current_login()
        if _session is not None and login and _session_login and login != _session_login:
            log.info("acp_worker: kiro-cli login changed, respawning")
            try:
                _session.stop()
            except Exception:
                pass
            _session = None
        # (Re-)spawn if dead or not yet started
        if _session is None or not _is_alive(_session):
            try:
                _session_login = login
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


def restart() -> None:
    """Kill the worker so the next query starts on the current login.

    Called right after a profile switch or logout: waiting for the next query
    leaves a window in which an in-flight query refreshes and writes the old
    login back. Does not take the lock — a query in flight just fails and
    respawns on the next call. A worker on its own internal login is left
    alone — the shared login does not reach it.
    """
    try:
        if _internal_auth().login_key():
            return
    except Exception:
        pass
    sess = _session
    if sess is not None:
        try:
            sess.stop()
        except Exception:
            pass


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
