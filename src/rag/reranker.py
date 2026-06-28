from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from src.providers.base import RerankerProvider
from src.rag.retriever import RetrievedChunk


class Reranker:
    def __init__(self, provider: RerankerProvider, top_k: int = 5):
        self.provider = provider
        self.top_k = top_k

    def rerank(self, query: str, chunks: list[RetrievedChunk], top_k: int | None = None) -> list[RetrievedChunk]:
        k = top_k if top_k is not None else self.top_k
        logger.debug("[Rerank] %d chunks → top_k=%s", len(chunks), k)
        if not chunks:
            return []

        documents = [c.text for c in chunks]
        # Filter out empty docs (Qwen/DashScope rejects empty strings),
        # while preserving original indices for result mapping.
        valid_indices: list[int] = []
        valid_docs: list[str] = []
        for i, doc in enumerate(documents):
            if doc and doc.strip():
                valid_indices.append(i)
                valid_docs.append(doc)

        if not valid_docs:
            return []

        ranked = self.provider.rerank(query, valid_docs, top_k=k)

        result = []
        for idx, score in ranked:
            chunk = chunks[valid_indices[idx]]
            chunk.score = score
            result.append(chunk)
        return result
