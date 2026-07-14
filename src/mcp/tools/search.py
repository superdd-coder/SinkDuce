"""MCP search tools — context-only retrieval.

Two atomic retrieval tools, both return chunks (no LLM answer generation —
the agent generates the answer from the chunks using its own LLM):

- :func:`search_direct_chunks` — Dense / Sparse / Hybrid + optional rerank.
  Bypasses agentic decomposition. Use for focused lookups.

- :func:`search_agentic_chunks` — Full Agentic RAG pipeline (rewrite →
  decompose → retrieve → grade → aggregate). Returns the same chunk
  structure as ``search_direct_chunks`` so the agent can format it
  however it wants. Use for complex / multi-hop questions.

Plus one operational tool:
- :func:`get_query_history` — recent questions, answers, and sources
  from past RAG queries.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from src.mcp.common import err, ok, require_collection, to_json

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────


def _chunk_to_dict(c) -> dict:
    """Normalize a RetrievedChunk to a plain dict."""
    return {
        "text": c.text,
        "score": c.score,
        "source": c.metadata.get("source", ""),
        "collection": c.metadata.get("collection", ""),
        "chunk_type": c.metadata.get("chunk_type", "normal"),
        "context": c.metadata.get("context"),
        "id": c.metadata.get("id", ""),
        "images": c.metadata.get("images", []),
    }


def _extract_images_base64(chunks: list) -> dict[str, dict]:
    """Extract and encode images referenced in chunk metadata."""
    from src.parsers.image_utils import encode_image_base64

    images: dict[str, dict] = {}
    seen: set[str] = set()
    for chunk in chunks:
        meta = getattr(chunk, "metadata", {}) if hasattr(chunk, "metadata") else {}
        chunk_images = meta.get("images", [])
        if not isinstance(chunk_images, list):
            continue
        for img_ref in chunk_images:
            if not isinstance(img_ref, dict):
                continue
            img_id = img_ref.get("image_id", "")
            file_id = img_ref.get("file_id", "")
            if not img_id or not file_id or img_id in seen:
                continue
            seen.add(img_id)
            encoded = encode_image_base64(img_id, file_id)
            if encoded:
                b64, mime = encoded
                images[img_id] = {"base64": b64, "mime": mime}
    return images


# ── search_direct_chunks ──────────────────────────────────────


async def search_direct_chunks(
    query: str,
    collection: str = "default",
    collections: list[str] | None = None,
    search_mode: str = "dense",
    top_k: int = 10,
    rerank_top_k: int = 5,
    use_reranker: bool = False,
    min_score: float = 0.0,
    include_images: bool = False,
) -> str:
    """Direct chunk retrieval — no LLM answer generation, no agentic decomposition.

    Use this for focused lookups against a specific collection. The agent is
    expected to read the returned chunks and generate an answer with its own
    LLM if needed.

    Args:
        query: The user question / search string.
        collection: Collection **ID** from ``list_collections`` (e.g. ``"col_abc123"``
            or ``"default"``). Used when ``collections`` is not given.
        collections: List of collection **IDs** to search across. If given,
            overrides ``collection``. Use ``list_collections`` first to get IDs.
        search_mode: One of ``dense`` / ``sparse`` / ``hybrid``.
        top_k: Max chunks to return per collection.
        rerank_top_k: Number of chunks to keep after reranking.
        use_reranker: If True, apply the configured reranker.
        min_score: Drop chunks below this score (post-rerank if applicable).
        include_images: Embed base64-encoded images referenced in chunks.

    Returns:
        JSON ``{"results": [...chunks...], "query_used": str, "total": N,
        "images"?: {...}}``.
    """
    from src.services import services
    from src.rag.collection_utils import get_embedding_overrides

    target_collections = collections or [collection]
    for c in target_collections:
        if e := require_collection(c):
            return to_json(e)

    def _run():
        if not services.direct_query:
            return {"error": "Direct query module not available"}

        embedding_overrides = get_embedding_overrides(target_collections)

        dq_result = services.direct_query.retrieve(
            query, target_collections, top_k=top_k,
            search_mode=search_mode,
            rerank_enabled=use_reranker,
            rerank_top_k=rerank_top_k,
            min_score=min_score,
            embedding_overrides=embedding_overrides,
        )

        payload = {
            "results": [_chunk_to_dict(c) for c in dq_result.chunks],
            "query_used": query,
            "total": len(dq_result.chunks),
        }
        if include_images:
            payload["images"] = _extract_images_base64(dq_result.chunks)
        return payload

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    return json.dumps(result, ensure_ascii=False, default=str)


# ── search_agentic_chunks ─────────────────────────────────────


async def search_agentic_chunks(
    query: str,
    include_images: bool = False,
) -> str:
    """Agentic RAG retrieval — full pipeline (rewrite → decompose → retrieve →
    grade → aggregate), then return chunks without LLM answer generation.

    Use this for complex / multi-hop questions where simple direct retrieval
    isn't enough. The agent receives the same chunk structure as
    ``search_direct_chunks`` and is expected to format an answer with its
    own LLM.

    No collection parameter — the agentic pipeline auto-discovers relevant
    collections based on the query.

    Args:
        query: The user question.
        include_images: Embed base64-encoded images from retrieved chunks.

    Returns:
        JSON ``{"results": [...chunks...], "query_used": str, "total": N,
        "tasks": [...], "images"?: {...}}``. ``tasks`` is the agentic
        sub-query trail for transparency.
    """
    from src.services import services

    def _run():
        if not services.agentic_query:
            return {"error": "Agentic query service not available"}

        # generate_answer=False: we only want chunks, not a generated answer.
        result = services.agentic_query.run(
            query, generate_answer=False, include_images=include_images
        )

        chunks = result.all_chunks or []
        payload = {
            "results": [_chunk_to_dict(c) for c in chunks],
            "query_used": query,
            "total": len(chunks),
            "tasks": result.tasks or [],
        }
        if include_images and result.images:
            payload["images"] = result.images
        return payload

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    return json.dumps(result, ensure_ascii=False, default=str)


# ── get_query_history ─────────────────────────────────────────


async def get_query_history(limit: int = 50, include_details: bool = False) -> str:
    """Get recent query history from past RAG queries.

    By default returns only ``question`` + ``timestamp`` per entry (lightweight).
    Set ``include_details=True`` to also include ``answer``, ``sources``, and
    other full fields.

    Reads from ``data/history/history.jsonl``.
    """
    def _run():
        file = Path("data/history/history.jsonl")
        if not file.exists():
            return []
        entries = []
        for line in file.read_text().strip().split("\n"):
            if line:
                entry = json.loads(line)
                if not include_details:
                    entry = {
                        "question": entry.get("question", ""),
                        "timestamp": entry.get("timestamp", ""),
                    }
                entries.append(entry)
        return entries[-limit:]

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    return json.dumps(result, ensure_ascii=False, default=str)


__all__ = [
    "search_direct_chunks",
    "search_agentic_chunks",
    "get_query_history",
]
