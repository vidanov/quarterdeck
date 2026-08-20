"""Shared pytest fixtures.

ISOLATION FIXTURE (session-scoped, autouse)
-------------------------------------------
Every backend module hard-codes its state paths at import time
(backend.config.STATE_DIR, backend.auth.TOKEN_FILE, ...).  Without
intervention, pytest writes into the developer's live ~/.osa-kiro and the
macOS login keychain.

The `isolate_state` fixture below:

1. Builds a temp state root under tmp_path_factory.
2. Monkeypatches every Path constant in the affected modules so it resolves
   inside the temp root instead of ~/.osa-kiro.
3. Replaces auth._keychain_read and auth._keychain_write with in-memory dict
   stubs so no `security` subprocess is ever called.
4. Skips test_auth — that module manages its own token state via monkeypatch
   and must not be interfered with.

The guard test (tests/test_isolation.py) verifies the seam is in place by
asserting that no module-level Path is still inside the real state root.  It
fails if this fixture is removed or if a new constant is added without being
covered here.

LOCAL TOKEN FIXTURE (autouse, unchanged)
-----------------------------------------
Stubs read_local_token to return "" so the local-token gate fails-open for
every module except test_auth (which needs the real gate logic).
"""
import importlib
import inspect
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Modules whose Path constants are redirected to the temp root.
# ---------------------------------------------------------------------------
_STATE_MODULES = [
    "backend.config",
    "backend.auth",
    "backend.tmux_manager",
    "backend.audit",
    "backend.devices",
    "backend.api",   # re-exports config paths as its own module-level names
    "backend.pastes",
]

_REAL_STATE_ROOT = Path.home() / ".osa-kiro"


def _path_attrs_under(mod, root: Path) -> list[tuple[str, Path]]:
    """Return (attr, value) pairs for every Path in *mod* inside *root*."""
    results = []
    for attr, val in inspect.getmembers(mod):
        if not isinstance(val, Path):
            continue
        try:
            val.relative_to(root)
            results.append((attr, val))
        except ValueError:
            pass
    return results


@pytest.fixture(scope="session", autouse=True)
def isolate_state(tmp_path_factory):
    """Redirect all backend state paths to a temp directory and stub the keychain.

    Skipped for test_auth which manages its own state. All other modules get
    the isolated paths automatically via autouse.
    """
    tmp_root = tmp_path_factory.mktemp("osa-kiro-state")

    patches = []

    for mname in _STATE_MODULES:
        try:
            mod = importlib.import_module(mname)
        except ImportError:
            continue
        for attr, real_path in _path_attrs_under(mod, _REAL_STATE_ROOT):
            # Compute the equivalent path under the temp root.
            try:
                rel = real_path.relative_to(_REAL_STATE_ROOT)
                tmp_path = tmp_root / rel
            except ValueError:
                # Shouldn't happen given we filtered above, but be safe.
                continue
            patches.append(patch.object(mod, attr, tmp_path))

    # Keychain stubs — in-memory dict, no `security` subprocess.
    _fake_keychain: dict[str, str] = {}

    def _fake_read(account: str) -> str:
        return _fake_keychain.get(account, "")

    def _fake_write(account: str, value: str) -> bool:
        _fake_keychain[account] = value
        return True

    def _fake_delete(account: str) -> None:
        _fake_keychain.pop(account, None)

    from backend import auth as _auth
    patches.append(patch.object(_auth, "_keychain_read", _fake_read))
    patches.append(patch.object(_auth, "_keychain_write", _fake_write))
    patches.append(patch.object(_auth, "_keychain_delete", _fake_delete))

    for p in patches:
        p.start()

    yield tmp_root

    for p in patches:
        try:
            p.stop()
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Local-token gate — fail-open for all modules except test_auth.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_local_token_for_api_tests(request):
    """For API tests: stub read_local_token to return '' so the gate fails-open.

    test_auth manages its own token state via monkeypatch and must not be
    affected. All other test modules need the gate open so they can reach
    endpoints without setting up a token.
    """
    if "test_auth" in request.fspath.basename:
        yield
        return
    from backend import auth
    with patch.object(auth, "read_local_token", return_value=""), \
         patch.object(auth, "_local_token_cache", ""):
        yield


# ---------------------------------------------------------------------------
# Sessions cache — every test starts from a cold one.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def cold_sessions_cache():
    """Drop the /api/sessions cache before each test.

    The listing is served from a background-refreshed cache, so without this a
    test's patches never run: it gets the scan a previous test left behind and
    asserts against someone else's state. Using the production invalidator
    rather than reaching into the dict means these tests also fail if
    invalidation stops working — which it had, silently, when it cleared only
    the timestamp and the read path never looked at one.
    """
    try:
        from backend import api
    except ImportError:
        yield
        return
    api._invalidate_sessions_cache()
    yield
