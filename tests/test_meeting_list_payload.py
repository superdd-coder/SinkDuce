"""GET /api/meetings strips speaker_slots vectors (list-payload slimming).

The frontend only reads speaker_slots_status/_ms and speaker_matches; the
slot vectors are consumed server-side via the store. Shipping them made the
list ~260KB and jammed the client main thread on every Meeting-view
activation, delaying the view-switch fade-in.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.meeting import store as meeting_store
from src.meeting.models import Meeting
from src.meeting.routes import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_list_meetings_drops_speaker_slots_but_keeps_metadata():
    meeting = Meeting(
        id="m1",
        title="T",
        speaker_slots={"spk_0": {"embedding": [0.1] * 512, "speech_sec": 3.2}},
        speaker_slots_status="ready",
        speaker_slots_ms=1234,
        speaker_matches={"spk_0": {"auto": True}},
    )
    with patch.object(meeting_store, "list_meetings", return_value=[meeting]):
        res = _client().get("/api/meetings")
    assert res.status_code == 200
    (item,) = res.json()
    assert "speaker_slots" not in item
    assert item["speaker_slots_status"] == "ready"
    assert item["speaker_slots_ms"] == 1234
    assert item["speaker_matches"]["spk_0"]["auto"] is True


def test_get_meeting_single_still_serializes_without_slots():
    """GET /meetings/{id} already stripped slots via _serialize_meeting — pin it."""
    meeting = Meeting(
        id="m1",
        title="T",
        speaker_slots={"spk_0": {"embedding": [0.1] * 512}},
        speaker_slots_status="ready",
    )
    with patch.object(meeting_store, "get_meeting", return_value=meeting):
        res = _client().get("/api/meetings/m1")
    assert res.status_code == 200
    body = res.json()
    assert "speaker_slots" not in body
    assert body["speaker_slots_status"] == "ready"
