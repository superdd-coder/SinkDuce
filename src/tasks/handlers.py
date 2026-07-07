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

from src.tasks.task_manager import Task, task_manager
from src.services import services
from src.parsers import parse_file
from src.parsers.mineru_parser import parse_with_mineru, MINERU_SUPPORTED_EXTENSIONS, MinerUError
from src.rag.chunker import ParentChildChunker, ParagraphChunker
from src.rag.markdown_chunker import MarkdownChunker, MarkdownParentChildChunker
from src.rag.collection_utils import get_collection_embedding
from src.rag.summary_manager import SummaryManager

logger = logging.getLogger(__name__)

# Pipeline stage locks: only one file can be in enriching/embedding at a time,
# but different files can be at different stages concurrently.
# Use threading.Lock for reliable cross-thread synchronization.
_enrich_lock = threading.Lock()
_embed_executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix="embed-worker")
_cpu_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cpu-worker")


class _EmbedBatcher:
    """Collects ready chunks, assembles enriched text, and submits batches to the
    shared embedding executor as soon as 10 chunks accumulate.  Chunks that arrive
    before the summary is available are queued and flushed once ``set_summary()``
    is called.
    """

    def __init__(self, embedding, *, total_chunks: int = 0, on_progress=None):
        self._embedding = embedding
        self._lock = threading.Lock()
        self._buffer: list[str] = []
        self._all_texts: list[str] = []
        self._futures: list[tuple] = []          # (future, batch_texts, indices) for retry
        self._summary: str | None = None
        self._pending: list[tuple] = []          # (chunk, context) waiting for summary
        self._assembled = 0
        self._total = total_chunks
        self._on_progress = on_progress

    def set_summary(self, summary: str):
        with self._lock:
            self._summary = summary
            for chunk, ctx in self._pending:
                self._assemble_and_buffer(chunk, ctx)
            self._pending.clear()

    def on_ready(self, chunk, context: str):
        # Parent chunks are never searched — skip embedding
        if chunk.chunk_type == "parent":
            return
        with self._lock:
            if self._summary is None:
                self._pending.append((chunk, context))
                return
            self._assemble_and_buffer(chunk, context)

    def _assemble_and_buffer(self, chunk, context: str):
        idx = chunk.metadata.get("_embed_idx", 0)
        text = _build_enriched_text(chunk)
        self._buffer.append((idx, text))
        self._all_texts.append(text)
        self._assembled += 1
        if self._on_progress and self._total:
            self._on_progress(self._assembled, self._total)
        if len(self._buffer) >= 10:
            self._submit_batch()

    def _submit_batch(self):
        from src.tasks.task_manager import check_cancelled
        check_cancelled()
        batch_items = self._buffer[:10]
        self._buffer = self._buffer[10:]
        indices = [i for i, _t in batch_items]
        batch_texts = [t for _i, t in batch_items]
        logger.info("[EmbedBatcher] submitting batch of %d (assembled=%d/%d)",
                    len(batch_texts), self._assembled, self._total)
        f = _embed_executor.submit(self._embedding.embed_texts, batch_texts)
        self._futures.append((f, batch_texts, indices))

    def flush(self):
        """Submit any remaining <10 chunk batch."""
        with self._lock:
            if self._buffer:
                self._submit_batch()

    def get_all_texts(self) -> list[str]:
        """Return snapshot of all assembled texts for sparse encoding."""
        with self._lock:
            return list(self._all_texts)

    def wait_all(self, chunks: list = None) -> list[list[float]]:
        """Wait for all submitted batches, retrying up to 3 times with 5s delay.

        Returns flat list of embeddings matching *chunks* order.  Parent chunks
        are filled with zero vectors since they're never searched directly.
        """
        import time as _time
        from src.tasks.task_manager import check_cancelled
        dims = self._embedding.dimensions
        zero = [0.0] * dims
        # Collect (idx, embedding) pairs from all batches
        pairs: dict[int, list[float]] = {}
        for i, (f, batch, indices) in enumerate(self._futures):
            check_cancelled()
            try:
                embs = f.result()
            except Exception:
                logger.warning("[EmbedBatcher] batch %d failed, retrying (%d texts)", i, len(batch))
                for attempt in range(3):
                    _time.sleep(5)
                    try:
                        embs = self._embedding.embed_texts(batch)
                        logger.info("[EmbedBatcher] batch %d retry %d ok", i, attempt + 1)
                        break
                    except Exception:
                        logger.warning("[EmbedBatcher] batch %d retry %d/%d failed", i, attempt + 1, 3)
                else:
                    logger.error("[EmbedBatcher] batch %d permanently failed (%d texts), "
                                 "using zero vectors", i, len(batch))
                    embs = [zero] * len(batch)
            for j, emb in enumerate(embs):
                pairs[indices[j]] = emb
        # Build result matching chunk list order
        if chunks:
            return [pairs.get(c.metadata.get("_embed_idx", 0), zero) for c in chunks]
        # Fallback: sort by index
        return [emb for _idx, emb in sorted(pairs.items())]


