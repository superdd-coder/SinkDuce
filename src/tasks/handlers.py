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
from src.rag.chunker import ParentChildChunker, ParagraphChunker, chunk_with_sheet_boundaries
from src.rag.markdown_chunker import MarkdownChunker, MarkdownParentChildChunker
from src.rag.collection_utils import get_collection_embedding
from src.rag.summary_manager import SummaryManager

logger = logging.getLogger(__name__)

# MinerU/local parse is slow and would starve chunk if they share a pool.
# Parse matches upload concurrency (5); chunk/sparse stay on a small CPU pool.
# Summary + Context orchestration uses a dedicated pool; ingest LLM calls
# share a process-wide cap (Settings Parallel, default 50).
_embed_executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix="embed-worker")
_parse_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="parse-worker")
_cpu_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cpu-worker")
_enrich_executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="enrich-worker")
# Separate pools so one file's OCR never blocks that file's Vision.
# OCR is CPU-heavy (RapidOCR ONNX): 3 file workers match 3 engines.
# Vision is I/O-bound: files enter like Summary (enrich pool size) and
# individual image calls compete for ingest_request_limiter.
_ocr_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ocr-worker")
_vision_executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="vision-worker")
# Do not wait forever for OCR/Vision before embed/store.
IMAGE_JOB_DEADLINE_SEC = 180.0


class _MonotonicProgress:
    """Task progress that never moves backward (avoids 70% → 32% jumps)."""

    def __init__(self, task):
        self._task = task
        self._lock = threading.Lock()
        self._value = float(getattr(task, "progress", 0) or 0)

    def set(self, pct: float, msg: str) -> None:
        with self._lock:
            self._value = max(self._value, min(100.0, float(pct)))
            self._task.progress = self._value
            self._task.message = msg


class _WorkProgress:
    """Linear progress over discrete **completed** work units within [lo, hi].

    Completions that arrive before ``add_work`` are banked.
    Does **not** shrink the bar when total grows mid-stage — new units only
    affect remaining headroom from the current percent (via parent monotonic).
    Prefer one ``_WorkProgress`` per stage with a fixed total when possible.
    """

    def __init__(self, progress: _MonotonicProgress, lo: float = 30.0, hi: float = 90.0):
        self._progress = progress
        self._lo = lo
        self._hi = hi
        self._lock = threading.Lock()
        self._total = 0
        self._done = 0
        self._pending_done = 0  # completions before total was registered

    def add_work(self, n: int) -> None:
        if n <= 0:
            return
        with self._lock:
            self._total += n
            if self._pending_done:
                self._done += self._pending_done
                self._pending_done = 0
            if self._done > self._total:
                self._done = self._total
            self._emit()

    def set_total(self, n: int) -> None:
        """Set absolute unit total for this stage (preferred over incremental add)."""
        if n <= 0:
            return
        with self._lock:
            self._total = max(self._total, n)
            if self._pending_done:
                self._done += self._pending_done
                self._pending_done = 0
            if self._done > self._total:
                self._done = self._total
            self._emit()

    def done(self, n: int = 1, msg: str | None = None) -> None:
        if n <= 0:
            return
        with self._lock:
            if self._total <= 0:
                self._pending_done += n
                return
            self._done = min(self._done + n, self._total)
            self._emit(msg)

    def snapshot(self) -> tuple[int, int]:
        with self._lock:
            return self._done, max(self._total, 1)

    def finish(self, msg: str | None = None) -> None:
        with self._lock:
            if self._total > 0:
                self._done = self._total
            if self._total <= 0:
                label = msg or "Done"
            else:
                label = msg or f"Processing {self._done}/{self._total}…"
            self._progress.set(self._hi, label)

    def _emit(self, msg: str | None = None) -> None:
        if self._total <= 0:
            pct = self._lo
            label = msg or "Processing…"
        else:
            frac = min(1.0, self._done / self._total)
            pct = self._lo + (self._hi - self._lo) * frac
            label = msg or f"Processing {self._done}/{self._total}…"
        self._progress.set(min(pct, self._hi), label)


class _EmbedBatcher:
    """Collects ready chunks, assembles enriched text, and submits batches to the
    shared embedding executor as soon as 10 chunks accumulate.  Chunks that arrive
    before the summary is available are queued and flushed once ``set_summary()``
    is called.

    Progress (``on_embed_done``) is reported when a batch **finishes** embedding,
    not when it is submitted.
    """

    def __init__(self, embedding, *, total_chunks: int = 0, on_embed_done=None):
        self._embedding = embedding
        self._lock = threading.Lock()
        self._buffer: list = []                  # (idx, text)
        self._all_texts: list[str] = []
        self._futures: list[tuple] = []          # (future, batch_texts, indices) for retry
        self._summary: str | None = None
        self._pending: list[tuple] = []          # (chunk, context) waiting for summary
        self._assembled = 0
        self._total = total_chunks
        self._on_embed_done = on_embed_done      # called with n=batch_size after result

    def set_summary(self, summary: str):
        """Kept for callers; summary is stored separately and is not embedded."""
        with self._lock:
            self._summary = summary

    def on_ready(self, chunk, context: str):
        # Parent chunks are never searched — skip embedding
        if chunk.chunk_type == "parent":
            return
        with self._lock:
            self._assemble_and_buffer(chunk, context)

    def _assemble_and_buffer(self, chunk, context: str):
        idx = chunk.metadata.get("_embed_idx", 0)
        text = _build_enriched_text(chunk)
        self._buffer.append((idx, text))
        self._all_texts.append(text)
        self._assembled += 1
        # Progress is deferred until embed completes (see wait_all)
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
        Reports ``on_embed_done`` after each batch **completes** (success or zero-fill).
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
            # Count completed embeds only after the batch has finished
            if self._on_embed_done:
                self._on_embed_done(len(batch))
        # Build result matching chunk list order
        if chunks:
            return [pairs.get(c.metadata.get("_embed_idx", 0), zero) for c in chunks]
        # Fallback: sort by index
        return [emb for _idx, emb in sorted(pairs.items())]


def _get_enriching_llm(config: dict):
    """Get LLM for contextual enrichment / Summary.

    Resolution: per-collection override → Settings "Contextual enrichment &
    Summary" → system default LLM.
    """
    from src.rag.contextual import get_enriching_llm

    return get_enriching_llm(config)


