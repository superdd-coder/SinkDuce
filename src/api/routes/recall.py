"""Recall testing module — search with adjustable parameters and benchmarking."""

from __future__ import annotations

import json
import logging
import math
import random
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from qdrant_client.models import FieldCondition, Filter, MatchValue

from src.api.schemas import (
    BenchmarkResult,
    EvalRequest,
    EvalTestCase,
    RecallBenchmarkRequest,
    RecallResult,
    RecallSearchRequest,
    RecallSearchResponse,
)
from src.rag.collection_utils import (
    get_embedding_overrides,
    retrieve_standard,
    retrieve_parent_child_multi,
)
from src.rag.retriever import RetrievedChunk
from src.services import services
from src.collections import store as collections_store

logger = logging.getLogger(__name__)
router = APIRouter()

EVAL_DIR = Path("data/eval")
EVAL_DIR.mkdir(parents=True, exist_ok=True)


# ── Helpers ────────────────────────────────────────────────


def _hydrate_recall_result(chunk: RetrievedChunk, *, collection: str, id: str = "") -> RecallResult:
    meta = chunk.metadata
    return RecallResult(
        id=id or meta.get("id", ""),
        text=chunk.text,
        score=chunk.score,
        source=meta.get("source", ""),
        collection=collection or meta.get("collection", ""),
        chunk_index=meta.get("chunk_index", 0),
        chunk_type=meta.get("chunk_type", "normal"),
        context=meta.get("context"),
        parent_id=meta.get("parent_id"),
    )


def _resolve_reranker(rerank_provider_id: str | None = None):
    """Resolve a reranker instance: temporary override by ID, or global default.

    NOTE: The returned Reranker instance carries its own default top_k,
    but the actual top_k used at rerank time comes from params["rerank_top_k"]
    passed to reranker.rerank(..., top_k=...).  The instance default is only
    a fallback; the call-site always provides the resolved value.
    """
    from src.providers.reranker import create_reranker_provider
    from src.rag.reranker import Reranker

    if rerank_provider_id:
        provider_cfg = next(
            (p for p in services.config.rerank.providers if p.id == rerank_provider_id), None
        )
        if provider_cfg:
            provider = create_reranker_provider(provider_cfg)
            if provider:
                top_k = provider_cfg.top_k if provider_cfg.top_k > 0 else services.config.rag.rerank_top_k
                logger.info("Using reranker from request: provider=%s", provider_cfg.name)
                return Reranker(provider=provider, top_k=top_k)

    # Fall back to global default
    if services.reranker and services.reranker.provider:
        logger.info("Using global reranker: provider=%s",
                     getattr(services.reranker.provider, '__class__', type(services.reranker.provider)).__name__)
        return services.reranker
    logger.warning(
        "No reranker configured — rerank skipped. "
        "reranker=%s, reranker.provider=%s, config.rerank.providers=%d",
        services.reranker, getattr(services.reranker, 'provider', 'N/A'),
        len(services.config.rerank.providers) if services.config else 0,
    )
    return None


def _resolve_recall_params(req: RecallSearchRequest, col_config: dict) -> dict:
    """Resolve recall parameters with 3-level fallback: request → collection config → global config.

    Mirrors chat's _resolve_params so recall evaluates the same pipeline chat uses.
    """
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
        "sparse_llm_tokenize": _resolve_recall_sparse_llm_tokenize(req, col_config, agent_enabled),
    }


def _resolve_recall_sparse_llm_tokenize(req: RecallSearchRequest, col_config: dict, agent_enabled: bool) -> bool:
    """Resolve LLM keyword extraction for sparse encoding in recall mode."""
    search_mode = req.search_mode or col_config.get("search_mode", "dense")
    if search_mode != "hybrid":
        return False
    if agent_enabled:
        return True
    if req.sparse_llm_tokenize is not None:
        return req.sparse_llm_tokenize
    return col_config.get("sparse_llm_tokenize", True)