def _get_enriching_llm(config: dict):
    """Get LLM for contextual enrichment.

    Resolution: per-collection override → global Settings default → system default LLM.
    """
    from src.providers.llm import create_llm_for_provider
    from src.config import get_config
    cfg = get_config()

    # 1. Per-collection override (collection-config.tsx)
    provider_id = config.get("enriching_llm_provider")
    if provider_id:
        for p in cfg.llm.providers:
            if p.id == provider_id:
                model = config.get("enriching_llm_model")
                return create_llm_for_provider(p, model=model)

    # 2. Global enrichment model (Settings → Advanced → Enrichment → Model)
    enrich_model = cfg.enrichment.enrichment_model
    if enrich_model:
        for p in cfg.llm.providers:
            if p.id == enrich_model:
                return create_llm_for_provider(p)

    # 3. System default LLM
    if cfg.llm.providers:
        default_p = next((p for p in cfg.llm.providers if p.is_default), cfg.llm.providers[0])
        return create_llm_for_provider(default_p)
    return services.llm


def _do_enrich(chunks, doc, config, collection_id: str = "", *,
                on_summary=None, on_chunk_ready=None):
    """Run contextual enrichment (blocking). Must be called with _enrich_lock held."""
    enriching_llm = _get_enriching_llm(config)
    ctx_window = config.get("contextual_window", 1)
    from src.rag.contextual import ContextualRetrieval
    contextual = ContextualRetrieval(llm=enriching_llm, context_window=ctx_window)
    kwargs = dict(on_summary=on_summary, on_chunk_ready=on_chunk_ready)
    if config.get("chunk_mode") == "parent_child":
        parent_chunks = [c for c in chunks if c.chunk_type == "parent"]
        child_chunks = [c for c in chunks if c.chunk_type == "child"]
        # Children first — generates summary in parallel with contexts
        child_chunks = contextual.add_context(child_chunks, full_document=doc.content,
                                               on_summary=on_summary,
                                               on_chunk_ready=on_chunk_ready)
        # Parents reuse children's summary if available, else generate their own
        shared_summary = (child_chunks[0].metadata.get("summary", "") if child_chunks else "")
        shared_structured = (child_chunks[0].metadata.get("_structured_summary", "") if child_chunks else "")
        parent_chunks = contextual.add_context(parent_chunks, full_document=doc.content,
                                                summary=shared_summary,
                                                structured_summary=shared_structured,
                                                on_chunk_ready=on_chunk_ready)
        enriched = parent_chunks + child_chunks
    else:
        enriched = contextual.add_context(chunks, full_document=doc.content, **kwargs)

    # Store structured summary if enrichment produced one
    _store_structured_summary(enriched, doc, config, collection_id)
    return enriched


def _store_structured_summary(enriched_chunks, doc, config, collection_id: str):
    """If enrichment produced a structured summary, store it via SummaryManager.

    Reads ``_structured_summary`` from chunk metadata (set by add_context),
    parses it, stores it with *include_in_summary=False*, then cleans up
    the temporary metadata field so it doesn't leak into Qdrant.
    """
    structured_raw = None
    for c in enriched_chunks:
        s = c.metadata.pop("_structured_summary", None)
        if s:
            structured_raw = s
            break

    if not structured_raw:
        logger.info("[ENRICH] No structured summary in chunks — LLM may have returned empty")
        return

    try:
        from src.rag.contextual import _parse_structured_summary
        from src.api.routes.info import _get_summary_manager

        parsed = _parse_structured_summary(structured_raw)
        data = parsed.get("data", [])
        facts = parsed.get("facts", [])
        insights = parsed.get("insights", [])

        if not data and not facts and not insights:
            logger.info("[ENRICH] Structured summary parsed but all categories empty, skipping store")
            return

        # Always use chunk metadata "source" — it's the sanitized filename.
        # doc.source (if it exists) may be a full path, which breaks
        # doc_summary_handler's file lookup.
        source = (
            enriched_chunks[0].metadata.get("source", "") if enriched_chunks else ""
        )

        sm = _get_summary_manager()
        sm.ensure_collection()
        sm.store_doc_summary(collection_id, source, data, facts, insights, include_in_summary=False)
        logger.info("[ENRICH] Stored structured summary col=%r src=%r (data=%d, facts=%d, insights=%d)",
                    collection_id, source, len(data), len(facts), len(insights))
    except Exception:
        logger.exception("[ENRICH] Failed to store structured summary")


