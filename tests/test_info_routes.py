"""Tests for Info API routes — TDD red phase.

Tests for:
  - GET /collections/{collection}/info/summary
  - GET /collections/{collection}/info/conflicts
  - GET /collections/{collection}/info/doc-summaries/{source}
  - POST /collections/{collection}/info/consolidate
  - GET /collections/{collection}/info/meeting-log

Run: rtk pytest tests/test_info_routes.py -x -v
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────

def _make_services(db=None):
    """Create a mock Services object."""
    from src.services import Services

    svc = Services()
    svc.db = db or MagicMock()
    return svc


# ── GET /info/summary ───────────────────────────────────────


class TestGetCollectionSummary:
    def test_summary_exists(self):
        """Return summary content when a collection summary is stored."""
        from src.api.routes.info import get_collection_summary

        mock_sm = MagicMock()
        mock_sm.get_collection_summary.return_value = {
            "type": "collection_summary",
            "collection_id": "mycol",
            "content": "This collection covers X.",
        }
        svc = _make_services()

        with (
            patch("src.api.routes.info.services", svc),
            patch("src.api.routes.info._get_summary_manager", return_value=mock_sm),
        ):
            result = get_collection_summary("mycol")

        assert result["content"] == "This collection covers X."
        mock_sm.get_collection_summary.assert_called_once_with("mycol")

    def test_summary_not_found(self):
        """Return 404 when no summary exists."""
        from src.api.routes.info import get_collection_summary
        from fastapi import HTTPException

        mock_sm = MagicMock()
        mock_sm.get_collection_summary.return_value = None
        svc = _make_services()

        with (
            patch("src.api.routes.info.services", svc),
            patch("src.api.routes.info._get_summary_manager", return_value=mock_sm),
            pytest.raises(HTTPException) as exc_info,
        ):
            get_collection_summary("mycol")

        assert exc_info.value.status_code == 404


# ── GET /info/conflicts ─────────────────────────────────────


class TestGetConflicts:
    def test_returns_conflicts(self):
        """Return list of conflicts for the collection."""
        from src.api.routes.info import get_collection_conflicts

        mock_sm = MagicMock()
        mock_sm.get_conflicts.return_value = [
            {"description": "Doc A says X, Doc B says Y"},
            {"description": "Doc C contradicts Doc D"},
        ]
        svc = _make_services()

        with (
            patch("src.api.routes.info.services", svc),
            patch("src.api.routes.info._get_summary_manager", return_value=mock_sm),
        ):
            result = get_collection_conflicts("mycol")

        assert len(result["conflicts"]) == 2
        mock_sm.get_conflicts.assert_called_once_with("mycol")

    def test_no_conflicts(self):
        """Return empty list when no conflicts exist."""
        from src.api.routes.info import get_collection_conflicts

        mock_sm = MagicMock()
        mock_sm.get_conflicts.return_value = []
        svc = _make_services()

        with (
            patch("src.api.routes.info.services", svc),
            patch("src.api.routes.info._get_summary_manager", return_value=mock_sm),
        ):
            result = get_collection_conflicts("mycol")

        assert result["conflicts"] == []


# ── GET /info/doc-summaries/{source} ────────────────────────


class TestGetDocSummary:
    def test_doc_summary_exists(self):
        """Return structured summary for a specific document."""
        from src.api.routes.info import get_doc_summary

        mock_sm = MagicMock()
        mock_sm.get_doc_summary.return_value = {
            "type": "doc_summary",
            "collection_id": "mycol",
            "source": "report.pdf",
            "data": ["Section 1 overview"],
            "facts": ["Revenue grew 10%"],
            "insights": ["Market expanding"],
        }
        svc = _make_services()

        with (
            patch("src.api.routes.info.services", svc),
            patch("src.api.routes.info._get_summary_manager", return_value=mock_sm),
        ):
            result = get_doc_summary("mycol", "report.pdf")

        assert result["source"] == "report.pdf"
        assert "Revenue grew 10%" in result["facts"]
        mock_sm.get_doc_summary.assert_called_once_with(
            "mycol", "report.pdf", version_id=None
        )

    def test_doc_summary_not_found(self):
        """Return 404 when doc summary does not exist."""
        from src.api.routes.info import get_doc_summary
        from fastapi import HTTPException

        mock_sm = MagicMock()
        mock_sm.get_doc_summary.return_value = None
        svc = _make_services()

        with (
            patch("src.api.routes.info.services", svc),
            patch("src.api.routes.info._get_summary_manager", return_value=mock_sm),
            pytest.raises(HTTPException) as exc_info,
        ):
            get_doc_summary("mycol", "missing.pdf")

        assert exc_info.value.status_code == 404


# ── POST /info/consolidate ──────────────────────────────────


class TestTriggerConsolidation:
    def test_creates_task(self):
        """POST consolidate should create a task via TaskManager."""
        from src.api.routes.info import trigger_consolidation

        mock_tm = MagicMock()
        mock_task = MagicMock()
        mock_task.to_dict.return_value = {"id": "task-123", "status": "pending"}
        mock_tm.create_task.return_value = mock_task

        svc = _make_services()

        with (
            patch("src.api.routes.info.services", svc),
            patch("src.api.routes.info.task_manager", mock_tm),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                trigger_consolidation("mycol")
            )

        assert "task" in result
        mock_tm.create_task.assert_called_once()
        call_kwargs = mock_tm.create_task.call_args
        assert call_kwargs.kwargs.get("task_type") or call_kwargs[1].get("task_type") == "consolidate"


# ── GET /info/meeting-log ───────────────────────────────────


class TestGetMeetingLog:
    def test_returns_matching_meetings(self, tmp_path):
        """Return meetings whose allocated_collections includes the target."""
        from src.api.routes.info import get_meeting_log

        # Create fake meeting dirs with allocated_file_ids for verification
        m1_dir = tmp_path / "meeting-1"
        m1_dir.mkdir()
        (m1_dir / "meta.json").write_text(json.dumps({
            "id": "meeting-1",
            "title": "Standup",
            "allocated_collections": ["mycol", "other"],
            "allocated_file_ids": ["file-a", "file-b"],
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T00:00:00",
        }))

        m2_dir = tmp_path / "meeting-2"
        m2_dir.mkdir()
        (m2_dir / "meta.json").write_text(json.dumps({
            "id": "meeting-2",
            "title": "Other meeting",
            "allocated_collections": ["other"],
            "allocated_file_ids": ["file-c"],
            "created_at": "2025-01-02T00:00:00",
            "updated_at": "2025-01-02T00:00:00",
        }))

        m3_dir = tmp_path / "meeting-3"
        m3_dir.mkdir()
        (m3_dir / "meta.json").write_text(json.dumps({
            "id": "meeting-3",
            "title": "Review",
            "allocated_collections": ["mycol"],
            "allocated_file_ids": ["file-d"],
            "created_at": "2025-01-03T00:00:00",
            "updated_at": "2025-01-03T00:00:00",
        }))

        mock_services = _make_services()
        mock_services.db.collection_exists.return_value = False  # bypass secondary scan
        # Primary scan uses limit=1 for file verification; secondary uses limit=100
        def _fake_scroll(collection=None, scroll_filter=None, limit=None, **kwargs):
            if limit == 1:
                return ([MagicMock()], None)  # Primary scan verification
            return ([], None)  # Secondary scan (no meeting_id chunks)
        mock_services.db.scroll_points.side_effect = _fake_scroll

        with patch("src.api.routes.info.MEETINGS_DIR", tmp_path), \
             patch("src.api.routes.info.services", mock_services):
            result = get_meeting_log("mycol")

        assert len(result["meetings"]) == 2
        titles = {m["title"] for m in result["meetings"]}
        assert titles == {"Standup", "Review"}

    def test_no_meetings_dir(self, tmp_path):
        """Return empty list when meetings directory doesn't exist."""
        from src.api.routes.info import get_meeting_log

        missing = tmp_path / "no_meetings"
        with patch("src.api.routes.info.MEETINGS_DIR", missing):
            result = get_meeting_log("mycol")

        assert result["meetings"] == []

    def test_no_matching_meetings(self, tmp_path):
        """Return empty list when no meetings match the collection."""
        from src.api.routes.info import get_meeting_log

        m1_dir = tmp_path / "meeting-1"
        m1_dir.mkdir()
        (m1_dir / "meta.json").write_text(json.dumps({
            "id": "meeting-1",
            "title": "Something else",
            "allocated_collections": ["other"],
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T00:00:00",
        }))

        with patch("src.api.routes.info.MEETINGS_DIR", tmp_path):
            result = get_meeting_log("mycol")

        assert result["meetings"] == []


