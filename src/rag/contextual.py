from __future__ import annotations

import json
import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

from concurrent.futures import ThreadPoolExecutor, as_completed

from src.providers.base import LLMProvider
from src.rag.chunker import Chunk, _strip_image_fences, iter_image_fence_spans, strip_table_source_fences

from src.prompts import (  # noqa: E402
    INGEST_CONTEXT_TASK,
    INGEST_SUMMARY_TASK,
    INGEST_SYSTEM_PROMPT,
    STRUCTURED_SUMMARY_PROMPT,
)

__all__ = [
    "ContextualRetrieval",
    "STRUCTURED_SUMMARY_PROMPT",
    "generate_structured_summary",
    "_parse_structured_summary",
    "ingest_parallel_limit",
]

CONTEXT_BATCH_SIZE = 10
CONTEXT_DEFAULT_PARALLEL = 50
CONTEXT_MAX_PARALLEL = 100


def ingest_parallel_limit() -> int:
    """Live Settings cap for concurrent ingest Summary + Context LLM calls."""
    try:
        from src.config import get_config

        n = int(get_config().enrichment.max_parallel_context or 0)
    except Exception:
        n = CONTEXT_DEFAULT_PARALLEL
    return max(1, min(CONTEXT_MAX_PARALLEL, n))


class _IngestRequestLimiter:
    """Process-wide ceiling on in-flight ingest LLM requests.

    Covers Summary, Context, and Vision describe. Files are not serialized:
    File A's Summary can run next to File B's Context / Vision; only the
    request count is capped (default 50). The limit follows Settings live
    so changing Parallel takes effect on the next acquire.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._in_flight = 0

    def acquire(self) -> None:
        from src.tasks.task_manager import check_cancelled

        t0 = time.time()
        with self._cond:
            while self._in_flight >= ingest_parallel_limit():
                check_cancelled()
                self._cond.wait(timeout=0.5)
            self._in_flight += 1
        waited = time.time() - t0
        if waited >= 1.0:
            logger.info(
                "[Enrich] waited %.1fs for ingest LLM slot (now %d/%d)",
                waited,
                self._in_flight,
                ingest_parallel_limit(),
            )

    def release(self) -> None:
        with self._cond:
            self._in_flight = max(0, self._in_flight - 1)
            self._cond.notify_all()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


ingest_request_limiter = _IngestRequestLimiter()


def _parse_model_ref(value: str) -> tuple[str, str | None]:
    """Split ``providerId|modelName`` (or a bare provider id) into parts."""
    text = (value or "").strip()
    if not text:
        return "", None
    if "|" in text:
        pid, model = text.split("|", 1)
        return pid.strip(), (model.strip() or None)
    return text, None


def resolve_enrichment_target(config: dict | None) -> tuple[object | None, str | None]:
    """Collection override → global Settings item → default LLM provider.

    Returns ``(provider_cfg, model_name)``. *model_name* may be None (use
    the provider default).
    """
    from src.config import get_config

    cfg = get_config()
    providers = list(cfg.llm.providers or [])
    config = config or {}

    pid = str(config.get("enriching_llm_provider") or "").strip()
    if pid:
        for p in providers:
            if p.id == pid:
                model = str(config.get("enriching_llm_model") or "").strip() or None
                return p, model

    ref = getattr(cfg.enrichment, "enrichment_model", "") or ""
    pid, model = _parse_model_ref(ref)
    if pid:
        for p in providers:
            if p.id == pid:
                return p, model

    if providers:
        default_p = next((p for p in providers if p.is_default), providers[0])
        return default_p, None
    return None, None


def get_enriching_llm(config: dict | None):
    """LLM used for ingest Summary + Context (and collection consolidate)."""
    from src.providers.cache import get_or_create as cached_provider
    from src.providers.llm import create_llm_for_provider
    from src.services import services

    provider_cfg, model = resolve_enrichment_target(config)
    if provider_cfg is None:
        return services.llm
    effective = model or getattr(provider_cfg, "default_model", None) or getattr(provider_cfg, "model", None) or ""
    cache_key = f"llm:enrich:{provider_cfg.id}:{effective}"
    return cached_provider(
        cache_key,
        lambda: create_llm_for_provider(provider_cfg, model=model),
    )


def enrichment_model_is_visual(config: dict | None) -> bool:
    """True if the selected enrich model is in that provider's visual_model_ids."""
    provider_cfg, model = resolve_enrichment_target(config)
    if provider_cfg is None:
        return False
    mid = model or getattr(provider_cfg, "default_model", None) or getattr(provider_cfg, "model", None) or ""
    visual_ids = getattr(provider_cfg, "visual_model_ids", None) or []
    return bool(mid) and mid in visual_ids


