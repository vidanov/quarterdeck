"""Guard test: verify that no backend Path attribute resolves under the real
~/.osa-kiro, and that auth keychain functions are not the real implementations.

This test MUST FAIL on unpatched code. When the isolation fixture in
conftest.py is in place, it must pass. Running it without the fixture is
the canary that the seam exists and is working.

If a new constant is added to a backend module that points at real state,
this test will fail immediately — no silent regression.
"""
import importlib
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# All backend modules that hold Path constants for persistent state.
_STATE_MODULES = [
    "backend.config",
    "backend.auth",
    "backend.tmux_manager",
    "backend.audit",
    "backend.devices",
]

_REAL_STATE_ROOT = Path.home() / ".osa-kiro"


class TestStateIsolation:
    """Every Path constant in the backend must point outside the real state root
    while tests are running. The isolation fixture in conftest.py is responsible
    for redirecting them; this test proves it did so.
    """

    def test_no_backend_path_under_real_state_root(self):
        """No module-level Path attribute may resolve inside ~/.osa-kiro."""
        offenders: list[str] = []
        for mname in _STATE_MODULES:
            try:
                mod = importlib.import_module(mname)
            except ImportError:
                continue
            for attr, val in inspect.getmembers(mod):
                if not isinstance(val, Path):
                    continue
                # Check whether this path is inside the real state root.
                try:
                    val.relative_to(_REAL_STATE_ROOT)
                    offenders.append(f"{mname}.{attr} = {val}")
                except ValueError:
                    pass  # not under ~/.osa-kiro — fine

        assert not offenders, (
            "The following backend Path constants still point at the real ~/.osa-kiro.\n"
            "The isolation fixture in conftest.py must redirect them:\n"
            + "\n".join(f"  {o}" for o in offenders)
        )

    def test_keychain_write_is_stubbed(self):
        """auth._keychain_write must be a stub, not the real security subprocess."""
        from backend import auth

        # The real implementation calls subprocess.run with ["security", ...]
        # A stub will not do that. We detect the real one by source inspection.
        import inspect as _inspect
        src = _inspect.getsource(auth._keychain_write)
        assert "security" not in src, (
            "auth._keychain_write still calls the real macOS keychain.\n"
            "The isolation fixture must replace it with an in-memory stub."
        )

    def test_keychain_read_is_stubbed(self):
        """auth._keychain_read must be a stub, not the real security subprocess."""
        from backend import auth
        import inspect as _inspect
        src = _inspect.getsource(auth._keychain_read)
        assert "security" not in src, (
            "auth._keychain_read still calls the real macOS keychain.\n"
            "The isolation fixture must replace it with an in-memory stub."
        )
