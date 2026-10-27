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
        args = db.update_collection_config.call_args
        assert args[0][1]["sparse_lock"] is True
        assert args[0][1]["sparse_lock_at"] > 0

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
        db.update_collection_config.assert_called_once_with(
            "col", {"sparse_lock": False, "sparse_lock_at": 0}
        )

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
        # Lock should be released (with timestamp cleared)
        assert call("col", {"sparse_lock": False, "sparse_lock_at": 0}) in db.update_collection_config.call_args_list

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

    def test_lock_failure_raises_readable_error(self):
        """Lock timeout surfaces a readable cause (was: silent None → generic
        'Sparse recalc failed' in task.error)."""
        import pytest as _pytest
        from src.rag.sparse_recalc import run_sparse_recalc, SparseRecalcLocked
        db = MagicMock()
        db.get_collection_config.return_value = {"sparse_lock": True}  # busy

        with patch("src.rag.sparse_recalc.LOCK_TIMEOUT", 0.1):
            with _pytest.raises(SparseRecalcLocked) as exc_info:
                run_sparse_recalc(db, "col")

        assert "sparse_lock" in str(exc_info.value)
        # Should NOT have upserted
        db.upsert_sparse_vectors.assert_not_called()

    def test_scroll_error_propagates_real_cause(self):
        """If rebuild fails mid-way, the real exception propagates (was:
        swallowed into a silent None). Counter is NOT reset."""
        import pytest as _pytest
        from src.rag.sparse_recalc import run_sparse_recalc
        db = MagicMock()
        db.get_collection_config.side_effect = [
            {"sparse_lock": False, "sparse_recalc_counter": 5000},
        ]
        db.scroll_points.side_effect = RuntimeError("Qdrant down")

        with _pytest.raises(RuntimeError, match="Qdrant down"):
            run_sparse_recalc(db, "col")

        # Counter should NOT have been reset (no call with counter=0)
        reset_calls = [
            c for c in db.update_collection_config.call_args_list
            if "sparse_recalc_counter" in str(c) and "0" in str(c)
        ]
        assert len(reset_calls) == 0
        # Lock must still be released (with timestamp cleared)
        assert call("col", {"sparse_lock": False, "sparse_lock_at": 0}) in db.update_collection_config.call_args_list

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
        tman_mock.has_active_task.return_value = False
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

    def test_cross_threshold_skips_when_one_active(self):
        """Batch uploads cross the threshold repeatedly while an earlier
        rebuild is queued/running — no duplicate tasks."""
        from src.tasks.handlers import _bump_sparse_recalc_counter
        db_mock = MagicMock()
        db_mock.get_collection_config.return_value = {
            "sparse_recalc_threshold": 10,
            "sparse_recalc_counter": 9,
        }
        tman_mock = MagicMock()
        tman_mock.has_active_task.return_value = True
        with patch("src.tasks.handlers.services") as svc:
            svc.db = db_mock
            with patch("src.tasks.task_manager", tman_mock):
                _bump_sparse_recalc_counter("col", 5)

        tman_mock.create_task.assert_not_called()
        tman_mock.has_active_task.assert_called_once_with("col", "sparse_recalc")


# ── Batched sparse write-back ─────────────────────────────────

def test_rebuild_preserves_ingest_metadata_and_excludes_parent_containers():
    from src.rag.sparse_recalc import run_sparse_recalc

    db = MagicMock()
    db.get_collection_config.return_value = {"sparse_lock": False}
    db.scroll_points.return_value = ([
        {"id": "parent", "payload": {"text": "parentonly", "chunk_type": "parent"}},
        {"id": "child", "payload": {"text": "ordinary", "chunk_type": "child",
            "source_label": "zebratrigger.txt", "context": "ambertrigger",
            "images": [{"ocr_text": "quartztrigger"}]}},
    ], None)
    result = run_sparse_recalc(db, "col")
    saved = next(c.args[1]["sparse_vocab"] for c in db.update_collection_config.call_args_list
                 if "sparse_vocab" in c.args[1])
    assert {"zebratrigger", "ambertrigger", "quartztrigger"}.issubset(saved["term_to_id"])
    assert "parentonly" not in saved["term_to_id"]
    assert result["rebuilt_chunks"] == 1
    assert db.upsert_sparse_vectors.call_args.args[1] == ["child"]