def vision_description_configured() -> bool:
    """True if Settings has a Visual model (eye icon) selected."""
    from src.config import get_config

    cfg = get_config()
    vision_model_id = getattr(cfg, "visual_model_id", "") or ""
    if not vision_model_id:
        return False
    for p in cfg.llm.providers or []:
        if vision_model_id in (getattr(p, "visual_model_ids", None) or []):
            return True
    return False


_DESC_LINE = re.compile(r"^description:\s*\S", re.I | re.M)


def chunk_situating_card(chunk: Chunk, index: int) -> str:
    """What the Context LLM sees for one chunk: [id] plus the chunk body.

    [N] is the id the model must return in JSON. Independent :::image fences
    stay; table-source fences are dropped (the table markup is the content).
    Heading / sheet are not repeated — they are already in the document above.
    """
    lines = [f"[{index}]"]
    body = strip_table_source_fences(chunk.text or "").strip()
    if body:
        lines.append(body)
    return "\n".join(lines)


def is_image_only_text(text: str) -> bool:
    """True when the chunk is only empty ``:::image`` fence(s) (no prose, no description)."""
    if not text or ":::image" not in text:
        return False
    if _strip_image_fences(text).strip():
        return False
    return _DESC_LINE.search(text) is None


def _table_source_image_ids(text: str) -> set[str]:
    """image_id values of :::image fences that sit immediately before a table."""
    from src.parsers.image_utils import _IMAGE_BLOCK_RE

    ids: set[str] = set()
    if not text or ":::image" not in text:
        return ids
    for start, end, is_table_source in iter_image_fence_spans(text):
        if not is_table_source:
            continue
        match = _IMAGE_BLOCK_RE.search(text[start:end])
        if match:
            ids.add(match.group(1))
    return ids


def _collect_images_for_text(text: str, collection_id: str, file_id: str) -> dict[str, dict]:
    """Load unique images referenced by ``:::image`` fences in *text*.

    Table-source fences are skipped — those screenshots never go to Vision.
    """
    from src.parsers.image_utils import (
        _IMAGE_BLOCK_RE,
        _encode_image_base64_direct,
        encode_image_base64,
    )

    images: dict[str, dict] = {}
    if not text:
        return images
    skip_ids = _table_source_image_ids(text)
    for match in _IMAGE_BLOCK_RE.finditer(text):
        img_id = match.group(1)
        if img_id in skip_ids:
            continue
        fid = (match.group(2) or "").strip() or file_id
        if not img_id or img_id in images or not fid:
            continue
        encoded = None
        if collection_id:
            encoded = _encode_image_base64_direct(img_id, fid, collection_id)
        if encoded is None:
            encoded = encode_image_base64(img_id, fid)
        if encoded:
            images[img_id] = {"base64": encoded[0], "mime": encoded[1]}
    return images