# ── SummaryManager.get_doc_summary (singular) ───────────────


class TestSummaryManagerGetDocSummary:
    """Test the new get_doc_summary(collection_id, source) method."""

    def test_returns_single_doc_summary(self):
        """Should return a single doc summary dict when found."""
        from src.rag.summary_manager import SummaryManager

        mock_db = MagicMock()
        mock_db.scroll_points.return_value = (
            [{"payload": {"type": "doc_summary", "collection_id": "c", "source": "file.pdf", "data": [], "facts": [], "insights": []}}],
            None,
        )
        sm = SummaryManager(db=mock_db, vector_size=8)

        result = sm.get_doc_summary("c", "file.pdf")
        assert result is not None
        assert result["source"] == "file.pdf"

    def test_returns_none_when_not_found(self):
        """Should return None when no matching doc summary exists."""
        from src.rag.summary_manager import SummaryManager

        mock_db = MagicMock()
        mock_db.scroll_points.return_value = ([], None)
        sm = SummaryManager(db=mock_db, vector_size=8)

        result = sm.get_doc_summary("c", "missing.pdf")
        assert result is None


# ── Default config fields ───────────────────────────────────


class TestDefaultConfigFields:
    def test_summary_change_counter_in_defaults(self):
        """Default config should include summary_change_counter."""
        from src.db.qdrant import get_default_collection_config

        cfg = get_default_collection_config()
        assert "summary_change_counter" in cfg
        assert cfg["summary_change_counter"] == 0

    def test_summary_consolidate_threshold_in_defaults(self):
        """Default config should include summary_consolidate_threshold."""
        from src.db.qdrant import get_default_collection_config

        cfg = get_default_collection_config()
        assert "summary_consolidate_threshold" in cfg
        assert cfg["summary_consolidate_threshold"] == 5


