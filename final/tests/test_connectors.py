import os
import sys
from unittest.mock import MagicMock, patch

PYTHON_SRC = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "src", "main", "python")
)
if PYTHON_SRC not in sys.path:
    sys.path.insert(0, PYTHON_SRC)

import pytest  # noqa: E402

import config  # noqa: E402
import connectors  # noqa: E402
import encryption  # noqa: E402
import intelligence  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_state(tmp_path):
    config.init(str(tmp_path))
    intelligence.init(str(tmp_path))
    intelligence._HEALTH.clear()
    encryption._KEY = None
    yield


def _fake_response(status_code=200, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    return resp


def test_add_and_list_connector_redacts_secret():
    connectors.add_connector("openai", "https://api.openai.com", auth_header="Authorization", auth_value="sk-secret123")
    listed = connectors.list_connectors()
    assert len(listed) == 1
    assert listed[0]["name"] == "openai"
    assert listed[0]["has_secret"] is True
    assert "sk-secret123" not in str(listed)


def test_add_connector_rejects_missing_fields():
    with pytest.raises(ValueError):
        connectors.add_connector("", "https://x.com")
    with pytest.raises(ValueError):
        connectors.add_connector("name", "")


def test_delete_connector():
    connectors.add_connector("temp", "https://x.com")
    assert connectors.delete_connector("temp") is True
    assert connectors.list_connectors() == []
    assert connectors.delete_connector("temp") is False


def test_call_unknown_connector_raises():
    with pytest.raises(connectors.ConnectorError):
        connectors.call("does-not-exist")


def test_call_success_passes_auth_header():
    connectors.add_connector("api", "https://api.example.com", auth_header="X-Key", auth_value="secret")
    with patch("connectors.requests.request", return_value=_fake_response(200, {"ok": True})) as mock_req:
        resp = connectors.call("api", "GET", "/status")
    assert resp.status_code == 200
    called_headers = mock_req.call_args.kwargs["headers"]
    assert called_headers["X-Key"] == "secret"
    assert mock_req.call_args.args[1] == "https://api.example.com/status"


def test_call_retries_on_5xx_then_succeeds():
    connectors.add_connector("flaky", "https://flaky.example.com")
    responses = [_fake_response(503), _fake_response(200, {"ok": True})]
    with patch("connectors.requests.request", side_effect=responses):
        resp = connectors.call("flaky", "GET", "/", attempts=2)
    assert resp.status_code == 200


def test_call_does_not_retry_4xx():
    connectors.add_connector("badreq", "https://x.example.com")
    # 404 is not in _RETRYABLE_STATUS, so it should just be returned, not raise
    with patch("connectors.requests.request", return_value=_fake_response(404)) as mock_req:
        resp = connectors.call("badreq", "GET", "/missing")
    assert resp.status_code == 404
    assert mock_req.call_count == 1


def test_secret_encrypted_when_key_available():
    encryption.init("00" * 32)
    connectors.add_connector("secure", "https://x.com", auth_header="Authorization", auth_value="topsecret")
    stored = config.get("connectors")["secure"]
    assert stored["auth_value_encrypted"] is True
    assert stored["auth_value"] != "topsecret"

    # and it still resolves correctly when actually calling out
    with patch("connectors.requests.request", return_value=_fake_response(200)) as mock_req:
        connectors.call("secure", "GET", "/")
    assert mock_req.call_args.kwargs["headers"]["Authorization"] == "topsecret"


def test_test_connector_reports_failure_cleanly():
    connectors.add_connector("unreachable", "https://nope.invalid")
    with patch("connectors.requests.request", side_effect=ConnectionError("no route")):
        result = connectors.test_connector("unreachable")
    assert result["ok"] is False
    assert "error" in result
