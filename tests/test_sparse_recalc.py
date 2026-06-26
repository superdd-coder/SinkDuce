"""Mock-based integration tests for sparse recalc pipeline."""

import pytest
from unittest.mock import MagicMock, patch, call


# ── Lock tests ────────────────────────────────────────────────

class TestLock:
    def test_acquire_lock_free(self):
        from src.rag.sparse_recalc import _acquire_lock
        db = MagicMock()
        db.get_collection_config.return_value = {"sparse_lock": False}
        assert _acquire_lock(db, "col") is True
        db.update_collection_config.assert_called_once_with("col", {"sparse_lock": True})

    def test_acquire_lock_busy_then_free(self):
        from src.rag.sparse_recalc import _acquire_lock
        db = MagicMock()
        db.get_collection_config.side_effect = [
            {"sparse_lock": True},
            {"sparse_lock": True},
            {"sparse_lock": False},  # frees up on 3rd poll
        ]
        assert _acquire_lock(db, "col") is True

    def test_acquire_lock_timeout(self):
        from src.rag.sparse_recalc import _acquire_lock, LOCK_TIMEOUT
        db = MagicMock()
        db.get_collection_config.return_value = {"sparse_lock": True}
        # Override timeout to 0.1s for fast test
        with patch("src.rag.sparse_recalc.LOCK_TIMEOUT", 0.1):
            assert _acquire_lock(db, "col") is False

    def test_release_lock(self):
        from src.rag.sparse_recalc import _release_lock
        db = MagicMock()
        _release_lock(db, "col")
        db.update_collection_config.assert_called_once_with("col", {"sparse_lock": False})

    def test_release_lock_swallows_error(self):
        from src.rag.sparse_recalc import _release_lock
        db = MagicMock()
        db.update_collection_config.side_effect = RuntimeError("network down")
        # Should not raise
        _release_lock(db, "col")


# ── Rebuild flow tests ────────────────────────────────────────

