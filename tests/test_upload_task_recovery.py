"""Upload task ledger + crash recovery.

Covers the folder-upload orphan fix: upload tasks are persisted when
queued, cleared on terminal states, and re-enqueued on startup so a
crashed backend never leaves uploaded files un-ingested.

Run: pytest tests/test_upload_task_recovery.py -v --tb=short
"""

from __future__ import annotations

import asyncio

import pytest

from src.tasks import task_persistence as tp
from src.tasks.task_manager import TaskManager, TaskStatus


@pytest.fixture(autouse=True)
def _tmp_ledger(tmp_path, monkeypatch):
    """Point the ledger at a throwaway DB so tests never touch real data/."""
    monkeypatch.setattr(tp, "DB_PATH", tmp_path / "tasks.db")
    yield


def _seed(task_id: str, collection: str, payload: dict) -> None:
    tp.record_upload_task(task_id, collection, payload)


class TestLedger:
    def test_record_then_pending_then_finish(self):
        _seed("t1", "col1", {"filename": "a.pdf", "file_path": "/x/a.pdf",
                             "file_id": "f1", "version_id": "v1"})

        rows = tp.pending_upload_tasks()
        assert len(rows) == 1
        assert rows[0]["task_id"] == "t1"
        assert rows[0]["collection_id"] == "col1"
        assert rows[0]["file_id"] == "f1"
        assert rows[0]["payload"]["filename"] == "a.pdf"

        tp.finish_upload_task("t1")
        assert tp.pending_upload_tasks() == []

    def test_record_is_upsert(self):
        _seed("t1", "col1", {"filename": "a.pdf", "file_path": "/x/a.pdf"})
        _seed("t1", "col1", {"filename": "a.pdf", "file_path": "/x/a.pdf"})
        assert len(tp.pending_upload_tasks()) == 1

    def test_missing_db_returns_empty(self):
        assert tp.pending_upload_tasks() == []


async def _drive_recovery(seed_rows, settle_delay=0.0):
    """Seed ledger rows, run recovery on a fresh manager, wait for completion."""
    for task_id, collection, payload in seed_rows:
        _seed(task_id, collection, payload)

    tm = TaskManager(max_concurrent=2)
    calls: list[dict] = []

    async def handler(task, **kwargs):
        calls.append({"id": task.id, **kwargs})
        return {"ok": True}

    tm.register_handler("upload", handler)
    await tm.start()
    try:
        recovered = await tm.recover_upload_tasks(settle_delay=settle_delay)
        deadline = asyncio.get_running_loop().time() + 5.0
        while any(
            t.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            for t in tm.tasks.values()
        ):
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("tasks did not finish in time")
            await asyncio.sleep(0.02)
        return tm, calls, recovered
    finally:
        await tm.stop()


class TestRecovery:
    def test_reenqueues_and_clears_ledger(self):
        tm, calls, recovered = asyncio.run(_drive_recovery([
            ("t1", "col1", {"filename": "a.pdf", "file_path": "/x/a.pdf"}),
            ("t2", "col2", {"filename": "b.docx", "file_path": "/y/b.docx"}),
        ]))
        assert recovered == 2
        assert {c["id"] for c in calls} == {"t1", "t2"}
        # collection injected by the executor, kwargs round-tripped
        by_id = {c["id"]: c for c in calls}
        assert by_id["t1"]["collection"] == "col1"
        assert by_id["t1"]["file_path"] == "/x/a.pdf"
        # terminal state cleared the ledger
        assert tp.pending_upload_tasks() == []
        # tasks visible with their original ids
        assert set(tm.tasks) == {"t1", "t2"}

    def test_dedupes_to_newest_per_file(self, monkeypatch):
        # Dedupe is orthogonal to version freshness — that's covered by
        # test_drops_task_for_missing_collection.
        monkeypatch.setattr(TaskManager, "_version_is_current", staticmethod(lambda *a: True))
        tm, calls, recovered = asyncio.run(_drive_recovery([
            ("old", "col1", {"filename": "a.pdf", "file_path": "/x/a.pdf",
                             "file_id": "f1", "version_id": "v1"}),
            ("new", "col1", {"filename": "a.pdf", "file_path": "/x/a.pdf",
                             "file_id": "f1", "version_id": "v2"}),
        ]))
        assert recovered == 1
        assert [c["id"] for c in calls] == ["new"]
        # loser cleared immediately as superseded; winner cleared on completion
        assert tp.pending_upload_tasks() == []

    def test_drops_task_for_missing_collection(self):
        # file_id + version_id set, but no meta.db on disk → collection gone
        tm, calls, recovered = asyncio.run(_drive_recovery([
            ("t1", "ghost-col", {"filename": "a.pdf", "file_path": "/x/a.pdf",
                                 "file_id": "f1", "version_id": "v1"}),
        ]))
        assert recovered == 0
        assert calls == []
        assert tp.pending_upload_tasks() == []

    def test_legacy_rows_without_file_id_are_kept(self):
        tm, calls, recovered = asyncio.run(_drive_recovery([
            ("t1", "col1", {"filename": "a.pdf", "file_path": "/x/a.pdf"}),
            ("t2", "col1", {"filename": "a.pdf", "file_path": "/x/a.pdf"}),
        ]))
        # same file_path → dedupe key without file_id
        assert recovered == 1
        assert len(calls) == 1

    def test_empty_ledger_is_noop(self):
        tm, calls, recovered = asyncio.run(_drive_recovery([]))
        assert recovered == 0
        assert calls == []