def _is_tabular_document(doc) -> bool:
    """Excel / CSV skip section LLM and use sheet_name in the embedding instead."""
    file_type = getattr(doc, "file_type", "") or ""
    if file_type in ("excel", "csv"):
        return True
    meta = getattr(doc, "metadata", None) or {}
    if not isinstance(meta, dict):
        return False
    original = str(meta.get("original_file_type") or "").lower()
    return original in ("xls", "xlsx", "csv")


def _do_enrich(chunks, doc, config, collection_id: str = "", *,
                on_summary=None, on_chunk_ready=None,
                contextual_enabled: bool | None = None,
                full_document: str | None = None,
                inspect_out: dict | None = None,
                summary: str | None = None,
                structured_summary: str | None = None,
                cache_warmup_delay: float | None = None):
    """Run Summary (always) and optional block-level Context.

    Safe to call concurrently across files. Summary + Context LLM calls
    share a process-wide cap (``enrichment.max_parallel_context``, default 50).
    Collection Contextual switch only gates Context — Summary still runs
    when the switch is off.
    """
    from src.rag.contextual import ContextualRetrieval, enrichment_model_is_visual

    enriching_llm = _get_enriching_llm(config)
    ctx_window = config.get("contextual_window", 1)
    if contextual_enabled is None:
        contextual_enabled = bool(config.get("contextual_enabled", True))
    if enriching_llm is None:
        logger.warning("[ENRICH] No LLM configured — skipping Summary/Context")
        if on_chunk_ready:
            for chunk in chunks:
                try:
                    on_chunk_ready(chunk, chunk.metadata.get("context", ""))
                except Exception:
                    pass
        return chunks
    kwargs = {}
    if cache_warmup_delay is not None:
        kwargs["cache_warmup_delay"] = cache_warmup_delay
    contextual = ContextualRetrieval(llm=enriching_llm, context_window=ctx_window, **kwargs)
    file_id = ""
    if chunks:
        file_id = str(chunks[0].metadata.get("file_id") or "")

    def _on_sum(short: str) -> None:
        _store_structured_summary(chunks, doc, config, collection_id)
        if on_summary:
            try:
                on_summary(short)
            except Exception:
                logger.debug("[ENRICH] on_summary failed")

    document_text = full_document
    if document_text is None:
        document_text = getattr(doc, "content", "") or ""
    enriched = contextual.add_context(
        chunks,
        full_document=document_text,
        summary=summary,
        structured_summary=structured_summary,
        on_summary=_on_sum,
        on_chunk_ready=on_chunk_ready,
        tabular=_is_tabular_document(doc),
        contextual_enabled=contextual_enabled,
        is_visual=enrichment_model_is_visual(config),
        collection_id=collection_id,
        file_id=file_id,
    )
    if inspect_out is not None:
        inspect_out.update(getattr(contextual, "inspect", {}) or {})
    return enriched


def _record_enrich_trace(trace, inspect: dict) -> None:
    """Write Summary / Context steps from ContextualRetrieval.inspect."""
    if trace is None:
        return
    inspect = inspect or {}
    attempts = int(inspect.get("summary_attempts") or 0)
    summary_ok = bool(inspect.get("summary_ok"))
    short = inspect.get("short_summary") or ""
    structured = inspect.get("structured_summary") or ""
    summary_ms = inspect.get("summary_ms")
    summary_ms_val = int(summary_ms) if summary_ms is not None else 0
    summary_detail = (
        f"{'ok' if summary_ok else 'failed'} after {attempts or 1} attempt(s)"
        + (f" — {short[:160]}" if short else "")
    )
    summary_data = {
        "attempts": attempts,
        "ok": summary_ok,
        "short_summary": short,
        "structured_summary": structured,
    }
    patched = trace.update(
        "summary",
        status="ok" if summary_ok else "error",
        title="Summary",
        detail=summary_detail,
        data=summary_data,
        ms=summary_ms_val,
    )
    if not patched:
        patched = trace.update(
            "summary_start",
            id="summary",
            title="Summary",
            status="ok" if summary_ok else "error",
            detail=summary_detail,
            data=summary_data,
            ms=summary_ms_val,
        )
    if not patched:
        trace.add(
            "summary",
            "Summary",
            status="ok" if summary_ok else "error",
            detail=summary_detail,
            data=summary_data,
            ms=summary_ms,
        )
    wait_s = inspect.get("cache_wait_s") or 0
    sum_end = trace.ended_epoch("summary") or time.time()
    wait_started = inspect.get("cache_wait_started")
    wait_ended = inspect.get("cache_wait_ended")
    ctx_started = inspect.get("context_started")
    if wait_s:
        wait_ms = int(float(wait_s) * 1000)
        t0 = float(wait_started) if wait_started else sum_end
        t1 = float(wait_ended) if wait_ended else t0 + float(wait_s)
        trace.add(
            "cache_wait",
            "Prefix-cache wait",
            detail=f"Waited {wait_s}s after successful Summary",
            data={"seconds": wait_s},
            ms=wait_ms,
            started_at=t0,
            ended_at=t1,
        )
        ctx_start = t1
    elif inspect.get("context_ran"):
        trace.add(
            "cache_wait",
            "Prefix-cache wait",
            status="skip",
            detail=(
                "No extra wait — Summary already covered the 3s prefix-cache window"
                if summary_ok
                else "Summary failed — Context started immediately"
            ),
            started_at=sum_end,
            ended_at=sum_end,
        )
        ctx_start = sum_end
    else:
        ctx_start = sum_end
    if ctx_started:
        ctx_start = float(ctx_started)
    if inspect.get("context_ran"):
        written = inspect.get("context_written") or 0
        batches = inspect.get("context_batches") or 0
        skipped = inspect.get("context_skipped_image_only") or 0
        contexts = inspect.get("contexts") or []
        context_ms = int(inspect.get("context_ms") or 0)
        trace.add(
            "context",
            "Situating context",
            detail=f"{written} written, {batches} batch(es), {skipped} image-only skipped",
            data={
                "written": written,
                "batches": batches,
                "skipped_image_only": skipped,
                "contexts": contexts,
            },
            ms=context_ms,
            started_at=ctx_start,
            ended_at=ctx_start + context_ms / 1000.0,
        )
    else:
        reason = inspect.get("context_skip_reason") or "skipped"
        labels = {
            "tabular": "Excel/CSV — Sheet: used instead of Context",
            "off": "Collection Contextual switch off — Summary still ran",
            "no_chunks": "No searchable chunks",
            "image_only": "Only empty image chunks (non-visual model)",
        }
        trace.add(
            "context",
            "Situating context",
            status="skip",
            detail=labels.get(reason, reason),
            data={"reason": reason},
            started_at=ctx_start,
            ended_at=ctx_start,
        )


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
        version_id = None
        file_id = None
        is_def = False
        if (source or "").startswith("__file__:"):
            file_id = source[len("__file__:") :]
            try:
                from src.api.routes.info import (
                    current_version_id_for_source,
                    source_is_definitive,
                )

                version_id = current_version_id_for_source(collection_id, source)
                is_def = source_is_definitive(collection_id, source)
            except Exception:
                version_id = None
                is_def = False
        sm.store_doc_summary(
            collection_id,
            source,
            data,
            facts,
            insights,
            include_in_summary=is_def,
            version_id=version_id,
            file_id=file_id,
        )
        logger.info(
            "[ENRICH] Stored structured summary col=%r src=%r version_id=%r "
            "definitive=%s (data=%d, facts=%d, insights=%d)",
            collection_id,
            source,
            version_id,
            is_def,
            len(data),
            len(facts),
            len(insights),
        )
        # Version update / re-ingest of a definitive file must rebuild
        # Collection Summary even though membership did not change.
        if is_def:
            try:
                from src.api.routes.info import (
                    _snapshot_includes,
                    schedule_debounced_consolidate,
                )

                schedule_debounced_consolidate(
                    collection_id,
                    _snapshot_includes(collection_id),
                    force_content_change=True,
                )
                logger.info(
                    "[ENRICH] definitive source=%s — scheduled consolidate "
                    "(force_content_change)",
                    source,
                )
            except Exception:
                logger.warning(
                    "[ENRICH] Failed to schedule consolidate for definitive %s",
                    source,
                    exc_info=True,
                )
    except Exception:
        logger.exception("[ENRICH] Failed to store structured summary")