def _compute_metrics(results_list: list[dict], k: int = 5) -> dict:
    if not results_list:
        return {"recall@k": 0.0, "mrr": 0.0, "ndcg": 0.0}

    total_recall = total_mrr = total_ndcg = 0.0
    n = len(results_list)

    for item in results_list:
        ranked = item["results"][:k]
        relevant_retrieved = sum(1 for r in ranked if r.get("relevant"))
        total_relevant = sum(1 for r in item["results"] if r.get("relevant"))
        if total_relevant > 0:
            total_recall += relevant_retrieved / total_relevant
        mrr = 0.0
        for i, r in enumerate(ranked, start=1):
            if r.get("relevant"):
                mrr = 1.0 / i
                break
        total_mrr += mrr
        dcg = sum((1.0 if r.get("relevant") else 0.0) / math.log2(i + 1) for i, r in enumerate(ranked, start=1))
        ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(total_relevant, k) + 1))
        total_ndcg += dcg / ideal if ideal > 0 else 0.0

    return {"recall@k": total_recall / n, "mrr": total_mrr / n, "ndcg": total_ndcg / n}


def _load_cases(collection: str) -> list[dict]:
    path = EVAL_DIR / f"{collection}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _save_cases(collection: str, cases: list[dict]):
    path = EVAL_DIR / f"{collection}.json"
    path.write_text(json.dumps(cases, ensure_ascii=False, indent=2))


# Generic demonstratives/refs that signal an ambiguous query
_GENERIC_REFS = {
    "this proposal", "this project", "this document", "this file", "this paper",
    "this report", "this system", "this product", "this design",
    "the proposal", "the project", "the document", "the file", "the paper",
    "the report", "the system", "the product", "the design",
    "此项目", "该项目", "该方案", "该提案", "本项目", "本方案", "本文件",
    "这个项目", "这个方案", "这个文件", "这个文档", "这个报告",
}
_CJK_RANGE = range(0x4E00, 0x9FFF + 1)


def _has_cjk(text: str) -> bool:
    return any(ord(ch) in _CJK_RANGE for ch in text)


def _is_specific_query(query: str) -> bool:
    q_lower = query.lower()
    words = query.split()
    is_cjk = _has_cjk(query)

    # Length check
    if is_cjk:
        char_count = sum(1 for ch in query if ord(ch) > 127 or ch.isalpha() or ch.isdigit())
        if char_count < 5 or char_count > 120:
            return False
    else:
        if len(words) < 5 or len(words) > 35:
            return False

    # Specific identifier checks
    has_long_number = any(any(c.isdigit() for c in w) and sum(c.isdigit() for c in w) >= 4 for w in words)
    has_mixed_alphanum = any(
        any(c.isdigit() for c in w) or (any(c.isalpha() and ord(c) < 128 for c in w))
        for w in words
    )
    has_proper_noun = any(w[0].isupper() and not w.isupper() for w in words[1:])
    has_acronym = any(w.isupper() and 2 <= len(w) <= 12 and w.isalpha() for w in words)

    # Reject queries that are purely generic demonstratives with no specific identifiers.
    # A query with specific identifiers is allowed even if it contains a generic ref.
    if not (has_long_number or has_acronym or has_proper_noun or has_mixed_alphanum or (is_cjk and char_count >= 8)):
        for ref in _GENERIC_REFS:
            if ref in q_lower:
                return False

    if is_cjk:
        return has_long_number or has_mixed_alphanum or char_count >= 8
    else:
        return has_long_number or has_acronym or has_proper_noun or has_mixed_alphanum


# ── Endpoints ──────────────────────────────────────────────


