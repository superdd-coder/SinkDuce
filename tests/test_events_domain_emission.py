"""Phase-5 domain emission: collections, meetings, notes, hot words, people.

Design: development_doc/2026-09-16-multi-device-state-sync-design.md (§5).
Each domain's action routes must broadcast so a peer's cached view can be
invalidated. Handlers are driven directly with their stores stubbed: a real call
would write into data/ or hit Qdrant, which tests must not do.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.events.bus import EventBus


def _run(coro):
    """Own the loop; asyncio.run would poison asyncio.get_event_loop() for later tests."""
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


# ── Collections ─────────────────────────────────────────────────
# These handlers are sync `def`, so Starlette runs them in a threadpool:
# publishing from here is the thread-safety case the bus must handle.


class TestCollectionEmission:
    def _stub(self, monkeypatch, *, name="Probe"):
        from src.api.routes import collections as mod

        monkeypatch.setattr(
            mod.collections_store,
            "get_collection_meta",
            lambda cid: {"id": cid, "name": name},
        )
        return mod

    def test_archive_emits_collection_archived(self, monkeypatch, fresh_bus):
        mod = self._stub(monkeypatch)
        monkeypatch.setattr(mod.collections_store, "set_archived", lambda cid, v: True)

        mod.archive_collection("col-1")

        evs = events_of(fresh_bus, "collection.archived")
        assert len(evs) == 1
        assert evs[0].domain == "collections"
        assert evs[0].scope.get("collection_id") == "col-1"

    def test_unarchive_emits_collection_unarchived(self, monkeypatch, fresh_bus):
        mod = self._stub(monkeypatch)
        monkeypatch.setattr(mod.collections_store, "set_archived", lambda cid, v: True)

        mod.unarchive_collection("col-1")
        assert len(events_of(fresh_bus, "collection.unarchived")) == 1

    def test_rename_emits_collection_renamed(self, monkeypatch, fresh_bus):
        mod = self._stub(monkeypatch, name="Old")
        monkeypatch.setattr(
            mod.collections_store, "update_collection_meta", lambda cid, **kw: True
        )

        mod.rename_collection("col-1", {"name": "New"})
        evs = events_of(fresh_bus, "collection.renamed")
        assert len(evs) == 1
        assert evs[0].scope.get("collection_id") == "col-1"

    def test_rename_to_same_name_does_not_emit(self, monkeypatch, fresh_bus):
        # A no-op must not wake every client.
        mod = self._stub(monkeypatch, name="Same")
        mod.rename_collection("col-1", {"name": "Same"})
        assert fresh_bus.snapshot_since(0) == []

    def test_missing_collection_does_not_emit(self, monkeypatch, fresh_bus):
        from src.api.routes import collections as mod

        monkeypatch.setattr(mod.collections_store, "get_collection_meta", lambda cid: None)
        mod.archive_collection("nope")
        assert fresh_bus.snapshot_since(0) == []


# ── Notes ───────────────────────────────────────────────────────


class TestNoteEmission:
    def test_create_note_emits_note_created(self, monkeypatch, fresh_bus):
        from src.notes import routes as mod

        note = SimpleNamespace(
            id="n1",
            title="Probe",
            collection="col-1",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        monkeypatch.setattr(
            mod.store, "create_note", lambda collection, title: note
        )

        _run(mod.create_note("col-1", {"title": "Probe"}))

        evs = events_of(fresh_bus, "note.created")
        assert len(evs) == 1
        assert evs[0].domain == "notes"
        assert evs[0].scope.get("note_id") == "n1"
        assert evs[0].scope.get("collection_id") == "col-1"

    def test_delete_note_emits_note_deleted(self, monkeypatch, fresh_bus):
        from src.notes import routes as mod

        monkeypatch.setattr(mod.store, "delete_note", lambda nid: True)
        monkeypatch.setattr(
            mod.services,
            "db",
            SimpleNamespace(collection_exists=lambda c: False),
            raising=False,
        )
        monkeypatch.setattr(
            "src.file_mgmt.service.unregister_files_for_source",
            lambda *a, **kw: None,
            raising=False,
        )

        _run(mod.delete_note("col-1", "n1"))
        evs = events_of(fresh_bus, "note.deleted")
        assert len(evs) == 1
        assert evs[0].scope.get("note_id") == "n1"

    def test_missing_note_delete_does_not_emit(self, monkeypatch, fresh_bus):
        from src.notes import routes as mod

        monkeypatch.setattr(mod.store, "delete_note", lambda nid: False)
        monkeypatch.setattr(
            mod.services,
            "db",
            SimpleNamespace(collection_exists=lambda c: False),
            raising=False,
        )
        monkeypatch.setattr(
            "src.file_mgmt.service.unregister_files_for_source",
            lambda *a, **kw: None,
            raising=False,
        )

        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _run(mod.delete_note("col-1", "nope"))
        assert fresh_bus.snapshot_since(0) == []


# ── Hot words ───────────────────────────────────────────────────


class TestHotWordsEmission:
    def _lib(self, lid="lib1", name="Probe"):
        from src.hot_words.models import HotWordsLibrary

        return HotWordsLibrary(id=lid, name=name)

    def _stub_public(self, monkeypatch, mod):
        monkeypatch.setattr(mod.store, "get_pinned_library_ids", lambda: [])
        monkeypatch.setattr(mod.store, "library_summary", lambda lib, pinned_ids=None: {})

    def test_create_library_emits(self, monkeypatch, fresh_bus):
        from src.hot_words import routes as mod

        self._stub_public(monkeypatch, mod)
        monkeypatch.setattr(mod.store, "create_library", lambda **kw: self._lib())
        _run(mod.create_library({"name": "Probe", "words": []}))

        evs = events_of(fresh_bus, "hot_words.library_created")
        assert len(evs) == 1
        assert evs[0].domain == "hot_words"
        assert evs[0].scope.get("library_id") == "lib1"

    def test_update_library_emits(self, monkeypatch, fresh_bus):
        from src.hot_words import routes as mod

        self._stub_public(monkeypatch, mod)
        monkeypatch.setattr(
            mod.store, "update_library", lambda lid, **kw: self._lib(lid, "Renamed")
        )
        _run(mod.update_library("lib1", {"name": "Renamed"}))
        assert len(events_of(fresh_bus, "hot_words.library_updated")) == 1

    def test_delete_library_emits(self, monkeypatch, fresh_bus):
        from src.hot_words import routes as mod

        monkeypatch.setattr(mod.store, "delete_library", lambda lid: True)
        _run(mod.delete_library("lib1"))
        assert len(events_of(fresh_bus, "hot_words.library_deleted")) == 1

    def test_delete_missing_library_does_not_emit(self, monkeypatch, fresh_bus):
        from src.hot_words import routes as mod

        monkeypatch.setattr(mod.store, "delete_library", lambda lid: False)
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _run(mod.delete_library("nope"))
        assert fresh_bus.snapshot_since(0) == []


# ── People ──────────────────────────────────────────────────────


class TestPeopleEmission:
    def test_create_speaker_emits(self, monkeypatch, fresh_bus):
        from src.speakers import routes as mod

        person = SimpleNamespace(id="p1", display_name="Probe", nickname=None)
        monkeypatch.setattr(mod.store, "create_person", lambda name, dis: person)
        monkeypatch.setattr(
            mod.store, "person_public_dict", lambda p: {"id": p.id, "display_name": p.display_name}
        )
        _run(mod.create_speaker({"display_name": "Probe"}))

        evs = events_of(fresh_bus, "person.created")
        assert len(evs) == 1
        assert evs[0].domain == "people"
        assert evs[0].scope.get("person_id") == "p1"

    def test_patch_speaker_emits(self, monkeypatch, fresh_bus):
        from src.speakers import routes as mod

        person = SimpleNamespace(id="p1", display_name="Old", nickname=None)
        monkeypatch.setattr(mod.store, "get_person", lambda pid: person)
        monkeypatch.setattr(mod.store, "update_person", lambda pid, **kw: person)
        monkeypatch.setattr(
            mod.store, "person_public_dict", lambda p: {"id": p.id, "display_name": p.display_name}
        )
        _run(mod.patch_speaker("p1", {"display_name": "New"}))
        assert len(events_of(fresh_bus, "person.updated")) == 1

    def test_delete_speaker_emits(self, monkeypatch, fresh_bus):
        from src.speakers import routes as mod

        person = SimpleNamespace(id="p1", display_name="X", recent=[])
        monkeypatch.setattr(mod.store, "get_person", lambda pid: person)
        monkeypatch.setattr(mod.store, "delete_person", lambda pid: True)
        _run(mod.delete_speaker("p1"))
        evs = events_of(fresh_bus, "person.deleted")
        assert len(evs) == 1
        assert evs[0].scope.get("person_id") == "p1"

    def test_delete_missing_speaker_does_not_emit(self, monkeypatch, fresh_bus):
        from src.speakers import routes as mod

        monkeypatch.setattr(mod.store, "get_person", lambda pid: None)
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _run(mod.delete_speaker("nope"))
        assert fresh_bus.snapshot_since(0) == []


# ── Config ──────────────────────────────────────────────────────


class TestConfigEmission:
    """Every mutating config endpoint emits config.updated via the write decorator.

    The config router wraps 22 mutating endpoints in `@_serialize_writes`; emitting
    from that one seam covers them all instead of 22 hand edits.
    """

    def test_config_write_emits_config_updated(self, fresh_bus):
        from src.api.routes.config import _serialize_writes

        async def handler(*args, **kwargs):
            return {"ok": True}

        wrapped = _serialize_writes(handler)
        _run(wrapped())

        evs = events_of(fresh_bus, "config.updated")
        assert len(evs) == 1, "a config write must notify peers"
        assert evs[0].domain == "config"

    def test_config_emit_does_not_change_the_return_value(self, fresh_bus):
        from src.api.routes.config import _serialize_writes

        async def handler(*args, **kwargs):
            return {"value": 42}

        assert _run(_serialize_writes(handler)()) == {"value": 42}

    def test_config_emit_propagates_exceptions(self, fresh_bus):
        # A failed write must not be reported as a successful change.
        from src.api.routes.config import _serialize_writes

        async def boom(*args, **kwargs):
            raise ValueError("nope")

        wrapped = _serialize_writes(boom)
        with pytest.raises(ValueError):
            _run(wrapped())
        assert events_of(fresh_bus, "config.updated") == []


# ── Work-started signals (spec §11) ─────────────────────────────
# ── Work-started signals (spec §11) ─────────────────────────────
#
# A route that only *starts* background work must announce it, so a peer knows to
# poll that scope until quiet. These patches use monkeypatch (auto-undone), not
# direct attribute assignment, so nothing leaks into other test files.


class _BackgroundCollector:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn):
        self.tasks.append(fn)


class TestNoteWorkStartedEmission:
    def _note(self):
        return SimpleNamespace(
            id="n1",
            title="Probe",
            collection="col-1",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    def test_propagation_emits_note_propagation_started(
        self, monkeypatch, fresh_bus
    ):
        from src.notes import routes as mod

        monkeypatch.setattr(mod.store, "get_note", lambda nid: self._note())
        bg = _BackgroundCollector()
        # 直调路由须显式传 auto/wait（Query() 默认对象直调不解析；批次 4 加的参数）
        _run(mod.trigger_propagation("col-1", "n1", bg, auto=True, wait=False))

        evs = events_of(fresh_bus, "note.propagation_started")
        assert len(evs) == 1, "starting propagation must announce itself"
        assert evs[0].scope.get("note_id") == "n1"
        assert evs[0].scope.get("collection_id") == "col-1"
        assert bg.tasks, "background work must still be scheduled"

    def test_ingest_emits_note_ingest_started(self, monkeypatch, fresh_bus):
        from src.notes import routes as mod

        monkeypatch.setattr(mod.store, "get_note", lambda nid: self._note())
        monkeypatch.setattr(mod.store, "get_content", lambda nid: "some content")
        monkeypatch.setattr(mod, "_resolve_note_file_id", lambda c, n: None)
        monkeypatch.setattr(
            mod.services,
            "db",
            SimpleNamespace(collection_exists=lambda c: False),
            raising=False,
        )
        bg = _BackgroundCollector()
        _run(mod.ingest_note("col-1", "n1", bg))

        evs = events_of(fresh_bus, "note.ingest_started")
        assert len(evs) == 1, "starting ingest must announce itself"
        assert evs[0].scope.get("note_id") == "n1"
        assert bg.tasks, "background ingest must still be scheduled"

    def test_missing_note_does_not_emit(self, monkeypatch, fresh_bus):
        from src.notes import routes as mod

        monkeypatch.setattr(mod.store, "get_note", lambda nid: None)
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _run(mod.trigger_propagation("col-1", "nope", _BackgroundCollector()))
        assert fresh_bus.snapshot_since(0) == []


class TestPersonProfileStartedEmission:
    def test_profile_regeneration_emits_person_profile_started(
        self, monkeypatch, fresh_bus
    ):
        from src.speakers import routes as mod

        monkeypatch.setattr(
            mod.store, "get_person", lambda pid: SimpleNamespace(id=pid)
        )
        monkeypatch.setattr(
            mod.profile_domain,
            "start_regenerate",
            lambda pid, locale=None, force=True: {"status": "started"},
        )
        _run(mod.regenerate_person_profile("p1", {"locale": "zh-CN"}))

        evs = events_of(fresh_bus, "person.profile_started")
        assert len(evs) == 1, "starting profile regeneration must announce itself"
        assert evs[0].scope.get("person_id") == "p1"

    def test_missing_person_does_not_emit(self, monkeypatch, fresh_bus):
        from src.speakers import routes as mod

        monkeypatch.setattr(mod.store, "get_person", lambda pid: None)
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _run(mod.regenerate_person_profile("nope", {}))
        assert fresh_bus.snapshot_since(0) == []
