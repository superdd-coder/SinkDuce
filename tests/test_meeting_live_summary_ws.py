"""WS wiring + REST endpoint for the in-meeting live summary.

Run: pytest tests/test_meeting_live_summary_ws.py -v --tb=short
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock


class _FakeRealtimeProvider:
    """Emits one FINAL segment every 2nd PCM frame."""

    def __init__(self):
        self.frames = 0
        self.on_segment = None

    async def start(self, on_segment, hot_words=None, language_hints=None, translation_target=None):
        self.on_segment = on_segment

    async def send_frame(self, data):
        self.frames += 1
        if self.frames % 2 == 0:
            i = self.frames // 2
            seg = SimpleNamespace(
                start=float((i - 1) * 5),
                end=float((i - 1) * 5 + 4),
                text=f"line {i}",
                speaker_id="spk:0",
            )
            self.on_segment(seg, True, f"k{i}")

    async def stop(self):
        pass


class _FakeLLM:
    def __init__(self):
        self.calls = 0

    def generate(self, prompt, system="", **kw):
        self.calls += 1
        return json.dumps(
            {"topic": "Planning", "add": [{"kind": "decision", "text": "Ship it"}]}
        )


def _recv_until(ws, pred, limit=40):
    for _ in range(limit):
        msg = ws.receive_json()
        if pred(msg):
            return msg
    raise AssertionError("expected message not received")


def _recv_final_transcripts(ws, count):
    """Sync barrier: wait until the server has processed (and ingested) N finals."""
    seen = 0
    while seen < count:
        msg = ws.receive_json()
        if msg.get("type") == "transcript" and msg.get("is_final"):
            seen += 1


def _setup(monkeypatch, tmp_path):
    import src.meeting.store as meeting_store
    from src.meeting import live_summary as live_summary_mod

    monkeypatch.setattr(meeting_store, "MEETINGS_DIR", tmp_path)
    meeting = meeting_store.create_meeting("ws live summary")

    fake_llm = _FakeLLM()
    fake_provider = _FakeRealtimeProvider()

    fake_service = MagicMock()
    fake_service.get_active_realtime_provider_meta.return_value = {
        "id": "x",
        "adapter": "fake",
        "name": "Fake",
        "model": "m",
    }
    fake_service.get_active_realtime_provider.return_value = fake_provider
    monkeypatch.setattr("src.meeting.routes.meeting_service", fake_service)
    monkeypatch.setattr(live_summary_mod, "resolve_live_summary_llm", lambda: fake_llm)
    return meeting, fake_llm, fake_provider, live_summary_mod


def test_ws_enable_round_snapshot_and_rest(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.main import app

    meeting, fake_llm, _provider, live_summary_mod = _setup(monkeypatch, tmp_path)
    client = TestClient(app)
    try:
        with client.websocket_connect(
            f"/api/meetings/{meeting.id}/realtime-transcribe"
        ) as ws:
            assert ws.receive_json()["type"] == "provider"
            assert _recv_until(ws, lambda m: m.get("type") == "ready")

            ws.send_json({"action": "live_summary", "enabled": True})
            status = _recv_until(ws, lambda m: m.get("type") == "live_summary_status")
            assert status["engine"] == "running"
            snap = _recv_until(ws, lambda m: m.get("type") == "live_summary")
            assert snap["state"]["round"] == 0

            # 4 PCM frames → 2 final segments buffered server-side
            for _ in range(4):
                ws.send_bytes(b"\x00\x01")
            _recv_final_transcripts(ws, 2)

            eng = live_summary_mod.get_engine(meeting.id)
            assert eng is not None
            assert eng.run_round() is True

            updated = _recv_until(
                ws,
                lambda m: m.get("type") == "live_summary" and m["state"]["round"] >= 1,
            )
            entries = updated["state"]["entries"]
            assert entries and entries[0]["text"] == "Ship it"
            assert updated["state"]["topic"]["text"] == "Planning"

            ws.send_json({"action": "stop"})
            # handler: provider flush (2s) → finalize → pushes an idle snapshot
            _recv_until(
                ws,
                lambda m: m.get("type") == "live_summary"
                and m["state"]["engine"] == "idle",
            )

        # graceful stop finalizes the engine
        eng = live_summary_mod.get_engine(meeting.id)
        assert eng.state.engine == "idle"

        resp = client.get(f"/api/meetings/{meeting.id}/live-summary")
        assert resp.status_code == 200
        assert resp.json()["state"]["round"] == 1
    finally:
        live_summary_mod.drop_engine(meeting.id)


def test_ws_reset_and_disable(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.main import app

    meeting, fake_llm, _provider, live_summary_mod = _setup(monkeypatch, tmp_path)
    client = TestClient(app)
    try:
        with client.websocket_connect(
            f"/api/meetings/{meeting.id}/realtime-transcribe"
        ) as ws:
            assert _recv_until(ws, lambda m: m.get("type") == "ready")
            ws.send_json({"action": "live_summary", "enabled": True})
            assert _recv_until(
                ws, lambda m: m.get("type") == "live_summary_status"
            )
            _recv_until(ws, lambda m: m.get("type") == "live_summary")

            for _ in range(2):
                ws.send_bytes(b"\x00\x01")
            _recv_final_transcripts(ws, 1)
            eng = live_summary_mod.get_engine(meeting.id)
            assert eng.run_round() is True
            assert _recv_until(
                ws,
                lambda m: m.get("type") == "live_summary" and m["state"]["round"] == 1,
            )

            ws.send_json({"action": "live_summary_reset"})
            reset = _recv_until(
                ws,
                lambda m: m.get("type") == "live_summary"
                and m["state"]["round"] == 0
                and m["state"]["entries"] == [],
            )
            assert reset["state"]["topic"] is None

            ws.send_json({"action": "live_summary", "enabled": False})
            status = _recv_until(ws, lambda m: m.get("type") == "live_summary_status")
            assert status["engine"] == "idle"

            ws.send_json({"action": "stop"})
    finally:
        live_summary_mod.drop_engine(meeting.id)


def test_rest_live_summary_state_and_404(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.main import app

    meeting, *_ , live_summary_mod = _setup(monkeypatch, tmp_path)
    client = TestClient(app)
    try:
        resp = client.get("/api/meetings/does-not-exist/live-summary")
        assert resp.status_code == 404

        resp = client.get(f"/api/meetings/{meeting.id}/live-summary")
        assert resp.status_code == 200
        assert resp.json() == {"state": None}
    finally:
        live_summary_mod.drop_engine(meeting.id)
