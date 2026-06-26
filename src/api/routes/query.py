"""Query endpoints — direct and agentic retrieval with streaming support."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from src.api.schemas import QueryRequest, QueryResponse, SourceItem
from src.config import get_config
from src.services import services
from src.rag.collection_utils import get_embedding_overrides
from src.collections import store as collections_store

logger = logging.getLogger(__name__)
router = APIRouter()

HISTORY_DIR = Path("data/history")
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


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
        "timestamp": datetime.now().isoformat(),
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

@router.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    try:
        def resolve_collection(cid: str) -> str:
            meta = collections_store.get_collection_meta(cid)
            return meta["id"] if meta else cid

        collection = resolve_collection(req.collection)
        target_collections = [resolve_collection(c) for c in (req.collections or [req.collection])]

        if not services.db.collection_exists(collection):
            return QueryResponse(
                answer=f"Collection '{req.collection}' does not exist. Please create it first.",
                sources=[], iterations=0, query_used=req.question,
            )

        col_config = services.db.get_collection_config(collection)
        params = _resolve_params(req, col_config)
        llm, provider_info, temperature = _resolve_llm(req)
        embedding_overrides = get_embedding_overrides(target_collections)

        logger.info("[Query] collections=%s use_agent=%s", target_collections, params["use_agent"])

        if params["use_agent"]:
            r = _run_agentic(req.question, target_collections, params, llm, temperature)
        else:
            r = _run_direct(req.question, target_collections, params,
                            embedding_overrides, None, llm, temperature)

        note = _multi_collection_note(r["sources"])
        if note:
            r["answer"] += note

        sources = [SourceItem(**s) for s in r["sources"]]
        _save_history(req.question, r["answer"], req.collection, r["sources"])
        return QueryResponse(
            answer=r["answer"], sources=sources,
            iterations=r.get("iterations", 1), query_used=r.get("query_used", req.question),
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error processing query: {str(e)}")


# ══════════════════════════════════════════════════════════════════════════
# POST /query/stream
# ══════════════════════════════════════════════════════════════════════════

@router.post("/query/stream")
async def query_stream(req: QueryRequest):
    """SSE streaming endpoint — streams answer tokens one by one."""

    def generate():
        try:
            def resolve_collection(cid: str) -> str:
                meta = collections_store.get_collection_meta(cid)
                return meta["id"] if meta else cid

            collection = resolve_collection(req.collection)
            target_collections = [resolve_collection(c) for c in (req.collections or [req.collection])]

            if not services.db.collection_exists(collection):
                yield f"data: {json.dumps({'type': 'error', 'content': f'Collection {req.collection} does not exist'})}\n\n"
                return

            col_config = services.db.get_collection_config(collection)
            params = _resolve_params(req, col_config)
            llm, provider_info, temperature = _resolve_llm(req)
            embedding_overrides = get_embedding_overrides(target_collections)

            logger.info("Query stream: collections=%s use_agent=%s", target_collections, params["use_agent"])

            if params["use_agent"]:
                # Agentic mode — use AgenticQueryService.run_stream style
                if not services.agentic_query:
                    yield f"data: {json.dumps({'type': 'error', 'content': 'Agentic query service not available'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    return

                result = services.agentic_query.run(
                    req.question, collections=target_collections, generate_answer=True,
                    top_k=params["top_k"],
                    rerank_enabled=True,
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
                meta = {
                    "type": "meta", "sources": sources,
                    "iterations": 0,
                    "query_used": req.question,
                    "mode": "agentic", "agent_active": True,
                    "provider": provider_info["name"], "model": provider_info["model"],
                    "search_mode": params["search_mode"],
                }
                yield f"data: {json.dumps(meta)}\n\n"
                if result.answer:
                    note = _multi_collection_note(sources)
                    yield f"data: {json.dumps({'type': 'token', 'content': result.answer})}\n\n"
                    if note:
                        yield f"data: {json.dumps({'type': 'token', 'content': note})}\n\n"
                    _save_history(req.question, result.answer + (note if note else ""), req.collection, sources)
            else:
                # Direct mode
                if not services.direct_query:
                    yield f"data: {json.dumps({'type': 'error', 'content': 'Direct query module not available'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    return

                dq_result = services.direct_query.retrieve(
                    req.question, target_collections, top_k=params["top_k"],
                    search_mode=params["search_mode"],
                    rerank_enabled=params["use_reranker"],
                    rerank_top_k=params["rerank_top_k"],
                    min_score=params["min_score"],
                    sparse_llm_tokenize=params["sparse_llm_tokenize"],
                    embedding_overrides=embedding_overrides,
                    llm_for_sparse=llm if params["sparse_llm_tokenize"] else None,
                    generate_answer=True,
                )
                sources = [{"text": c.text, "score": c.score, "metadata": c.metadata} for c in dq_result.chunks]
                meta = {
                    "type": "meta", "sources": sources, "iterations": 1,
                    "query_used": req.question, "mode": "direct", "agent_active": False,
                    "provider": provider_info["name"], "model": provider_info["model"],
                    "search_mode": params["search_mode"],
                }
                yield f"data: {json.dumps(meta)}\n\n"
                answer = dq_result.answer or ""
                # Stream pre-generated answer token by token
                for token in answer:
                    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
                note = _multi_collection_note(sources)
                if note:
                    yield f"data: {json.dumps({'type': 'token', 'content': note})}\n\n"
                _save_history(req.question, answer, req.collection, sources)

            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


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