def _document_user_content(
    document: str,
    *,
    is_visual: bool,
    collection_id: str,
    file_id: str,
) -> str | list:
    """Build the shared user-prefix: full document, optionally multimodal."""
    if not is_visual:
        return document or ""
    from src.rag.agentic_query import _build_multimodal_context

    images = _collect_images_for_text(document, collection_id, file_id)
    if not images:
        return document or ""
    return _build_multimodal_context(
        document or "", images, include_alt_text=False,
    )


def _append_task(prefix: str | list, task: str) -> str | list:
    """Append the task suffix after the shared document prefix."""
    if isinstance(prefix, list):
        return list(prefix) + [{"type": "text", "text": "\n\n---\n\n" + task}]
    return (prefix or "") + "\n\n---\n\n" + task


def _parse_context_batch(raw: str, ids: list[int]) -> dict[int, str]:
    """Parse situating JSON into ``{chunk_id: context}``. Missing ids are omitted."""
    if not raw or not raw.strip():
        return {}
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return {}
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return {}

    wanted = set(ids)
    out: dict[int, str] = {}
    items = None
    if isinstance(data, dict):
        items = data.get("contexts", data.get("items"))
        if items is None:
            # {"0": "...", "1": "..."}
            for key, value in data.items():
                try:
                    idx = int(key)
                except (TypeError, ValueError):
                    continue
                if idx in wanted and isinstance(value, str) and value.strip():
                    out[idx] = value.strip()
            return out
    elif isinstance(data, list):
        items = data
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id", item.get("index"))
        ctx = item.get("context", item.get("text", ""))
        try:
            idx = int(raw_id)
        except (TypeError, ValueError):
            continue
        if idx in wanted and isinstance(ctx, str) and ctx.strip():
            out[idx] = ctx.strip()
    return out


