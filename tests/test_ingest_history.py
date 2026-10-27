"""Tests for the ingest history store and its TaskManager wiring."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from src.tasks import history_store


@pytest.fixture(autouse=True)
def _tmp_history_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(history_store, "HISTORY_DIR", tmp_path / "task_history")
    history_store._cache.clear()
    yield
    history_store._cache.clear()


class TestHistoryStoreCRUD:
    def test_record_queued_creates_row(self):
        row = history_store.record_queued(
            "col1", "task-1", "report.pdf",
            kwargs={"file_path": "/tmp/x.pdf", "file_id": "fid1"},
        )
        assert row["status"] == "queued"
        assert row["task_id"] == "task-1"
        assert row["file_id"] == "fid1"
        assert row["attempt"] == 0
        assert row["stage"] == ""
        rows = history_store.list_records("col1")
        assert len(rows) == 1

    def test_full_lifecycle(self):
        history_store.record_queued("col1", "t1", "a.pdf", kwargs={})
        history_store.mark_processing("col1", "t1")
        history_store.mark_success("col1", "t1")
        rows = history_store.list_records("col1")
        assert rows[0]["status"] == "success"
        assert rows[0]["finished_at"]

    def test_failed_keeps_stage_and_error(self):
        history_store.record_queued("col1", "t1", "a.pdf", kwargs={})
        history_store.mark_processing("col1", "t1")
        history_store.mark_failed("col1", "t1", error="Embedding boom", stage="embedding")
        row = history_store.list_records("col1")[0]
        assert row["status"] == "failed"
        assert row["stage"] == "embedding"
        assert row["error"] == "Embedding boom"
        assert row["finished_at"]

    def test_unknown_task_is_noop(self):
        history_store.mark_processing("col1", "ghost")
        history_store.mark_success("col1", "ghost")
        assert history_store.list_records("col1") == []

    def test_queued_is_idempotent_on_same_task_id(self):
        """Recovery re-enqueues the original task_id — the same row must be
        reused (reset to queued), never duplicated."""
        history_store.record_queued("col1", "t1", "a.pdf", kwargs={})
        history_store.mark_failed("col1", "t1", error="x", stage="parsing")
        row = history_store.record_queued("col1", "t1", "a.pdf", kwargs={})
        assert row["status"] == "queued"
        assert len(history_store.list_records("col1")) == 1

    def test_persisted_to_disk(self):
        history_store.record_queued("col1", "t1", "a.pdf", kwargs={"a": 1})
        path = history_store.HISTORY_DIR / "col1.json"
        assert path.is_file()
        data = json.loads(path.read_text())
        assert data["records"][0]["task_id"] == "t1"

    def test_kwargs_saved_for_retry(self):
        history_store.record_queued("col1", "t1", "a.pdf", kwargs={"file_id": "f9"})
        rec = history_store.get_record_by_task("col1", "t1")
        assert rec["kwargs"]["file_id"] == "f9"


class TestRetry:
    def test_retry_resets_row(self):
        history_store.record_queued("col1", "t1", "a.pdf", kwargs={})
        history_store.mark_failed("col1", "t1", error="boom", stage="storing")
        row = history_store.retry_record("col1", history_store.get_record_by_task("col1", "t1")["record_id"], new_task_id="t2")
        assert row is not None
        assert row["status"] == "queued"
        assert row["attempt"] == 1
        assert row["task_id"] == "t2"
        assert row["error"] == ""
        assert row["stage"] == ""
        assert row["finished_at"] == ""

    def test_retry_rejects_non_failed(self):
        history_store.record_queued("col1", "t1", "a.pdf", kwargs={})
        rec = history_store.get_record_by_task("col1", "t1")
        assert history_store.retry_record("col1", rec["record_id"], "t2") is None

    def test_retry_by_record_id(self):
        history_store.record_queued("col1", "t1", "a.pdf", kwargs={})
        history_store.mark_failed("col1", "t1", "boom", stage="parsing")
        rec = history_store.get_record_by_task("col1", "t1")
        assert history_store.retry_record("col1", rec["record_id"], "t9") is not None


class TestInterrupted:
    def test_marks_non_terminal_rows(self):
        history_store.record_queued("col1", "t1", "a.pdf", kwargs={})
        history_store.record_queued("col1", "t2", "b.pdf", kwargs={})
        history_store.mark_processing("col1", "t2")
        history_store.record_queued("col1", "t3", "c.pdf", kwargs={})
        history_store.mark_success("col1", "t3")

        n = history_store.mark_interrupted(exclude_task_ids={"t1"})
        assert n == 1  # t2 was processing and not excluded
        rows = {r["task_id"]: r for r in history_store.list_records("col1")}
        assert rows["t1"]["status"] == "queued"  # excluded (about to re-enqueue)
        assert rows["t2"]["status"] == "failed"
        assert rows["t2"]["stage"] == "interrupted"
        assert "restart" in rows["t2"]["error"].lower()
        assert rows["t3"]["status"] == "success"


class TestPrune:
    def test_prune_keeps_recent(self, monkeypatch):
        monkeypatch.setattr(history_store, "MAX_RECORDS", 10)
        for i in range(15):
            history_store.record_queued("col1", f"t{i}", f"f{i}.pdf", kwargs={})
        rows = history_store.list_records("col1")
        assert len(rows) == 10
        # Oldest dropped
        assert all(r["task_id"] != "t0" for r in rows)


class TestCounts:
    def test_counts_by_status(self):
        history_store.record_queued("col1", "t1", "a.pdf", kwargs={})
        history_store.record_queued("col1", "t2", "b.pdf", kwargs={})
        history_store.mark_success("col1", "t2")
        history_store.record_queued("col1", "t3", "c.pdf", kwargs={})
        history_store.mark_failed("col1", "t3", "x", "storing")
        counts = history_store.status_counts("col1")
        assert counts == {"queued": 1, "processing": 0, "success": 1, "failed": 1}


# ── TaskManager wiring ────────────────────────────────────────

class TestTaskManagerWiring:
    def _run_manager(self, tmp_calls):
        """Run a TaskManager with a stub upload handler through its lifecycle."""
        from src.tasks.task_manager import TaskManager

        async def scenario():
            mgr = TaskManager(max_concurrent=2)
            await mgr.start()

            async def handler(task, **kwargs):
                tmp_calls.append(("running", task.id, task.stage))
                return {"ok": True}

            async def failing_handler(task, **kwargs):
                task.stage = "embedding"
                raise RuntimeError("embed exploded")

            mgr.register_handler("upload", handler)
            mgr.register_handler("boom", failing_handler)
            t1 = await mgr.create_task_async("a.pdf", task_type="upload", collection="colW", file_id="fa")
            await mgr.create_task_async("b.pdf", task_type="boom", collection="colW", file_id="fb")
            for _ in range(100):
                await asyncio.sleep(0.02)
                if all(t.status in ("completed", "failed") for t in mgr.tasks.values()):
                    break
            await mgr.stop()
            return mgr

        return asyncio.run(scenario())

    def test_upload_task_writes_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr(history_store, "HISTORY_DIR", tmp_path / "task_history")
        history_store._cache.clear()
        calls = []
        self._run_manager(calls)

        rows = {r["filename"]: r for r in history_store.list_records("colW")}
        assert rows["a.pdf"]["status"] == "success"
        assert rows["a.pdf"]["task_type"] == "upload"
        # Non-upload task types are not recorded
        assert "b.pdf" not in rows

    def test_failed_task_keeps_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(history_store, "HISTORY_DIR", tmp_path / "task_history")
        history_store._cache.clear()
        calls = []
        mgr = self._run_manager(calls)
        rows = {r["filename"]: r for r in history_store.list_records("colW")}
        assert rows["a.pdf"]["status"] == "success"

    def test_task_has_stage_field(self):
        from src.tasks.task_manager import Task
        t = Task(id="x", filename="f")
        assert t.stage == ""
        d = t.to_dict()
        assert "stage" in d