def _after_ingest_definitive_followup(
    collection_id: str,
    file_id: str,
    *,
    version_id: str | None = None,
) -> None:
    """After upload/version-ingest of a definitive file, ensure Collection Summary refreshes.

    - If a doc_summary already exists for the current version (e.g. from enrich),
      schedule debounced consolidate with ``force_content_change`` so membership-
      only debounce still rebuilds.
    - If no current-version summary exists (contextual off / enrich skipped),
      queue ``doc_summary`` which will then schedule consolidate when definitive.
    """
    if not file_id or not collection_id:
        return
    source = f"__file__:{file_id}"
    try:
        from src.api.routes.info import (
            _get_summary_manager,
            _snapshot_includes,
            schedule_debounced_consolidate,
            source_is_definitive,
        )
    except Exception:
        logger.warning(
            "[INGEST] definitive follow-up imports failed for %s/%s",
            collection_id,
            file_id,
            exc_info=True,
        )
        return

    if not source_is_definitive(collection_id, source):
        return

    sm = _get_summary_manager()
    existing = None
    try:
        if version_id:
            existing = sm.get_doc_summary(collection_id, source, version_id=version_id)
        if existing is None:
            existing = sm.get_doc_summary(collection_id, source)
            # Stale summary from an older version — regenerate for current content
            if (
                existing
                and version_id
                and (existing.get("version_id") or "").strip()
                and existing.get("version_id") != version_id
            ):
                existing = None
    except Exception:
        logger.warning(
            "[INGEST] get_doc_summary failed for %s", source, exc_info=True
        )
        existing = None

    if existing is not None:
        try:
            schedule_debounced_consolidate(
                collection_id,
                _snapshot_includes(collection_id),
                force_content_change=True,
            )
            logger.info(
                "[INGEST] definitive %s has summary — scheduled consolidate",
                source,
            )
        except Exception:
            logger.warning(
                "[INGEST] schedule consolidate failed for %s",
                source,
                exc_info=True,
            )
        return

    try:
        task_manager.create_task(
            filename=f"doc_summary:{collection_id}:{source}",
            task_type="doc_summary",
            collection=collection_id,
            source=source,
        )
        logger.info(
            "[INGEST] definitive %s — queued doc_summary (will consolidate)",
            source,
        )
    except Exception:
        logger.warning(
            "[INGEST] Failed to queue doc_summary for %s",
            source,
            exc_info=True,
        )


_IDENTITY_SOURCE_PREFIXES = (
    "__file__:",
    "__note__:",
    "__meeting__:",
    "__url__:",
    "__youtube__:",
)


def _embed_source_name(meta: dict) -> str:
    """Human filename for the Source: embedding prefix — never the identity key."""
    for key in ("source_label", "note_title"):
        label = str(meta.get(key) or "").strip()
        if label:
            return label.replace("\\", "/").rsplit("/", 1)[-1]
    source = str(meta.get("source") or "").strip()
    if not source:
        return ""
    if source.startswith(_IDENTITY_SOURCE_PREFIXES):
        return ""
    return source.replace("\\", "/").rsplit("/", 1)[-1]