class ContextualRetrieval:
    def __init__(
        self,
        llm: LLMProvider,
        context_window: int = 1,
        *,
        summary_retry_delay: float = 1.0,
        cache_warmup_delay: float = 3.0,
    ):
        self.llm = llm
        self.context_window = context_window
        self.summary_retry_delay = summary_retry_delay
        self.cache_warmup_delay = cache_warmup_delay
        self.inspect: dict = {
            "summary_attempts": 0,
            "summary_ok": False,
            "short_summary": "",
            "structured_summary": "",
            "summary_ms": 0,
            "cache_wait_s": 0,
            "context_ran": False,
            "context_batches": 0,
            "context_written": 0,
            "context_skipped_image_only": 0,
            "context_ms": 0,
            "contexts": [],
        }

    def _ingest_generate(self, prompt: str | list, **kwargs) -> str:
        from src.providers.retry import is_rate_limit_error, retry_delay

        last: BaseException | None = None
        for attempt in range(1, 4):
            try:
                with ingest_request_limiter:
                    return (self.llm.generate(
                        prompt,
                        system=INGEST_SYSTEM_PROMPT,
                        thinking=False,
                        **kwargs,
                    ) or "").strip()
            except Exception as exc:
                last = exc
                if not is_rate_limit_error(exc) or attempt >= 3:
                    raise
                delay = retry_delay(attempt)
                logger.warning(
                    "[Enrich] rate-limited on ingest LLM (attempt %d/3), sleeping %.1fs",
                    attempt,
                    delay,
                )
                time.sleep(delay)
        raise last  # pragma: no cover

    def _generate_summary(self, document_prefix: str | list) -> dict:
        """Call LLM to produce structured_summary + short_summary.

        Retries up to 3 times with ``summary_retry_delay`` between attempts.
        """
        from pydantic import BaseModel

        class CombinedSummary(BaseModel):
            short_summary: str = ""
            structured_summary: str = ""

        prompt = _append_task(document_prefix, INGEST_SUMMARY_TASK)
        t0 = time.time()
        for attempt in range(3):
            self.inspect["summary_attempts"] = attempt + 1
            if attempt > 0:
                time.sleep(self.summary_retry_delay)
            try:
                raw = self._ingest_generate(
                    prompt,
                    response_format={"type": "json_object"},
                    max_tokens=8192,
                )
            except Exception:
                logger.exception("[Enrich] summary generation failed, attempt %d/3", attempt + 1)
                continue

            try:
                result = CombinedSummary.model_validate_json(raw)
            except Exception:
                try:
                    cleaned = raw
                    if cleaned.startswith("```"):
                        lines = cleaned.split("\n")
                        if lines[0].startswith("```"):
                            lines = lines[1:]
                        if lines and lines[-1].strip().startswith("```"):
                            lines = lines[:-1]
                        cleaned = "\n".join(lines)
                    result = CombinedSummary.model_validate_json(cleaned)
                except Exception:
                    logger.exception(
                        "[Enrich] failed to parse summary JSON (attempt %d/3), raw (first 500): %r",
                        attempt + 1,
                        raw[:500],
                    )
                    continue

            self.inspect["summary_ok"] = True
            self.inspect["short_summary"] = result.short_summary
            self.inspect["structured_summary"] = result.structured_summary
            self.inspect["summary_ms"] = max(0, int((time.time() - t0) * 1000))
            return {
                "short_summary": result.short_summary,
                "structured_summary": result.structured_summary,
            }

        logger.error("[Enrich] summary permanently failed after 3 attempts")
        self.inspect["summary_ok"] = False
        self.inspect["summary_ms"] = max(0, int((time.time() - t0) * 1000))
        return {"short_summary": "", "structured_summary": ""}

    def _situate_batch(
        self,
        document_prefix: str | list,
        batch: list[tuple[int, Chunk]],
        *,
        is_visual: bool,
        collection_id: str,
        file_id: str,
        prefix_image_ids: set[str] | None = None,
    ) -> dict[int, str]:
        lines: list[str] = []
        for idx, chunk in batch:
            lines.append(chunk_situating_card(chunk, idx))
        task = INGEST_CONTEXT_TASK.format(chunks="\n\n".join(lines))
        if is_visual:
            from src.rag.agentic_query import _build_multimodal_context

            images = _collect_images_for_text(task, collection_id, file_id)
            if images:
                task_content = _build_multimodal_context(
                    task, images, skip_ids=prefix_image_ids,
                    include_alt_text=False,
                )
                if isinstance(document_prefix, list):
                    prompt: str | list = list(document_prefix) + [
                        {"type": "text", "text": "\n\n---\n\n"}
                    ] + list(task_content)
                else:
                    prompt = [{"type": "text", "text": (document_prefix or "") + "\n\n---\n\n"}] + list(task_content)
            else:
                prompt = _append_task(document_prefix, task)
        else:
            prompt = _append_task(document_prefix, task)

        try:
            raw = self._ingest_generate(
                prompt,
                response_format={"type": "json_object"},
                max_tokens=800,
            )
        except Exception:
            logger.exception("[Enrich] context batch failed (%d chunks)", len(batch))
            return {}
        return _parse_context_batch(raw, [idx for idx, _c in batch])

    def add_context(
        self,
        chunks: list[Chunk],
        full_document: str,
        *,
        summary: str | None = None,
        structured_summary: str | None = None,
        on_summary=None,
        on_chunk_ready=None,
        tabular: bool = False,
        contextual_enabled: bool = True,
        is_visual: bool = False,
        collection_id: str = "",
        file_id: str = "",
    ) -> list[Chunk]:
        """Enrich chunks with a document summary and per-chunk situating context.

        Summary always runs (unless *summary* is pre-supplied). Context runs
        only when *contextual_enabled* and the file is not Excel/CSV. Parent
        chunks are never sent to the Context LLM and are never embedded.
        """
        from src.tasks.task_manager import check_cancelled

        structured = structured_summary or ""
        pre_generated = summary is not None

        def _stamp_summary(short: str | None, structured_raw: str) -> None:
            for chunk in chunks:
                chunk.metadata["summary"] = short or ""
                if structured_raw:
                    chunk.metadata["_structured_summary"] = structured_raw

        def _notify(chunk: Chunk, ctx: str) -> None:
            if not on_chunk_ready:
                return
            try:
                on_chunk_ready(chunk, ctx)
            except Exception:
                logger.debug("[Enrich] on_chunk_ready failed")

        file_id = file_id or (
            chunks[0].metadata.get("file_id", "") if chunks else ""
        )
        document_prefix = _document_user_content(
            full_document,
            is_visual=is_visual,
            collection_id=collection_id,
            file_id=file_id,
        )
        prefix_image_ids = (
            set(_collect_images_for_text(full_document, collection_id, file_id))
            if is_visual else set()
        )

        summary_ok = pre_generated
        if not pre_generated:
            combined = self._generate_summary(document_prefix)
            summary = combined.get("short_summary", "")
            structured = structured or combined.get("structured_summary", "")
            summary_ok = bool(summary or structured)
            logger.info("[Enrich] summary ready, has_structured=%s ok=%s", bool(structured), summary_ok)
        else:
            self.inspect["summary_ok"] = True
            self.inspect["short_summary"] = summary or ""
            self.inspect["structured_summary"] = structured or ""
        self.inspect["summary_ok"] = bool(summary_ok)
        self.inspect["short_summary"] = summary or self.inspect.get("short_summary") or ""
        self.inspect["structured_summary"] = structured or self.inspect.get("structured_summary") or ""

        _stamp_summary(summary, structured)
        if on_summary:
            on_summary(summary or "")

        work = [c for c in chunks if c.chunk_type != "parent"]
        run_context = bool(contextual_enabled) and not tabular and bool(work)

        if not run_context:
            for chunk in chunks:
                check_cancelled()
                _notify(chunk, chunk.metadata.get("context", ""))
            self.inspect["context_ran"] = False
            self.inspect["context_skip_reason"] = (
                "tabular" if tabular else ("off" if not contextual_enabled else "no_chunks")
            )
            logger.info(
                "[Enrich] skipped context LLM (enabled=%s tabular=%s searchable=%d)",
                contextual_enabled,
                tabular,
                len(work),
            )
            return chunks

        if not work:
            for chunk in chunks:
                check_cancelled()
                _notify(chunk, chunk.metadata.get("context", ""))
            self.inspect["context_ran"] = False
            self.inspect["context_skip_reason"] = "no_chunks"
            logger.info("[Enrich] no searchable chunks need context")
            return chunks

        if summary_ok and self.cache_warmup_delay > 0:
            self.inspect["cache_wait_s"] = self.cache_warmup_delay
            t_wait = time.time()
            self.inspect["cache_wait_started"] = t_wait
            time.sleep(self.cache_warmup_delay)
            self.inspect["cache_wait_ended"] = time.time()
        else:
            self.inspect["cache_wait_s"] = 0
            self.inspect["cache_wait_started"] = None
            self.inspect["cache_wait_ended"] = None
        t_ctx = time.time()
        self.inspect["context_started"] = t_ctx

        batches: list[list[tuple[int, Chunk]]] = []
        current: list[tuple[int, Chunk]] = []
        for i, chunk in enumerate(work):
            current.append((i, chunk))
            if len(current) >= CONTEXT_BATCH_SIZE:
                batches.append(current)
                current = []
        if current:
            batches.append(current)

        written = 0
        max_workers = min(ingest_parallel_limit(), max(1, len(batches)))
        results: dict[int, str] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self._situate_batch,
                    document_prefix,
                    batch,
                    is_visual=is_visual,
                    collection_id=collection_id,
                    file_id=file_id,
                    prefix_image_ids=prefix_image_ids,
                ): batch
                for batch in batches
            }
            for future in as_completed(futures):
                check_cancelled()
                batch = futures[future]
                try:
                    parsed = future.result() or {}
                except Exception:
                    logger.warning("[Enrich] context batch of %d failed", len(batch))
                    parsed = {}
                results.update(parsed)

        work_pos = {id(chunk): i for i, chunk in enumerate(work)}
        for i, chunk in enumerate(work):
            ctx = results.get(i, "")
            if ctx:
                chunk.metadata["context"] = ctx
                written += 1
            else:
                chunk.metadata.setdefault("context", "")

        for chunk in chunks:
            pos = work_pos.get(id(chunk))
            ctx = chunk.metadata.get("context", "") if pos is not None else chunk.metadata.get("context", "")
            _notify(chunk, ctx)

        self.inspect["context_ran"] = True
        self.inspect["context_batches"] = len(batches)
        self.inspect["context_written"] = written
        self.inspect["context_ms"] = max(0, int((time.time() - t_ctx) * 1000))
        self.inspect["contexts"] = [
            {
                "index": i,
                "chunk_preview": ((chunk.text or "").strip()[:160]),
                "context": (chunk.metadata.get("context") or ""),
            }
            for i, chunk in enumerate(work)
        ]
        logger.info(
            "[Enrich] %d/%d searchable chunks received situating context (%d batches)",
            written,
            len(work),
            len(batches),
        )
        return chunks


