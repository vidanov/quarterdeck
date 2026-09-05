"""Every JSONResponse error path must actually return, not raise NameError.

backend/api.py used JSONResponse in 19 places while importing only
FileResponse, Response and StreamingResponse. Each of those paths raised
`NameError: name 'JSONResponse' is not defined` and surfaced as HTTP 500 —
including /api/intake's "template not found" and "missing required vars"
branches, which is what the app's bare "Alert HTTP 500" was.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import backend.api as api_mod


def test_json_response_is_importable_from_the_module():
    """The name has to resolve in the module's own globals, not just fastapi."""
    assert "JSONResponse" in vars(api_mod), "api.py does not import JSONResponse"


def test_dev_token_returns_404_outside_dev_mode(monkeypatch):
    """Previously raised NameError and became a 500."""
    monkeypatch.delenv("DECK_DEV", raising=False)

    class _Req:
        client = type("c", (), {"host": "127.0.0.1"})()
        headers: dict = {}

    resp = api_mod.dev_token(_Req())
    assert resp.status_code == 404


def test_every_json_response_call_site_can_resolve_the_name():
    """Guards the import against a future tidy-up that drops it again."""
    source = Path(api_mod.__file__).read_text()
    assert "JSONResponse" in source.split("\n")[19], "import line moved — update this test"
    assert source.count("JSONResponse(") >= 19
