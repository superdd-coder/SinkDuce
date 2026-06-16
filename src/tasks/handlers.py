"""任务处理器 - 处理文件上传和索引"""

from __future__ import annotations

import asyncio
import logging
import time
import threading
import uuid
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor

from src.tasks.task_manager import Task
from src.services import services
from src.parsers import parse_file
from src.rag.chunker import ParentChildChunker, ParagraphChunker
from src.rag.collection_utils import get_collection_embedding
from src.rag.summary_manager import SummaryManager

logger = logging.getLogger(__name__)

# Pipeline stage locks: only one file can be in enriching/embedding at a time,
# but different files can be at different stages concurrently.
# Use threading.Lock for reliable cross-thread synchronization.
_enrich_lock = threading.Lock()
_embed_lock = threading.Lock()

# Separate thread pool for CPU-intensive operations (embedding, parsing)
# This prevents embedding tasks from blocking the main async event loop
_cpu_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cpu-worker")


def _get_enriching_llm(config: dict):
    """Get LLM for contextual enrichment. Uses per-collection config if set, else global."""
    from src.providers.llm import create_llm_for_provider
    from src.config import get_config
    provider_id = config.get("enriching_llm_provider")
    if provider_id:
        for p in get_config().llm.providers:
            if p.id == provider_id:
                model = config.get("enriching_llm_model")
                return create_llm_for_provider(p, model=model)
    # Fall back to default provider from config
    cfg = get_config()
    if cfg.llm.providers:
        default_p = next((p for p in cfg.llm.providers if p.is_default), cfg.llm.providers[0])
        return create_llm_for_provider(default_p)
    return services.llm


def _do_enrich(chunks, doc, config):
    """Run contextual enrichment (blocking). Must be called with _enrich_lock held."""
    enriching_llm = _get_enriching_llm(config)
    ctx_window = config.get("contextual_window", 1)
    from src.rag.contextual import ContextualRetrieval
    contextual = ContextualRetrieval(llm=enriching_llm, context_window=ctx_window)
    if config.get("chunk_mode") == "parent_child":
        parent_chunks = [c for c in chunks if c.chunk_type == "parent"]
        child_chunks = [c for c in chunks if c.chunk_type == "child"]
        if len(parent_chunks) <= 200:
            parent_chunks = contextual.add_context(parent_chunks, full_document=doc.content)
        if len(child_chunks) <= 500:
            child_chunks = contextual.add_context(child_chunks, full_document=doc.content)
        return parent_chunks + child_chunks
    else:
        if len(chunks) <= 200:
            return contextual.add_context(chunks, full_document=doc.content)
        return chunks


def _do_embed(chunks, config, collection):
    """Run embedding (blocking). Must be called with _embed_lock held."""
    embedding = get_collection_embedding(config, collection)
    texts = [c.text for c in chunks]
    return embedding.embed_texts(texts)


# ── Consolidation ──────────────────────────────────────────

CONSOLIDATION_PROMPT = """You are analyzing multiple document summaries from a single project. Synthesize them into:

1. A CONCISE PROJECT SUMMARY (300 words max): Write a high-level overview of the project, NOT a per-document re-summary. Synthesize across all documents to answer:
   - What is this project? (type, scope, scale)
   - Who is involved? (client, vendor, key parties)
   - Key technical parameters (capacity, process, specs)
   - Key commercial terms (contract value, rate, duration)
   - Timeline and status
   Write in concise paragraphs without ## sub-headings. Use **bold** for key numbers and names.

2. CONFLICTS: Identify ONLY genuine contradictions where two documents make different claims about the SAME fact.

Document summaries:
{summaries}

===OUTPUT FORMAT===

Output a single JSON object with this EXACT schema (no markdown, no extra text):

{{
  "summary": "(Concise project overview, max 300 words, plain paragraphs with **bold** highlights)",
  "conflicts": [
    {{"content1": "claim from doc 1", "source1": "filename1", "content2": "claim from doc 2", "source2": "filename2"}}
  ]
}}

If no conflicts, use an empty array: "conflicts": []"""