# ---------------------------------------------------------------------------
# Structured Summary Generation
# ---------------------------------------------------------------------------


def _parse_structured_summary(raw: str) -> dict[str, list[str]]:
    """Parse LLM output into structured summary dict.

    Splits on ``===`` delimiters, extracts bullet items under DATA, FACTS,
    and INSIGHTS sections, and filters out "None identified" placeholders.

    Returns ``{"data": [...], "facts": [...], "insights": [...]}``.
    """
    if not raw or not raw.strip():
        return {"data": [], "facts": [], "insights": []}

    result: dict[str, list[str]] = {"data": [], "facts": [], "insights": []}
    section_map = {"data": "data", "facts": "facts", "insights": "insights"}

    current_key: str | None = None

    for line in raw.splitlines():
        stripped = line.strip()

        if stripped.startswith("===") and stripped.endswith("==="):
            header = stripped[3:-3].strip().lower()
            if header in section_map:
                current_key = section_map[header]
            else:
                current_key = None
            continue

        if current_key is not None and stripped.startswith("-"):
            item = stripped.lstrip("-").strip()
            if item and item.lower() != "none identified":
                result[current_key].append(item)

    return result


def run_ingest_summary(
    config: dict | None,
    document: str,
    *,
    collection_id: str = "",
    file_id: str = "",
    is_visual: bool = False,
    summary_retry_delay: float = 1.0,
) -> dict:
    """Generate the ingest Summary (short + structured) for a document snapshot.

    Safe to call as soon as images are filtered — does not wait for OCR.
    """
    llm = get_enriching_llm(config)
    if llm is None:
        return {"short_summary": "", "structured_summary": "", "inspect": {}}
    cr = ContextualRetrieval(llm, summary_retry_delay=summary_retry_delay)
    prefix = _document_user_content(
        document or "",
        is_visual=is_visual,
        collection_id=collection_id,
        file_id=file_id,
    )
    combined = cr._generate_summary(prefix)
    return {
        "short_summary": combined.get("short_summary", ""),
        "structured_summary": combined.get("structured_summary", ""),
        "inspect": dict(cr.inspect),
    }


def generate_structured_summary(llm: LLMProvider, document: str) -> dict[str, list[str]]:
    """Generate a structured summary (data/facts/insights) from a document.

    Uses the LLM to extract three categories of information and returns
    a parsed dict.  Returns empty lists on any failure.
    """
    prompt = STRUCTURED_SUMMARY_PROMPT.format(document=document)
    try:
        raw = llm.generate(prompt, max_tokens=8192, thinking=False)
    except Exception:
        return {"data": [], "facts": [], "insights": []}
    return _parse_structured_summary(raw)
