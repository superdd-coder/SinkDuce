"""Regression checks for searchable vector identity and failed embeddings."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.db.qdrant import QdrantManager
from src.rag.chunker import Chunk
from src.rag.direct_query import DirectQueryModule
from src.rag.retriever import Retriever
from src.tasks.handlers import _EmbedBatcher


class Embedding:
    dimensions = 2

    def embed_texts(self, texts):
        return [[float(len(text)), 1.0] for text in texts]

    def embed_query(self, query):
        return [float(len(query)), 1.0]


@pytest.fixture
def ingest(monkeypatch, tmp_path):
    from src.tasks import handlers
    from src.tasks.task_manager import Task

    path = tmp_path / "evidence.txt"
    path.write_text("alpha beta gamma", encoding="utf-8")
    chunks = [
        Chunk("alpha beta gamma", {"chunk_id": "parent"}, chunk_type="parent"),
        Chunk("alpha", {"chunk_id": "child-a"}, parent_id="parent", chunk_type="child"),
        Chunk("beta gamma", {"chunk_id": "child-b"}, parent_id="parent", chunk_type="child"),
    ]
    db = MagicMock()
    db.get_collection_config.return_value = {
        "chunk_mode": "parent_child", "contextual_enabled": False,
        "cloud_parsing": False,
    }
    db.collection_exists.return_value = True
    service = SimpleNamespace(db=db, config=SimpleNamespace(mineru=None), embedding=Embedding(), catalog=None)
    monkeypatch.setattr(handlers, "services", service)
    monkeypatch.setattr(handlers, "get_collection_embedding", lambda *args: service.embedding)
    chunker = MagicMock()
    chunker.chunk_with_metadata.return_value = chunks
    monkeypatch.setattr(handlers, "ParentChildChunker", lambda **kwargs: chunker)
    monkeypatch.setattr(handlers, "_bump_sparse_recalc_counter", lambda *args: None)

    def enrich(chunks, *args, on_chunk_ready, **kwargs):
        # Context completion can arrive in any order, even without parents.
        for chunk in reversed(chunks):
            on_chunk_ready(chunk, "")
        return chunks

    monkeypatch.setattr(handlers, "_do_enrich", enrich)

    def run():
        return asyncio.run(handlers.upload_handler(Task(id="integrity", filename=path.name),
                                                  str(path), "isolated", path.name))

    return run, db, service, chunks


def test_ingest_sparse_vectors_belong_to_their_child_not_callback_position(ingest):
    run, db, service, chunks = ingest
    run()
    write = db.upsert_sparse_vectors.call_args.kwargs
    # The encoder persists its term ids; decode each vector using that state.
    vocab = db.update_collection_config.call_args.args[1]["sparse_vocab"]
    inverse = {v: k for k, v in vocab["term_to_id"].items()}
    actual = {id_: {inverse[term] for term in vector}
              for id_, vector in zip(write["ids"], write["sparse_vectors"], strict=True)}
    assert set(actual) == {"child-a", "child-b"}
    assert "alpha" in actual["child-a"] and "beta" not in actual["child-a"]
    assert "beta" in actual["child-b"] and "alpha" not in actual["child-b"]


def test_failed_embedding_aborts_ingest_before_points_are_written(ingest, monkeypatch):
    run, db, service, chunks = ingest
    service.embedding.embed_texts = MagicMock(side_effect=RuntimeError("embedding unavailable"))
    monkeypatch.setattr("time.sleep", lambda _: None)
    with pytest.raises(Exception, match="[Ee]mbedding"):
        run()
    db.upsert_points.assert_not_called()
    db.upsert_sparse_vectors.assert_not_called()


def test_failed_embedding_does_not_publish_vocabulary_for_unstored_chunks(ingest, monkeypatch):
    run, db, service, chunks = ingest
    service.embedding.embed_texts = MagicMock(side_effect=RuntimeError("embedding unavailable"))
    monkeypatch.setattr("time.sleep", lambda _: None)
    with pytest.raises(Exception, match="[Ee]mbedding"):
        run()
    db.update_collection_config.assert_not_called()


def test_sparse_failure_is_visible_in_ingest_trace(ingest, monkeypatch, tmp_path):
    from src.rag.ingest_trace import load_trace

    run, db, service, chunks = ingest
    monkeypatch.setattr("src.tasks.handlers._do_sparse", lambda *args: None)
    run()
    sparse = next(step for step in load_trace(tmp_path)["steps"] if step["id"] == "sparse")
    assert sparse["status"] == "error"
    assert sparse["data"]["fallback"] == "dense"


@pytest.mark.parametrize("response", [[], [[0.0, 0.0]], [[1.0]], [[float("nan"), 1.0]]])
def test_invalid_embedding_batch_is_rejected(response, monkeypatch):
    embedding = Embedding()
    embedding.embed_texts = lambda texts: response
    batcher = _EmbedBatcher(embedding)
    chunk = Chunk("searchable evidence", {"_embed_idx": 0})
    batcher.on_ready(chunk, "")
    batcher.flush()
    monkeypatch.setattr("time.sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="[Ee]mbedding"):
        batcher.wait_all([chunk])


def test_parent_container_keeps_zero_dense_vector():
    batcher = _EmbedBatcher(Embedding())
    chunks = [Chunk("parent", {"_embed_idx": 0}, chunk_type="parent"),
              Chunk("child", {"_embed_idx": 1}, chunk_type="child")]
    for chunk in reversed(chunks):
        batcher.on_ready(chunk, "")
    batcher.flush()
    assert batcher.wait_all(chunks) == [[0.0, 0.0], [5.0, 1.0]]


@pytest.mark.parametrize("ids,vectors", [(["a", "b"], [{0: 1.0}]),
                                       (["a"], [{0: 1.0}, {1: 1.0}]),
                                       (["a"], []), ([], [{0: 1.0}])])
def test_sparse_write_rejects_mismatched_lengths(ids, vectors):
    manager = QdrantManager.__new__(QdrantManager)
    manager.client = MagicMock()
    with pytest.raises(ValueError, match="length"):
        manager.upsert_sparse_vectors("isolated", ids, vectors)
    manager.client.update_vectors.assert_not_called()


@pytest.mark.parametrize("vectors,payloads", [([], [{"text": "a"}]),
                                            ([[1.0, 1.0]], []),
                                            ([[1.0, 1.0], [1.0, 1.0]], [{"text": "a"}])])
def test_dense_write_rejects_mismatched_lengths(vectors, payloads):
    manager = QdrantManager.__new__(QdrantManager)
    manager.client = MagicMock()
    with pytest.raises(ValueError, match="length"):
        manager.upsert_points("isolated", ["a"], vectors, payloads)
    manager.client.upsert.assert_not_called()


def test_concurrent_hybrid_queries_keep_collection_specific_term_ids(monkeypatch):
    from src.rag import sparse_encoder

    a_loaded = threading.Event()
    b_encoded = threading.Event()
    db = MagicMock()
    db.get_collection_config.side_effect = lambda col: {"sparse_vocab": {
        "term_to_id": {"alpha": 101 if col == "a" else 202},
        "doc_freqs": {101 if col == "a" else 202: 1}, "avg_dl": 1, "doc_count": 1,
    }}
    encoded = {}

    def preprocess(query, llm):
        if query == "a":
            a_loaded.set()
            assert b_encoded.wait(5)
        return "alpha", []

    def search(**kwargs):
        encoded[kwargs["collection"]] = kwargs["sparse_vector"]
        if kwargs["collection"] == "b":
            b_encoded.set()
        return []

    monkeypatch.setattr(sparse_encoder, "preprocess_query_for_sparse", preprocess)
    db.hybrid_search.side_effect = search
    retriever = Retriever(db, Embedding())
    retriever._get_sparse_encoder()  # Initialize the old shared state before racing.
    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(retriever._hybrid_retrieve, "a", [1.0, 1.0], "a", 5)
        assert a_loaded.wait(5)
        b = executor.submit(retriever._hybrid_retrieve, "b", [1.0, 1.0], "b", 5)
        a.result(timeout=10)
        b.result(timeout=10)
    assert set(encoded["a"]) == {101}
    assert set(encoded["b"]) == {202}


def test_parent_child_hybrid_uses_collection_embedding():
    from qdrant_client.models import Filter

    retriever = Retriever(MagicMock(), Embedding())
    retriever.retrieve = MagicMock(return_value=[])
    override = Embedding()
    direct = DirectQueryModule(retriever, retriever.db)
    direct._retrieve_parent_child("alpha", "a", 5, embedding=override, search_mode="hybrid")
    assert retriever.retrieve.call_args.kwargs.get("embedding_override") is override
    assert isinstance(retriever.retrieve.call_args.kwargs["filter_condition"], Filter)
