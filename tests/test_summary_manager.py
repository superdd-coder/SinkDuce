"""SummaryManager Tests -- TDD red phase.

Tests for all SummaryManager methods before implementation.
Run: rtk pytest tests/test_summary_manager.py -x -v
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ── Helpers ──────────────────────────────────────────────────


def _make_mock_db(vector_size: int = 128):
    """Create a mock QdrantManager with common defaults."""
    mock_db = MagicMock()
    mock_db.collection_exists.return_value = False
    mock_db.scroll_points.return_value = ([], None)
    return mock_db


def _make_summary_manager(db=None, vector_size: int = 128):
    """Create a SummaryManager with a mock db injected."""
    from src.rag.summary_manager import SummaryManager

    if db is None:
        db = _make_mock_db(vector_size)
    sm = SummaryManager(db=db, vector_size=vector_size)
    return sm, db


# ── ensure_collection ────────────────────────────────────────


class TestEnsureCollection:
    def test_creates_collection_when_missing(self):
        """Creates __summaries__ collection when it does not exist."""
        sm, db = _make_summary_manager()
        db.collection_exists.return_value = False

        sm.ensure_collection()

        db.collection_exists.assert_called_once_with("__summaries__")
        db.create_collection.assert_called_once_with("__summaries__", vector_size=128)

    def test_skips_creation_when_exists(self):
        """Does not create collection if it already exists."""
        sm, db = _make_summary_manager()
        db.collection_exists.return_value = True

        sm.ensure_collection()

        db.collection_exists.assert_called_once_with("__summaries__")
        db.create_collection.assert_not_called()

    def test_idempotent(self, ):
        """Multiple calls do not create collection multiple times if it exists."""
        sm, db = _make_summary_manager()
        db.collection_exists.return_value = True

        sm.ensure_collection()
        sm.ensure_collection()

        assert db.collection_exists.call_count == 2
        db.create_collection.assert_not_called()


# ── store_doc_summary ───────────────────────────────────────


def _extract_upsert_args(mock_db):
    """Extract ids, vectors, payloads from a mock upsert_points call."""
    kwargs = mock_db.upsert_points.call_args.kwargs
    return kwargs["ids"], kwargs["vectors"], kwargs["payloads"]


class TestStoreDocSummary:
    def test_upserts_with_correct_payload(self):
        """Stores a doc_summary with all fields in the payload."""
        sm, db = _make_summary_manager()

        sm.store_doc_summary(
            collection_id="col-1",
            source="document.pdf",
            data=["item1", "item2"],
            facts=["fact1"],
            insights=["insight1"],
        )

        db.upsert_points.assert_called_once()
        ids, vectors, payloads = _extract_upsert_args(db)

        assert len(ids) == 1
        # Check payload
        payload = payloads[0]
        assert payload["type"] == "doc_summary"
        assert payload["collection_id"] == "col-1"
        assert payload["source"] == "document.pdf"
        assert payload["data"] == ["item1", "item2"]
        assert payload["facts"] == ["fact1"]
        assert payload["insights"] == ["insight1"]

    def test_uses_dummy_zero_vector(self):
        """Doc summaries use a dummy zero vector (not real embeddings)."""
        sm, db = _make_summary_manager(vector_size=64)

        sm.store_doc_summary(
            collection_id="col-1",
            source="doc.pdf",
            data=["d"],
            facts=["f"],
            insights=["i"],
        )

        _, vectors, _ = _extract_upsert_args(db)
        assert vectors[0] == [0.0] * 64

    def test_generates_deterministic_id(self):
        """Same collection_id + source always produces the same point ID."""
        sm1, db1 = _make_summary_manager()
        sm1.store_doc_summary("col-1", "doc.pdf", [], [], [])
        ids1, _, _ = _extract_upsert_args(db1)

        sm2, db2 = _make_summary_manager()
        sm2.store_doc_summary("col-1", "doc.pdf", [], [], [])
        ids2, _, _ = _extract_upsert_args(db2)

        assert ids1[0] == ids2[0]

    def test_different_source_different_id(self):
        """Different source for the same collection produces different IDs."""
        sm1, db1 = _make_summary_manager()
        sm1.store_doc_summary("col-1", "a.pdf", [], [], [])
        ids1, _, _ = _extract_upsert_args(db1)

        sm2, db2 = _make_summary_manager()
        sm2.store_doc_summary("col-1", "b.pdf", [], [], [])
        ids2, _, _ = _extract_upsert_args(db2)

        assert ids1[0] != ids2[0]


# ── get_doc_summaries ───────────────────────────────────────


class TestGetDocSummaries:
    def test_returns_list_of_summaries(self):
        """Returns doc summaries for a given collection_id."""
        mock_db = _make_mock_db()
        mock_db.scroll_points.return_value = (
            [
                {
                    "id": "pt-1",
                    "payload": {
                        "type": "doc_summary",
                        "collection_id": "col-1",
                        "source": "a.pdf",
                        "data": ["d1"],
                        "facts": ["f1"],
                        "insights": ["i1"],
                    },
                },
                {
                    "id": "pt-2",
                    "payload": {
                        "type": "doc_summary",
                        "collection_id": "col-1",
                        "source": "b.pdf",
                        "data": ["d2"],
                        "facts": ["f2"],
                        "insights": ["i2"],
                    },
                },
            ],
            None,
        )
        sm, _ = _make_summary_manager(db=mock_db)

        results = sm.get_doc_summaries("col-1")

        assert len(results) == 2
        assert results[0]["source"] == "a.pdf"
        assert results[1]["source"] == "b.pdf"

    def test_returns_empty_list_when_none(self):
        """Returns empty list when no summaries exist."""
        mock_db = _make_mock_db()
        mock_db.scroll_points.return_value = ([], None)
        sm, _ = _make_summary_manager(db=mock_db)

        results = sm.get_doc_summaries("col-1")

        assert results == []

    def test_passes_correct_scroll_filter(self):
        """Calls scroll_points with collection __summaries__."""
        mock_db = _make_mock_db()
        mock_db.scroll_points.return_value = ([], None)
        sm, _ = _make_summary_manager(db=mock_db)

        sm.get_doc_summaries("col-1")

        mock_db.scroll_points.assert_called_once()
        kwargs = mock_db.scroll_points.call_args.kwargs
        assert kwargs["collection"] == "__summaries__"
        assert kwargs["scroll_filter"] is not None


# ── delete_doc_summary ──────────────────────────────────────


class TestDeleteDocSummary:
    def test_deletes_by_point_id(self):
        """Deletes the specific doc summary by its deterministic point ID."""
        sm, db = _make_summary_manager()

        sm.delete_doc_summary("col-1", "doc.pdf")

        db.delete_points.assert_called_once()
        args = db.delete_points.call_args
        assert args.args[0] == "__summaries__"
        assert len(args.kwargs["ids"]) == 1


# ── store_collection_summary ────────────────────────────────


class TestStoreCollectionSummary:
    def test_upserts_with_real_embedding(self):
        """Collection summary is stored with a real embedding vector."""
        sm, db = _make_summary_manager(vector_size=64)
        embedding = [0.1] * 64

        sm.store_collection_summary("col-1", "This is a summary", embedding)

        db.upsert_points.assert_called_once()
        _, vectors, _ = _extract_upsert_args(db)
        assert vectors[0] == embedding  # NOT a dummy zero vector

    def test_payload_has_correct_fields(self):
        """Payload contains type, collection_id, and content."""
        sm, db = _make_summary_manager(vector_size=32)

        sm.store_collection_summary("col-1", "summary text", [0.1] * 32)

        _, _, payloads = _extract_upsert_args(db)
        payload = payloads[0]
        assert payload["type"] == "collection_summary"
        assert payload["collection_id"] == "col-1"
        assert payload["content"] == "summary text"

    def test_generates_deterministic_id(self):
        """Same collection_id always produces the same point ID for collection summary."""
        sm1, db1 = _make_summary_manager(vector_size=32)
        sm1.store_collection_summary("col-1", "text", [0.1] * 32)
        ids1, _, _ = _extract_upsert_args(db1)

        sm2, db2 = _make_summary_manager(vector_size=32)
        sm2.store_collection_summary("col-1", "other", [0.2] * 32)
        ids2, _, _ = _extract_upsert_args(db2)

        assert ids1[0] == ids2[0]


# ── get_collection_summary ──────────────────────────────────


class TestGetCollectionSummary:
    def test_returns_summary_when_found(self):
        """Returns collection summary dict when it exists."""
        mock_db = _make_mock_db()
        mock_db.scroll_points.return_value = (
            [
                {
                    "id": "pt-1",
                    "payload": {
                        "type": "collection_summary",
                        "collection_id": "col-1",
                        "content": "This is the summary",
                    },
                }
            ],
            None,
        )
        sm, _ = _make_summary_manager(db=mock_db)

        result = sm.get_collection_summary("col-1")

        assert result is not None
        assert result["content"] == "This is the summary"
        assert result["collection_id"] == "col-1"

    def test_returns_none_when_not_found(self):
        """Returns None when no collection summary exists."""
        mock_db = _make_mock_db()
        mock_db.scroll_points.return_value = ([], None)
        sm, _ = _make_summary_manager(db=mock_db)

        result = sm.get_collection_summary("col-1")

        assert result is None


# ── delete_collection_summary ───────────────────────────────


class TestDeleteCollectionSummary:
    def test_deletes_by_point_id(self):
        """Deletes the collection summary by its deterministic point ID."""
        sm, db = _make_summary_manager()

        sm.delete_collection_summary("col-1")

        db.delete_points.assert_called_once()
        args = db.delete_points.call_args
        assert args.args[0] == "__summaries__"
        assert len(args.kwargs["ids"]) == 1


# ── store_conflicts ─────────────────────────────────────────


class TestStoreConflicts:
    def test_stores_multiple_conflicts(self):
        """Stores a list of conflict points."""
        sm, db = _make_summary_manager()

        conflicts = [
            {
                "content1": "Statement A",
                "source1": "doc1.pdf",
                "content2": "Statement B",
                "source2": "doc2.pdf",
            },
            {
                "content1": "Statement C",
                "source1": "doc3.pdf",
                "content2": "Statement D",
                "source2": "doc4.pdf",
            },
        ]

        sm.store_conflicts("col-1", conflicts)

        db.upsert_points.assert_called_once()
        ids, vectors, payloads = _extract_upsert_args(db)

        # Should upsert 2 points
        assert len(ids) == 2

        # Check payloads
        assert all(p["type"] == "conflict" for p in payloads)
        assert all(p["collection_id"] == "col-1" for p in payloads)
        assert payloads[0]["content1"] == "Statement A"
        assert payloads[0]["source1"] == "doc1.pdf"

    def test_empty_conflicts_does_nothing(self):
        """Empty conflict list does not call upsert_points."""
        sm, db = _make_summary_manager()

        sm.store_conflicts("col-1", [])

        db.upsert_points.assert_not_called()

    def test_conflicts_use_dummy_vector(self):
        """Conflict points use dummy zero vectors."""
        sm, db = _make_summary_manager(vector_size=64)

        sm.store_conflicts("col-1", [{"content1": "a", "source1": "s1", "content2": "b", "source2": "s2"}])

        _, vectors, _ = _extract_upsert_args(db)
        assert vectors[0] == [0.0] * 64


# ── get_conflicts ───────────────────────────────────────────


class TestGetConflicts:
    def test_returns_conflicts_for_collection(self):
        """Returns all conflicts for a given collection_id."""
        mock_db = _make_mock_db()
        mock_db.scroll_points.return_value = (
            [
                {
                    "id": "c1",
                    "payload": {
                        "type": "conflict",
                        "collection_id": "col-1",
                        "content1": "A",
                        "source1": "d1.pdf",
                        "content2": "B",
                        "source2": "d2.pdf",
                    },
                },
            ],
            None,
        )
        sm, _ = _make_summary_manager(db=mock_db)

        results = sm.get_conflicts("col-1")

        assert len(results) == 1
        assert results[0]["content1"] == "A"
        assert results[0]["source2"] == "d2.pdf"

    def test_returns_empty_when_no_conflicts(self):
        """Returns empty list when no conflicts exist."""
        mock_db = _make_mock_db()
        mock_db.scroll_points.return_value = ([], None)
        sm, _ = _make_summary_manager(db=mock_db)

        results = sm.get_conflicts("col-1")

        assert results == []


# ── delete_conflicts ────────────────────────────────────────


class TestDeleteConflicts:
    def test_deletes_conflicts_for_collection(self):
        """Deletes all conflicts for a given collection_id using filter."""
        sm, db = _make_summary_manager()

        sm.delete_conflicts("col-1")

        # Implementation uses db.client.delete() directly with a filter
        db.client.delete.assert_called_once()
        kwargs = db.client.delete.call_args.kwargs
        assert kwargs["collection_name"] == "__summaries__"


# ── get_all_collection_summaries ────────────────────────────


class TestGetAllCollectionSummaries:
    def test_returns_all_collection_summaries(self):
        """Returns all collection summaries across all collections."""
        mock_db = _make_mock_db()
        mock_db.scroll_points.return_value = (
            [
                {
                    "id": "s1",
                    "payload": {
                        "type": "collection_summary",
                        "collection_id": "col-1",
                        "content": "Summary 1",
                    },
                },
                {
                    "id": "s2",
                    "payload": {
                        "type": "collection_summary",
                        "collection_id": "col-2",
                        "content": "Summary 2",
                    },
                },
            ],
            None,
        )
        sm, _ = _make_summary_manager(db=mock_db)

        results = sm.get_all_collection_summaries()

        assert len(results) == 2
        assert results[0]["collection_id"] == "col-1"
        assert results[1]["collection_id"] == "col-2"

    def test_returns_empty_when_no_summaries(self):
        """Returns empty list when no collection summaries exist."""
        mock_db = _make_mock_db()
        mock_db.scroll_points.return_value = ([], None)
        sm, _ = _make_summary_manager(db=mock_db)

        results = sm.get_all_collection_summaries()

        assert results == []


# ── Integration / Constructor ───────────────────────────────


class TestConstructor:
    def test_accepts_db_and_vector_size(self):
        """SummaryManager can be constructed with db and vector_size."""
        from src.rag.summary_manager import SummaryManager

        db = MagicMock()
        sm = SummaryManager(db=db, vector_size=256)

        assert sm.db is db
        assert sm.vector_size == 256

    def test_collection_name_constant(self):
        """COLLECTION_NAME is __summaries__."""
        from src.rag.summary_manager import SummaryManager

        assert SummaryManager.COLLECTION_NAME == "__summaries__"