def test_rebuild_keeps_legacy_vector_format_until_explicit_migration():
    from src.rag.sparse_recalc import run_sparse_recalc

    db = MagicMock()
    db.get_collection_config.return_value = {"sparse_lock": False, "sparse_vocab": {
        "term_to_id": {"alpha": 0}, "doc_freqs": {0: 1}, "avg_dl": 1, "doc_count": 1,
    }}
    db.scroll_points.return_value = ([{"id": "child", "payload": {"text": "alpha"}}], None)
    run_sparse_recalc(db, "col")
    saved = next(c.args[1]["sparse_vocab"] for c in db.update_collection_config.call_args_list
                 if "sparse_vocab" in c.args[1])
    assert saved["vector_format"] == "bm25_idf_v1"


class TestBatchedSparseUpsert:
    def _mgr(self):
        from src.db.qdrant import QdrantManager
        mgr = QdrantManager.__new__(QdrantManager)
        mgr.client = MagicMock()
        return mgr

    def test_small_write_single_batch(self):
        mgr = self._mgr()
        mgr.upsert_sparse_vectors("col", ["a", "b"], [{0: 1.0}, {1: 2.0}])
        assert mgr.client.update_vectors.call_count == 1

    def test_large_write_batched(self):
        mgr = self._mgr()
        ids = [f"p{i}" for i in range(1200)]
        vecs = [{0: 1.0} for _ in ids]
        mgr.upsert_sparse_vectors("col", ids, vecs)
        assert mgr.client.update_vectors.call_count == 3  # 500 + 500 + 200
        sizes = [len(c.kwargs["points"]) for c in mgr.client.update_vectors.call_args_list]
        assert sizes == [500, 500, 200]

    def test_empty_noop(self):
        mgr = self._mgr()
        mgr.upsert_sparse_vectors("col", [], [])
        mgr.client.update_vectors.assert_not_called()

    def test_empty_vectors_skipped(self):
        """A chunk whose text tokenizes to nothing yields an empty sparse
        dict; Qdrant 422s the whole update_vectors batch on it, so it must
        be dropped instead of sent."""
        mgr = self._mgr()
        mgr.upsert_sparse_vectors("col", ["a", "b", "c"], [{0: 1.0}, {}, {1: 2.0}])
        assert mgr.client.update_vectors.call_count == 1
        sent = mgr.client.update_vectors.call_args.kwargs["points"]
        assert [p.id for p in sent] == ["a", "c"]

    def test_all_empty_noop(self):
        mgr = self._mgr()
        mgr.upsert_sparse_vectors("col", ["a", "b"], [{}, {}])
        mgr.client.update_vectors.assert_not_called()


# ── Backlog re-trigger after a successful rebuild ─────────────