@router.post("/recall/search", response_model=RecallSearchResponse)
def recall_search(req: RecallSearchRequest):
    t0 = time.time()
    # Resolve collections: try as ID first, fall back to name (for legacy)
    def resolve_collection(cid: str) -> str:
        meta = collections_store.get_collection_meta(cid)
        return meta["id"] if meta else cid

    resolved_collections = [resolve_collection(c) for c in req.collections]
    valid_collections = [c for c in resolved_collections if services.db.collection_exists(c)]
    if not valid_collections:
        return RecallSearchResponse(results=[], time_ms=0, total=0, query_used=req.query)

    col_config = services.db.get_collection_config(valid_collections[0])
    params = _resolve_recall_params(req, col_config)
    logger.info("Recall search: collections=%s, valid=%s, min_score=%.2f, search_mode=%s, sparse_llm_tokenize=%s",
                req.collections, valid_collections, req.min_score, req.search_mode, params["sparse_llm_tokenize"])
    embedding_overrides = get_embedding_overrides(valid_collections)

    # ── Agentic branch ─────────────────────────────────────────────────
    if params["agent_enabled"]:
        from src.rag.agent import AgenticRAG
        reranker = _resolve_reranker(req.rerank_provider_id)
        agent = AgenticRAG(
            llm=services.llm, retriever=services.retriever,
            reranker=reranker if reranker and reranker.provider else None,
            rerank_top_k=params["rerank_top_k"],
            max_iterations=params["max_iterations"],
            embedding_overrides=embedding_overrides,
            search_mode=params["search_mode"],
            min_score=params["min_score"],
            db=services.db,
        )
        result = agent.run(query=req.query, collections=valid_collections, top_k=params["top_k"])
        elapsed = int((time.time() - t0) * 1000)
        results = [
            RecallResult(
                id=s.get("metadata", {}).get("id", ""), text=s["text"], score=s["score"],
                source=s.get("metadata", {}).get("source", ""),
                collection=s.get("metadata", {}).get("collection", valid_collections[0]),
                chunk_index=s.get("metadata", {}).get("chunk_index", 0),
                chunk_type=s.get("metadata", {}).get("chunk_type", "normal"),
                context=s.get("metadata", {}).get("context"),
            ) for s in result.sources
        ]
        return RecallSearchResponse(
            results=results, time_ms=elapsed, total=len(results),
            query_used=result.query_used or req.query, agent_iterations=result.iterations,
        )

    # ── Non-agentic branch ─────────────────────────────────────────────
    reranker = None
    if params["use_reranker"]:
        reranker = _resolve_reranker(req.rerank_provider_id)

    recall_llm = services.llm if params["sparse_llm_tokenize"] else None

    pc_collections = [c for c in valid_collections if services.db.get_collection_config(c).get("chunk_mode") == "parent_child"]
    normal_collections = [c for c in valid_collections if c not in pc_collections]

    child_groups_map: dict[str, list[dict]] = {}
    chunks: list[RetrievedChunk] = []

    # Parent-child collections (defer rerank to after merge)
    if pc_collections:
        pc_chunks, child_groups_list = retrieve_parent_child_multi(
            req.query, pc_collections, params["top_k"],
            embedding_overrides=embedding_overrides,
            min_score=params["min_score"],
            reranker=None,  # defer to post-merge rerank
            return_child_groups=True,
            retriever=services.retriever,
            search_mode=params["search_mode"],
            llm=recall_llm,
        )
        chunks.extend(pc_chunks)
        for group in child_groups_list:
            child_groups_map[group["parent_id"]] = group["children"]

    # Normal collections (defer rerank to after merge)
    if normal_collections:
        chunks.extend(retrieve_standard(
            req.query, normal_collections, params["top_k"],
            embedding_overrides=embedding_overrides,
            search_mode=params["search_mode"],
            min_score=params["min_score"],
            reranker=None,  # defer to post-merge rerank
            llm=recall_llm,
        ))

    # Post-merge: sort, truncate, rerank
    chunks.sort(key=lambda c: c.score, reverse=True)
    chunks = chunks[:params["top_k"]]

    if reranker and chunks:
        try:
            chunks = reranker.rerank(req.query, chunks, top_k=params["rerank_top_k"])
        except Exception as e:
            logger.warning("Reranker failed: %s", e)

    elapsed = int((time.time() - t0) * 1000)
    results = []
    for c in chunks:
        result = _hydrate_recall_result(c, collection=c.metadata.get("collection", valid_collections[0]))
        if result.chunk_type == "parent" and result.id in child_groups_map:
            result.children = [RecallResult(**child) for child in child_groups_map[result.id]]
        results.append(result)

    return RecallSearchResponse(results=results, time_ms=elapsed, total=len(results), query_used=req.query)