class TestRunSparseRecalc:
    """Test run_sparse_recalc with mocked Qdrant."""

    def test_empty_collection(self):
        from src.rag.sparse_recalc import run_sparse_recalc
        db = MagicMock()
        db.get_collection_config.return_value = {"sparse_lock": False, "sparse_recalc_counter": 5}
        db.scroll_points.return_value = ([], None)  # no points

        result = run_sparse_recalc(db, "col")

        assert result == {"collection": "col", "rebuilt_chunks": 0}
        # Counter should be reset
        assert call("col", {"sparse_recalc_counter": 0}) in db.update_collection_config.call_args_list
        # Lock should be released
        assert call("col", {"sparse_lock": False}) in db.update_collection_config.call_args_list

    def test_full_flow(self):
        from src.rag.sparse_recalc import run_sparse_recalc
        db = MagicMock()
        db.get_collection_config.return_value = {
            "sparse_lock": False,
            "sparse_recalc_counter": 5000,
            "sparse_recalc_threshold": 5000,
        }
        # Return two batches, then empty (loop termination)
        db.scroll_points.side_effect = [
            (
                [
                    {"id": "id1", "payload": {"text": "hello world"}},
                    {"id": "id2", "payload": {"text": "foo bar"}},
                ],
                "offset-2",
            ),
            (
                [{"id": "id3", "payload": {"text": "hello foo"}}],
                None,
            ),
            ([], None),  # signal end-of-collection
        ]

        result = run_sparse_recalc(db, "col")

        assert result["rebuilt_chunks"] == 3
        assert result["terms"] >= 4  # hello, world, foo, bar
        # Verify sparse vectors were upserted
        db.upsert_sparse_vectors.assert_called_once()
        _args, _kwargs = db.upsert_sparse_vectors.call_args
        # upsert_sparse_vectors(self, collection, ids, sparse_vectors)
        assert _args[0] == "col"
        assert _args[1] == ["id1", "id2", "id3"]
        assert len(_args[2]) == 3
        # Vocab should be saved
        assert any(
            "sparse_vocab" in str(c)
            for c in db.update_collection_config.call_args_list
        )
        # Counter: 5000 - 3 = 4997
        assert call("col", {"sparse_recalc_counter": 4997}) in db.update_collection_config.call_args_list

    def test_lock_failure_returns_none(self):
        from src.rag.sparse_recalc import run_sparse_recalc
        db = MagicMock()
        db.get_collection_config.return_value = {"sparse_lock": True}  # busy

        with patch("src.rag.sparse_recalc.LOCK_TIMEOUT", 0.1):
            result = run_sparse_recalc(db, "col")

        assert result is None
        # Should NOT have upserted
        db.upsert_sparse_vectors.assert_not_called()

    def test_scroll_error_preserves_counter(self):
        """If rebuild fails mid-way, counter is NOT reset."""
        from src.rag.sparse_recalc import run_sparse_recalc
        db = MagicMock()
        db.get_collection_config.side_effect = [
            {"sparse_lock": False, "sparse_recalc_counter": 5000},
        ]
        db.scroll_points.side_effect = RuntimeError("Qdrant down")

        result = run_sparse_recalc(db, "col")

        assert result is None
        # Counter should NOT have been reset (no call with counter=0)
        reset_calls = [
            c for c in db.update_collection_config.call_args_list
            if "sparse_recalc_counter" in str(c) and "0" in str(c)
        ]
        assert len(reset_calls) == 0

    def test_skip_points_without_text(self):
        from src.rag.sparse_recalc import run_sparse_recalc
        db = MagicMock()
        db.get_collection_config.return_value = {"sparse_lock": False, "sparse_recalc_counter": 3}
        db.scroll_points.side_effect = [
            (
                [
                    {"id": "id1", "payload": {"text": "hello"}},
                    {"id": "cfg", "payload": {"chunk_type": "__config__"}},  # no text
                    {"id": "id2", "payload": {"text": "world"}},
                ],
                None,
            ),
            ([], None),  # signal end
        ]

        result = run_sparse_recalc(db, "col")

        assert result["rebuilt_chunks"] == 2  # config point skipped
        _args, _kwargs = db.upsert_sparse_vectors.call_args
        assert _args[1] == ["id1", "id2"]


# ── Counter bump tests ────────────────────────────────────────

class TestCounterBump:
    def test_under_threshold(self):
        from src.tasks.handlers import _bump_sparse_recalc_counter
        db_mock = MagicMock()
        db_mock.get_collection_config.return_value = {
            "sparse_recalc_threshold": 5000,
            "sparse_recalc_counter": 100,
        }
        with patch("src.tasks.handlers.services") as svc:
            svc.db = db_mock
            _bump_sparse_recalc_counter("col", 10)

        # Should update counter to 110
        db_mock.update_collection_config.assert_called_with("col", {"sparse_recalc_counter": 110})

    def test_cross_threshold_triggers_task(self):
        from src.tasks.handlers import _bump_sparse_recalc_counter
        db_mock = MagicMock()
        db_mock.get_collection_config.return_value = {
            "sparse_recalc_threshold": 10,
            "sparse_recalc_counter": 9,
        }
        # Patch the task_manager (imported via `from src.tasks import task_manager`)
        tman_mock = MagicMock()
        with patch("src.tasks.handlers.services") as svc:
            svc.db = db_mock
            with patch("src.tasks.task_manager", tman_mock):
                _bump_sparse_recalc_counter("col", 5)

        # Counter should be 14
        assert call("col", {"sparse_recalc_counter": 14}) in db_mock.update_collection_config.call_args_list
        # Should create a recalc task
        tman_mock.create_task.assert_called_once()
        call_args = tman_mock.create_task.call_args
        assert call_args[1]["task_type"] == "sparse_recalc"
        assert call_args[1]["collection"] == "col"
