"""Query endpoints — direct and agentic retrieval with streaming support."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from src.api.schemas import QueryRequest, QueryResponse, SourceItem
from src.config import get_config
from src.services import services
from src.rag.collection_utils import get_embedding_overrides
from src.collections import store as collections_store

logger = logging.getLogger(__name__)
router = APIRouter()

HISTORY_DIR = Path("data/history")
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _deprecated_response():
    return JSONResponse(
        status_code=410,
        content={
            "error": "Gone",
            "message": "This endpoint is deprecated. Please migrate to POST /api/sessions/{id}/messages",
            "migration_doc": "https://github.com/superdd-coder/sinkduce",
        },
        headers={"Deprecation": "true", "Sunset": "Sat, 01 Aug 2026 00:00:00 GMT"},
    )


def _col_display_name(col_id: str) -> str:
    meta = collections_store.get_collection_meta(col_id)
    if meta:
        return meta.get("name", col_id)
    return col_id


def _multi_collection_note(sources: list) -> str:
    cols: set[str] = set()
    for s in sources:
        meta = s.get("metadata", {}) if isinstance(s, dict) else getattr(s, "metadata", {})
        col = meta.get("collection", "")
        if col:
            cols.add(_col_display_name(col))
    if len(cols) > 1:
        return (
            f"\n\n---\n\n> ⚠️ This answer synthesizes information from {len(cols)} "
            f"different collections ({', '.join(sorted(cols))}). "
            f"Cross-collection information may introduce inconsistencies."
        )
    return ""


def _save_history(question: str, answer: str, collection: str, sources: list):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": question,
        "answer": answer,
        "collection": collection,
        "sources": sources,
    }
    file = HISTORY_DIR / "history.jsonl"
    with open(file, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _resolve_params(req: QueryRequest, col_config: dict) -> dict:
    """Resolve query parameters from request + collection config + global config."""
    if req.use_agent:
        cfg = services.config.rag
    else:
        cfg = services.config.direct_rag

    use_reranker = req.use_reranker if req.use_reranker is not None else (
        True if req.use_agent else getattr(services.config.direct_rag, "use_reranker", True)
    )
    if req.use_agent:
        use_reranker = True
    return {
        "top_k": req.top_k or cfg.top_k,
        "rerank_top_k": req.rerank_top_k or cfg.rerank_top_k,
        "use_agent": req.use_agent,
        "search_mode": req.search_mode or cfg.default_search_mode,
        "min_score": req.min_score if req.min_score is not None else cfg.min_score,
        "use_reranker": use_reranker,
        "max_iterations": req.max_iterations if req.max_iterations is not None else cfg.max_iterations,
        "sparse_llm_tokenize": _resolve_sparse_llm_tokenize(req, col_config),
    }


def _resolve_sparse_llm_tokenize(req: QueryRequest, col_config: dict) -> bool:
    search_mode = req.search_mode or col_config.get("search_mode", services.config.rag.default_search_mode)
    if search_mode != "hybrid":
        return False
    if req.sparse_llm_tokenize is not None:
        return req.sparse_llm_tokenize
    return col_config.get("sparse_llm_tokenize", True)


def _resolve_llm(req: QueryRequest) -> tuple:
    from src.providers.llm import create_llm_for_provider
    provider_info = {"name": "", "model": ""}
    config = get_config()
    if req.provider_id:
        for p in config.llm.providers:
            if p.id == req.provider_id:
                llm = create_llm_for_provider(p, model=req.model)
                provider_info["name"] = p.name
                provider_info["model"] = req.model or p.default_model or p.model
                return llm, provider_info, req.temperature
    if config.llm.providers:
        default_p = next((p for p in config.llm.providers if p.is_default), config.llm.providers[0])
        llm = create_llm_for_provider(default_p)
        provider_info["name"] = default_p.name
        provider_info["model"] = default_p.default_model or default_p.model
        return llm, provider_info, req.temperature
    return services.llm, provider_info, req.temperature


def _run_direct(query_text: str, target_collections: list[str], params: dict,
                embedding_overrides: dict, reranker, llm, temperature) -> dict:
    """Direct retrieval → build_context → LLM generate."""
    if not services.direct_query:
        raise HTTPException(status_code=503, detail="Direct query module not available")

    result = services.direct_query.retrieve(
        query_text, target_collections, top_k=params["top_k"],
        search_mode=params["search_mode"],
        rerank_enabled=params["use_reranker"],
        rerank_top_k=params["rerank_top_k"],
        min_score=params["min_score"],
        sparse_llm_tokenize=params["sparse_llm_tokenize"],
        embedding_overrides=embedding_overrides,
        llm_for_sparse=llm if params["sparse_llm_tokenize"] else None,
        generate_answer=True,
    )
    sources = [{"text": c.text, "score": c.score, "metadata": c.metadata} for c in result.chunks]
    return {"answer": result.answer or "", "sources": sources, "query_used": query_text}


def _run_agentic(query_text: str, target_collections: list[str], params: dict,
                llm, temperature) -> dict:
    """Agentic RAG via AgenticQueryService."""
    if not services.agentic_query:
        raise HTTPException(status_code=503, detail="Agentic query service not available")

    result = services.agentic_query.run(
        query_text, collections=target_collections, generate_answer=True,
        top_k=params["top_k"],
        rerank_enabled=True,  # agentic always reranks
        rerank_top_k=params["rerank_top_k"],
        search_mode=params["search_mode"],
        min_score=params["min_score"],
        max_iterations=params["max_iterations"],
        sparse_llm_tokenize=params["sparse_llm_tokenize"],
    )
    sources = [
        {"text": c.text, "score": c.score, "metadata": c.metadata}
        for c in (result.all_chunks or [])
    ]
    return {
        "answer": result.answer or "",
        "sources": sources,
        "iterations": max((t.get("sub_queries", [{}])[0].get("iterations", 0) if t.get("sub_queries") else 0) for t in (result.tasks or [{}])),
        "query_used": query_text,
    }


# ══════════════════════════════════════════════════════════════════════════
# POST /query
# ══════════════════════════════════════════════════════════════════════════

@router.post("/query", include_in_schema=False)
def query(req: QueryRequest = None):
    """Deprecated. Use POST /api/sessions/{id}/messages instead."""
    return _deprecated_response()


# ══════════════════════════════════════════════════════════════════════════
# POST /query/stream
# ══════════════════════════════════════════════════════════════════════════

@router.post("/query/stream", include_in_schema=False)
def query_stream(req: QueryRequest = None):
    """Deprecated. Use POST /api/sessions/{id}/messages instead."""
    return _deprecated_response()


# ══════════════════════════════════════════════════════════════════════════
# GET /history
# ══════════════════════════════════════════════════════════════════════════

@router.get("/history")
def get_history(limit: int = 50):
    file = HISTORY_DIR / "history.jsonl"
    if not file.exists():
        return []
    entries = []
    for line in file.read_text().strip().split("\n"):
        if line:
            entries.append(json.loads(line))
    return entries[-limit:]