def _build_enriched_text(chunk) -> str:
    """Build text for embedding/sparse encoding from chunk text + key metadata."""
    parts = []
    filename = _embed_source_name(chunk.metadata)
    if filename:
        parts.append(f"Source: {filename}")
    sheet = chunk.metadata.get("sheet_name", "")
    if sheet:
        parts.append(f"Sheet: {sheet}")
    meeting_date = chunk.metadata.get("meeting_date", "")
    if meeting_date:
        parts.append(f"Meeting Date: {meeting_date}")
    # short_summary is stored on the chunk for INFO / consolidate, but is
    # not prepended here — a shared document line homogenizes chunk vectors.
    context = chunk.metadata.get("context", "")
    if context:
        parts.append(f"Context: {context}")
    from src.rag.chunker import (
        _strip_image_fences,
        fence_lexical_text,
        strip_table_source_fences,
    )

    # Table-source fences never enter the vector. Independent figures contribute
    # only their description/OCR, not the :::image markup.
    without_table_src = strip_table_source_fences(chunk.text)
    prose = _strip_image_fences(without_table_src).strip()
    lexical = fence_lexical_text(chunk.text)
    extra: list[str] = []
    for ref in (chunk.metadata or {}).get("images") or []:
        if not isinstance(ref, dict):
            continue
        for key in ("ocr_text", "description"):
            val = str(ref.get(key) or "").strip()
            if val and val not in lexical and val not in extra:
                extra.append(val)
    if extra:
        lexical = "\n".join(p for p in (lexical, *extra) if p)
    body = "\n".join(p for p in (lexical, prose) if p)
    if body.strip():
        parts.append(body)
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
    """Consolidate definitive document summaries into a collection summary."""
    logger.info("[CONSOLIDATE] Starting consolidation for collection='%s'", collection)
    summary_mgr = SummaryManager(db=services.db)
    summary_mgr.ensure_collection()
    logger.info("[CONSOLIDATE] __summaries__ collection ensured")

    # 1. Doc summaries for definitive files only (files.is_definitive),
    #    one row per source at *current* version_id.
    from src.api.routes.info import (
        pick_current_doc_summaries,
        source_is_definitive,
    )

    all_summaries = summary_mgr.get_doc_summaries(collection, included_only=False)
    current_only = pick_current_doc_summaries(collection, all_summaries)
    doc_summaries = [
        s
        for s in current_only
        if s.get("source") and source_is_definitive(collection, s["source"])
    ]
    logger.info(
        "[CONSOLIDATE] Found %d definitive current doc_summaries "
        "(of %d points / %d sources) for collection='%s'",
        len(doc_summaries),
        len(all_summaries),
        len(current_only),
        collection,
    )
    if not doc_summaries:
        # Re-check files table: zero *usable* summaries is not enough —
        # a definitive file may still be waiting on doc_summary generation.
        # Only clear stale consolidate results when no definitive files remain.
        definitive_file_count = 0
        try:
            from src.file_mgmt.store import get_db as _fm_get_db

            _conn = _fm_get_db(collection)
            try:
                row = _conn.execute(
                    "SELECT COUNT(*) AS c FROM files WHERE is_definitive=1"
                ).fetchone()
                definitive_file_count = int(row["c"] if row else 0)
            finally:
                _conn.close()
        except Exception:
            logger.warning(
                "[CONSOLIDATE] Could not count definitive files for %s",
                collection,
                exc_info=True,
            )

        if definitive_file_count > 0:
            logger.info(
                "[CONSOLIDATE] No doc_summaries yet but %d definitive file(s) "
                "remain for '%s' — skip clear (wait for summaries)",
                definitive_file_count,
                collection,
            )
            return {
                "message": "No summaries yet for definitive files — consolidate deferred",
            }

        # Truly no definitive files after debounce window — drop stale results.
        logger.info(
            "[CONSOLIDATE] No definitive files for collection='%s' — "
            "clearing previous consolidate results",
            collection,
        )
        summary_mgr.delete_collection_summary(collection)
        summary_mgr.delete_project_description(collection)
        summary_mgr.delete_conflicts(collection)
        try:
            services.db.update_collection_config(collection, {"summary_change_counter": 0})
        except Exception:
            logger.warning(
                "[CONSOLIDATE] Failed to reset summary_change_counter for %s",
                collection,
                exc_info=True,
            )
        try:
            from src.api.routes.info import clear_debounce

            clear_debounce(collection)
        except Exception:
            pass
        return {"message": "No documents to consolidate — previous results cleared"}

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
        None, lambda: enriching_llm.generate(CONSOLIDATION_PROMPT.format(summaries=summaries_text), max_tokens=8192, thinking=False)
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
                thinking=False,
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


