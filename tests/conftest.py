"""Shared pytest fixtures.

test_api exercises endpoint logic, not auth. The local-token gate rejects
mutating loopback requests that lack X-Local-Token, but test_api's module-level
client has no header. Mock read_local_token to return "" so the gate fails-open
(same behaviour as dev mode / first run before ensure_local_token), letting
test_api reach the endpoints it is testing.

test_auth manages its own token state via monkeypatch and must not be affected,
so this fixture only applies to test_api.
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


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
