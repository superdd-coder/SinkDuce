"""Real local Qdrant exercises ingestion, query caches, and stable gold IDs."""

import math

import pytest

from tests.test_duretrieval import source_rows


class Embedding:
    dimensions = 3

    def __init__(self):
        self.query_calls = 0

    def embed_texts(self, texts):
        return [[float("a" in text), float("b" in text), float("noise" in text)] for text in texts]

    def embed_query(self, text):
        self.query_calls += 1
        return [1., 1., 0.]


def dataset(tmp_path):
    from src.evaluation.duretrieval import write_dataset
    from src.evaluation.models import load_dataset

    path = tmp_path / "dataset"
    write_dataset(*source_rows(), path, source={}, pilot=True)
    return load_dataset(path)


def test_real_benchmark_reuses_query_vectors_and_preserves_official_ids(tmp_path):
    from src.evaluation.benchmark import BenchmarkIndex

    data = dataset(tmp_path)
    emb = Embedding()
    with BenchmarkIndex(data, emb, tmp_path / "index", model_identity={"model": "test"}) as index:
        query = data.queries["q"]
        dense = index.runner("dense")(query)
        hybrid = index.runner("hybrid")(query)
        sparse = index.runner("bm25")(query)
        assert set(dense.candidates[:2]) == {"a", "b"}
        assert set(hybrid.candidates[:2]) == {"a", "b"}
        assert set(sparse.candidates[:2]) == {"a", "b"}
        assert emb.query_calls == 1
        assert not dense.errors and not hybrid.errors and not sparse.errors
        assert hybrid.trace[0]["mode"] == "hybrid"
        assert sparse.trace[0]["mode"] == "bm25"
        assert index.stats["indexed_passages"] == 3


@pytest.mark.parametrize("bad", [[], [[1., 0.]], [[0., 0., 0.]] * 3,
                                  [[math.nan, 0., 1.]] * 3])
def test_bad_embedding_never_publishes_a_complete_index(tmp_path, bad):
    from src.evaluation.benchmark import BenchmarkIndex

    data = dataset(tmp_path)
    emb = Embedding()
    emb.embed_texts = lambda _: bad
    path = tmp_path / "index"
    with pytest.raises(ValueError):
        BenchmarkIndex(data, emb, path, model_identity={"model": "test"})
    assert not (path / "complete.json").exists()


def test_complete_index_can_be_reopened_without_reembedding(tmp_path):
    from src.evaluation.benchmark import BenchmarkIndex

    data = dataset(tmp_path)
    path = tmp_path / "index"
    with BenchmarkIndex(data, Embedding(), path, model_identity={"model": "test"}):
        pass
    emb = Embedding()
    emb.embed_texts = lambda _: pytest.fail("Reopening must not embed passages again")
    with BenchmarkIndex(data, emb, path, model_identity={"model": "test"}) as index:
        assert index.runner("bm25")(data.queries["q"]).candidates


def test_index_refuses_reuse_when_model_identity_changes(tmp_path):
    from src.evaluation.benchmark import BenchmarkIndex

    data = dataset(tmp_path)
    path = tmp_path / "index"
    with BenchmarkIndex(data, Embedding(), path, model_identity={"model": "test"}):
        pass
    with pytest.raises(ValueError, match="fingerprint"):
        BenchmarkIndex(data, Embedding(), path, model_identity={"model": "other"})


def test_index_does_not_overwrite_unrelated_existing_directory(tmp_path):
    from src.evaluation.benchmark import BenchmarkIndex

    data = dataset(tmp_path)
    path = tmp_path / "index"
    path.mkdir()
    (path / "keep").write_text("untouched")
    with pytest.raises(FileExistsError):
        BenchmarkIndex(data, Embedding(), path, model_identity={"model": "test"})
    assert (path / "keep").read_text() == "untouched"


def test_benchmark_reranker_changes_context_but_keeps_original_candidates(tmp_path):
    from src.evaluation.benchmark import BenchmarkIndex

    class Reranker:
        def rerank(self, query, documents, top_k=5):
            return [(i, float(i + 1)) for i in reversed(range(len(documents)))][:top_k]

    data = dataset(tmp_path)
    with BenchmarkIndex(data, Embedding(), tmp_path / "index", model_identity={"model": "test"}) as index:
        query = data.queries["q"]
        base = index.runner("dense")(query)
        result = index.runner("dense", reranker=Reranker(), rerank_top_k=1)(query)
        assert result.candidates == base.candidates
        assert result.context_ids == ["noise"]
        assert result.parameters["rerank_enabled"] is True
