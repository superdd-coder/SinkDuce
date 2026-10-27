"""Offline baseline: real ingest/Direct/Qdrant, controlled embeddings, no LLM.

All Qdrant writes stay in memory. Parser artifacts use the system temporary directory and
are removed on exit. This fixture measures wiring, never business quality.
"""

import asyncio
import hashlib
import json
import math
import platform
import re
import tempfile
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from qdrant_client.qdrant_client import QdrantClient

from src.db.qdrant import QdrantManager
from src.evaluation.service import PipelineRunner
from src.rag.direct_query import DirectQueryModule
from src.rag.retriever import Retriever
from src.tasks.task_manager import Task


class ControlledEmbedding:
    def __init__(self, corpus):
        terms = sorted({term for doc in corpus.values() for term in self.tokenize(doc["text"])})
        self.terms = {term: index for index, term in enumerate(terms)}
        self.dimensions = max(len(terms), 1)

    def embed_query(self, text):
        counts = Counter(self.tokenize(text))
        vector = [0.0] * self.dimensions
        for term, index in self.terms.items():
            vector[index] = float(counts[term])
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def embed_texts(self, texts):
        return [self.embed_query(text) for text in texts]

    @staticmethod
    def tokenize(text):
        # Frozen surrogate model, independent of the production BM25 tokenizer.
        text = text.lower()
        tokens = re.findall(r"[a-z0-9]+|[一-鿿]", text)
        for run in re.findall(r"[一-鿿]+", text):
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
        return tokens


def _make_runner(dataset, search_mode):
    from src.tasks import handlers

    if dataset.manifest["kind"] != "controlled":
        raise ValueError("The controlled fixture accepts only controlled datasets")
    embedding = ControlledEmbedding(dataset.corpus)
    db = QdrantManager.__new__(QdrantManager)
    db.client = QdrantClient(":memory:")
    collection = "controlled-evaluation"
    db.create_collection(collection, embedding.dimensions, {
        "chunk_mode": "parent_child", "contextual_enabled": False, "cloud_parsing": False,
        "parent_chunk_size": 1024, "child_chunk_size": 128,
    })
    services = SimpleNamespace(db=db, embedding=embedding, config=SimpleNamespace(mineru=None), catalog=None)

    def enrich(chunks, *args, on_chunk_ready, **kwargs):
        for chunk in reversed(chunks):
            on_chunk_ready(chunk, "")
        return chunks

    with tempfile.TemporaryDirectory(prefix="controlled-rag-") as temporary, \
         patch.object(handlers, "services", services), \
         patch.object(handlers, "get_collection_embedding", return_value=embedding), \
         patch.object(handlers, "_do_enrich", side_effect=enrich), \
         patch("src.rag.contextual.resolve_ingest_vision", return_value=(None, "")), \
         patch("src.rag.contextual.enrichment_model_is_visual", return_value=False), \
         patch("src.rag.contextual.resolve_enrichment_target", return_value=(None, "")):
        for index, (id_, document) in enumerate(dataset.corpus.items()):
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", id_):
                raise ValueError("Controlled document IDs must be simple filenames")
            path = Path(temporary) / f"{index}.txt"
            path.write_text(document["text"], encoding="utf-8")
            asyncio.run(handlers.upload_handler(Task(id=f"controlled-{index}", filename=id_),
                                                str(path), collection, id_))
    direct = DirectQueryModule(Retriever(db, embedding), db)
    parameters = {"top_k": 20, "search_mode": search_mode, "rerank_enabled": False,
                  "rerank_top_k": None, "min_score": 0.0, "sparse_llm_tokenize": False}
    # Model objects cannot be serialized as report parameters. Binding the
    # override at the Direct boundary keeps the report explicit and JSON-safe.
    class BoundDirect:
        def retrieve(self, *args, **kwargs):
            result = direct.retrieve(*args, **kwargs, embedding_overrides={collection: embedding})
            # Allows read-only comparison with the pre-fix source snapshot.
            # With rerank disabled and a single collection the old final top K
            # has the same ranking as the candidate top K used by these tests.
            if not hasattr(result, "candidate_chunks"):
                result.candidate_chunks = list(result.chunks)
            return result

    return PipelineRunner(BoundDirect(), mode="direct", collections=[collection], parameters=parameters,
                          run_metadata={"embedding_model": "controlled-bag-of-words",
                                        "embedding_revision": "frozen-v1",
                                        "embedding_vocabulary_sha256": hashlib.sha256(
                                            json.dumps(embedding.terms, sort_keys=True).encode()).hexdigest(),
                                        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                        "python_version": platform.python_version(),
                                        "qdrant_version": version("qdrant-client"),
                                        "llm_calls": 0, "qdrant": "local-memory"})


def make_runner(dataset):
    return _make_runner(dataset, "hybrid")


def make_dense_runner(dataset):
    return _make_runner(dataset, "dense")