@router.post("/recall/benchmark", response_model=BenchmarkResult)
def recall_benchmark(req: RecallBenchmarkRequest):
    per_query_results: list[dict] = []
    total_time = 0.0

    # Use min_score as relevance threshold for benchmark
    relevance_threshold = req.min_score if req.min_score is not None else 0.5

    for q in req.queries:
        search_req = RecallSearchRequest(query=q, collections=req.collections, top_k=req.top_k, use_agent=req.use_agent, min_score=0.0)
        resp = recall_search(search_req)
        total_time += resp.time_ms
        per_query_results.append({
            "query": q, "time_ms": resp.time_ms, "results_count": resp.total,
            "results": [{"score": r.score, "relevant": r.score >= relevance_threshold} for r in resp.results],
        })

    n = len(req.queries) or 1
    metrics = _compute_metrics(per_query_results, k=req.top_k if req.top_k else 10)
    return BenchmarkResult(
        total_queries=len(req.queries), avg_time_ms=total_time / n,
        results=per_query_results, metrics=metrics,
    )


@router.get("/recall/params/{collection}")
def get_recall_params(collection: str):
    # Resolve collection: try as ID first, fall back to name (for legacy)
    col_meta = collections_store.get_collection_meta(collection)
    collection_id = col_meta["id"] if col_meta else collection

    config = services.db.get_collection_config(collection_id)
    return {
        "search_modes": ["dense", "hybrid"],
        "max_top_k": 50,
        "has_reranker": services.reranker is not None and services.reranker.provider is not None,
        "has_sparse_encoder": True,
        "chunk_mode": config.get("chunk_mode", "normal"),
    }


# ── Recall Evaluation ──────────────────────────────────────


@router.get("/recall/eval/{collection}/cases")
def get_eval_cases(collection: str):
    return {"cases": _load_cases(collection)}


@router.post("/recall/eval/{collection}/cases")
def add_eval_case(collection: str, case: EvalTestCase):
    cases = _load_cases(collection)
    new_case = case.model_dump()
    if not new_case.get("id"):
        new_case["id"] = str(uuid.uuid4())[:8]
    new_case["created_at"] = datetime.now().isoformat()
    cases.append(new_case)
    _save_cases(collection, cases)
    return new_case


@router.delete("/recall/eval/{collection}/cases/{case_id}")
def delete_eval_case(collection: str, case_id: str):
    cases = _load_cases(collection)
    cases = [c for c in cases if c.get("id") != case_id]
    _save_cases(collection, cases)
    return {"message": "Deleted"}


