"""Tests for the ingest-history list + retry API routes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.api.errors import ApiError


def _record(**over):
    base = {
        "record_id": "rec1",
        "task_id": "t1",
        "filename": "a.pdf",
        "collection_id": "colX",
        "file_id": "fid1",
        "status": "failed",
        "stage": "embedding",
        "error": "boom",
        "attempt": 0,
        "created_at": "2026-09-10T00:00:00Z",
        "finished_at": "2026-09-10T00:05:00Z",
        "kwargs": {
            "file_path": "/data/collections/colX/files/fid1/v1/a.pdf",
            "file_id": "fid1",
            "filename_param": "__file__:fid1",
            "source_label": "a.pdf",
            "managed": True,
        },
    }
    base.update(over)
    return base


class TestListIngestHistory:
    def test_returns_records_and_counts(self):
        from src.api.routes.documents import get_ingest_history

        rows = [_record(), _record(record_id="rec2", task_id="t2", status="success", stage="")]
        with patch("src.api.routes.documents.history_store") as hs:
            hs.list_records.return_value = rows
            hs.status_counts.return_value = {"queued": 0, "processing": 0, "success": 1, "failed": 1}
            result = get_ingest_history(collection="colX")
        assert result["counts"]["failed"] == 1
        assert len(result["records"]) == 2


class TestRetryIngestHistory:
    def test_unknown_record_404(self):
        from src.api.routes.documents import retry_ingest_history

        with patch("src.api.routes.documents.history_store") as hs:
            hs.get_record.return_value = None
            with pytest.raises(ApiError) as ei:
                retry_ingest_history("nope", body=MagicMock(collection="colX"))
        assert ei.value.status_code == 404

    def test_non_failed_400(self):
        from src.api.routes.documents import retry_ingest_history

        with patch("src.api.routes.documents.history_store") as hs:
            hs.get_record.return_value = _record(status="success")
            with pytest.raises(ApiError) as ei:
                retry_ingest_history("rec1", body=MagicMock(collection="colX"))
        assert ei.value.status_code == 400

    def test_missing_source_file_400(self, tmp_path):
        from src.api.routes.documents import retry_ingest_history

        rec = _record(kwargs={"file_path": str(tmp_path / "gone.pdf"), "file_id": "fid1"})
        with patch("src.api.routes.documents.history_store") as hs, \
             patch("src.api.routes.documents.services") as svc:
            hs.get_record.return_value = rec
            db = MagicMock()
            svc.db = db
            db.get_collection_config.return_value = {}
            with patch("src.api.routes.documents._resolve_retry_file_path", return_value=None):
                with pytest.raises(ApiError) as ei:
                    retry_ingest_history("rec1", body=MagicMock(collection="colX"))
        assert ei.value.status_code == 400

    def test_success_clears_chunks_and_requeues(self, tmp_path):
        from src.api.routes.documents import retry_ingest_history

        blob = tmp_path / "a.pdf"
        blob.write_text("hello")
        rec = _record(kwargs={
            "file_path": str(blob), "file_id": "fid1",
            "filename_param": "__file__:fid1", "source_label": "a.pdf", "managed": True,
        })

        tman = MagicMock()
        new_task = MagicMock()
        new_task.id = "task-new"
        tman.create_task_async = None  # sync path used
        tman.create_task.return_value = new_task

        with patch("src.api.routes.documents.history_store") as hs, \
             patch("src.api.routes.documents.services") as svc, \
             patch("src.api.routes.documents.task_manager", tman):
            hs.get_record.return_value = rec
            db = MagicMock()
            svc.db = db
            db.get_collection_config.return_value = {}

            result = retry_ingest_history("rec1", body=MagicMock(collection="colX"))

        # Old half-written chunks cleared by source before re-ingest
        db.delete_by_filter.assert_called_once_with("colX", key="source", value="__file__:fid1")
        # Row flipped to queued with the new task id
        hs.retry_record.assert_called_once_with("colX", "rec1", new_task_id="task-new")
        assert result["message"]
