"""BM25 sparse encoder for hybrid search.

Lightweight implementation: whitespace tokenization + character bigrams for CJK.
Returns sparse vectors as {term_id: weight} dicts.

Optional LLM-powered query preprocessing: extracts keywords, expands synonyms,
and removes stop words before tokenization. Controlled by sparse_llm_tokenize
collection config / per-query flag. Agentic RAG enables it by default.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter

from src.rag.agent_prompts import (
    PREPROCESS_SPARSE_QUERY_SYSTEM,
    PREPROCESS_SPARSE_QUERY_USER,
)

logger = logging.getLogger(__name__)


def preprocess_query_for_sparse(
    query: str,
    llm=None,
    *,
    temperature: float | None = None,
) -> tuple[str, list[str]]:
    """Use LLM to extract keywords for BM25 sparse encoding.

    Returns (processed_query, keywords_list).
    - processed_query: space-separated keywords ready for _tokenize()
    - keywords_list: the raw keyword list from LLM (for logging)

    Fallback behaviour (returns original query on any failure):
    - llm is None → returns (query, [])
    - LLM call fails → returns (query, []) + WARNING log
    - LLM returns empty/invalid JSON → returns (query, []) + WARNING log
    """
    if llm is None:
        return query, []

    try:
        raw = llm.generate(
            PREPROCESS_SPARSE_QUERY_USER.format(query=query),
            system=PREPROCESS_SPARSE_QUERY_SYSTEM,
            temperature=temperature if temperature is not None else 0.0,
            max_tokens=512, thinking=False,
        ).strip()

        result = json.loads(raw)
        keywords: list[str] = result.get("keywords", [])

        if not keywords or not isinstance(keywords, list):
            logger.warning(
                "[SPARSE-PREPROCESS] LLM returned empty/invalid keywords for query=%r, "
                "fallback to raw query", query[:120],
            )
            return query, []

        # Filter out empty strings and deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for kw in keywords:
            kw = str(kw).strip()
            kw_lower = kw.lower()
            if kw and kw_lower not in seen:
                seen.add(kw_lower)
                deduped.append(kw)

        if not deduped:
            return query, []

        processed = " ".join(deduped)
        logger.info(
            "[SPARSE-PREPROCESS] query=%r → keywords=%s → processed=%r",
            query[:120], deduped, processed[:200],
        )
        return processed, deduped

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(
            "[SPARSE-PREPROCESS] JSON parse failed for query=%r: %s, fallback to raw query",
            query[:120], e,
        )
        return query, []
    except Exception as e:
        logger.warning(
            "[SPARSE-PREPROCESS] LLM call failed for query=%r: %s, fallback to raw query",
            query[:120], e,
        )
        return query, []


def _tokenize(text: str) -> list[str]:
    """Tokenize text: lowercase, split on whitespace/punctuation, add char bigrams for CJK."""
    text = text.lower()
    tokens = re.findall(r"[a-z0-9]+|[一-鿿]", text)
    # Only adjacent characters form bigrams; separators are real boundaries.
    for run in re.findall(r"[一-鿿]+", text):
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


class SparseEncoder:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.term_to_id: dict[str, int] = {}
        self.doc_freqs: dict[int, int] = {}
        self.avg_dl: float = 0.0
        self._doc_count: int = 0
        self.vector_format = "bm25_tf_v2"

    def save(self, db, collection: str) -> None:
        """Persist vocabulary state to Qdrant collection config."""
        data = {
            "term_to_id": self.term_to_id,
            "doc_freqs": self.doc_freqs,
            "avg_dl": self.avg_dl,
            "doc_count": self._doc_count,
            "vector_format": self.vector_format,
        }
        try:
            db.update_collection_config(collection, {"sparse_vocab": data})
            logger.info("[Sparse] vocab saved to Qdrant collection=%s terms=%d docs=%d avg_dl=%.1f",
                        collection, len(self.term_to_id), self._doc_count, self.avg_dl)
        except Exception:
            logger.warning("[Sparse] failed to save vocab to Qdrant for %s", collection, exc_info=True)

    def _reset_vocab(self) -> None:
        self.term_to_id = {}
        self.doc_freqs = {}
        self.avg_dl = 0.0
        self._doc_count = 0
        self.vector_format = "bm25_tf_v2"

    def load(self, db, collection: str) -> None:
        """Restore vocabulary state from Qdrant collection config.

        Missing vocab clears any previously loaded collection so a shared
        encoder cannot encode queries against the wrong term ids.
        """
        try:
            config = db.get_collection_config(collection)
            data = config.get("sparse_vocab")
        except Exception:
            data = None
        if not data:
            self._reset_vocab()
            logger.info("[Sparse] no vocab found in Qdrant for collection=%s", collection)
            return
        self.vector_format = data.get("vector_format", "bm25_idf_v1")
        if self.vector_format not in ("bm25_tf_v2", "bm25_idf_v1"):
            raise ValueError("Unsupported sparse vector format")
        self.term_to_id = data["term_to_id"]
        self.doc_freqs = {int(k): v for k, v in data["doc_freqs"].items()}
        self.avg_dl = float(data["avg_dl"])
        self._doc_count = int(data["doc_count"])
        logger.info("[Sparse] vocab loaded from Qdrant collection=%s terms=%d docs=%d avg_dl=%.1f",
                    collection, len(self.term_to_id), self._doc_count, self.avg_dl)

    def build_vocab(self, texts: list[str]) -> None:
        """Build vocabulary and document frequencies from a corpus."""
        if not texts:
            return

        for text in texts:
            tokens = _tokenize(text)
            for t in set(tokens):
                if t not in self.term_to_id:
                    self.term_to_id[t] = len(self.term_to_id)
                tid = self.term_to_id[t]
                self.doc_freqs[tid] = self.doc_freqs.get(tid, 0) + 1

        # Preserve old total token length before updating doc_count
        old_total_len = self.avg_dl * self._doc_count
        new_count = len(texts)
        new_total_len = sum(len(_tokenize(t)) for t in texts)
        self._doc_count += new_count
        self.avg_dl = (old_total_len + new_total_len) / self._doc_count if self._doc_count else 1.0

    def encode(self, texts: list[str]) -> list[dict[int, float]]:
        """Encode texts into BM25 sparse vectors. Builds vocabulary on first call."""
        if not texts:
            return []

        self.build_vocab(texts)

        vectors = []
        for text in texts:
            tokens = _tokenize(text)
            vec = self._compute_bm25(tokens)
            vectors.append(vec)
        return vectors

    def encode_query(self, query: str) -> dict[int, float]:
        """Apply IDF exactly once, respecting the persisted document format.

        New vectors store normalized TF; query-time IDF stays current as the
        corpus grows. Legacy vectors retain their stored IDF until a rebuild.
        """
        tokens = _tokenize(query)
        vec = {}
        for t in tokens:
            tid = self.term_to_id.get(t)
            if tid is None:
                continue
            vec[tid] = vec.get(tid, 0) + 1

        if self.vector_format == "bm25_idf_v1":
            return vec
        return {tid: tf * math.log((self._doc_count - self.doc_freqs.get(tid, 0) + .5)
                                  / (self.doc_freqs.get(tid, 0) + .5) + 1)
                for tid, tf in vec.items()}

    def _compute_bm25(self, tokens: list[str]) -> dict[int, float]:
        """Compute BM25 vector for a document's tokens."""
        tf = Counter(tokens)
        dl = len(tokens)

        vec = {}
        for t, count in tf.items():
            tid = self.term_to_id.get(t)
            if tid is None:
                continue
            df = self.doc_freqs.get(tid, 0)
            idf = math.log((self._doc_count - df + 0.5) / (df + 0.5) + 1)
            numerator = count * (self.k1 + 1)
            denominator = count + self.k1 * (1 - self.b + self.b * dl / max(self.avg_dl, 1))
            weight = numerator / denominator
            vec[tid] = weight * idf if self.vector_format == "bm25_idf_v1" else weight
        return vec

    @classmethod
    def rebuild_from_texts(cls, texts: list[str], *, vector_format: str = "bm25_tf_v2") -> tuple["SparseEncoder", list[dict[int, float]]]:
        """Build vocabulary and BM25 vectors from scratch for all texts.

        Unlike :meth:`encode` (which incrementally extends an existing encoder),
        this creates a fresh encoder, builds the complete vocabulary from *all*
        texts, and computes every vector against the final statistics.  No
        `load`/`save` round-trip — the caller is responsible for persistence.

        Returns ``(encoder, vectors)`` where *vectors[i]* corresponds to
        *texts[i]*.
        """
        encoder = cls()
        if vector_format not in ("bm25_tf_v2", "bm25_idf_v1"):
            raise ValueError("Unsupported sparse vector format")
        encoder.vector_format = vector_format
        if not texts:
            return encoder, []

        total_len = 0
        # Phase 1: build vocabulary and document frequencies
        for text in texts:
            tokens = _tokenize(text)
            total_len += len(tokens)
            for t in set(tokens):
                if t not in encoder.term_to_id:
                    encoder.term_to_id[t] = len(encoder.term_to_id)
                tid = encoder.term_to_id[t]
                encoder.doc_freqs[tid] = encoder.doc_freqs.get(tid, 0) + 1

        encoder._doc_count = len(texts)
        encoder.avg_dl = total_len / encoder._doc_count

        # Phase 2: compute BM25 vectors using the complete vocabulary
        vectors = [encoder._compute_bm25(_tokenize(t)) for t in texts]

        logger.info(
            "[Sparse] rebuild_from_texts: %d docs, %d terms, avg_dl=%.1f",
            encoder._doc_count, len(encoder.term_to_id), encoder.avg_dl,
        )
        return encoder, vectors
