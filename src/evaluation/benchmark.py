"""Batch passage indexing and real retrieval against an isolated local Qdrant.

This measures the retrieval core, preserving public passage boundaries. File
parsing, enrichment and answer generation require separate end-to-end tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import uuid
from importlib.metadata import version
from pathlib import Path

from qdrant_client.qdrant_client import QdrantClient
from qdrant_client.models import SparseVector

from src.db.qdrant import QdrantManager
from src.evaluation.models import StageResult, load_dataset
from src.evaluation.service import PipelineRunner, evaluate
from src.rag.direct_query import DirectQueryModule
from src.rag.retriever import Retriever
from src.rag.reranker import Reranker
from src.rag.sparse_encoder import SparseEncoder


def _validate(vectors, count, dimensions):
    if len(vectors) != count:
        raise ValueError("Embedding batch length mismatch")
    for vector in vectors:
        if (len(vector) != dimensions or not all(math.isfinite(value) for value in vector)
                or not any(value != 0 for value in vector)):
            raise ValueError("Embedding contains wrong dimensions, nonfinite or zero vector")


class _QueryCache:
    def __init__(self, provider):
        self.provider = provider
        self.dimensions = provider.dimensions
        self.cache = {}

    def embed_query(self, text):
        if text not in self.cache:
            vector = self.provider.embed_query(text)
            _validate([vector], 1, self.dimensions)
            self.cache[text] = vector
        return self.cache[text]


class BenchmarkIndex:
    collection = "public-benchmark"

    def __init__(self, dataset, embedding, path, *, model_identity, batch_size=100):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.dataset, self.path = dataset, Path(path)
        self.embedding = _QueryCache(embedding)
        self.model_identity = dict(model_identity)
        sparse_source = Path(__file__).resolve().parents[1] / "rag" / "sparse_encoder.py"
        identity = {"dataset": dataset.fingerprint, "model": model_identity,
                    "dimensions": embedding.dimensions, "recipe": "official-title-newline-passage-v1",
                    "sparse_code_sha256": hashlib.sha256(sparse_source.read_bytes()).hexdigest()}
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        marker = self.path / "complete.json"
        reuse = marker.exists()
        if reuse:
            self.stats = json.loads(marker.read_text(encoding="utf-8"))
            if self.stats["fingerprint"] != fingerprint:
                raise ValueError("Index fingerprint differs; use another index directory")
        else:
            if self.path.exists():
                raise FileExistsError("Existing/incomplete index directory cannot be overwritten")
            self.path.mkdir(parents=True)
            self.stats = {"fingerprint": fingerprint, "identity": identity}
        self.db = QdrantManager.__new__(QdrantManager)
        self.db.client = QdrantClient(path=str(self.path / "qdrant"))
        try:
            if not reuse:
                started = time.perf_counter()
                self.db.create_collection(self.collection, embedding.dimensions, {
                    "chunk_mode": "normal", "contextual_enabled": False, "cloud_parsing": False,
                    "sparse_llm_tokenize": False,
                })
                documents = list(dataset.corpus.items())
                texts = ["\n".join(part for part in [row.get("title", ""), row["text"]] if part)
                         for _, row in documents]
                sparse_started = time.perf_counter()
                encoder, sparse = SparseEncoder.rebuild_from_texts(texts)
                self.stats["sparse_build_seconds"] = time.perf_counter() - sparse_started
                dense_started = time.perf_counter()
                for start in range(0, len(documents), batch_size):
                    batch = documents[start:start + batch_size]
                    vectors = embedding.embed_texts(texts[start:start + batch_size])
                    _validate(vectors, len(batch), embedding.dimensions)
                    ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"benchmark:{id_}")) for id_, _ in batch]
                    payloads = [{"text": row["text"], "source": id_, "evaluation_id": id_,
                                 "chunk_type": "text", "is_current": True,
                                 "source_label": row.get("title") or id_} for id_, row in batch]
                    self.db.upsert_points(self.collection, ids, vectors, payloads)
                    self.db.upsert_sparse_vectors(self.collection, ids, sparse[start:start + batch_size])
                    print(f"Indexed {min(start + batch_size, len(documents))}/{len(documents)} passages", flush=True)
                self.stats["dense_and_storage_seconds"] = time.perf_counter() - dense_started
                encoder.save(self.db, self.collection)
                self.stats.update(indexed_passages=len(documents), input_characters=sum(map(len, texts)),
                                  index_seconds=time.perf_counter() - started,
                                  empty_sparse_passages=sum(not vector for vector in sparse),
                                  provider_truncation_limit=getattr(embedding, "max_characters", None))
                if self.db.get_collection_config(self.collection).get("sparse_vocab", {}).get("doc_count") != len(documents):
                    raise ValueError("Sparse vocabulary was not persisted completely")
                marker.write_text(json.dumps(self.stats, indent=2) + "\n", encoding="utf-8")
            self.encoder = SparseEncoder()
            self.encoder.load(self.db, self.collection)
            self.direct = DirectQueryModule(Retriever(self.db, self.embedding), self.db)
        except BaseException:
            self.db.client.close()
            raise

    def runner(self, mode, *, top_k=20, reranker=None, rerank_top_k=20):
        if mode not in ("dense", "hybrid", "bm25") or top_k < 1:
            raise ValueError("Invalid benchmark mode or cutoff")
        metadata = {"embedding": self.model_identity, "index": self.stats,
                    "qdrant_version": version("qdrant-client"), "qdrant": "isolated-local",
                    "query_embedding_cache": True, "llm_calls": 0, "answer_generation": False}
        if reranker is not None and mode == "bm25":
            raise ValueError("Use Direct dense/hybrid modes for the reranker comparison")
        parameters = {"top_k": top_k, "search_mode": mode, "rerank_enabled": reranker is not None,
                      "rerank_top_k": rerank_top_k if reranker is not None else None,
                      "min_score": 0., "sparse_llm_tokenize": False}
        if mode != "bm25":
            direct = (DirectQueryModule(self.direct.retriever, self.db,
                                        reranker=Reranker(reranker, top_k=rerank_top_k))
                      if reranker is not None else self.direct)
            collection, embedding = self.collection, self.embedding

            class BoundDirect:
                def retrieve(self, *args, **kwargs):
                    return direct.retrieve(*args, **kwargs, embedding_overrides={collection: embedding})

            return PipelineRunner(BoundDirect(), mode="direct", collections=[collection],
                                  parameters=parameters, run_metadata=metadata)

        def sparse(query):
            from src.identity import get_actor
            from src.identity.visibility import is_collection_visible

            if not is_collection_visible(get_actor(), self.collection):
                raise PermissionError("Benchmark collection is not visible")
            vector = self.encoder.encode_query(query.text)
            rows = []
            if vector:
                rows = self.db.client.query_points(
                    collection_name=self.collection, using="sparse",
                    query=SparseVector(indices=list(vector), values=list(vector.values())),
                    query_filter=self.direct.retriever.exclusion_filter(), limit=top_k,
                ).points
            ids = [row.payload["evaluation_id"] for row in rows]
            return StageResult(candidates=ids, context_ids=ids,
                               context="\n\n".join(row.payload["text"] for row in rows),
                               parameters=dict(metadata, **parameters),
                               trace=[{"kind": "retrieval", "collection": self.collection, "mode": "bm25"}])

        return sparse

    def close(self):
        self.db.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True, help="Read-only existing configuration")
    parser.add_argument("--with-reranker", action="store_true")
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(args.output)
    from src.config import load_config
    from src.providers.embedding import create_embedding_provider
    from src.providers.reranker import create_reranker_provider

    dataset = load_dataset(args.dataset)
    config = load_config(args.config)
    provider_config = config.embedding.default
    if not provider_config:
        raise ValueError("Configure a real Embedding provider before benchmarking")
    provider = create_embedding_provider(provider_config)
    if not provider:
        raise ValueError("Embedding provider is unavailable")
    identity = {"provider": provider_config.provider, "provider_id": provider_config.id,
                "model": provider_config.model, "dimensions": provider.dimensions,
                "endpoint_sha256": hashlib.sha256(provider_config.base_url.encode()).hexdigest(),
                "model_revision": "provider does not expose an immutable revision"}
    args.output.mkdir(parents=True)
    with BenchmarkIndex(dataset, provider, args.index, model_identity=identity) as index:
        # Precompute once so all modes measure cached-query retrieval, not a
        # cold Dense request against a warm Hybrid request. Remote time is separate.
        started = time.perf_counter()
        for query in dataset.queries.values():
            index.embedding.embed_query(query.text)
        query_embedding_seconds = time.perf_counter() - started
        runs = [(mode, index.runner(mode)) for mode in ["dense", "bm25", "hybrid"]]
        if args.with_reranker:
            rerank_config = config.rerank.default
            reranker = create_reranker_provider(rerank_config) if rerank_config else None
            if not reranker:
                raise ValueError("No configured reranker is available")
            runs.append(("hybrid-rerank", index.runner("hybrid", top_k=20, reranker=reranker,
                                                       rerank_top_k=20)))
        for mode, runner in runs:
            report = evaluate(dataset, runner, ks=[5, 10, 20])
            report["setup"] = {"index": index.stats, "query_embedding_seconds": query_embedding_seconds,
                               "unique_query_vectors": len(index.embedding.cache),
                               "latency_scope": "retrieval with precomputed query embeddings",
                               "embedding_token_usage": None,
                               "reranker_model": config.rerank.default.model
                               if mode == "hybrid-rerank" else None}
            (args.output / f"{mode}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                                 allow_nan=False) + "\n", encoding="utf-8")
            print(json.dumps({"mode": mode, "summary": report["summary"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