async def upload_handler(task: Task, file_path: str, collection: str, filename_param: str, meeting_id: str | None = None, source_label: str | None = None, file_id: str | None = None, meeting_date: str | None = None, version_id: str | None = None) -> dict[str, Any]:
    """处理文件上传任务 - 使用流水线队列控制并发"""
    from src.tasks.task_manager import set_current_task, clear_current_task, check_cancelled

    # Progress is monotonic and stage-banded by collection pipeline:
    #   setup/parse → images (optional) → chunk → enrich (optional) → embed → store
    # Ranges shrink/expand so skipped stages do not leave a dead zone or rewind %.
    prog = _MonotonicProgress(task)

    def update(progress: float, msg: str):
        prog.set(progress, msg)

    loop = asyncio.get_running_loop()
    ingest_trace = None

    try:
        set_current_task(task.id)
        t_start = time.time()
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")

        from src.rag.ingest_trace import IngestTrace

        ingest_trace = IngestTrace(
            path.parent,
            {
                "file_id": file_id or "",
                "version_id": version_id or "",
                "filename": filename_param,
                "collection": collection,
            },
        )

        # Stage band ends (filled after config / parse known)
        # Defaults assume local parse, no images, enrich on — adjusted below.
        parse_hi = 22.0
        images_hi = 22.0  # = parse_hi if no images
        chunk_hi = 28.0
        mid_lo = 28.0
        mid_hi = 90.0

        update(5, "Checking collection...")

        def _parse_and_prepare():
            nonlocal parse_hi, images_hi, chunk_hi, mid_lo

            if not services.db.collection_exists(collection):
                services.db.create_collection(collection, vector_size=services.embedding.dimensions)

            # Load collection config first (needed for cloud_parsing / contextual)
            config = services.db.get_collection_config(collection)
            contextual_enabled = bool(config.get("contextual_enabled", True))

            # Decide: cloud parsing (MinerU) or local parsing
            # Default True to match Collection Config UI + _DEFAULT_COLLECTION_CONFIG
            # (previously default False while UI showed ON → silent local parse).
            cloud_parsing = bool(config.get("cloud_parsing", True))
            mineru_cfg = services.config.mineru if hasattr(services.config, "mineru") else None
            file_ext = path.suffix.lower()

            mineru_ready = (
                cloud_parsing
                and mineru_cfg
                and mineru_cfg.enabled
                and bool(mineru_cfg.api_token)
                and file_ext in MINERU_SUPPORTED_EXTENSIONS
            )
            # Cloud parse is slower — give it a wider band before images/chunk
            parse_hi = 28.0 if mineru_ready else 18.0
            logger.info(
                "[%s] Parsing path: collection=%s, cloud_parsing=%s (raw=%r), "
                "mineru_enabled=%s, has_token=%s, ext=%s, supported=%s → %s",
                filename_param,
                collection,
                cloud_parsing,
                config.get("cloud_parsing", "<missing>"),
                mineru_cfg.enabled if mineru_cfg else "N/A",
                bool(mineru_cfg and mineru_cfg.api_token),
                file_ext,
                file_ext in MINERU_SUPPORTED_EXTENSIONS,
                "MinerU" if mineru_ready else "local",
            )

            update(8, "Parsing file via MinerU cloud..." if mineru_ready else "Parsing file...")

            t_parse = time.time()
            if mineru_ready:
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

            parse_via = "MinerU" if mineru_ready and doc is not None else "local"
            ingest_trace.add(
                "parse",
                "Parse",
                detail=f"{parse_via}, {len(doc.content or '')} chars, {len(doc.images or [])} images",
                data={
                    "parser": parse_via,
                    "chars": len(doc.content or ""),
                    "images_extracted": len(doc.images or []),
                    "file_type": getattr(doc, "file_type", "") or "",
                },
                ms=int((time.time() - t_parse) * 1000),
            )
            update(parse_hi, "Parse complete")
            file_dir = path.parent

            # ── Filter / save images (Vision describe runs in parallel with chunking) ──
            has_images = bool(doc.images and file_id)
            if has_images:
                images_hi = parse_hi + 8.0
                from src.parsers.image_utils import process_document_images

                update(parse_hi + 0.5, "Filtering images...")
                t_filter = time.time()
                doc = process_document_images(
                    doc, file_id, file_dir,
                    describe=False,
                    ocr=False,
                )
                kept = list(doc.images or [])
                ingest_trace.add(
                    "images_filter",
                    "Filter / save images",
                    detail=(
                        f"{len(kept)} kept "
                        f"({sum(1 for i in kept if i.is_table_source)} table-source)"
                    ),
                    data={
                        "kept": len(kept),
                        "table_source": sum(1 for i in kept if i.is_table_source),
                        "pending_vision": sum(
                            1 for i in kept
                            if (not i.is_table_source) and (not i.description) and i.image_bytes
                        ),
                    },
                    ms=int((time.time() - t_filter) * 1000),
                )
                logger.info("[%s] Image filter/save done: %d images in doc",
                            filename_param, len(doc.images or []))
            else:
                images_hi = parse_hi
                ingest_trace.add("images_filter", "Filter / save images", status="skip", detail="No images")

            # Save parsed text for preview (same text the chunker uses)
            try:
                parsed_path = file_dir / "parsed.txt"
                parsed_path.write_text(doc.content, encoding="utf-8")
                # Per-blob cache so historical version Source stays instant after
                # a later version upload replaces shared parsed.txt.
                blob_cache = file_dir / f"{path.name}.extracted.txt"
                blob_cache.write_text(doc.content, encoding="utf-8")
            except Exception as e:
                logger.warning("[%s] Failed to save parsed text: %s", filename_param, e)

            chunk_hi = images_hi + 6.0
            mid_lo = chunk_hi
            update(images_hi + 0.5, "Parse complete")
            return doc, config, file_dir

        def _chunk_document(doc, config, content: str):
            update(images_hi + 1.0, "Chunking...")
            ingest_trace.add(
                "chunk",
                "Chunk",
                status="running",
                detail="Started after image filter (parallel with OCR / Summary)",
            )
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
            position_map = getattr(doc, "position_map", None)
            if isinstance(position_map, list) and position_map:
                extra_meta["position_map"] = position_map
            if meeting_id:
                extra_meta["meeting_id"] = meeting_id
            if meeting_date:
                extra_meta["meeting_date"] = meeting_date
            if file_id:
                extra_meta["file_id"] = file_id
            if version_id:
                extra_meta["version_id"] = version_id
            # Phase 4: Qdrant payload extensions for file management v2
            extra_meta["archived"] = False
            extra_meta["is_current"] = True
            from src.identity import get_actor

            extra_meta["created_by"] = get_actor().id
            # Human-readable label for search results display
            extra_meta["source_label"] = source_label if source_label else filename_param
            chunks = chunk_with_sheet_boundaries(
                chunker, content, source=filename_param, extra_metadata=extra_meta
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

            update(chunk_hi, f"Chunked {len(chunks)}")
            ingest_trace.update(
                "chunk",
                status="ok",
                detail=f"{len(chunks)} chunks ({sum(1 for c in chunks if c.chunk_type != 'parent')} searchable)",
                data={
                    "total": len(chunks),
                    "searchable": sum(1 for c in chunks if c.chunk_type != "parent"),
                    "parents": sum(1 for c in chunks if c.chunk_type == "parent"),
                    "mode": config.get("chunk_mode") or "normal",
                },
                ms=int((time.time() - t_chunk) * 1000),
            )
            return chunks

        # Use separate CPU thread pool for parsing; chunk ∥ Vision after that
        doc, config, file_dir = await loop.run_in_executor(_parse_executor, _parse_and_prepare)
        # Parse/filter is done — let the next queued file start. Remaining
        # OCR / Vision / enrich of *this* file no longer occupy the upload slot.
        task_manager.release_upload_slot(task.id)

        from src.parsers.image_utils import apply_image_updates_to_chunks, describe_document_images
        from src.prompts import VISUAL_PROMPT
        from src.rag.contextual import enrichment_model_is_visual, resolve_ingest_vision

        vision_provider, vision_model_id = resolve_ingest_vision()
        is_visual = enrichment_model_is_visual(config)
        from src.rag.contextual import resolve_enrichment_target

        _prov, _model = resolve_enrichment_target(config)
        ingest_trace.set_config(
            contextual_enabled=bool(config.get("contextual_enabled", True)),
            is_visual=is_visual,
            enrich_provider=getattr(_prov, "id", "") or "",
            enrich_model=_model or getattr(_prov, "default_model", None) or getattr(_prov, "model", None) or "",
            vision_model=vision_model_id or "",
            vision_configured=bool(vision_provider and vision_model_id),
            tabular=_is_tabular_document(doc),
        )
        has_non_table_images = bool(
            doc.images
            and any((not img.is_table_source) and img.image_bytes for img in doc.images)
        )
        needs_ocr = bool(has_non_table_images)
        needs_vision = bool(
            has_non_table_images and vision_provider and vision_model_id
        )
        # Non-visual + Vision: Summary still waits for descriptions.
        # Visual / no Vision: Summary starts now — does not wait for OCR.
        wait_summary_for_vision = (not is_visual) and needs_vision

        from src.rag.contextual import run_ingest_summary

        summary_document = doc.content
        summary_future = None
        summary_started_at = 0.0
        if not wait_summary_for_vision:
            def _early_summary():
                return run_ingest_summary(
                    config,
                    summary_document,
                    collection_id=collection,
                    file_id=file_id or "",
                    is_visual=is_visual,
                )

            summary_future = loop.run_in_executor(_enrich_executor, _early_summary)
            summary_started_at = time.time()
            ingest_trace.add(
                "summary",
                "Summary",
                status="running",
                detail="Fired after image filter (not waiting for OCR)",
                started_at=summary_started_at,
            )

        def _persist_parsed(content: str) -> None:
            try:
                (file_dir / "parsed.txt").write_text(content, encoding="utf-8")
                blob_cache = file_dir / f"{path.name}.extracted.txt"
                blob_cache.write_text(content, encoding="utf-8")
            except Exception as e:
                logger.warning("[%s] Failed to update parsed text after images: %s", filename_param, e)

        def _rewrite_image_fences():
            from src.parsers.image_utils import rewrite_image_fences_in_document

            rewrite_image_fences_in_document(doc)
            _persist_parsed(doc.content)

        def _note_image_queue(step_id: str, title: str, submitted: float, started: float) -> None:
            queued = started - submitted
            if queued < 1.0:
                return
            ingest_trace.add(
                f"{step_id}_queue",
                f"Waiting for {title} worker",
                detail=f"Queued {queued:.1f}s — other files were using the image workers",
                ms=int(queued * 1000),
                started_at=submitted,
                ended_at=started,
            )

        def _run_ocr():
            from src.parsers.image_utils import ocr_classify_document_images

            t_ocr = time.time()
            _note_image_queue("ocr", "OCR", ocr_submitted_at, t_ocr)
            ocr_classify_document_images(doc, write_content=False)
            n_ocr = sum(1 for img in (doc.images or []) if img.ocr_text)
            t_ocr_end = time.time()
            ingest_trace.update(
                "ocr",
                status="ok",
                detail=f"{n_ocr} image(s) with OCR text (parallel with Vision / chunk / Summary)",
                data={"with_ocr": n_ocr},
                started_at=t_ocr,
                ended_at=t_ocr_end,
                ms=int((t_ocr_end - t_ocr) * 1000),
            )
            return doc

        def _run_vision_only():
            images_hi_local = images_hi + 10.0
            img_work = _WorkProgress(prog, lo=images_hi, hi=images_hi_local)

            def _img_done():
                img_work.done(1, None)
                d, t = img_work.snapshot()
                update(
                    images_hi + (images_hi_local - images_hi) * min(1.0, d / max(t, 1)),
                    f"Describing images… {d}/{t}",
                )

            t_vision = time.time()
            _note_image_queue("vision", "Vision", vision_submitted_at, t_vision)
            described = describe_document_images(
                doc,
                vision_provider=vision_provider,
                vision_model_id=vision_model_id,
                vision_prompt=VISUAL_PROMPT,
                on_describe_planned=lambda n: img_work.set_total(n),
                on_image_done=_img_done,
                write_content=False,
                clear_bytes=False,
            )
            img_work.finish("Images done")
            vision_ms = int((time.time() - t_vision) * 1000)
            described_imgs = [
                {
                    "image_id": img.image_id,
                    "description": (img.description or "")[:400],
                    "ocr_text": (img.ocr_text or "")[:200],
                    "is_table_source": bool(img.is_table_source),
                }
                for img in (described.images or [])
                if img.description or img.ocr_text
            ]
            ingest_trace.update(
                "vision",
                status="ok",
                detail=f"{sum(1 for i in described_imgs if i.get('description'))} described with {vision_model_id}",
                data={"model": vision_model_id, "described": described_imgs},
                started_at=t_vision,
                ended_at=t_vision + vision_ms / 1000.0,
                ms=vision_ms,
            )
            return described

        content_snapshot = doc.content
        chunk_future = loop.run_in_executor(
            _cpu_executor, _chunk_document, doc, config, content_snapshot
        )
        ocr_future = None
        vision_future = None
        ocr_submitted_at = 0.0
        vision_submitted_at = 0.0
        if needs_ocr:
            ocr_submitted_at = time.time()
            ingest_trace.add(
                "ocr",
                "OCR classification",
                status="running",
                detail="Started after image filter (parallel with Vision / chunk / Summary)",
                started_at=ocr_submitted_at,
            )
            ocr_future = loop.run_in_executor(_ocr_executor, _run_ocr)
        else:
            ingest_trace.add(
                "ocr",
                "OCR classification",
                status="skip",
                detail="No images to OCR",
            )
        if needs_vision:
            vision_submitted_at = time.time()
            ingest_trace.add(
                "vision",
                "Vision descriptions",
                status="running",
                detail="Started after image filter (parallel with OCR / chunk / Summary)",
                started_at=vision_submitted_at,
            )
            vision_future = loop.run_in_executor(_vision_executor, _run_vision_only)
        else:
            ingest_trace.add(
                "vision",
                "Vision descriptions",
                status="skip",
                detail="Not needed (no pending images, or no Visual model)",
            )

        image_deadline = time.monotonic() + IMAGE_JOB_DEADLINE_SEC

        async def _await_image_jobs(*jobs):
            pending = [j for j in jobs if j is not None and not j.done()]
            if not pending:
                _rewrite_image_fences()
                return
            timeout = max(0.05, image_deadline - time.monotonic())
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[%s] OCR/Vision exceeded %.0fs — continuing ingest",
                    filename_param, IMAGE_JOB_DEADLINE_SEC,
                )
                for step_id, title, fut in (
                    ("ocr", "OCR", ocr_future),
                    ("vision", "Vision", vision_future),
                ):
                    if fut is None or fut.done():
                        continue
                    ingest_trace.update(
                        step_id,
                        status="error",
                        detail=(
                            f"{title} exceeded {int(IMAGE_JOB_DEADLINE_SEC)}s "
                            "deadline; ingest continues"
                        ),
                    )
                _rewrite_image_fences()
                return
            for result in results:
                if isinstance(result, Exception):
                    logger.warning("[%s] Image job failed", filename_param, exc_info=result)
            _rewrite_image_fences()

        if summary_future is not None:
            chunk_res, summary_res = await asyncio.gather(
                chunk_future, summary_future, return_exceptions=True
            )
        else:
            chunk_res = await chunk_future
            summary_res = None
        if isinstance(chunk_res, Exception):
            raise chunk_res
        chunks = chunk_res
        if doc.images:
            apply_image_updates_to_chunks(chunks, doc.images or [])
        if wait_summary_for_vision and vision_future is not None:
            await _await_image_jobs(vision_future)
            apply_image_updates_to_chunks(chunks, doc.images or [])
            logger.info("[%s] Vision descriptions applied before Summary/Context", filename_param)

        # ── Stage 2+3: Enriching + Embedding (pipelined) ──
        # Fresh work band [mid_lo, 90] — never reuses image units (that caused
        # progress to jump backward when total grew after images finished).
        t_ctx = time.time()
        contextual_enabled = bool(config.get("contextual_enabled", True))
        embed_count = len([c for c in chunks if c.chunk_type != "parent"])
        enrich_count = len(chunks)
        mid_hi = 90.0
        mid_work = _WorkProgress(prog, lo=mid_lo, hi=mid_hi)
        total_mid = enrich_count + max(embed_count, 1)
        mid_work.set_total(total_mid)
        update(mid_lo, f"Enrich+Embed 0/{total_mid}…")

        embedding = get_collection_embedding(config, collection)

        def _on_embed_done(n: int):
            mid_work.done(n)
            d, t = mid_work.snapshot()
            update(
                mid_lo + (mid_hi - mid_lo) * min(1.0, d / max(t, 1)),
                f"Embedding… {d}/{t}",
            )

        batcher = _EmbedBatcher(
            embedding,
            total_chunks=embed_count,
            on_embed_done=_on_embed_done,
        )

        # Pre-number chunks so embeddings map back to list position
        for i, c in enumerate(chunks):
            c.metadata["_embed_idx"] = i

        # Wait for OCR (and Vision, if any) before embed so fence lexical
        # text is in the vector. Summary/Context still do not wait for OCR.
        defer_embed = ocr_future is not None or vision_future is not None

        def _on_enrich_ready(chunk, context: str):
            mid_work.done(1)
            d, t = mid_work.snapshot()
            update(
                mid_lo + (mid_hi - mid_lo) * min(1.0, d / max(t, 1)),
                f"Enriching… {d}/{t}",
            )
            if not defer_embed:
                batcher.on_ready(chunk, context)

        enrich_document = (
            doc.content if (wait_summary_for_vision and vision_future is not None) else content_snapshot
        )
        enrich_inspect: dict = {}
        pre_short = None
        pre_structured = None
        warmup = None
        early_inspect: dict = {}
        if summary_future is not None:
            try:
                if isinstance(summary_res, Exception):
                    raise summary_res
                early = summary_res or {}
                pre_short = early.get("short_summary") or ""
                pre_structured = early.get("structured_summary") or ""
                early_inspect = dict(early.get("inspect") or {})
                enrich_inspect.update(early_inspect)
                if early_inspect.get("summary_ok"):
                    # Prefix cache only needs a leftover wait — don't sleep 3s
                    # again if chunk/OCR already burned that window.
                    finished = summary_started_at + (int(early_inspect.get("summary_ms") or 0) / 1000.0)
                    leftover = 3.0 - max(0.0, time.time() - finished)
                    warmup = leftover if leftover > 0.05 else 0.0
                else:
                    pre_short = None
                    pre_structured = None
                    warmup = None
            except Exception:
                logger.exception("[%s] early Summary failed — will retry in enrich", filename_param)
                pre_short = None
                warmup = None
        elif wait_summary_for_vision:
            enrich_document = doc.content
            ingest_trace.add(
                "summary",
                "Summary",
                status="running",
                detail="Waited for Vision descriptions (non-visual model)",
            )

        def _enrich_and_embed():
            return _do_enrich(
                chunks,
                doc,
                config,
                collection,
                on_summary=batcher.set_summary,
                on_chunk_ready=_on_enrich_ready,
                contextual_enabled=contextual_enabled,
                full_document=enrich_document,
                inspect_out=enrich_inspect,
                summary=pre_short,
                structured_summary=pre_structured,
                cache_warmup_delay=warmup,
            )

        try:
            chunks = await loop.run_in_executor(_enrich_executor, _enrich_and_embed)
            if early_inspect:
                for key in (
                    "summary_attempts",
                    "summary_ok",
                    "short_summary",
                    "structured_summary",
                    "summary_ms",
                ):
                    if key in early_inspect:
                        enrich_inspect[key] = early_inspect[key]
            _record_enrich_trace(ingest_trace, enrich_inspect)
        except Exception:
            logger.exception("[%s] enrichment failed — continuing to embed", filename_param)
            if not ingest_trace.update(
                "summary",
                status="error",
                detail="Enrichment raised; ingest continues",
            ):
                ingest_trace.add(
                    "summary",
                    "Summary",
                    status="error",
                    detail="Enrichment raised; ingest continues",
                )
            for c in chunks:
                _on_enrich_ready(c, c.metadata.get("context", ""))

        if defer_embed:
            try:
                await _await_image_jobs(ocr_future, vision_future)
                apply_image_updates_to_chunks(chunks, doc.images or [])
                for img in doc.images or []:
                    img.image_bytes = None
            except Exception:
                logger.warning("[%s] Image OCR/Vision after enrich failed", filename_param, exc_info=True)

        if defer_embed:
            for c in chunks:
                batcher.on_ready(c, c.metadata.get("context", ""))

        # Flush remaining embed batches; start sparse in parallel.
        # IMPORTANT: both batcher.wait_all() and sparse_future.result() are
        # sync blocking calls. Calling them directly on the event loop would
        # stall the entire server (list_files, chat, etc.) for the full
        # duration of embedding + sparse encoding — which can be many seconds
        # for slow APIs. Run them in threads and await both concurrently.
        t_embed_store = time.time()
        batcher.flush()
        if batcher._futures:
            sparse_future = _cpu_executor.submit(_do_sparse, batcher.get_all_texts(), collection)
            embeddings, sparse_vectors = await asyncio.gather(
                loop.run_in_executor(None, batcher.wait_all, chunks),
                asyncio.wrap_future(sparse_future),
            )
        else:
            logger.info("[%s] no embed batches submitted, embedding all %d chunks inline",
                        filename_param, len(chunks))
            if enrich_count:
                mid_work.done(enrich_count)
            texts = [_build_enriched_text(c) for c in chunks]

            def _embed_and_sparse():
                emb = embedding.embed_texts(texts)
                if embed_count:
                    mid_work.done(embed_count)
                    d, t = mid_work.snapshot()
                    update(
                        mid_lo + (mid_hi - mid_lo) * min(1.0, d / max(t, 1)),
                        f"Embedding… {d}/{t}",
                    )
                sp = _do_sparse(texts, collection)
                return emb, sp

            embeddings, sparse_vectors = await loop.run_in_executor(None, _embed_and_sparse)

        mid_work.finish("Enrich+Embed done")
        t_emb = time.time()
        logger.info("[%s] Enrich+Embed done in %.1fs (%d chunks)",
                    filename_param, t_emb - t_ctx, len(chunks))

        # ── Stage 4: Storage ──
        def _do_store():
            update(92, "Storing...")
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
        ingest_trace.add(
            "store",
            "Embed + store",
            status="running",
            detail="Writing vectors",
            started_at=t_embed_store,
        )
        await loop.run_in_executor(None, _do_store)
        t_store_end = time.time()
        ingest_trace.update(
            "store",
            status="ok",
            detail=f"{len(chunks)} chunks written",
            data={"chunks": len(chunks), "searchable": embed_count},
            started_at=t_embed_store,
            ended_at=t_store_end,
            ms=int((t_store_end - t_embed_store) * 1000),
        )

        update(100, f"Indexed {len(chunks)} chunks")
        ingest_trace.finish("ok")

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

        if file_id:
            # Definitive file version update / re-ingest:
            # Enrich path schedules consolidate when it stores a new summary.
            # When contextual is off (or enrich skipped), no new summary is
            # written — queue doc_summary so Collection Summary still refreshes.
            try:
                _after_ingest_definitive_followup(
                    collection, file_id, version_id=version_id
                )
            except Exception:
                logger.warning(
                    "[%s] definitive follow-up after ingest failed",
                    filename_param,
                    exc_info=True,
                )

        clear_current_task()
        return {"message": "Done", "filename": filename_param, "chunks_count": len(chunks), "collection": collection}

    except Exception as e:
        if ingest_trace is not None:
            try:
                ingest_trace.finish("error", error=str(e))
            except Exception:
                pass
        clear_current_task()
        raise Exception(f"Failed to process {filename_param}: {e}")


