"""Desktop app must open http(s) links in the system browser, not the WebView."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.config import router as config_router
from src.desktop_open import allowed_external_url, open_external_url


@pytest.mark.parametrize(
    "url",
    [
        "https://mineru.net/apiManage/token",
        "https://tavily.com",
        "http://example.com/a",
        "https://github.com/superdd-coder/sinkduce/releases/download/v1.2.0/SinkDuce-macos-arm64-v1.2.0.dmg",
    ],
)
def test_https_links_are_allowed(url: str):
    assert allowed_external_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "data:text/html,hi",
        "mineru.net",
        "ftp://files.example",
        "/relative/path",
    ],
)
def test_non_http_urls_are_rejected(url: str):
    assert allowed_external_url(url) is None


def test_open_external_url_uses_macos_open(monkeypatch):
    monkeypatch.setattr("src.desktop_open.sys.platform", "darwin")
    with patch("src.desktop_open.subprocess.Popen") as popen:
        open_external_url("https://mineru.net/apiManage/token")
        popen.assert_called_once()
        args = popen.call_args[0][0]
        assert args == ["open", "https://mineru.net/apiManage/token"]


def _client():
    app = FastAPI()
    app.include_router(config_router, prefix="/api")
    return TestClient(app)


def test_open_url_route_forbidden_outside_desktop(monkeypatch):
    monkeypatch.delenv("SINKDUCE_DESKTOP", raising=False)
    res = _client().post(
        "/api/desktop/open-url",
        json={"url": "https://mineru.net/apiManage/token"},
    )
    assert res.status_code == 403


def test_open_url_route_opens_https_on_desktop(monkeypatch):
    monkeypatch.setenv("SINKDUCE_DESKTOP", "1")
    with patch("src.desktop_open.open_external_url") as opener:
        res = _client().post(
            "/api/desktop/open-url",
            json={"url": "https://mineru.net/apiManage/token"},
        )
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    opener.assert_called_once_with("https://mineru.net/apiManage/token")


def test_open_url_route_rejects_javascript(monkeypatch):
    monkeypatch.setenv("SINKDUCE_DESKTOP", "1")
    res = _client().post(
        "/api/desktop/open-url",
        json={"url": "javascript:alert(1)"},
    )
    assert res.status_code == 400
