"""CV-03: scoped routes raise HTTPException instead of HTTP 200 + {error}."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

SCOPED_ROUTE_FILES = [
    SRC / "meeting" / "routes.py",
    SRC / "hot_words" / "routes.py",
]

_RETURN_ERROR = re.compile(r"""return\s+\{\s*['"]error['"]""")


def _return_error_hits(path: Path) -> list[str]:
    hits: list[str] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if _RETURN_ERROR.search(line):
            hits.append(f"{path.name}:{i}:{line.strip()}")
    return hits


def test_scoped_routes_do_not_return_error_dicts():
    leftover: list[str] = []
    for path in SCOPED_ROUTE_FILES:
        leftover.extend(_return_error_hits(path))
    assert leftover == []


def test_realtime_ws_still_sends_error_json():
    """WebSocket is not HTTP 200 — keep the existing payload contract."""
    text = (SRC / "meeting" / "routes.py").read_text(encoding="utf-8")
    assert '{"error": "No active realtime transcription provider"}' in text


def test_config_unknown_section_and_missing_provider_use_http_exception():
    text = (SRC / "api" / "routes" / "config.py").read_text(encoding="utf-8")
    assert "raise HTTPException(400, f\"Unknown config section: {req.section}\")" in text
    assert "raise HTTPException(404, f\"Provider '{provider_id}' not found\")" in text
    assert _RETURN_ERROR.search(text) is None


def test_config_provider_tests_still_return_success_false():
    """Connectivity tests stay HTTP 200 + {success: false, error}."""
    text = (SRC / "api" / "routes" / "config.py").read_text(encoding="utf-8")
    assert 'return {"success": False, "error":' in text


def test_hot_words_missing_library_is_404_detail():
    from src.hot_words.routes import router

    app = FastAPI()
    app.include_router(router)
    resp = TestClient(app).get("/api/hot-words/cv03-missing-library")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Hot words library not found"}


def test_hot_words_create_requires_name():
    from src.hot_words.routes import router

    app = FastAPI()
    app.include_router(router)
    resp = TestClient(app).post("/api/hot-words", json={"name": "  "})
    assert resp.status_code == 400
    assert resp.json() == {"detail": "Name is required"}


def test_meeting_missing_is_404_detail():
    from src.meeting.routes import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    resp = TestClient(app).get("/api/meetings/cv03-missing-meeting")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Meeting not found"}


def test_config_unknown_section_is_400_detail():
    from src.api.routes.config import router

    app = FastAPI()
    app.include_router(router)
    resp = TestClient(app).put(
        "/config",
        json={"section": "not_a_real_section", "data": {}},
    )
    assert resp.status_code == 400
    assert resp.json() == {"detail": "Unknown config section: not_a_real_section"}
