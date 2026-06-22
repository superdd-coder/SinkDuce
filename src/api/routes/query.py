from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from src.api.schemas import QueryRequest, QueryResponse, SourceItem
from src.config import get_config
from src.rag.agent import AgenticRAG, AgentResult
from src.services import services
from src.rag.collection_utils import (
    build_context,
    get_embedding_overrides,
    retrieve_parent_child_multi,
    retrieve_standard,
)
from src.rag.agent import AgenticRAG, AgentResult
from src.collections import store as collections_store

logger = logging.getLogger(__name__)
router = APIRouter()

HISTORY_DIR = Path("data/history")
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _col_display_name(col_id: str) -> str:
    """Resolve a collection ID to its display name. Falls back to ID if not found."""
    meta = collections_store.get_collection_meta(col_id)
    if meta:
        return meta.get("name", col_id)
    return col_id


def _multi_collection_note(sources: list) -> str:
    """Return a warning note if sources span multiple collections, empty string otherwise."""
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
    """Resolve all query parameters from request + collection config + global config."""
    agent_enabled = req.use_agent and col_config.get("agent_enabled", col_config.get("self_rag_enabled", True))
    # Agentic RAG requires reranker — force enable when agent is on
    use_reranker = req.use_reranker if req.use_reranker is not None else True
    if agent_enabled:
        use_reranker = True
    return {
        "top_k": req.top_k or col_config.get("top_k", services.config.rag.top_k),
        "rerank_top_k": req.rerank_top_k or col_config.get("rerank_top_k", services.config.rag.rerank_top_k),
        "agent_enabled": agent_enabled,
        "search_mode": req.search_mode or col_config.get("search_mode", "dense"),
        "min_score": req.min_score if req.min_score is not None else 0.0,
        "use_reranker": use_reranker,
        "max_iterations": req.max_iterations if req.max_iterations is not None else col_config.get("agent_max_iterations", col_config.get("self_rag_max_iterations", 3)),
        "sparse_llm_tokenize": _resolve_sparse_llm_tokenize(req, col_config, agent_enabled),
    }


def _resolve_sparse_llm_tokenize(req: QueryRequest, col_config: dict, agent_enabled: bool) -> bool:
    """Resolve whether to use LLM keyword extraction for sparse encoding.

    Agentic RAG → always True (LLM is already in the loop).
    Non-agentic Hybrid → per-query override or collection config (default True).
    Dense mode → False (sparse encoding not used).
    """
    search_mode = req.search_mode or col_config.get("search_mode", "dense")
    if search_mode != "hybrid":
        return False
    if agent_enabled:
        return True
    # Per-query override takes precedence over collection config
    if req.sparse_llm_tokenize is not None:
        return req.sparse_llm_tokenize
    return col_config.get("sparse_llm_tokenize", True)


def _resolve_llm(req: QueryRequest) -> tuple:
    """Resolve LLM and provider info for streaming (supports per-request provider switching)."""
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


