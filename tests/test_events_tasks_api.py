"""GET /api/documents/tasks must expose file_id per task.

Design: development_doc/2026-09-16-multi-device-state-sync-design.md (§9).
The Ingesting lock is a projection of server task state keyed by file_id, so a
peer that receives `task.queued` (or reconciles after a reconnect) can only draw
the lock if the task list identifies which file each task belongs to. file_id
lives in the task's kwargs, and the route is what has access to them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.events.bus import EventBus


@pytest.fixture
def fresh_bus(monkeypatch):
    """Swap the process-wide bus so events don't leak between tests."""
    from src.events import bus as bus_module

    test_bus = EventBus()
    monkeypatch.setattr(bus_module, "bus", test_bus)
    return test_bus


@pytest.fixture
def client(monkeypatch):
    from src.pairing import service as pairing_service
    from src.main import app
    from fastapi.testclient import TestClient

    pairing_service.ensure_local_key()
    # Loopback client address so the pairing gate treats this as a local request
    # (TestClient's default "testclient" host is neither loopback nor a key holder).
    return TestClient(app, base_url="http://127.0.0.1:18900", client=("127.0.0.1", 51111))


class TestTasksExposeFileId:
    def test_task_dict_includes_file_id(self, monkeypatch, client):
        from src.api.routes import documents as mod

        task = SimpleNamespace(
            id="t-1",
            filename="a.txt",
            collection="col-x",
        )

        def fake_to_dict():
            return {"id": "t-1", "filename": "a.txt", "collection": "col-x"}

        def fake_to_dict_with_type(task_type: str):
            d = fake_to_dict()
            d["task_type"] = task_type
            return d

        task.to_dict = fake_to_dict
        task.to_dict_with_type = fake_to_dict_with_type

        monkeypatch.setattr(mod.task_manager, "get_all_tasks", lambda c=None: [task])
        monkeypatch.setattr(
            mod.task_manager,
            "_task_args",
            {"t-1": ("upload", {"file_id": "f-9", "source_label": "a.txt"})},
        )
        monkeypatch.setattr(mod.task_manager, "get_pending_tasks", lambda c=None: [task])
        monkeypatch.setattr(mod.task_manager, "get_processing_tasks", lambda c=None: [])

        resp = client.get("/api/documents/tasks?collection=col-x")
        assert resp.status_code == 200, resp.text
        tasks = resp.json()["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["file_id"] == "f-9"

    def test_task_without_file_id_still_returns(self, monkeypatch, client):
        # Non-upload tasks have no file_id; they must not break the list.
        from src.api.routes import documents as mod

        task = SimpleNamespace(id="t-2", filename="x", collection="col-x")
        task.to_dict = lambda: {"id": "t-2", "filename": "x", "collection": "col-x"}
        task.to_dict_with_type = lambda tt: {**task.to_dict(), "task_type": tt}

        monkeypatch.setattr(mod.task_manager, "get_all_tasks", lambda c=None: [task])
        monkeypatch.setattr(mod.task_manager, "_task_args", {"t-2": ("consolidate", {})})
        monkeypatch.setattr(mod.task_manager, "get_pending_tasks", lambda c=None: [task])
        monkeypatch.setattr(mod.task_manager, "get_processing_tasks", lambda c=None: [])

        resp = client.get("/api/documents/tasks?collection=col-x")
        assert resp.status_code == 200, resp.text
        assert resp.json()["tasks"][0]["file_id"] is None


class TestTaskActionEmission:
    """Task actions must notify peers (spec §5: task.cancelled/retried/cleared)."""

    def _client(self):
        from src.pairing import service as pairing_service
        from src.main import app
        from fastapi.testclient import TestClient

        pairing_service.ensure_local_key()
        return TestClient(app, base_url="http://127.0.0.1:18900", client=("127.0.0.1", 51113))

    def test_cancel_emits_task_cancelled(self, monkeypatch, fresh_bus):
        from src.api.routes import documents as mod

        # 2026-09-18：任务可见性判定在动作**之前**（看不见=不存在），所以要先有这条任务。
        # 这是行为修正：此前对不存在的 task_id 也能"取消成功"（stub 直接返回 True）。
        task = SimpleNamespace(id="t1", collection="col-x", status=None)
        monkeypatch.setattr(mod.task_manager, "get_task", lambda tid: task)
        monkeypatch.setattr(mod.task_manager, "cancel_task", lambda tid: True)
        resp = self._client().post("/api/documents/tasks/t1/cancel")
        assert resp.status_code == 200, resp.text
        assert [e.type for e in fresh_bus.snapshot_since(0) if e.type == "task.cancelled"]

    def test_retry_emits_task_retried(self, monkeypatch, fresh_bus):
        from src.api.routes import documents as mod

        task = SimpleNamespace(
            id="t1", collection="col-x", to_dict=lambda: {"id": "t1"}
        )
        # 可见性判定前置：get_task 也要给（同 cancel 的说明）。
        monkeypatch.setattr(mod.task_manager, "get_task", lambda tid: task)
        monkeypatch.setattr(mod.task_manager, "retry_task", lambda tid: task)
        resp = self._client().post("/api/documents/tasks/t1/retry")
        assert resp.status_code == 200, resp.text
        assert [e.type for e in fresh_bus.snapshot_since(0) if e.type == "task.retried"]

    def test_clear_completed_emits_task_cleared(self, monkeypatch, fresh_bus):
        from src.api.routes import documents as mod

        monkeypatch.setattr(mod.task_manager, "clear_completed_tasks", lambda: None)
        resp = self._client().delete("/api/documents/tasks/completed")
        assert resp.status_code == 200, resp.text
        assert [e.type for e in fresh_bus.snapshot_since(0) if e.type == "task.cleared"]

    def test_failed_cancel_does_not_emit(self, monkeypatch, fresh_bus):
        from src.api.routes import documents as mod

        task = SimpleNamespace(id="t1", collection="col-x", status=None)
        monkeypatch.setattr(mod.task_manager, "get_task", lambda tid: task)
        monkeypatch.setattr(mod.task_manager, "cancel_task", lambda tid: False)
        resp = self._client().post("/api/documents/tasks/t1/cancel")
        assert resp.status_code == 400
        assert fresh_bus.snapshot_since(0) == []