def _build_enriched_text(chunk) -> str:
    """Build text for embedding/sparse encoding from chunk text + key metadata."""
    parts = []
    source = chunk.metadata.get("source", "")
    if source:
        # Use just the filename, not the full path
        filename = source.replace("\\", "/").rsplit("/", 1)[-1]
        parts.append(f"Source: {filename}")
    meeting_date = chunk.metadata.get("meeting_date", "")
    if meeting_date:
        parts.append(f"Meeting Date: {meeting_date}")
    summary = chunk.metadata.get("summary", "")
    if summary:
        parts.append(f"Document: {summary}")
    context = chunk.metadata.get("context", "")
    if context:
        parts.append(f"Context: {context}")
    parts.append(chunk.text)
    return "\n".join(parts)


def _do_sparse(texts: list[str], collection: str):
    """Run sparse encoding on assembled chunk texts. Returns sparse vectors or None."""
    if not texts:
        return None
    try:
        from src.rag.sparse_encoder import SparseEncoder
        encoder = SparseEncoder()
        encoder.load(services.db, collection)
        sparse_vectors = encoder.encode(texts)
        encoder.save(services.db, collection)
        return sparse_vectors
    except Exception:
        logger.warning("[Sparse] encoding failed for collection=%s", collection, exc_info=True)
        return None


def _bump_sparse_recalc_counter(collection: str, delta: int) -> None:
    """Increment the sparse recalc counter and trigger a rebuild if the threshold is crossed."""
    config = services.db.get_collection_config(collection)
    threshold = config.get("sparse_recalc_threshold", 5000)
    counter = config.get("sparse_recalc_counter", 0) + delta
    services.db.update_collection_config(collection, {"sparse_recalc_counter": counter})

    logger.info("[SparseRecalc] counter col=%s delta=%+d counter=%d threshold=%d",
                collection, delta, counter, threshold)

    if counter >= threshold:
        from src.tasks import task_manager as _tman
        _tman.create_task(
            filename=f"recalc:{collection}",
            task_type="sparse_recalc",
            collection=collection,
        )
        logger.info("[SparseRecalc] triggered for %s (counter=%d >= threshold=%d)",
                    collection, counter, threshold)


# ── Consolidation ──────────────────────────────────────────

from src.prompts import CONSOLIDATION_PROMPT  # noqa: E402

PROJECT_DESCRIPTION_PROMPT = """Based on the following document summaries from project "{project_name}", write a concise 2-sentence project description that captures what this project is about.

The description should:
- START with the project name: "{project_name}" followed by a dash or colon
- Sentence 1: What the project is (type, scope, scale)
- Sentence 2: What makes this project distinctive (key parties, location, or unique characteristics)

Output ONLY the 2-sentence description, nothing else.

Document summaries:
{summaries}"""


def format_doc_summaries_for_prompt(summaries: list[dict], alias_map: dict[str, str] | None = None) -> str:
    """Format doc summaries into text for the consolidation prompt.

    If alias_map is provided, source identifiers are rewritten to short
    human-readable aliases (e.g. FILE_A, NOTE_B) so the LLM does not invent
    UUIDs. The alias_map is the reverse lookup used later to translate
    LLM-returned aliases back to real sources.
    """
    if not summaries:
        return ""
    parts = []
    for s in summaries:
        real_source = s.get("source", "unknown")
        # If alias_map given, use the alias in the prompt; otherwise use real source.
        display_source = (alias_map or {}).get(real_source, real_source)
        lines = [f"--- {display_source} ---"]
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