PROJECT_DESCRIPTION_PROMPT = """Based on the following document summaries from project "{project_name}", write a concise 2-sentence project description that captures what this project is about.

The description should:
- START with the project name: "{project_name}" followed by a dash or colon
- Sentence 1: What the project is (type, scope, scale)
- Sentence 2: What makes this project distinctive (key parties, location, or unique characteristics)

Output ONLY the 2-sentence description, nothing else.

Document summaries:
{summaries}"""


def format_doc_summaries_for_prompt(summaries: list[dict]) -> str:
    """Format doc summaries into text for the consolidation prompt."""
    if not summaries:
        return ""
    parts = []
    for s in summaries:
        source = s.get("source", "unknown")
        lines = [f"--- {source} ---"]
        data = s.get("data", [])
        facts = s.get("facts", [])
        insights = s.get("insights", [])
        if data:
            lines.append("Data:")
            for d in data:
                lines.append(f"  - {d}")
        if facts:
            lines.append("Facts:")
            for f in facts:
                lines.append(f"  - {f}")
        if insights:
            lines.append("Insights:")
            for i in insights:
                lines.append(f"  - {i}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def parse_consolidation_response(raw: str) -> tuple[str, list[dict]]:
    """Parse LLM consolidation response into summary text and conflict dicts.

    Returns ``(collection_summary, conflicts)`` where each conflict is a dict
    with keys ``content1``, ``source1``, ``content2``, ``source2``.

    Handles both JSON output (preferred) and legacy === delimiter format.
    """
    if not raw or not raw.strip():
        return "", []

    import json as _json
    import re as _re

    # Try JSON first
    raw_stripped = raw.strip()
    # Extract JSON object from response (may have markdown fences or extra text)
    json_match = _re.search(r"\{[\s\S]*\}", raw_stripped)
    if json_match:
        try:
            data = _json.loads(json_match.group())
            summary_text = data.get("summary", "")
            conflicts = data.get("conflicts", [])
            if isinstance(conflicts, list) and summary_text:
                return summary_text, conflicts
        except (_json.JSONDecodeError, KeyError):
            pass

    # Fallback: legacy === delimiter format
    summary_text = ""
    conflicts: list[dict] = []
    current_section: str | None = None
    summary_lines: list[str] = []

    for line in raw.splitlines():
        stripped = line.strip()

        if stripped.startswith("===") and stripped.endswith("==="):
            header = stripped[3:-3].strip().lower()
            if header == "summary":
                current_section = "summary"
            elif header == "conflicts":
                current_section = "conflicts"
            else:
                current_section = None
            continue

        if current_section == "summary":
            summary_lines.append(line)
            continue

        if current_section == "conflicts":
            if not stripped:
                continue
            conflict_line = stripped.lstrip("-").strip()
            if not conflict_line or conflict_line.lower() == "none identified":
                continue
            parts = [p.strip() for p in conflict_line.split("|")]
            if len(parts) >= 4:
                conflicts.append({
                    "content1": parts[0],
                    "source1": parts[1],
                    "content2": parts[2],
                    "source2": parts[3],
                })

    summary_text = "\n".join(summary_lines).strip()
    return summary_text, conflicts


async def consolidate_handler(task: Task, collection: str) -> dict:
    """Consolidate all document summaries into a collection summary and detect conflicts."""
    logger.info("[CONSOLIDATE] Starting consolidation for collection='%s'", collection)
    summary_mgr = SummaryManager(db=services.db)
    summary_mgr.ensure_collection()
    logger.info("[CONSOLIDATE] __summaries__ collection ensured")

    # 1. Read all doc_summaries
    doc_summaries = summary_mgr.get_doc_summaries(collection, included_only=True)
    logger.info("[CONSOLIDATE] Found %d doc_summaries for collection='%s'", len(doc_summaries), collection)
    if not doc_summaries:
        logger.info("[CONSOLIDATE] No documents to consolidate, aborting")
        return {"message": "No documents to consolidate"}

    # 1b. Check if any doc summary has usable content
    has_content = any(
        s.get("data") or s.get("facts") or s.get("insights")
        for s in doc_summaries
    )
    if not has_content:
        logger.info("[CONSOLIDATE] No doc summaries have usable content (data/facts/insights), skipping LLM")
        services.db.update_collection_config(collection, {"summary_change_counter": 0})
        return {"message": "No usable doc summaries to consolidate", "conflicts_count": 0}

    # 2. Format and call LLM (generate first, delete old only on success)
    summaries_text = format_doc_summaries_for_prompt(doc_summaries)
    logger.info("[CONSOLIDATE] Formatted summaries (%d chars), calling LLM...", len(summaries_text))
    config = services.db.get_collection_config(collection)
    enriching_llm = _get_enriching_llm(config)
    loop = asyncio.get_running_loop()
    raw = await loop.run_in_executor(
        None, lambda: enriching_llm.generate(CONSOLIDATION_PROMPT.format(summaries=summaries_text))
    )
    logger.info("[CONSOLIDATE] LLM returned %d chars", len(raw))
    collection_summary, conflicts = parse_consolidation_response(raw)
    logger.info("[CONSOLIDATE] Parsed: summary=%d chars, %d conflicts", len(collection_summary), len(conflicts))

    if not collection_summary:
        logger.error("[CONSOLIDATE] LLM returned empty collection_summary, aborting to preserve old data. Raw: %s", raw[:500])
        return {"message": "Consolidation failed: LLM returned empty summary", "conflicts_count": 0}

    # 3. Generate project description
    project_desc = ""
    try:
        logger.info("[CONSOLIDATE] Generating project description...")
        desc_raw = await loop.run_in_executor(
            None, lambda: enriching_llm.generate(
                PROJECT_DESCRIPTION_PROMPT.format(summaries=summaries_text, project_name=collection),
                max_tokens=512,
            )
        )
        project_desc = desc_raw.strip()
        logger.info("[CONSOLIDATE] Project description: %d chars", len(project_desc))
    except Exception as e:
        logger.error("[CONSOLIDATE] Project description generation failed: %s", e, exc_info=True)

    # 4. Delete old data and store new (atomic: all new content ready before deleting)
    logger.info("[CONSOLIDATE] Deleting old data for collection='%s'", collection)
    summary_mgr.delete_collection_summary(collection)
    summary_mgr.delete_project_description(collection)
    summary_mgr.delete_conflicts(collection)

    summary_mgr.store_collection_summary(collection, collection_summary)
    summary_mgr.store_conflicts(collection, conflicts)
    if project_desc:
        summary_mgr.store_project_description(collection, project_desc)
        logger.info("[CONSOLIDATE] Project description stored")
    logger.info("[CONSOLIDATE] Storage done")

    # 6. Reset counter
    services.db.update_collection_config(collection, {"summary_change_counter": 0})
    logger.info("[CONSOLIDATE] Counter reset to 0 for collection='%s'", collection)
    logger.info("[CONSOLIDATE] Consolidation complete for collection='%s' (summary=%d chars, conflicts=%d, desc=%d chars)",
                collection, len(collection_summary), len(conflicts), len(project_desc))
    return {"message": "Consolidation done", "conflicts_count": len(conflicts)}


async def upload_handler(task: Task, file_path: str, collection: str, filename_param: str, meeting_id: str | None = None) -> dict[str, Any]:
    """处理文件上传任务 - 使用流水线队列控制并发"""

    def update(progress: float, msg: str):
        task.progress = progress
        task.message = msg

    loop = asyncio.get_running_loop()

    try:
        t_start = time.time()
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")

        # ── Stage 1: Parsing + Chunking (concurrent, no lock) ──
        update(10, "Checking collection...")

        def _parse_and_chunk():
            if not services.db.collection_exists(collection):
                services.db.create_collection(collection, vector_size=services.embedding.dimensions)

            update(20, "Parsing file...")
            doc = parse_file(path)
            logger.info("[%s] Parse done in %.1fs, content length: %d",
                        filename_param, time.time() - t_start, len(doc.content or ""))

            if not doc.content or not doc.content.strip():
                raise ValueError(
                    f"No extractable text found in '{filename_param}'. "
                    "The file may be empty or the images could not be read by OCR."
                )

            # Save parsed text for preview (same text the chunker uses)
            try:
                parsed_path = path.with_suffix(path.suffix + ".parsed.txt")
                parsed_path.write_text(doc.content, encoding="utf-8")
            except Exception as e:
                logger.warning("[%s] Failed to save parsed text: %s", filename_param, e)

            update(40, "Chunking...")
            config = services.db.get_collection_config(collection)
            if config.get("chunk_mode") == "parent_child":
                chunker = ParentChildChunker(
                    parent_strategy=config.get("parent_strategy", "paragraph"),
                    parent_chunk_size=config.get("parent_chunk_size", 1024),
                    parent_overlap=config.get("parent_chunk_overlap", 128),
                    parent_buffer_ratio=config.get("buffer_ratio", 0.5),
                    child_chunk_size=config.get("child_chunk_size", 128),
                    child_overlap=config.get("child_chunk_overlap", 32),
                    child_buffer_ratio=config.get("buffer_ratio", 0.5),
                )
            else:
                chunker = ParagraphChunker(
                    max_tokens=config.get("chunk_size", 512),
                    buffer_ratio=config.get("buffer_ratio", 0.5),
                    chunk_overlap=config.get("chunk_overlap", 64),
                )

            t_chunk = time.time()
            extra_meta: dict = {"file_type": doc.file_type}
            if doc.position_map:
                extra_meta["position_map"] = doc.position_map
            if meeting_id:
                extra_meta["meeting_id"] = meeting_id
            chunks = chunker.chunk_with_metadata(
                doc.content, source=filename_param, extra_metadata=extra_meta
            )
            logger.info("[%s] Chunking done in %.1fs, %d chunks",
                        filename_param, time.time() - t_chunk, len(chunks))

            if not chunks:
                raise ValueError(
                    f"Chunking produced no results for '{filename_param}'. "
                    "The content may be too short or not match the chunking strategy."
                )

            return doc, chunks, config

        # Use separate CPU thread pool for parsing/chunking
        doc, chunks, config = await loop.run_in_executor(_cpu_executor, _parse_and_chunk)

        # ── Stage 2: Enriching (serialized via _enrich_lock) ──
        t_ctx = time.time()
        contextual_enabled = config.get("contextual_enabled", True)
        if contextual_enabled:
            update(50, "Waiting for enriching slot...")

            def _enrich_with_lock():
                _enrich_lock.acquire()
                try:
                    update(50, "Enriching with context...")
                    return _do_enrich(chunks, doc, config)
                finally:
                    _enrich_lock.release()

            # Use separate CPU thread pool for enriching
            chunks = await loop.run_in_executor(_cpu_executor, _enrich_with_lock)

        logger.info("[%s] Enrichment done in %.1fs (%d chunks)",
                    filename_param, time.time() - t_ctx, len(chunks))

        # ── Stage 3: Embedding (serialized via _embed_lock) ──
        t_emb = time.time()
        update(70, "Waiting for embedding slot...")

        def _embed_with_lock():
            _embed_lock.acquire()
            try:
                update(70, "Generating embeddings...")
                return _do_embed(chunks, config, collection)
            finally:
                _embed_lock.release()

        # Use separate CPU thread pool for embedding
        embeddings = await loop.run_in_executor(_cpu_executor, _embed_with_lock)

        # ── Stage 4: Storage ──
        def _do_store():
            update(85, "Storing...")
            ids = []
            for c in chunks:
                if c.chunk_type in ("parent", "child"):
                    ids.append(c.metadata["chunk_id"])
                else:
                    new_id = str(uuid.uuid4())
                    c.metadata["chunk_id"] = new_id
                    ids.append(new_id)
            payloads = []
            for c in chunks:
                payload = {"text": c.text, "parent_id": c.parent_id, "chunk_type": c.chunk_type}
                if c.metadata.get("context"):
                    payload["context"] = c.metadata["context"]
                if c.metadata.get("summary"):
                    payload["summary"] = c.metadata["summary"]
                payload.update({k: v for k, v in c.metadata.items() if k not in ("context", "summary")})
                payload["collection"] = collection
                payloads.append(payload)
            logger.info("[%s] Embedding done in %.1fs", filename_param, time.time() - t_emb)

            t_store = time.time()
            services.db.upsert_points(collection=collection, ids=ids, vectors=embeddings, payloads=payloads)
            logger.info("[%s] Store done in %.1fs. Total: %.1fs",
                        filename_param, time.time() - t_store, time.time() - t_start)

        # Use default thread pool for storage (I/O bound)
        await loop.run_in_executor(None, _do_store)

        update(100, f"Indexed {len(chunks)} chunks")
        return {"message": "Done", "filename": filename_param, "chunks_count": len(chunks), "collection": collection}

    except Exception as e:
        raise Exception(f"Failed to process {filename_param}: {e}")


# ---------------------------------------------------------------------------
# Meeting Summary handler
# ---------------------------------------------------------------------------

async def meeting_summary_handler(task: Task, meeting_id: str, **kwargs) -> dict:
    """Generate meeting summary via LLM, with summarizing flag on meeting model."""
    from src.meeting import store
    from src.meeting.service import MeetingService
    logger.info("[MEETING_SUMMARY] Starting for meeting %s", meeting_id)

    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")

    store.update_meeting(meeting_id, summarizing=True)
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _do_meeting_summary, meeting_id)
        return {"message": "Summary generated", "meeting_id": meeting_id}
    except Exception:
        store.update_meeting(meeting_id, summarizing=False)
        raise


