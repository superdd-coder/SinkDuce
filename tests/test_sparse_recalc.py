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
            "col", {"sparse_lock": False, "sparse_lock_at": 0, "sparse_rebuild_stage": ""}
        )

    def test_release_lock_swallows_error(self):
        from src.rag.sparse_recalc import _release_lock
        db = MagicMock()
        db.update_collection_config.side_effect = RuntimeError("network down")
        # Should not raise
        _release_lock(db, "col")


# ── Rebuild flow tests ────────────────────────────────────────

@pytest.fixture
def index(monkeypatch):
    from qdrant_client.qdrant_client import QdrantClient
    from src.db.qdrant import QdrantManager
    from src.services import services

    db = QdrantManager.__new__(QdrantManager)
    db.client = QdrantClient(":memory:")
    monkeypatch.setattr(services, "config", None)
    db.create_collection("col", 2, {"sparse_recalc_counter": 50, "sparse_repair_pending": True})
    return db


def _points(db, payloads):
    db.upsert_points("col", list(range(1, len(payloads) + 1)), [[1., 0.]] * len(payloads), payloads)


class TestRunSparseRecalc:
    def test_empty_collection(self, index):
        from src.rag.sparse_recalc import run_sparse_recalc
        assert run_sparse_recalc(index, "col")["rebuilt_chunks"] == 0
        config = index.get_collection_config("col")
        assert config["sparse_recalc_counter"] == 0 and not config["sparse_lock"]
        assert not config["sparse_repair_pending"]

    def test_full_flow(self, index, monkeypatch):
        from src.rag import sparse_recalc
        monkeypatch.setattr(sparse_recalc, "SCROLL_BATCH", 2)
        _points(index, [{"text": "hello world"}, {"text": "foo bar"}, {"text": "hello foo"}, {"text": ""}])
        assert sparse_recalc.run_sparse_recalc(index, "col")["rebuilt_chunks"] == 3
        config = index.get_collection_config("col")
        assert config["sparse_recalc_counter"] == 0
        assert set(config["sparse_vocab"]["term_to_id"]) == {"hello", "foo", "bar", "world"}
        rows = index.client.retrieve("col", [1, 2, 3, 4], with_vectors=True)
        assert all("sparse" in row.vector for row in rows[:3])
        assert rows[-1].vector == [1., 0.] or "sparse" not in rows[-1].vector

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

    @pytest.mark.parametrize("stage", ["scroll", "write", "vocabulary", "publish"])
    def test_failure_preserves_live_index_and_allows_retry(self, index, monkeypatch, stage):
        from src.rag import sparse_recalc
        from src.rag.sparse_encoder import SparseEncoder
        from src.db.index_routing import STAGE_PREFIX
        _points(index, [{"text": "alpha"}, {"text": "beta"}])
        initial = SparseEncoder()
        index.upsert_sparse_vectors("col", [1, 2], initial.encode(["beta", "alpha"]))
        initial.save(index, "col")
        original = index.client.retrieve("col", [1, 2], with_vectors=True)
        def fail(*args, **kwargs):
            raise RuntimeError("interrupted rebuild")
        with monkeypatch.context() as m:
            if stage == "scroll":
                m.setattr(index.client, "scroll", fail)
            elif stage == "write":
                upsert = index.client.upsert
                def fail_stage(collection_name, **kwargs):
                    if collection_name.startswith(STAGE_PREFIX):
                        fail()
                    return upsert(collection_name, **kwargs)
                m.setattr(index.client, "upsert", fail_stage)
            elif stage == "vocabulary":
                m.setattr(SparseEncoder, "save", fail)
            else:
                m.setattr(index.client, "publish", fail)
            with pytest.raises(RuntimeError, match="interrupted rebuild"):
                sparse_recalc.run_sparse_recalc(index, "col")
        assert index.client.retrieve("col", [1, 2], with_vectors=True) == original
        config = index.get_collection_config("col")
        assert config["sparse_vocab"]["term_to_id"] == initial.term_to_id
        assert config["sparse_repair_pending"] and not config["sparse_lock"]
        assert not any(c.name.startswith(STAGE_PREFIX) for c in index.client.raw.get_collections().collections)
        with index.client.snapshot("col"):
            assert sparse_recalc.run_sparse_recalc(index, "col")["rebuilt_chunks"] == 2
            assert index.client.retrieve("col", [1, 2], with_vectors=True) == original
        saved = index.get_collection_config("col")["sparse_vocab"]["term_to_id"]
        assert index.client.retrieve("col", [1], with_vectors=True)[0].vector["sparse"].indices == [saved["alpha"]]


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

