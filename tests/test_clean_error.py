"""Provider Test toasts should not dump OpenAI dicts or echo API keys."""

from __future__ import annotations

from src.api.routes.config import _clean_error


class _AuthError(Exception):
    status_code = 401
    body = {
        "error": {
            "message": "Authentication Fails, Your api key: 212 is invalid",
            "type": "authentication_error",
            "param": None,
            "code": "invalid_request_error",
        }
    }


def test_openai_401_blob_becomes_invalid_api_key():
    raw = (
        "Error code: 401 - {'error': {'message': "
        "'Authentication Fails, Your api key: 212 is invalid', "
        "'type': 'authentication_error', 'param': None, "
        "'code': 'invalid_request_error'}}"
    )
    msg = _clean_error(Exception(raw))
    assert msg == "Invalid API key"
    assert "212" not in msg
    assert "Error code" not in msg


def test_openai_exception_body_is_used():
    msg = _clean_error(_AuthError(str(_AuthError.body)))
    assert msg == "Invalid API key"


def test_sk_token_is_redacted_when_not_mapped():
    msg = _clean_error(Exception("upstream said sk-abcdefghijklmnopqrstuvwxyz is banned"))
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in msg
    assert "sk-…" in msg


def test_html_body_is_stripped():
    msg = _clean_error(Exception("gateway <html><body>nginx</body></html>"))
    assert "<html" not in msg.lower()
    assert "gateway" in msg