def parse_consolidation_response(raw: str, alias_map: dict[str, str] | None = None) -> tuple[str, list[dict]]:
    """Parse LLM consolidation response into summary text and conflict dicts.

    Returns ``(collection_summary, conflicts)`` where each conflict is a dict
    with keys ``content1``, ``source1``, ``content2``, ``source2``.

    If alias_map is provided, source identifiers returned by the LLM are
    translated back to the real source strings via reverse lookup. Unknown
    aliases are passed through unchanged.

    Handles both JSON output (preferred) and legacy === delimiter format.
    """
    if not raw or not raw.strip():
        return "", []

    import json as _json
    import re as _re

    # Try JSON first
    def _resolve_alias(value: str) -> str:
        if not value or not alias_map:
            return value
        return alias_map.get(value, value)

    raw_stripped = raw.strip()
    # Extract JSON object from response (may have markdown fences or extra text)
    json_match = _re.search(r"\{[\s\S]*\}", raw_stripped)
    if json_match:
        try:
            data = _json.loads(json_match.group())
            summary_text = data.get("summary", "")
            conflicts = data.get("conflicts", [])
            if isinstance(conflicts, list) and summary_text:
                for c in conflicts:
                    for key in ("source1", "source2"):
                        if key in c:
                            c[key] = _resolve_alias(c[key])
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
                    "source1": _resolve_alias(parts[1]),
                    "content2": parts[2],
                    "source2": _resolve_alias(parts[3]),
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

    # 2. Resolve collection display name (not the internal ID)
    from src.rag.collection_utils import _resolve_collection_name
    collection_name = _resolve_collection_name(collection)

    # 2b. Build ephemeral alias map so the LLM doesn't invent UUIDs.
    #     Alias → real source. Forward (real → alias) used in prompt formatting;
    #     reverse (alias → real) used in response parsing.
    type_prefixes = ("__file__:", "__note__:", "__meeting__:", "__url__:", "__youtube__:")
    alias_map: dict[str, str] = {}  # alias → real source
    used_aliases: set[str] = set()
    for s in doc_summaries:
        real = s.get("source", "")
        if not real:
            continue
        if real in alias_map.values():
            continue  # already aliased
        # Derive a short type token from the source prefix
        if real.startswith("__file__:"):
            token = "FILE"
        elif real.startswith("__note__:"):
            token = "NOTE"
        elif real.startswith("__meeting__:"):
            token = "MEETING"
        elif real.startswith("__url__:"):
            token = "URL"
        elif real.startswith("__youtube__:"):
            token = "VIDEO"
        else:
            token = "SRC"
        # Find next available index (1-based, A/B/C suffix to keep aliases short)
        idx = 1
        while True:
            letter = chr(ord("A") + (idx - 1) % 26)
            suffix = "" if idx <= 26 else f"_{idx}"
            alias = f"{token}_{letter}{suffix}"
            if alias not in used_aliases:
                used_aliases.add(alias)
                alias_map[alias] = real
                break
            idx += 1

    # 3. Format and call LLM (generate first, delete old only on success)
    summaries_text = format_doc_summaries_for_prompt(doc_summaries, alias_map={v: k for k, v in alias_map.items()})
    logger.info("[CONSOLIDATE] Formatted summaries (%d chars, %d aliases), calling LLM...", len(summaries_text), len(alias_map))
    config = services.db.get_collection_config(collection)
    enriching_llm = _get_enriching_llm(config)
    loop = asyncio.get_running_loop()
    raw = await loop.run_in_executor(
        None, lambda: enriching_llm.generate(CONSOLIDATION_PROMPT.format(summaries=summaries_text), max_tokens=8192, thinking=True)
    )
    logger.info("[CONSOLIDATE] LLM returned %d chars", len(raw))
    collection_summary, conflicts = parse_consolidation_response(raw, alias_map=alias_map)
    logger.info("[CONSOLIDATE] Parsed: summary=%d chars, %d conflicts", len(collection_summary), len(conflicts))

    if not collection_summary:
        logger.error("[CONSOLIDATE] LLM returned empty collection_summary, aborting to preserve old data. Raw: %s", raw[:500])
        return {"message": "Consolidation failed: LLM returned empty summary", "conflicts_count": 0}

    # 4. Generate project description
    project_desc = ""
    try:
        logger.info("[CONSOLIDATE] Generating project description for '%s'...", collection_name)
        desc_raw = await loop.run_in_executor(
            None, lambda: enriching_llm.generate(
                PROJECT_DESCRIPTION_PROMPT.format(summaries=summaries_text, project_name=collection_name),
                max_tokens=512,
            )
        )
        project_desc = desc_raw.strip()
        logger.info("[CONSOLIDATE] Project description: %d chars", len(project_desc))
    except Exception as e:
        logger.error("[CONSOLIDATE] Project description generation failed: %s", e, exc_info=True)

    # 5. Delete old data and store new (atomic: all new content ready before deleting)
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

    # 6. Reset counter and clear debounce state
    services.db.update_collection_config(collection, {"summary_change_counter": 0})
    from src.api.routes.info import clear_debounce
    clear_debounce(collection)
    logger.info("[CONSOLIDATE] Counter reset & debounce cleared for collection='%s'", collection)
    logger.info("[CONSOLIDATE] Consolidation complete for collection='%s' (summary=%d chars, conflicts=%d, desc=%d chars)",
                collection, len(collection_summary), len(conflicts), len(project_desc))
    return {"message": "Consolidation done", "conflicts_count": len(conflicts)}