@router.post("/recall/eval/{collection}/cases/generate")
def generate_eval_cases(collection: str, regenerate: bool = False):
    if not services.db.collection_exists(collection):
        raise HTTPException(status_code=404, detail="Collection not found")

    all_points = []
    offset = None
    while True:
        points, offset = services.db.scroll_points(
            collection=collection, limit=1000, offset=offset,
            with_payload=["source", "text", "summary", "context"], with_vectors=False,
        )
        all_points.extend(points)
        if offset is None:
            break

    # Group chunks by source, keeping the chunk_id so we can pin a target
    source_data: dict[str, dict] = {}
    for p in all_points:
        src = p["payload"].get("source", "")
        chunk_id = str(p["id"])
        if not src or not chunk_id:
            continue
        if src not in source_data:
            source_data[src] = {"chunks": [], "summary": ""}
        source_data[src]["chunks"].append({
            "id": chunk_id,
            "text": p["payload"].get("text", ""),
        })
        if not source_data[src]["summary"]:
            source_data[src]["summary"] = p["payload"].get("summary", "") or p["payload"].get("context", "")

    # Build per-file chunk pools
    file_chunks: dict[str, list[dict]] = {}
    for src, data in source_data.items():
        if data["chunks"]:
            file_chunks[src] = [{**c, "source": src, "summary": data["summary"]} for c in data["chunks"]]

    if not file_chunks:
        return {"message": "No chunks found in collection", "total": 0}

    logger.info("generate_cases: %d files, %d total chunks, regenerate=%s",
                len(file_chunks), sum(len(v) for v in file_chunks.values()), regenerate)

    existing = [] if regenerate else _load_cases(collection)
    existing_ids = {c.get("target_chunk_id") for c in existing}
    logger.info("generate_cases: %d existing cases, %d unique chunk IDs used", len(existing), len(existing_ids))

    # Remove already-used chunks from each file's pool
    for src in list(file_chunks):
        file_chunks[src] = [c for c in file_chunks[src] if c["id"] not in existing_ids]
        if not file_chunks[src]:
            del file_chunks[src]

    logger.info("generate_cases: after filtering, %d files remain with chunks: %s",
                len(file_chunks), {s.split('/')[-1]: len(cs) for s, cs in file_chunks.items()})

    # Sample 10 files with replacement, pick one unused chunk per draw
    file_names = list(file_chunks)
    selected: list[dict] = []
    used_chunk_ids: set[str] = set()
    max_attempts = 10 * 3  # safety valve
    attempts = 0

    while len(selected) < 10 and file_names and attempts < max_attempts:
        attempts += 1
        src = random.choice(file_names)
        pool = file_chunks[src]
        # Pick a random chunk from this file that hasn't been used yet
        avail = [c for c in pool if c["id"] not in used_chunk_ids]
        if not avail:
            # All chunks from this file used, remove file from rotation
            file_chunks.pop(src, None)
            file_names.remove(src)
            continue
        chunk = random.choice(avail)
        selected.append(chunk)
        used_chunk_ids.add(chunk["id"])

    if not selected:
        return {"message": "All chunks already have test cases", "total": len(existing)}

    logger.info("generate_cases: selected %d chunks from %d unique files",
                len(selected), len({c['source'] for c in selected}))

    new_cases: list[dict] = []
    if selected:
        chunk_list_str = "\n\n".join(
            f"[{i+1}] (chunk_id: {c['id']}, file: {c['source'].split('/')[-1]})\n{c['text'][:1500]}"
            for i, c in enumerate(selected)
        )
        file_list = sorted({c["source"] for c in selected})
        summary_hint = ""
        for c in selected:
            if c.get("summary"):
                summary_hint = f"\nDocument summary (for {c['source'].split('/')[-1]}): {c['summary'][:500]}"
                break

        prompt = (
            f"You are building a search evaluation dataset. Below are {len(selected)} chunks sampled from {len(file_list)} document(s).\n\n"
            f"Generate exactly ONE test case per chunk. Each test case is {{query, target_chunk_index}}:\n"
            f"- query: a natural question (5-30 words) a real user would type to find this content\n"
            f"- target_chunk_index: 1-based index of the chunk (1 to {len(selected)}) that COMPLETELY answers this query\n\n"
            f"HARD REQUIREMENTS:\n"
            f"- target_chunk must FULLY answer the query (not partially)\n"
            f"- query MUST include specific identifiers (project name, document name, specific numbers, year, or named entity)\n"
            f"- DO NOT use generic references like 'this proposal', 'the project', '此项目' without naming them\n"
            f"- Vary query types: some specific/factual, some conceptual/broad, some problem-oriented\n\n"
            f"Files: {', '.join(f.split('/')[-1] for f in file_list)}{summary_hint}\n\n"
            f"Chunks (1-indexed):\n{chunk_list_str}\n\n"
            f"Reply with ONLY a JSON array, no other text:\n"
            f'[{{"query": "...", "target_chunk_index": 1}}, ...]  (exactly {len(selected)} entries)'
        )

        parsed_items: list[dict] = []
        for _attempt in range(3):
            try:
                response = services.llm.generate(prompt).strip()
            except Exception:
                continue
            import re as re_mod, json as json_mod
            json_match = re_mod.search(r"\[[\s\S]*\]", response)
            if not json_match:
                continue
            try:
                parsed = json_mod.loads(json_match.group())
            except Exception:
                continue
            if not isinstance(parsed, list):
                continue
            parsed_items = [p for p in parsed if isinstance(p, dict)]
            if parsed_items:
                break

        logger.info("generate_cases: LLM returned %d items (attempt %d)", len(parsed_items), _attempt + 1)

        seen_queries: set[str] = {c.get("query", "").lower() for c in existing}
        skipped_bad_query = 0
        skipped_bad_index = 0
        skipped_not_specific = 0
        for item in parsed_items:
            q = str(item.get("query", "")).strip().strip('"').strip("'")
            idx = item.get("target_chunk_index")
            if not q or len(q) < 5 or q.lower() in seen_queries:
                skipped_bad_query += 1
                continue
            if not isinstance(idx, int) or idx < 1 or idx > len(selected):
                skipped_bad_index += 1
                continue
            if not _is_specific_query(q):
                skipped_not_specific += 1
                logger.info("generate_cases: rejected non-specific query: %r", q[:100])
                continue
            seen_queries.add(q.lower())
            new_cases.append({
                "id": str(uuid.uuid4())[:8],
                "query": q,
                "target_chunk_id": selected[idx - 1]["id"],
                "target_source": selected[idx - 1]["source"],
                "created_at": datetime.now().isoformat(),
            })
            if len(new_cases) >= 10:
                break

        logger.info("generate_cases: validation — accepted=%d, skipped_bad_query=%d, skipped_bad_index=%d, skipped_not_specific=%d",
                    len(new_cases), skipped_bad_query, skipped_bad_index, skipped_not_specific)

    all_cases = existing + new_cases
    _save_cases(collection, all_cases)
    action = "Regenerated" if regenerate else "Generated"
    return {"message": f"{action} {len(new_cases)} test cases", "total": len(all_cases)}


