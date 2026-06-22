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
from pathlib import Path

logger = logging.getLogger(__name__)

# ── LLM prompt for sparse query preprocessing ──────────────────────────

PREPROCESS_SPARSE_QUERY_SYSTEM = """\
You are a keyword extraction engine for a BM25 (keyword-based) search system.
Your ONLY job is to extract search-relevant keywords and phrases from a user query.

Rules:
1. Extract key concepts, entities, and technical terms from the query
2. Add 2-4 synonyms or alternative phrasings for important concepts
   (e.g., "ML" → also add "machine learning"; "AI" → also add "artificial intelligence")
3. Strip question words (what, how, why, 怎么, 如何, 为什么), filler words, and
   stop words (the, a, is, are, 的, 了, 是, 在)
4. Output space-separated phrases — NOT full sentences
5. Keep abbreviations AND their expansions (e.g., both "RAG" and "retrieval augmented generation")
6. Handle both English and Chinese queries
7. For Chinese queries, extract meaningful word compounds (2-4 characters), not single characters
8. Preserve numbers, dates, and proper nouns exactly as they appear

Respond with ONLY a JSON object (no markdown fences, no extra text):
{"keywords": ["phrase1", "phrase2", "phrase3"]}"""

PREPROCESS_SPARSE_QUERY_USER = """Query: {query}"""


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
    # Add character bigrams for Chinese characters
    cjk_chars = re.findall(r"[一-鿿]", text)
    for i in range(len(cjk_chars) - 1):
        tokens.append(cjk_chars[i] + cjk_chars[i + 1])
    return tokens


class SparseEncoder:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.term_to_id: dict[str, int] = {}
        self.doc_freqs: dict[int, int] = {}
        self.avg_dl: float = 0.0
        self._doc_count: int = 0

    def save(self, path: str) -> None:
        """Persist vocabulary state to disk so queries can reuse the same encoding."""
        data = {
            "term_to_id": self.term_to_id,
            "doc_freqs": self.doc_freqs,
            "avg_dl": self.avg_dl,
            "doc_count": self._doc_count,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        logger.info(
            "[HYBRID-VERIFY] SparseEncoder.save path=%s terms=%d docs=%d avg_dl=%.1f",
            path, len(self.term_to_id), self._doc_count, self.avg_dl,
        )

    def load(self, path: str) -> None:
        """Restore vocabulary state from disk."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.term_to_id = data["term_to_id"]
        self.doc_freqs = {int(k): v for k, v in data["doc_freqs"].items()}
        self.avg_dl = float(data["avg_dl"])
        self._doc_count = int(data["doc_count"])
        logger.info(
            "[HYBRID-VERIFY] SparseEncoder.load path=%s terms=%d docs=%d avg_dl=%.1f",
            path, len(self.term_to_id), self._doc_count, self.avg_dl,
        )

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

        self._doc_count += len(texts)
        total_len = sum(len(_tokenize(t)) for t in texts)
        self.avg_dl = total_len / self._doc_count if self._doc_count else 1.0

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
        """Encode a query into BM25 sparse vector using stored vocabulary."""
        tokens = _tokenize(query)
        vec = {}
        for t in tokens:
            tid = self.term_to_id.get(t)
            if tid is None:
                continue
            vec[tid] = vec.get(tid, 0) + 1

        # Apply IDF weighting
        weighted = {}
        for tid, tf in vec.items():
            df = self.doc_freqs.get(tid, 0)
            idf = math.log((self._doc_count - df + 0.5) / (df + 0.5) + 1)
            weighted[tid] = tf * idf
        return weighted

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
            vec[tid] = idf * numerator / denominator
        return vec