async def upload_handler(task: Task, file_path: str, collection: str, filename_param: str, meeting_id: str | None = None, source_label: str | None = None, file_id: str | None = None, meeting_date: str | None = None) -> dict[str, Any]:
    """处理文件上传任务 - 使用流水线队列控制并发"""
    from src.tasks.task_manager import set_current_task, clear_current_task, check_cancelled

    def update(progress: float, msg: str):
        task.progress = progress
        task.message = msg

    loop = asyncio.get_running_loop()

    try:
        set_current_task(task.id)
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

            # Load collection config first (needed for cloud_parsing flag)
            config = services.db.get_collection_config(collection)

            # Decide: cloud parsing (MinerU) or local parsing
            cloud_parsing = config.get("cloud_parsing", False)
            mineru_cfg = services.config.mineru if hasattr(services.config, "mineru") else None
            file_ext = path.suffix.lower()

            mineru_ready = cloud_parsing and mineru_cfg and mineru_cfg.enabled and mineru_cfg.api_token and file_ext in MINERU_SUPPORTED_EXTENSIONS
            logger.info("[%s] Parsing path: cloud_parsing=%s, mineru_enabled=%s, has_token=%s, ext=%s, supported=%s → %s",
                        filename_param, cloud_parsing,
                        mineru_cfg.enabled if mineru_cfg else "N/A",
                        bool(mineru_cfg and mineru_cfg.api_token),
                        file_ext, file_ext in MINERU_SUPPORTED_EXTENSIONS,
                        "MinerU" if mineru_ready else "local")

            if mineru_ready:
                update(20, "Parsing file via MinerU cloud...")
                try:
                    doc = parse_with_mineru(path, mineru_cfg)
                    logger.info("[%s] MinerU parse done in %.1fs, content length: %d",
                                filename_param, time.time() - t_start, len(doc.content or ""))
                except (MinerUError, Exception) as e:
                    logger.warning("[%s] MinerU failed (%s: %s), falling back to local parser", filename_param, type(e).__name__, e)
                    doc = parse_file(path)
            else:
                doc = parse_file(path)
                logger.info("[%s] Parse done in %.1fs, content length: %d",
                            filename_param, time.time() - t_start, len(doc.content or ""))

            if not doc.content or not doc.content.strip():
                raise ValueError(
                    f"No extractable text found in '{filename_param}'. "
                    "The file may be empty or the images could not be read by OCR."
                )

            file_dir = path.parent

            # ── Image processing: filter, save, describe, update content ──
            if doc.images and file_id:
                from src.parsers.image_utils import process_document_images
                from src.config import get_config

                # Resolve Vision LLM config
                cfg = get_config()
                vision_provider = None
                vision_model_id = cfg.visual_model_id if hasattr(cfg, "visual_model_id") else ""
                if vision_model_id:
                    for p in cfg.llm.providers:
                        if hasattr(p, "visual_model_ids") and vision_model_id in p.visual_model_ids:
                            vision_provider = p
                            break

                # Use the VISUAL_PROMPT from prompts.py
                from src.prompts import VISUAL_PROMPT

                doc = process_document_images(
                    doc, file_id, file_dir,
                    vision_provider=vision_provider,
                    vision_model_id=vision_model_id,
                    vision_prompt=VISUAL_PROMPT,
                )
                logger.info("[%s] Image processing done: %d images in doc",
                            filename_param, len(doc.images or []))

            # Save parsed text for preview (same text the chunker uses)
            try:
                parsed_path = file_dir / "parsed.txt"
                parsed_path.write_text(doc.content, encoding="utf-8")
            except Exception as e:
                logger.warning("[%s] Failed to save parsed text: %s", filename_param, e)

            update(40, "Chunking...")
            # Use MarkdownChunker when content has ::: blocks (images, distill, etc.)
            # so fenced blocks are treated as atomic units and not split across chunks.
            use_markdown_chunker = doc.file_type == "markdown" or bool(doc.images)

            if config.get("chunk_mode") == "parent_child":
                if use_markdown_chunker:
                    chunker = MarkdownParentChildChunker(
                        parent_strategy=config.get("parent_strategy", "heading"),
                        parent_chunk_size=config.get("parent_chunk_size", 1024),
                        parent_overlap=config.get("parent_chunk_overlap", 128),
                        parent_buffer_ratio=config.get("buffer_ratio", 0.5),
                        child_chunk_size=config.get("child_chunk_size", 128),
                        child_overlap=config.get("child_chunk_overlap", 32),
                        child_buffer_ratio=config.get("buffer_ratio", 0.5),
                    )
                else:
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
                if use_markdown_chunker:
                    chunker = MarkdownChunker(
                        max_tokens=config.get("chunk_size", 512),
                        buffer_ratio=config.get("buffer_ratio", 0.5),
                        chunk_overlap=config.get("chunk_overlap", 64),
                    )
                else:
                    chunker = ParagraphChunker(
                        max_tokens=config.get("chunk_size", 512),
                        buffer_ratio=config.get("buffer_ratio", 0.5),
                        chunk_overlap=config.get("chunk_overlap", 64),
                    )

            t_chunk = time.time()
            extra_meta: dict = {"file_type": doc.file_type, "ingested_at": time.time()}
            if doc.position_map:
                extra_meta["position_map"] = doc.position_map
            if meeting_id:
                extra_meta["meeting_id"] = meeting_id
            if meeting_date:
                extra_meta["meeting_date"] = meeting_date
            if file_id:
                extra_meta["file_id"] = file_id
            # Human-readable label for search results display
            extra_meta["source_label"] = source_label if source_label else filename_param
            chunks = chunker.chunk_with_metadata(
                doc.content, source=filename_param, extra_metadata=extra_meta
            )
            logger.info("[%s] Chunking done in %.1fs, %d chunks",
                        filename_param, time.time() - t_chunk, len(chunks))

            # Annotate chunk metadata with image references
            if doc.images:
                from src.parsers.image_utils import annotate_chunks_with_images
                annotate_chunks_with_images(chunks, doc.images)
                logger.info("[%s] Annotated chunks with %d image references",
                            filename_param, len(doc.images))

            if not chunks:
                raise ValueError(
                    f"Chunking produced no results for '{filename_param}'. "
                    "The content may be too short or not match the chunking strategy."
                )

            return doc, chunks, config

        # Use separate CPU thread pool for parsing/chunking
        doc, chunks, config = await loop.run_in_executor(_cpu_executor, _parse_and_chunk)

        # ── Stage 2+3: Enriching + Embedding (pipelined) ──
        t_ctx = time.time()
        contextual_enabled = config.get("contextual_enabled", True)
        if contextual_enabled:
            embedding = get_collection_embedding(config, collection)
            # Only count non-parent chunks for progress (parents skip embed)
            embed_count = len([c for c in chunks if c.chunk_type != "parent"])
            batcher = _EmbedBatcher(embedding, total_chunks=embed_count,
                                    on_progress=lambda done, total:
                                        update(50 + int(30 * done / total),
                                               f"Enrich+Embed {done}/{total} chunks"))

            # Pre-number chunks so embeddings map back to list position
            for i, c in enumerate(chunks):
                c.metadata["_embed_idx"] = i

            def _enrich_and_embed():
                _enrich_lock.acquire()
                try:
                    update(50, f"Enriching with context ({len(chunks)} chunks)...")
                    return _do_enrich(chunks, doc, config, collection,
                                      on_summary=batcher.set_summary,
                                      on_chunk_ready=batcher.on_ready)
                finally:
                    _enrich_lock.release()

            chunks = await loop.run_in_executor(_cpu_executor, _enrich_and_embed)

            # Flush remaining embed batches; start sparse in parallel
            batcher.flush()
            if batcher._futures:
                sparse_future = _cpu_executor.submit(_do_sparse, batcher.get_all_texts(), collection)
                embeddings = batcher.wait_all(chunks)
                sparse_vectors = sparse_future.result()
            else:
                # Enrichment skipped (e.g. >200 chunks) — embed all at once
                logger.info("[%s] enrichment skipped, embedding all %d chunks inline",
                            filename_param, len(chunks))
                texts = [_build_enriched_text(c) for c in chunks]
                embeddings = embedding.embed_texts(texts)
                sparse_vectors = _do_sparse(texts, collection)

        else:
            # No enrichment — embed + sparse inline
            embedding = get_collection_embedding(config, collection)
            texts = [_build_enriched_text(c) for c in chunks]
            embeddings = embedding.embed_texts(texts)
            sparse_vectors = _do_sparse(texts, collection)

        t_emb = time.time()
        logger.info("[%s] Enrich+Embed done in %.1fs (%d chunks)",
                    filename_param, t_emb - t_ctx, len(chunks))

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
            services.db.upsert_points(
                collection=collection, ids=ids, vectors=embeddings,
                payloads=payloads,
            )
            # Add sparse vectors separately — does not touch dense vectors
            if sparse_vectors:
                services.db.upsert_sparse_vectors(
                    collection=collection, ids=ids, sparse_vectors=sparse_vectors,
                )
            # Track chunk changes for sparse vocab drift detection
            non_parent_count = len([c for c in chunks if c.chunk_type != "parent"])
            _bump_sparse_recalc_counter(collection, non_parent_count)
            logger.info("[%s] Store done in %.1fs. Total: %.1fs",
                        filename_param, time.time() - t_store, time.time() - t_start)

        # Use default thread pool for storage (I/O bound)
        await loop.run_in_executor(None, _do_store)

        update(100, f"Indexed {len(chunks)} chunks")

        # ── Catalog coverage refresh ──────────────────────────────────
        try:
            if services.catalog:
                remaining = len(task_manager.get_active_tasks(
                    collection=collection, task_types=["upload", "doc_summary"],
                )) - 1  # exclude self
                if remaining <= 0:
                    logger.info("[Coverage] TRIGGER by %r (last task for %s)", filename_param, collection)
                    services.catalog.update_coverage(collection)
                else:
                    logger.info("[Coverage] SKIP by %r (%d other upload task(s) remain for %s)",
                                filename_param, remaining, collection)
        except Exception:
            logger.exception("[Coverage] trigger failed for %r", filename_param)

        # Update file index
        if file_id:
            try:
                from src.collections.file_index import add as add_file_index
                # Preserve original extension for PDF/office files
                original_ext = Path(file_path).suffix.lower().lstrip(".")
                add_file_index(collection, file_id, filename_param,
                              source_label or filename_param,
                              doc.file_type, len(chunks),
                              original_ext if original_ext else None)
            except Exception:
                logger.warning("[%s] Failed to update files.json", filename_param, exc_info=True)

        clear_current_task()
        return {"message": "Done", "filename": filename_param, "chunks_count": len(chunks), "collection": collection}

    except Exception as e:
        clear_current_task()
        raise Exception(f"Failed to process {filename_param}: {e}")