@router.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    try:
        # Resolve collection: try as ID first, fall back to name (for legacy)
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
        col_embedding = embedding_overrides.get(collection) or next(iter(embedding_overrides.values()))

        logger.info("Query: collections=%s, min_score=%.2f, search_mode=%s, sparse_llm_tokenize=%s",
                     target_collections, params["min_score"], params["search_mode"], params["sparse_llm_tokenize"])

        is_parent_child = col_config.get("chunk_mode") == "parent_child"
        reranker = services.reranker if (params["use_reranker"] and services.reranker and services.reranker.provider) else None

        if is_parent_child and not params["agent_enabled"]:
            chunks = retrieve_parent_child_multi(
                req.question, target_collections, params["top_k"],
                embedding_overrides=embedding_overrides,
                min_score=params["min_score"],
                reranker=reranker,
                rerank_top_k=params["rerank_top_k"],
                retriever=services.retriever,
                search_mode=params["search_mode"],
                llm=llm if params["sparse_llm_tokenize"] else None,
            )
            context = build_context(chunks)
            sources = [{"text": c.text, "score": c.score, "metadata": c.metadata} for c in chunks]
            answer = llm.generate(f"Answer based on context:\n{context}\n\nQuestion: {req.question}", temperature=temperature)
            result = AgentResult(answer=answer, sources=sources, iterations=1, query_used=req.question)
        elif params["agent_enabled"]:
            agent = AgenticRAG(
                llm=llm,
                retriever=services.retriever,
                reranker=reranker,
                rerank_top_k=params["rerank_top_k"],
                max_iterations=params["max_iterations"],
                embedding_overrides=embedding_overrides,
                search_mode=params["search_mode"],
                min_score=params["min_score"],
                db=services.db,
                temperature=temperature,
            )
            result = agent.run(query=req.question, collections=target_collections, top_k=params["top_k"])
        else:
            chunks = retrieve_standard(
                req.question, target_collections, params["top_k"],
                embedding_overrides=embedding_overrides,
                search_mode=params["search_mode"],
                min_score=params["min_score"],
                reranker=reranker,
                rerank_top_k=params["rerank_top_k"],
                llm=llm if params["sparse_llm_tokenize"] else None,
            )
            context = build_context(chunks)
            sources = [{"text": c.text, "score": c.score, "metadata": c.metadata} for c in chunks]
            answer = llm.generate(f"Answer based on context:\n{context}\n\nQuestion: {req.question}", temperature=temperature)
            result = AgentResult(answer=answer, sources=sources, iterations=1, query_used=req.question)

        # Append multi-collection note if applicable
        note = _multi_collection_note(result.sources)
        if note:
            result.answer += note

        sources = [SourceItem(**s) for s in result.sources]
        _save_history(req.question, result.answer, req.collection, result.sources)
        return QueryResponse(
            answer=result.answer, sources=sources,
            iterations=result.iterations, query_used=result.query_used,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error processing query: {str(e)}")


@router.post("/query/stream")
async def query_stream(req: QueryRequest):
    """SSE streaming endpoint — streams answer tokens one by one."""

    def generate():
        try:
            # Resolve collection: try as ID first, fall back to name (for legacy)
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
            col_embedding = embedding_overrides.get(collection) or next(iter(embedding_overrides.values()))

            logger.info("Query stream: collections=%s, min_score=%.2f, search_mode=%s, sparse_llm_tokenize=%s",
                         target_collections, params["min_score"], params["search_mode"], params["sparse_llm_tokenize"])

            is_parent_child = col_config.get("chunk_mode") == "parent_child"
            reranker = services.reranker if (params["use_reranker"] and services.reranker and services.reranker.provider) else None

            if is_parent_child and not params["agent_enabled"]:
                chunks = retrieve_parent_child_multi(
                    req.question, target_collections, params["top_k"],
                    embedding_overrides=embedding_overrides,
                    min_score=params["min_score"],
                    reranker=reranker,
                    rerank_top_k=params["rerank_top_k"],
                    retriever=services.retriever,
                    search_mode=params["search_mode"],
                    llm=llm if params["sparse_llm_tokenize"] else None,
                )
                context = build_context(chunks)
                sources = [{"text": c.text, "score": c.score, "metadata": c.metadata} for c in chunks]
                meta = {
                    "type": "meta", "sources": sources, "iterations": 1,
                    "query_used": req.question, "mode": "parent-child", "agent_active": False,
                    "provider": provider_info["name"], "model": provider_info["model"],
                    "search_mode": params["search_mode"],
                }
                yield f"data: {json.dumps(meta)}\n\n"
                answer_parts = []
                for token in llm.generate_stream(f"Answer based on context:\n{context}\n\nQuestion: {req.question}", temperature=temperature):
                    answer_parts.append(token)
                    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
                note = _multi_collection_note(sources)
                if note:
                    answer_parts.append(note)
                    yield f"data: {json.dumps({'type': 'token', 'content': note})}\n\n"
                _save_history(req.question, "".join(answer_parts), req.collection, sources)

            elif params["agent_enabled"]:
                agent = AgenticRAG(
                    llm=llm, retriever=services.retriever,
                    reranker=reranker,
                    rerank_top_k=params["rerank_top_k"],
                    max_iterations=params["max_iterations"],
                    embedding_overrides=embedding_overrides,
                    search_mode=params["search_mode"],
                    min_score=params["min_score"],
                    db=services.db,
                    temperature=temperature,
                )
                # Send provider info upfront for the thinking steps UI
                info = {
                    "type": "info",
                    "provider": provider_info["name"],
                    "model": provider_info["model"],
                    "search_mode": params["search_mode"],
                    "mode": "agentic",
                    "max_iterations": params["max_iterations"],
                }
                yield f"data: {json.dumps(info)}\n\n"

                gen = agent.run_stream(query=req.question, collections=target_collections, top_k=params["top_k"])
                for first, second in gen:
                    if isinstance(first, dict):
                        yield f"data: {json.dumps(first)}\n\n"
                        continue
                    result = first
                    stream = second
                    meta = {
                        "type": "meta", "sources": result.sources, "iterations": result.iterations,
                        "query_used": result.query_used, "mode": "agentic", "agent_active": True,
                        "provider": provider_info["name"], "model": provider_info["model"],
                        "search_mode": params["search_mode"],
                    }
                    yield f"data: {json.dumps(meta)}\n\n"
                    if result.answer:
                        note = _multi_collection_note(result.sources)
                        answer_text = result.answer + note
                        yield f"data: {json.dumps({'type': 'token', 'content': answer_text})}\n\n"
                        _save_history(req.question, answer_text, req.collection, result.sources)
                    else:
                        answer_parts = []
                        for token in stream:
                            answer_parts.append(token)
                            yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
                        note = _multi_collection_note(result.sources)
                        if note:
                            answer_parts.append(note)
                            yield f"data: {json.dumps({'type': 'token', 'content': note})}\n\n"
                        _save_history(req.question, "".join(answer_parts), req.collection, result.sources)

            else:
                chunks = retrieve_standard(
                    req.question, target_collections, params["top_k"],
                    embedding_overrides=embedding_overrides,
                    search_mode=params["search_mode"],
                    min_score=params["min_score"],
                    reranker=reranker,
                    rerank_top_k=params["rerank_top_k"],
                    llm=llm if params["sparse_llm_tokenize"] else None,
                )
                context = build_context(chunks)
                sources = [{"text": c.text, "score": c.score, "metadata": c.metadata} for c in chunks]
                meta = {
                    "type": "meta", "sources": sources, "iterations": 1,
                    "query_used": req.question, "mode": "standard", "agent_active": False,
                    "provider": provider_info["name"], "model": provider_info["model"],
                    "search_mode": params["search_mode"],
                }
                yield f"data: {json.dumps(meta)}\n\n"
                answer_parts = []
                for token in llm.generate_stream(f"Answer based on context:\n{context}\n\nQuestion: {req.question}", temperature=temperature):
                    answer_parts.append(token)
                    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
                note = _multi_collection_note(sources)
                if note:
                    answer_parts.append(note)
                    yield f"data: {json.dumps({'type': 'token', 'content': note})}\n\n"
                _save_history(req.question, "".join(answer_parts), req.collection, sources)

            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


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
