"""MCP search tools — search_knowledge_base, search_chunks, get_query_history."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


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


async def search_knowledge_base(
    raw_query: str,
    generate_answer: bool = True,
    include_images: bool = False,
) -> str:
    """Search the private knowledge base with agentic RAG and optional AI-generated answer.

    Uses the full 7-layer AgenticQueryService pipeline: decompose → retrieve → grade → rewrite → aggregate → generate.
    No ``collection`` parameter needed — the system auto-discovers the most relevant collections.

    Set ``generate_answer=False`` for raw chunk results without LLM generation.
    Set ``include_images=True`` to include base64-encoded images from retrieved chunks.
    Use this when your LLM supports vision/image input.

    Returns a JSON string::

        {"answer": "...", "sources": [...], "tasks": [...], "images": {...}}
    """
    from src.services import services

    def _run():
        if not services.agentic_query:
            return {"error": "Agentic query service not available"}

        result = services.agentic_query.run(raw_query, generate_answer=generate_answer, include_images=include_images)

        sources = [
            {
                "text": c.text,
                "score": c.score,
                "source": c.metadata.get("source", ""),
                "collection": c.metadata.get("collection", ""),
                "chunk_type": c.metadata.get("chunk_type", "normal"),
                "context": c.metadata.get("context"),
                "id": c.metadata.get("id", ""),
            }
            for c in (result.all_chunks or [])
        ]

        response = {
            "answer": result.answer,
            "sources": sources,
            "tasks": result.tasks,
        }
        if include_images and result.images:
            response["images"] = result.images
        return response

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    return json.dumps(result, ensure_ascii=False, default=str)


async def search_chunks(
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
    """Search for relevant document chunks without LLM generation.

    Returns raw chunks with relevance scores. Use this for debugging retrieval
    quality or retrieving context for your own processing.

    Set ``include_images=True`` to include base64-encoded images from retrieved chunks.
    """
    from src.services import services
    from src.rag.collection_utils import get_embedding_overrides

    target_collections = collections or [collection]

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

        return {
            "results": [
                {
                    "text": c.text, "score": c.score,
                    "source": c.metadata.get("source", ""),
                    "collection": c.metadata.get("collection", ""),
                    "chunk_type": c.metadata.get("chunk_type", "normal"),
                    "context": c.metadata.get("context"),
                    "id": c.metadata.get("id", ""),
                    "images": c.metadata.get("images", []),
                }
                for c in dq_result.chunks
            ],
            "query_used": query,
            "total": len(dq_result.chunks),
            **({"images": _extract_images_base64(dq_result.chunks)} if include_images else {}),
        }

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    return json.dumps(result, ensure_ascii=False, default=str)


async def get_query_history(limit: int = 50) -> str:
    """Get recent questions, answers, and sources from past RAG queries.

    Useful for reviewing what has been asked before or referencing previous answers.
    """
    def _run():
        file = Path("data/history/history.jsonl")
        if not file.exists():
            return []
        entries = []
        for line in file.read_text().strip().split("\n"):
            if line:
                entries.append(json.loads(line))
        return entries[-limit:]

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    return json.dumps(result, ensure_ascii=False, default=str)