# ---------------------------------------------------------------------------
# Meeting Summary handler
# ---------------------------------------------------------------------------

async def meeting_summary_handler(task: Task, meeting_id: str, **kwargs) -> dict:
    """Generate meeting blueprint summary (Node 0.3)."""
    from src.meeting import store
    from src.meeting.models import ProcessingState
    from src.meeting.service import MeetingService
    logger.info("[MEETING_SUMMARY] Starting for meeting %s", meeting_id)

    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")

    store.update_meeting(
        meeting_id,
        processing_state=ProcessingState.summarizing.value,
    )
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _do_meeting_summary, meeting_id)
        return {"message": "Summary generated", "meeting_id": meeting_id}
    except Exception:
        store.update_meeting(
            meeting_id,
            processing_state=ProcessingState.idle.value,
        )
        raise


def _do_meeting_summary(meeting_id: str):
    from src.meeting.service import MeetingService

    svc = MeetingService()
    svc._do_blueprint_summary(meeting_id)


async def meeting_extract_handler(task: Task, meeting_id: str, receipts: list, **kwargs) -> dict:
    """Extract meeting sections via v3 pipeline (ThreadPoolExecutor 50)."""
    from src.meeting import store
    from src.meeting.models import ProcessingState
    from src.meeting.service import MeetingService
    logger.info("[MEETING_EXTRACT] Starting for meeting %s (%d receipts)", meeting_id, len(receipts))

    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")

    store.update_meeting(
        meeting_id,
        processing_state=ProcessingState.extracting.value,
    )
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _do_meeting_extract, meeting_id, receipts)
        return {"message": "Extract complete", "meeting_id": meeting_id}
    except Exception:
        store.update_meeting(
            meeting_id,
            processing_state=ProcessingState.idle.value,
        )
        raise


