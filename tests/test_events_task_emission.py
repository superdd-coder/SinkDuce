"""Task-domain emission: enqueuing and task actions must notify other clients.

Design: development_doc/2026-09-16-multi-device-state-sync-design.md (§5, §9).
The Ingesting lock on device B is derived from these signals plus
GET /documents/tasks, so a missing emit means a lock that never appears.

The route is invoked directly with the task manager stubbed: a real enqueue would
start actual ingestion and write into data/, which tests must not do.
"""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from starlette.datastructures import UploadFile

from src.events.bus import EventBus


def _run_async(coro):
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


@pytest.fixture
def stubbed_upload(monkeypatch, tmp_path):
    """Stub the task manager and the file directory so nothing real is touched."""
    from src.api.routes import documents as mod

    created: list[dict] = []

    def fake_create_task(**kwargs):
        created.append(kwargs)
        task_id = f"t{len(created)}"
        return SimpleNamespace(id=task_id, to_dict=lambda: {"id": task_id, **kwargs})

    monkeypatch.setattr(mod.task_manager, "create_task", fake_create_task)
    monkeypatch.setattr(mod, "_files_dir", lambda collection_id: tmp_path / collection_id)
    # Collection resolution: pass an id straight through.
    monkeypatch.setattr(mod.collection_store, "get_collection_meta", lambda c: None)
    monkeypatch.setattr(mod.collection_store, "find_collection_by_name", lambda c: None)
    monkeypatch.setattr(
        mod.services, "db", SimpleNamespace(collection_exists=lambda c: False), raising=False
    )
    return created


def _upload(filename: str, content: bytes = b"hello"):
    return UploadFile(file=io.BytesIO(content), filename=filename)


class TestUploadEmitsTaskQueued:
    def test_upload_emits_task_queued_scoped_to_collection(self, fresh_bus, stubbed_upload):
        from src.api.routes.documents import upload_document

        _run_async(upload_document(files=[_upload("probe.txt")], collection="col-x"))

        events = [e for e in fresh_bus.snapshot_since(0) if e.type == "task.queued"]
        assert len(events) == 1, "upload must emit exactly one task.queued"
        ev = events[0]
        assert ev.domain == "tasks"
        assert ev.scope.get("collection_id") == "col-x"

    def test_payload_carries_file_id_and_task_id(self, fresh_bus, stubbed_upload):
        # file_id keys the lock; task_id lets a peer correlate the task.
        from src.api.routes.documents import upload_document

        _run_async(upload_document(files=[_upload("probe.txt")], collection="col-x"))

        ev = [e for e in fresh_bus.snapshot_since(0) if e.type == "task.queued"][0]
        assert ev.payload.get("file_id"), "payload must carry file_id"
        assert ev.payload.get("task_id") or ev.payload.get("id")

    def test_multiple_files_emit_one_event_each(self, fresh_bus, stubbed_upload):
        from src.api.routes.documents import upload_document

        _run_async(
            upload_document(
                files=[_upload("a.txt"), _upload("b.txt")], collection="col-x"
            )
        )
        events = [e for e in fresh_bus.snapshot_since(0) if e.type == "task.queued"]
        assert len(events) == 2

    def test_event_reaches_a_live_subscriber(self, fresh_bus, stubbed_upload):
        from src.api.routes.documents import upload_document

        sub = fresh_bus.subscribe()
        _run_async(upload_document(files=[_upload("probe.txt")], collection="col-x"))
        assert sub.queue.get(timeout=1).type == "task.queued"

    def test_upload_event_is_attributed_to_the_requesting_client(
        self, fresh_bus, stubbed_upload
    ):
        # Without origin, the uploading device cannot recognise (and skip) its echo.
        from src.api.routes.documents import upload_document
        from src.events.context import reset_client, set_client

        token = set_client("device-a")
        try:
            _run_async(upload_document(files=[_upload("probe.txt")], collection="col-x"))
        finally:
            reset_client(token)

        ev = [e for e in fresh_bus.snapshot_since(0) if e.type == "task.queued"][0]
        assert ev.origin.get("client_id") == "device-a"
