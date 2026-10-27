"""file_mgmt upload paths must emit task.queued.

Regression guard for the audit finding: the folder view's normal upload goes
through `src/file_mgmt/files.py` (NOT `src/api/routes/documents.py`), so emitting
only in the latter left the reported symptom live — device B saw the uploaded
file (`file.uploaded` is emitted) but never the Ingesting lock, because the lock
is a projection of task state and no task event was published.

Design: development_doc/2026-09-16-multi-device-state-sync-design.md (§5, §9).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.events.bus import EventBus


@pytest.fixture
def fresh_bus(monkeypatch):
    from src.events import bus as bus_module

    test_bus = EventBus()
    monkeypatch.setattr(bus_module, "bus", test_bus)
    return test_bus


def events_of(bus: EventBus, event_type: str):
    return [e for e in bus.snapshot_since(0) if e.type == event_type]


class TestEmitIngestQueued:
    """The shared helper both upload paths call."""

    def test_emits_task_queued_with_scope_and_file_id(self, fresh_bus):
        from src.file_mgmt.events import emit_ingest_queued

        emit_ingest_queued("col-1", file_id="f1", task_id="t1", filename="a.txt")

        evs = events_of(fresh_bus, "task.queued")
        assert len(evs) == 1, "an enqueued ingest must announce itself"
        ev = evs[0]
        assert ev.domain == "tasks"
        assert ev.scope.get("collection_id") == "col-1"
        # file_id keys the peer's lock; task_id lets it correlate the task.
        assert ev.payload["file_id"] == "f1"
        assert ev.payload["task_id"] == "t1"
        assert ev.payload["filename"] == "a.txt"

    def test_carries_request_origin_for_echo_suppression(self, fresh_bus):
        from src.events.context import reset_client, set_client
        from src.file_mgmt.events import emit_ingest_queued

        token = set_client("device-a")
        try:
            emit_ingest_queued("col-1", file_id="f1", task_id="t1", filename="a.txt")
        finally:
            reset_client(token)

        assert events_of(fresh_bus, "task.queued")[0].origin.get("client_id") == "device-a"

    def test_missing_file_id_is_tolerated_but_not_keyed(self, fresh_bus):
        # Defensive: a task without a file_id cannot key a lock, but must not raise.
        from src.file_mgmt.events import emit_ingest_queued

        emit_ingest_queued("col-1", file_id=None, task_id="t1", filename="a.txt")
        evs = events_of(fresh_bus, "task.queued")
        assert len(evs) == 1
        assert evs[0].payload["file_id"] is None


class TestUploadPathsCallTheHelper:
    """The two real ingest-enqueueing upload paths must reach the helper."""

    def _seed(self, monkeypatch, tmp_path, collection="col-x"):
        """Minimal real store so the upload pipeline runs end to end.

        Mirrors tests/test_file_mgmt_v2_windows.py: the collection-existence
        check is patched, since that gate reads the global collection registry.
        """
        from unittest.mock import patch

        from src.file_mgmt import store as fm_store

        monkeypatch.setattr(fm_store, "COLLECTIONS_DIR", tmp_path)
        monkeypatch.setattr(
            "src.file_mgmt.access._validate_collection", lambda _x: None
        )
        with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
            fm_store.init_collection_db(collection)
        return collection

    def test_folder_upload_emits_task_queued(self, monkeypatch, tmp_path, fresh_bus):
        from src.file_mgmt import files as fm_files

        collection = self._seed(monkeypatch, tmp_path)

        # Stub the task manager so no real ingestion starts.
        created: list[dict] = []

        def fake_create_task(**kwargs):
            created.append(kwargs)
            return SimpleNamespace(id="t-fm-1")

        from src.tasks import task_manager as tm_mod

        monkeypatch.setattr(tm_mod, "create_task", fake_create_task)

        from unittest.mock import patch

        with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
            fm_files.upload_file_to_folder(
                collection_id=collection,
                folder_id=None,
                file_bytes=b"hello audit",
                filename="probe.txt",
            )

        # The upload itself emits file.uploaded; the enqueue must emit task.queued.
        assert events_of(fresh_bus, "file.uploaded"), "upload event expected"
        assert created, "a task should have been created"
        queued = events_of(fresh_bus, "task.queued")
        assert queued, (
            "folder upload must emit task.queued, or peers never draw the lock"
        )
        assert queued[0].scope.get("collection_id") == collection
        assert queued[0].payload.get("file_id"), "must key the lock by file_id"

    def test_version_upload_emits_task_queued(self, monkeypatch, tmp_path, fresh_bus):
        from unittest.mock import patch

        from src.file_mgmt import files as fm_files

        collection = self._seed(monkeypatch, tmp_path)

        created: list[dict] = []

        def fake_create_task(**kwargs):
            created.append(kwargs)
            return SimpleNamespace(id="t-ver-1")

        from src.tasks import task_manager as tm_mod

        monkeypatch.setattr(tm_mod, "create_task", fake_create_task)

        with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
            detail = fm_files.upload_file_to_folder(
                collection_id=collection,
                folder_id=None,
                file_bytes=b"v1",
                filename="v.txt",
            )
        file_id = getattr(detail, "file_id", None) or detail["file_id"]

        before = len(events_of(fresh_bus, "task.queued"))
        with patch("src.file_mgmt.access._validate_collection", lambda _x: None):
            fm_files.upload_file_version(
                collection_id=collection,
                file_id=file_id,
                file_bytes=b"v2",
                filename="v.txt",
            )

        assert len(events_of(fresh_bus, "task.queued")) > before, (
            "a new version enqueues ingest too and must emit task.queued"
        )
