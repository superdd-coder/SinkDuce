"""Meeting-domain emission: meeting CRUD and generation starts must notify peers.

Design: development_doc/2026-09-16-multi-device-state-sync-design.md (§5, §11).
Where a route only *starts* async work (transcription, generation), the event
means "work started" and the peer follows up with a scoped poll.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.events.bus import EventBus


def _run(coro):
    """Run a coroutine, or pass a plain value through (some routes are sync)."""
    if not asyncio.iscoroutine(coro):
        return coro
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture
def fresh_bus(monkeypatch):
    from src.events import bus as bus_module

    test_bus = EventBus()
    monkeypatch.setattr(bus_module, "bus", test_bus)
    return test_bus


def events_of(bus: EventBus, event_type: str):
    return [e for e in bus.snapshot_since(0) if e.type == event_type]


def _meeting(mid="m1", **kw):
    base = dict(
        id=mid,
        title="Probe",
        mode="record",
        status=SimpleNamespace(value="created"),
        archived=False,
        hot_words_library_ids=[],
    )
    base.update(kw)
    m = SimpleNamespace(**base)
    m.model_dump = lambda: {"id": mid, "title": base["title"]}
    return m


class TestMeetingEmission:
    def _stub_store(self, monkeypatch, mod, meeting):
        monkeypatch.setattr(mod.store, "create_meeting", lambda **kw: meeting)
        monkeypatch.setattr(mod.store, "update_meeting", lambda mid, **kw: meeting)
        monkeypatch.setattr(mod.store, "get_meeting", lambda mid: meeting)
        monkeypatch.setattr(mod.store, "delete_meeting", lambda mid: True)
        monkeypatch.setattr(mod, "_serialize_meeting", lambda m: {"id": m.id})
        monkeypatch.setattr(mod, "_active_transcription_supports_hot_words", lambda: False)
        monkeypatch.setattr(
            mod.meeting_service, "cleanup_meeting_allocations", lambda m: None
        )
        monkeypatch.setattr(mod.live_summary_mod, "drop_engine", lambda mid: None)

    def test_create_meeting_emits_meeting_created(self, monkeypatch, fresh_bus):
        from src.meeting import routes as mod

        self._stub_store(monkeypatch, mod, _meeting())
        _run(mod.create_meeting({"title": "Probe"}))

        evs = events_of(fresh_bus, "meeting.created")
        assert len(evs) == 1
        assert evs[0].domain == "meetings"
        assert evs[0].scope.get("meeting_id") == "m1"

    def test_update_meeting_emits_meeting_updated(self, monkeypatch, fresh_bus):
        from src.meeting import routes as mod

        self._stub_store(monkeypatch, mod, _meeting())
        _run(mod.update_meeting("m1", {"title": "Renamed"}))

        evs = events_of(fresh_bus, "meeting.updated")
        assert len(evs) == 1
        assert evs[0].scope.get("meeting_id") == "m1"

    def test_delete_meeting_emits_meeting_deleted(self, monkeypatch, fresh_bus):
        from src.meeting import routes as mod

        self._stub_store(monkeypatch, mod, _meeting())
        _run(mod.delete_meeting("m1"))
        assert len(events_of(fresh_bus, "meeting.deleted")) == 1

    def test_archive_emits_meeting_archived(self, monkeypatch, fresh_bus):
        from src.meeting import routes as mod

        self._stub_store(monkeypatch, mod, _meeting())
        _run(mod._set_meeting_archived("m1", True))
        assert len(events_of(fresh_bus, "meeting.archived")) == 1

    def test_unarchive_emits_meeting_unarchived(self, monkeypatch, fresh_bus):
        from src.meeting import routes as mod

        self._stub_store(monkeypatch, mod, _meeting())
        _run(mod._set_meeting_archived("m1", False))
        assert len(events_of(fresh_bus, "meeting.unarchived")) == 1

    def test_invalid_update_does_not_emit(self, monkeypatch, fresh_bus):
        # A rejected request must not wake peers.
        from src.meeting import routes as mod

        self._stub_store(monkeypatch, mod, _meeting())
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _run(mod.update_meeting("m1", {"nonsense": 1}))
        assert fresh_bus.snapshot_since(0) == []