# ---------------------------------------------------------------------------
# Meeting Summary handler
# ---------------------------------------------------------------------------

async def meeting_transcript_index_handler(task: Task, meeting_id: str, **kwargs) -> dict:
    """Build / rebuild the verbatim transcript Qdrant index for a meeting."""
    from src.meeting.transcript_index import index_from_store

    logger.info("[TX_INDEX] task start meeting=%s task=%s", meeting_id, task.id)
    loop = asyncio.get_running_loop()
    n = await loop.run_in_executor(None, index_from_store, meeting_id)
    logger.info("[TX_INDEX] task done meeting=%s packs=%d", meeting_id, n)
    return {"message": "Transcript index ready", "meeting_id": meeting_id, "packs": n}


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
    from src.collections.file_index import load_for_read
    from src.collections.file_index import COLLECTIONS_DIR as _COL_DIR

    file_path = None
    idx = load_for_read(collection)
    for fid, entry in idx.items():
        if entry.get("source") == source:
            try:
                from src.file_mgmt.storage_paths import (
                    ensure_layout_migrated,
                    resolve_version_blob,
                )
                from src.file_mgmt.store import get_db

                ensure_layout_migrated(collection)
                conn = get_db(collection)
                try:
                    row = conn.execute(
                        """SELECT fv.version_id, fv.storage_file_id
                           FROM files f
                           JOIN file_versions fv ON fv.version_id = f.current_version_id
                           WHERE f.file_id=?""",
                        (fid,),
                    ).fetchone()
                finally:
                    conn.close()
                if row:
                    blob = resolve_version_blob(
                        collection,
                        fid,
                        row["version_id"],
                        row["storage_file_id"],
                    )
                    if blob is not None:
                        parsed = blob.parent / "parsed.txt"
                        file_path = parsed if parsed.is_file() else blob
            except Exception:
                pass
            if file_path is None:
                fd = _COL_DIR / collection / "files" / fid
                if fd.is_dir():
                    if (fd / "parsed.txt").is_file():
                        file_path = fd / "parsed.txt"
                    else:
                        for f in sorted(fd.iterdir()):
                            if (
                                f.is_file()
                                and f.name != "parsed.txt"
                                and not f.name.endswith(".extracted.txt")
                            ):
                                file_path = f
                                break
            logger.info(
                "[DOC_SUMMARY] Resolved source=%s -> file_id=%s path=%s",
                source,
                fid,
                file_path,
            )
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

    # Truth source: files.is_definitive — store include for sync only.
    from src.api.routes.info import (
        _snapshot_includes,
        schedule_debounced_consolidate,
        source_is_definitive,
    )

    pre_snapshot = _snapshot_includes(collection)
    is_def = source_is_definitive(collection, source)

    version_id = None
    file_id = None
    if (source or "").startswith("__file__:"):
        file_id = source[len("__file__:") :]
        try:
            from src.api.routes.info import current_version_id_for_source

            version_id = current_version_id_for_source(collection, source)
        except Exception:
            version_id = None

    sm = _get_summary_manager()
    sm.ensure_collection()
    sm.store_doc_summary(
        collection,
        source,
        doc_summary.get("data", []),
        doc_summary.get("facts", []),
        doc_summary.get("insights", []),
        include_in_summary=is_def,
        version_id=version_id,
        file_id=file_id,
    )

    # Definitive: membership or *content* of current summary may have changed
    if is_def:
        schedule_debounced_consolidate(
            collection, pre_snapshot, force_content_change=True
        )
    else:
        logger.info(
            "[DOC_SUMMARY] source=%s not definitive — skip consolidate",
            source,
        )

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