class TestBacklogRetrigger:
    def test_requeues_when_counter_still_over_threshold(self):
        import asyncio
        from unittest.mock import MagicMock
        from src.tasks.handlers import sparse_recalc_handler
        db_mock = MagicMock()
        db_mock.get_collection_config.return_value = {
            "sparse_recalc_counter": 6000,
            "sparse_recalc_threshold": 5000,
        }
        with patch("src.tasks.handlers.services") as svc:
            svc.db = db_mock
            with patch("src.rag.sparse_recalc.run_sparse_recalc") as run_mock:
                run_mock.return_value = {"collection": "col", "rebuilt_chunks": 10}
                with patch("src.tasks.handlers._schedule_sparse_recalc") as sched:
                    result = asyncio.run(
                        sparse_recalc_handler(MagicMock(), collection="col")
                    )
        assert result["rebuilt_chunks"] == 10
        sched.assert_called_once()

    def test_no_requeue_when_counter_below_threshold(self):
        import asyncio
        from src.tasks.handlers import sparse_recalc_handler
        db_mock = MagicMock()
        db_mock.get_collection_config.return_value = {
            "sparse_recalc_counter": 100,
            "sparse_recalc_threshold": 5000,
        }
        with patch("src.tasks.handlers.services") as svc:
            svc.db = db_mock
            with patch("src.rag.sparse_recalc.run_sparse_recalc") as run_mock:
                run_mock.return_value = {"collection": "col", "rebuilt_chunks": 10}
                with patch("src.tasks.handlers._schedule_sparse_recalc") as sched:
                    asyncio.run(sparse_recalc_handler(MagicMock(), collection="col"))
        sched.assert_not_called()


# ── Stale lock takeover ───────────────────────────────────────

class TestStaleLockTakeover:
    def test_takes_over_stale_lock(self):
        """A lock left by a dead process (age > LOCK_STALE_SEC) is taken over
        instead of timing out every rebuild forever."""
        from src.rag.sparse_recalc import _acquire_lock
        import time as _t
        db = MagicMock()
        db.get_collection_config.return_value = {
            "sparse_lock": True,
            "sparse_lock_at": _t.time() - 3600,  # 1h old
        }
        with patch("src.rag.sparse_recalc.LOCK_TIMEOUT", 0.2):
            assert _acquire_lock(db, "col") is True
        db.update_collection_config.assert_called_once()
        args = db.update_collection_config.call_args
        assert args[0][1]["sparse_lock"] is True
        assert args[0][1]["sparse_lock_at"] > 0

    def test_waits_on_fresh_lock(self):
        from src.rag.sparse_recalc import _acquire_lock
        import time as _t
        db = MagicMock()
        db.get_collection_config.return_value = {
            "sparse_lock": True,
            "sparse_lock_at": _t.time(),  # fresh
        }
        with patch("src.rag.sparse_recalc.LOCK_TIMEOUT", 0.2):
            assert _acquire_lock(db, "col") is False

    def test_legacy_lock_without_timestamp_waits(self):
        """Old-format locks (no timestamp) are never force-taken — startup
        lock-clearing handles those."""
        from src.rag.sparse_recalc import _acquire_lock
        db = MagicMock()
        db.get_collection_config.return_value = {"sparse_lock": True}
        with patch("src.rag.sparse_recalc.LOCK_TIMEOUT", 0.2):
            assert _acquire_lock(db, "col") is False

    def test_release_clears_timestamp(self):
        from src.rag.sparse_recalc import _release_lock
        db = MagicMock()
        _release_lock(db, "col")
        db.update_collection_config.assert_called_once_with(
            "col", {"sparse_lock": False, "sparse_lock_at": 0}
        )


class TestClearSparseLocks:
    def _mgr(self):
        from src.db.qdrant import QdrantManager
        mgr = QdrantManager.__new__(QdrantManager)
        mgr.client = MagicMock()
        col = MagicMock()
        col.name = "colA"
        col2 = MagicMock()
        col2.name = "colB"
        mgr.client.get_collections.return_value = MagicMock(collections=[col, col2])
        return mgr

    def test_clears_only_locked_collections(self):
        mgr = self._mgr()
        configs = {"colA": {"sparse_lock": True}, "colB": {"sparse_lock": False}}
        mgr.get_collection_config = MagicMock(side_effect=lambda n: configs[n])
        mgr.update_collection_config = MagicMock()
        cleared = mgr.clear_sparse_locks()
        assert cleared == 1
        mgr.update_collection_config.assert_called_once_with(
            "colA", {"sparse_lock": False, "sparse_lock_at": 0}
        )