@router.post("/recall/eval/{collection}/run")
def run_eval(collection: str, req: EvalRequest):
    if not services.db.collection_exists(collection):
        raise HTTPException(status_code=404, detail="Collection not found")

    cases = _load_cases(collection)
    if not cases:
        raise HTTPException(status_code=400, detail="No test cases. Add or generate cases first.")

    col_config = services.db.get_collection_config(collection)
    config_snapshot = {
        "chunk_mode": col_config.get("chunk_mode", "normal"),
        "search_mode": req.search_mode,
        "dimensions": col_config.get("dimensions"),
        "embedding_model": col_config.get("embedding_model") or (services.config.embedding.default.model if services.config.embedding.default else ""),
        "use_reranker": req.use_reranker,
        "rerank_top_k": req.rerank_top_k,
        "top_k": req.top_k,
    }

    per_query: list[dict] = []
    for case in cases:
        t0 = time.time()
        query_text = case["query"]
        target_chunk_id = case.get("target_chunk_id", "")
        target_source = case.get("target_source", "")

        search_req = RecallSearchRequest(
            query=query_text, collections=[collection], search_mode=req.search_mode,
            top_k=req.top_k, rerank_top_k=req.rerank_top_k, use_reranker=req.use_reranker,
            min_score=req.min_score, sparse_llm_tokenize=req.sparse_llm_tokenize,
            rerank_provider_id=req.rerank_provider_id,
        )
        resp = recall_search(search_req)

        retrieved_chunks = [
            {
                "id": r.id,
                "text": r.text,
                "score": r.score,
                "source": r.source,
                "chunk_index": r.chunk_index,
                "chunk_type": r.chunk_type,
                "context": r.context,
            }
            for r in resp.results
        ]

        k = len(retrieved_chunks)
        retrieved_ids = {c["id"] for c in retrieved_chunks}

        # ── Hard recall: target chunk present in top K?
        hard_recall = 1 if target_chunk_id and target_chunk_id in retrieved_ids else 0
        target_position = next(
            (i + 1 for i, c in enumerate(retrieved_chunks) if c["id"] == target_chunk_id),
            0,
        )

        # ── Per-chunk judgment + holistic check via ONE batched LLM call
        #   Layer 1: per-chunk -1/0/+1 (loose "useful" definition) — for detailed feedback only
        #   Layer 2: holistic yes/no — drives the recalled metric
        chunk_judgments: list[dict] = []
        holistic_can_answer: int | None = None
        holistic_reason: str = ""
        has_positive = False
        negative_count = 0
        judged_count = 0

        if retrieved_chunks and services.llm:
            chunks_text = "\n\n".join(
                f"--- Chunk {i+1} (id: {c['id']}, source: {c['source'].split('/')[-1]}) ---\n{c['text'][:2000]}"
                for i, c in enumerate(retrieved_chunks)
            )
            judge_prompt = (
                f"You are evaluating retrieval quality for a RAG system.\n\n"
                f"A user asked:\n\"{query_text}\"\n\n"
                f"Below are {k} chunks retrieved by the search system. Do two things:\n\n"
                f"1) For each chunk, judge whether it would help the LLM produce a correct answer.\n"
                f"   - score +1: useful (has substantive info for the query, even if partial)\n"
                f"   - score 0: on-topic but not useful\n"
                f"   - score -1: off-topic / unrelated / would mislead the LLM\n\n"
                f"2) Aggregate judgment: given ALL chunks combined, can the LLM produce a correct\n"
                f"   and complete answer to the user's query? Reply \"yes\" or \"no\" with a brief reason.\n"
                f"   - yes: the retrieved set, taken together, contains enough info to answer correctly\n"
                f"   - no: the retrieved set is missing key info, or is misleading on its own\n\n"
                f"Chunks:\n{chunks_text}\n\n"
                f"Reply with ONLY this JSON:\n"
                f'{{"per_chunk": [{{"score": 1, "reason": "..."}}, ... {k} entries ...], '
                f'"aggregate": {{"can_answer": "yes", "reason": "..."}}}}'
            )
            try:
                judge_response = services.llm.generate(judge_prompt).strip()
                import re as re_mod, json as json_mod
                json_match = re_mod.search(r"\{[\s\S]*\}", judge_response)
                if json_match:
                    parsed = json_mod.loads(json_match.group())
                    if isinstance(parsed, dict):
                        # Layer 1: per-chunk judgments
                        per_chunk = parsed.get("per_chunk", [])
                        if isinstance(per_chunk, list):
                            for i, c in enumerate(retrieved_chunks):
                                j = per_chunk[i] if i < len(per_chunk) and isinstance(per_chunk[i], dict) else {}
                                raw_score = j.get("score", 0)
                                try:
                                    score = int(raw_score)
                                except (TypeError, ValueError):
                                    score = 0
                                score = max(-1, min(1, score))
                                reason = str(j.get("reason", "")).strip()
                                is_target = c["id"] == target_chunk_id
                                chunk_judgments.append({
                                    "id": c["id"],
                                    "source": c["source"],
                                    "chunk_index": c["chunk_index"],
                                    "score": c["score"],
                                    "judgment": score,
                                    "reason": reason,
                                    "is_target": is_target,
                                })
                                if score == 1:
                                    has_positive = True
                                elif score == -1:
                                    negative_count += 1
                                judged_count += 1
                        # Layer 2: aggregate holistic judgment
                        aggregate = parsed.get("aggregate", {})
                        if isinstance(aggregate, dict):
                            ans = str(aggregate.get("can_answer", "")).strip().lower()
                            holistic_can_answer = 1 if ans in ("yes", "true", "1") else 0
                            holistic_reason = str(aggregate.get("reason", "")).strip()
            except Exception:
                pass

        # Backfill any chunks that didn't get judged (in case LLM response was short)
        judged_ids = {j["id"] for j in chunk_judgments}
        for c in retrieved_chunks:
            if c["id"] not in judged_ids:
                chunk_judgments.append({
                    "id": c["id"],
                    "source": c["source"],
                    "chunk_index": c["chunk_index"],
                    "score": c["score"],
                    "judgment": 0,
                    "reason": "(no judgment)",
                    "is_target": c["id"] == target_chunk_id,
                })
                judged_count += 1

        # Coverage-dominant quality score in [-1, 1]:
        #   coverage (any +1 chunk) is the dominant signal — once useful info is present,
        #   the LLM can extract it despite some noise. Without coverage, noise actively hurts.
        #   Note: this is informational/feedback; recalled is driven by holistic_yes.
        coverage = 1 if has_positive else 0
        noise_ratio = (negative_count / judged_count) if judged_count else 0.0
        quality_score = (
            coverage * (1.0 - 0.3 * noise_ratio)
            - (1 - coverage) * (0.5 + 0.5 * noise_ratio)
        )
        quality_score = max(-1.0, min(1.0, quality_score))

        # Recalled: hard_recall OR holistic_can_answer (both query-level signals)
        recalled = hard_recall or (holistic_can_answer == 1)

        mrr = (1.0 / target_position) if target_position > 0 else 0.0

        elapsed = int((time.time() - t0) * 1000)
        per_query.append({
            "test_case_id": case.get("id", ""),
            "query": query_text,
            "target_source": target_source,
            "hard_recall": hard_recall,
            "holistic_can_answer": holistic_can_answer if holistic_can_answer is not None else 0,
            "holistic_reason": holistic_reason,
            "recalled": recalled,
            "quality_score": round(quality_score, 4),
            "mrr": round(mrr, 4),
            "target_position": target_position,
            "chunk_judgments": chunk_judgments,
            "retrieved_chunks": retrieved_chunks,
            "time_ms": elapsed,
        })

    n = len(per_query) or 1
    report = {
        "collection": collection,
        "config_snapshot": config_snapshot,
        "total_cases": len(per_query),
        "avg_hard_recall": round(sum(r["hard_recall"] for r in per_query) / n, 4),
        "avg_holistic_recall": round(
            sum(r["holistic_can_answer"] for r in per_query) / n, 4
        ),
        "avg_recall": round(sum(r["recalled"] for r in per_query) / n, 4),
        "avg_quality_score": round(sum(r["quality_score"] for r in per_query) / n, 4),
        "avg_mrr": round(sum(r["mrr"] for r in per_query) / n, 4),
        "hit_rate": round(sum(1 for r in per_query if r["recalled"]) / n, 4),
        "avg_time_ms": round(sum(r["time_ms"] for r in per_query) / n, 1),
        "per_query": per_query,
    }

    history_path = EVAL_DIR / f"{collection}_history.jsonl"
    with open(history_path, "a") as f:
        entry = {**report, "timestamp": datetime.now(timezone.utc).isoformat()}
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return report


@router.get("/recall/eval/{collection}/history")
def get_eval_history(collection: str):
    history_path = EVAL_DIR / f"{collection}_history.jsonl"
    if not history_path.exists():
        return {"history": []}
    entries = []
    for line in history_path.read_text().strip().split("\n"):
        if line:
            entries.append(json.loads(line))
    return {"history": entries[-20:]}


@router.get("/recall/eval/{collection}/chunk/{chunk_id}")
def get_chunk_content(collection: str, chunk_id: str):
    """Get chunk text content by ID for displaying in the evaluate tab."""
    if not services.db.collection_exists(collection):
        raise HTTPException(status_code=404, detail="Collection not found")
    points = services.db.get_points_by_ids(collection, [chunk_id])
    if not points:
        raise HTTPException(status_code=404, detail="Chunk not found")
    p = points[0]
    payload = p.get("payload", {})
    return {
        "id": str(p["id"]),
        "text": payload.get("text", ""),
        "source": payload.get("source", ""),
        "chunk_index": payload.get("chunk_index", 0),
    }