def _do_meeting_summary(meeting_id: str):
    from src.meeting.service import MeetingService
    svc = MeetingService()
    svc._do_generate_summary(meeting_id)


# ---------------------------------------------------------------------------
# Doc Summary handler
# ---------------------------------------------------------------------------

async def doc_summary_handler(task: Task, collection: str, source: str) -> dict:
    """Generate per-document structured summary via LLM."""
    from pathlib import Path as _Path
    from src.parsers import parse_file
    from src.rag.contextual import generate_structured_summary
    from src.api.routes.info import _get_summary_manager, _get_enriching_llm

    logger.info("[DOC_SUMMARY] Starting for collection=%s source=%s", collection, source)

    upload_dir = _Path("data").resolve() / "uploads"
    file_path = upload_dir / source
    if not file_path.exists():
        for f in upload_dir.iterdir():
            if f.name == source:
                file_path = f
                break
    if not file_path.exists():
        raise FileNotFoundError(f"Source file '{source}' not found in uploads")

    loop = asyncio.get_running_loop()
    doc = await loop.run_in_executor(None, parse_file, file_path)
    if not doc.content or not doc.content.strip():
        raise ValueError("File has no extractable text content")

    config = services.db.get_collection_config(collection)
    enriching_llm = _get_enriching_llm(config)
    doc_summary = await loop.run_in_executor(
        None, lambda: generate_structured_summary(enriching_llm, doc.content)
    )
    logger.info("[DOC_SUMMARY] Generated: data=%d, facts=%d, insights=%d",
                len(doc_summary.get("data", [])), len(doc_summary.get("facts", [])), len(doc_summary.get("insights", [])))

    sm = _get_summary_manager()
    sm.ensure_collection()
    sm.store_doc_summary(
        collection, source,
        doc_summary.get("data", []),
        doc_summary.get("facts", []),
        doc_summary.get("insights", []),
        include_in_summary=True,
    )

    counter = config.get("summary_change_counter", 0) + 1
    services.db.update_collection_config(collection, {"summary_change_counter": counter})
    if counter >= config.get("summary_consolidate_threshold", 10):
        from src.tasks import task_manager as _tm
        _tm.create_task(filename=f"consolidate:{collection}", task_type="consolidate", collection=collection)

    return {"message": "Summary generated", "source": source}