def test_rebuild_preserves_ingest_metadata_and_excludes_parent_containers(index):
    from src.rag.sparse_recalc import run_sparse_recalc
    rows = [{"text": "parentonly", "chunk_type": "parent"},
            {"text": "ordinary", "chunk_type": "child", "source_label": "zebratrigger.txt",
             "context": "ambertrigger", "images": [{"ocr_text": "quartztrigger"}]},
            {"text": "privatequestion", "source_kind": "qa_question"}]
    _points(index, rows)
    index.upsert_sparse_vectors("col", [3], [{77: 2.0}])
    assert run_sparse_recalc(index, "col")["rebuilt_chunks"] == 1
    saved = index.get_collection_config("col")["sparse_vocab"]["term_to_id"]
    assert {"zebratrigger", "ambertrigger", "quartztrigger"}.issubset(saved)
    assert not {"parentonly", "privatequestion"}.intersection(saved)
    retrieved = index.client.retrieve("col", [1, 2, 3], with_vectors=True)
    assert [r.payload for r in retrieved] == rows
    assert retrieved[2].vector["sparse"].indices == [77]


def test_rebuild_keeps_legacy_vector_format_until_explicit_migration(index):
    from src.rag.sparse_recalc import run_sparse_recalc
    index.update_collection_config("col", {"sparse_vocab": {
        "term_to_id": {"alpha": 0}, "doc_freqs": {"0": 1}, "avg_dl": 1, "doc_count": 1,
    }})
    _points(index, [{"text": "alpha"}])
    run_sparse_recalc(index, "col")
    assert index.get_collection_config("col")["sparse_vocab"]["vector_format"] == "bm25_idf_v1"


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
            "col", {"sparse_lock": False, "sparse_lock_at": 0, "sparse_rebuild_stage": ""}
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


def test_restart_resumes_interrupted_keyword_rebuild_without_touching_live_index(index):
    from src.rag.sparse_recalc import SPARSE_STAGE_PREFIX, run_sparse_recalc

    name = SPARSE_STAGE_PREFIX + "abandoned"
    index.create_collection(name, 2, {"sparse_recalc_counter": 0})
    index.update_collection_config("col", {"sparse_lock": True, "sparse_rebuild_stage": name})
    pending = []
    assert index.clear_sparse_locks(on_pending=pending.append) == 1
    assert pending == ["col"] and not index.client.raw.collection_exists(name)
    _points(index, [{"text": "source"}])
    run_sparse_recalc(index, "col")
    assert index.clear_sparse_locks(on_pending=pending.append) == 0
    assert pending == ["col"] and index.count_points("col") == 2

def test_edit_during_keyword_rebuild_is_preserved(index, monkeypatch):
    from src.rag import sparse_recalc
    _points(index, [{"text": "alpha"}])
    build = sparse_recalc._build_replacement
    def build_then_edit(db, collection, stage, config):
        result = build(db, collection, stage, config)
        db.client.set_payload(collection, {"archived": True}, points=[1])
        return result
    with monkeypatch.context() as m:
        m.setattr(sparse_recalc, "_build_replacement", build_then_edit)
        with pytest.raises(sparse_recalc.SparseIndexChanged):
            sparse_recalc.run_sparse_recalc(index, "col")
    assert index.client.retrieve("col", [1])[0].payload["archived"]
    sparse_recalc.run_sparse_recalc(index, "col")
    assert index.client.retrieve("col", [1])[0].payload["archived"]


def test_replacement_preserves_server_index_limits(index, monkeypatch):
    from qdrant_client import models
    from src.rag.sparse_recalc import run_sparse_recalc

    get_collection = index.client.get_collection
    create_collection = MagicMock(wraps=index.client.create_collection)
    def remote_info(collection_name):
        info = get_collection(collection_name)
        info.config.strict_mode_config = models.StrictModeConfigOutput(enabled=True)
        return info
    monkeypatch.setattr(index.client, "get_collection", remote_info)
    monkeypatch.setattr(index.client, "create_collection", create_collection)
    run_sparse_recalc(index, "col")
    strict = create_collection.call_args.kwargs["strict_mode_config"]
    assert isinstance(strict, models.StrictModeConfig) and strict.enabled