def _do_meeting_extract(meeting_id: str, receipts: list):
    from src.meeting.service import MeetingService
    svc = MeetingService()
    svc.extract_sections(meeting_id, receipts)


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

    # Resolve file path via file index
    from src.collections.file_index import load as load_file_index
    from src.collections.file_index import COLLECTIONS_DIR as _COL_DIR

    file_path = None
    idx = load_file_index(collection)
    for fid, entry in idx.items():
        if entry.get("source") == source:
            fd = _COL_DIR / collection / "files" / fid
            if (fd / "parsed.txt").is_file():
                file_path = fd / "parsed.txt"
            else:
                for f in sorted(fd.iterdir()):
                    if f.is_file() and f.name != "parsed.txt":
                        file_path = f
                        break
            logger.info("[DOC_SUMMARY] Resolved source=%s -> file_id=%s path=%s", source, fid, file_path)
            break

    if not file_path:
        raise FileNotFoundError(f"Source file '{source}' not found in files index for collection '{collection}'")

    loop = asyncio.get_running_loop()
    # If using parsed text, read it directly instead of re-parsing
    if file_path.name == "parsed.txt":
        doc_content = await loop.run_in_executor(None, file_path.read_text, "utf-8")
        doc_content = doc_content.strip()
    else:
        doc = await loop.run_in_executor(None, parse_file, file_path)
        doc_content = (doc.content or "").strip()
    if not doc_content:
        raise ValueError("File has no extractable text content")

    config = services.db.get_collection_config(collection)
    enriching_llm = _get_enriching_llm(config)
    doc_summary = await loop.run_in_executor(
        None, lambda: generate_structured_summary(enriching_llm, doc_content)
    )
    logger.info("[DOC_SUMMARY] Generated: data=%d, facts=%d, insights=%d",
                len(doc_summary.get("data", [])), len(doc_summary.get("facts", [])), len(doc_summary.get("insights", [])))

    # Take snapshot before storing (for debounce net-change detection)
    from src.api.routes.info import _snapshot_includes, schedule_debounced_consolidate
    pre_snapshot = _snapshot_includes(collection)

    sm = _get_summary_manager()
    sm.ensure_collection()
    sm.store_doc_summary(
        collection, source,
        doc_summary.get("data", []),
        doc_summary.get("facts", []),
        doc_summary.get("insights", []),
        include_in_summary=True,
    )

    # Schedule debounced consolidation (replaces old counter-based trigger)
    schedule_debounced_consolidate(collection, pre_snapshot)

    # ── Catalog coverage refresh ──────────────────────────────────
    try:
        if services.catalog:
            from src.tasks.task_manager import task_manager as _tman
            remaining = len(_tman.get_active_tasks(
                collection=collection, task_types=["upload", "doc_summary"],
            )) - 1  # exclude self
            if remaining <= 0:
                logger.info("[Coverage] TRIGGER by doc_summary %r (last task for %s)", source, collection)
                services.catalog.update_coverage(collection)
            else:
                logger.info("[Coverage] DEFER doc_summary %r (%d remaining tasks, marking dirty)", source, remaining)
                services.catalog.mark_dirty(collection)
    except Exception:
        logger.warning("[Coverage] refresh failed for %s", collection, exc_info=True)

    return {"message": "Summary generated", "source": source}


# ── Sparse Recalc ──────────────────────────────────────────


async def sparse_recalc_handler(task, collection: str) -> dict:
    """Rebuild sparse vocabulary and vectors from scratch for a collection."""
    del task  # unused
    from src.rag.sparse_recalc import run_sparse_recalc

    logger.info("[SparseRecalc] Starting recalc for collection=%s", collection)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, run_sparse_recalc, services.db, collection)
    if result is None:
        raise RuntimeError(f"Sparse recalc failed for collection={collection}")
    logger.info("[SparseRecalc] Completed: %s", result)
    return result