# ── Delete endpoint cleanup ─────────────────────────────────


class TestDeleteDocumentCleanup:
    def test_delete_clears_summaries(self):
        """Deleting a document should delete from db, clean up doc_summary, and update counter."""
        from src.api.routes.documents import delete_document

        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {
            "summary_change_counter": 0,
            "summary_consolidate_threshold": 5,
        }
        mock_sm = MagicMock()
        svc = _make_services(db=mock_db)

        with (
            patch("src.api.routes.documents.services", svc),
            patch("src.api.routes.documents._get_summary_manager", return_value=mock_sm),
            patch("src.api.routes.documents.task_manager") as mock_tm,
        ):
            asyncio.get_event_loop().run_until_complete(
                delete_document("mycol", "file.pdf")
            )

        # Original delete still called
        mock_db.delete_by_filter.assert_called_once_with("mycol", key="source", value="file.pdf")

        # Doc summary cleanup called
        mock_sm.delete_doc_summary.assert_called_once_with("mycol", "file.pdf")

        # Counter updated
        mock_db.update_collection_config.assert_called_once_with(
            "mycol", {"summary_change_counter": 1}
        )

    def test_delete_increments_counter(self):
        """Deleting a document should increment summary_change_counter."""
        from src.api.routes.documents import delete_document

        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {
            "summary_change_counter": 2,
            "summary_consolidate_threshold": 5,
        }
        mock_sm = MagicMock()
        svc = _make_services(db=mock_db)

        with (
            patch("src.api.routes.documents.services", svc),
            patch("src.api.routes.documents._get_summary_manager", return_value=mock_sm),
            patch("src.api.routes.documents.task_manager") as mock_tm,
        ):
            asyncio.get_event_loop().run_until_complete(
                delete_document("mycol", "file.pdf")
            )

        # Config updated with incremented counter
        mock_db.update_collection_config.assert_called_once_with(
            "mycol", {"summary_change_counter": 3}
        )

    def test_delete_triggers_consolidate_at_threshold(self):
        """When counter reaches threshold, a consolidate task should be created."""
        from src.api.routes.documents import delete_document

        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {
            "summary_change_counter": 4,
            "summary_consolidate_threshold": 5,
        }
        mock_sm = MagicMock()
        svc = _make_services(db=mock_db)

        mock_tm = MagicMock()
        mock_task = MagicMock()
        mock_tm.create_task.return_value = mock_task

        with (
            patch("src.api.routes.documents.services", svc),
            patch("src.api.routes.documents._get_summary_manager", return_value=mock_sm),
            patch("src.api.routes.documents.task_manager", mock_tm),
        ):
            asyncio.get_event_loop().run_until_complete(
                delete_document("mycol", "file.pdf")
            )

        # Counter becomes 5 which equals threshold -> create task
        mock_tm.create_task.assert_called_once()
        call_kwargs = mock_tm.create_task.call_args
        assert call_kwargs.kwargs.get("task_type") or call_kwargs[1].get("task_type") == "consolidate"

    def test_delete_no_consolidate_below_threshold(self):
        """When counter is below threshold, no consolidate task is created."""
        from src.api.routes.documents import delete_document

        mock_db = MagicMock()
        mock_db.get_collection_config.return_value = {
            "summary_change_counter": 1,
            "summary_consolidate_threshold": 5,
        }
        mock_sm = MagicMock()
        svc = _make_services(db=mock_db)

        mock_tm = MagicMock()

        with (
            patch("src.api.routes.documents.services", svc),
            patch("src.api.routes.documents._get_summary_manager", return_value=mock_sm),
            patch("src.api.routes.documents.task_manager", mock_tm),
        ):
            asyncio.get_event_loop().run_until_complete(
                delete_document("mycol", "file.pdf")
            )

        mock_tm.create_task.assert_not_called()
