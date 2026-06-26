from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from concurrent.futures import ThreadPoolExecutor, as_completed
from src.providers.base import LLMProvider
from src.rag.chunker import Chunk

from src.prompts import CONTEXT_PROMPT, SUMMARY_PROMPT, STRUCTURED_SUMMARY_PROMPT  # noqa: E402



class ContextualRetrieval:
    def __init__(self, llm: LLMProvider, context_window: int = 1):
        self.llm = llm
        self.context_window = context_window

    def _generate_summary(self, document: str) -> dict:
        """Call LLM to produce both structured_summary + short_summary in one go.

        Uses JSON mode (response_format) to force valid JSON output, parsed via Pydantic.
        Retries up to 3 times with 5s delay on failure.
        Returns ``{"short_summary": "...", "structured_summary": "===DATA===\\n..."}``.
        On permanent failure returns empty dict.
        """
        import time as _time
        from pydantic import BaseModel

        class CombinedSummary(BaseModel):
            short_summary: str = ""
            structured_summary: str = ""

        prompt = SUMMARY_PROMPT.format(document=document)
        for attempt in range(3):
            if attempt > 0:
                _time.sleep(5)
            try:
                raw = self.llm.generate(
                    prompt, response_format={"type": "json_object"},
                    max_tokens=8192, thinking=False,
                ).strip()
            except Exception:
                logger.exception("[Enrich] summary generation failed, attempt %d/3", attempt + 1)
                continue

            # Parse via Pydantic
            try:
                result = CombinedSummary.model_validate_json(raw)
            except Exception:
                # Strip markdown fences and retry parse
                try:
                    if raw.startswith("```"):
                        lines = raw.split("\n")
                        if lines[0].startswith("```"):
                            lines = lines[1:]
                        if lines and lines[-1].strip().startswith("```"):
                            lines = lines[:-1]
                        raw = "\n".join(lines)
                    result = CombinedSummary.model_validate_json(raw)
                except Exception:
                    logger.exception("[Enrich] failed to parse summary JSON (attempt %d/3), raw (first 500): %r",
                                     attempt + 1, raw[:500])
                    continue

            return {
                "short_summary": result.short_summary,
                "structured_summary": result.structured_summary,
            }

        logger.error("[Enrich] summary permanently failed after 3 attempts")
        return {"short_summary": "", "structured_summary": ""}

    def _use_batch(self) -> bool:
        try:
            from src.config import get_config
            cfg = get_config()
            if not cfg.enrichment.use_batch:
                return False
            self.llm.batch_submit  # raises AttributeError if not implemented
            return True
        except Exception:
            return False

    def _gen_contexts_parallel(self, chunks, _get_surrounding) -> dict:
        """ThreadPoolExecutor approach — one LLM call per chunk."""
        from src.config import get_config
        max_workers = get_config().enrichment.max_parallel_context

        def _gen(chunk: Chunk) -> tuple[int, str]:
            idx = chunk.metadata.get("chunk_index", 0)
            surrounding = _get_surrounding(idx) if self.context_window > 0 else ""
            ctx = self._generate_context(chunk.text, surrounding)
            return idx, ctx

        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_gen, c): c for c in chunks}
            for future in as_completed(futures):
                idx, ctx = future.result()
                results[idx] = ctx
        return results

    def _gen_contexts_batch(self, chunks, _get_surrounding) -> dict:
        """Batch API — all chunks submitted as one batch job."""
        from src.config import get_config
        cfg = get_config()
        poll_interval = cfg.enrichment.batch_poll_interval

        # Build requests for all eligible chunks
        reqs = []
        idx_map = []  # request_index → chunk_index
        for chunk in chunks:
            idx = chunk.metadata.get("chunk_index", 0)
            text = chunk.text.strip()
            if len(text) < 50:
                continue  # skip very short chunks (same as _generate_context)
            surrounding = _get_surrounding(idx) if self.context_window > 0 else ""
            surrounding_section = ""
            if surrounding:
                surrounding_section = f"Surrounding chunks (for context):\n{surrounding}\n\n"
            prompt = CONTEXT_PROMPT.format(
                chunk=text, surrounding_section=surrounding_section,
            )
            reqs.append({"prompt": prompt})
            idx_map.append(idx)

        if not reqs:
            return {}

        logger.info("[Enrich] batch: submitting %d context requests", len(reqs))
        try:
            batch_id = self.llm.batch_submit(reqs)
        except Exception as e:
            logger.error("[Enrich] batch submit failed: %s, falling back to parallel", e)
            return self._gen_contexts_parallel(chunks, _get_surrounding)

        # Poll until complete
        while True:
            try:
                results_list = self.llm.batch_poll(batch_id)
            except Exception as e:
                logger.error("[Enrich] batch poll failed: %s, falling back to parallel", e)
                return self._gen_contexts_parallel(chunks, _get_surrounding)

            if results_list is not None:
                break
            import time
            time.sleep(poll_interval)

        # Map results back to chunk indices
        results = {}
        for i, ctx in enumerate(results_list):
            if i < len(idx_map):
                ctx = (ctx or "").strip()
                if ctx:
                    results[idx_map[i]] = ctx
        return results

    def _generate_context(self, chunk_text: str, surrounding_text: str = "") -> str:
        # Skip context for very short chunks — they're self-contained
        if len(chunk_text.strip()) < 50:
            return ""
        import time as _time
        surrounding_section = ""
        if surrounding_text:
            surrounding_section = f"Surrounding chunks (for context):\n{surrounding_text}\n\n"
        prompt = CONTEXT_PROMPT.format(
            chunk=chunk_text, surrounding_section=surrounding_section,
        )
        for attempt in range(3):
            if attempt > 0:
                _time.sleep(5)
            try:
                ctx = self.llm.generate(prompt, max_tokens=1024, thinking=False).strip()
                if ctx:
                    return ctx
            except Exception:
                pass
        return ""

    def add_context(self, chunks: list[Chunk], full_document: str, *,
                     summary: str | None = None,
                     structured_summary: str | None = None,
                     on_summary=None, on_chunk_ready=None) -> list[Chunk]:
        """Enrich chunks with context + summary. Returns chunks (mutated in place).

        If *summary* is provided, summary generation is skipped entirely —
        useful for parent-child mode where the same document is processed twice.

        ``on_summary(summary)`` is called as soon as the document summary is ready.
        ``on_chunk_ready(chunk, context)`` is called per chunk as contexts complete,
        so callers can start downstream work (e.g. embedding) without waiting for
        all chunks to finish.

        Summary and all per-chunk contexts run in a single flat ThreadPoolExecutor.
        Failed items are retried up to 3 times internally.
        """
        from src.config import get_config
        max_workers = get_config().enrichment.max_parallel_context

        chunk_texts = [c.text for c in chunks]

        def _get_surrounding(idx: int) -> str:
            parts = []
            for offset in range(-self.context_window, self.context_window + 1):
                neighbor_idx = idx + offset
                if neighbor_idx == idx or neighbor_idx < 0 or neighbor_idx >= len(chunk_texts):
                    continue
                parts.append(chunk_texts[neighbor_idx])
            return "\n...\n".join(parts) if parts else ""

        # ── Summary (pre-generated or generated in parallel with contexts) ──
        structured = structured_summary or ""
        pre_generated = summary is not None
        pool_size = max_workers if pre_generated else max_workers + 1
        with ThreadPoolExecutor(max_workers=pool_size) as executor:
            if not pre_generated:
                summary_future = executor.submit(self._generate_summary, full_document)

            # Submit per-chunk contexts — use list position, not chunk_index
            # (chunk_index can repeat across parents in parent-child mode)
            ctx_futures: dict = {}  # future → list position
            for pos, chunk in enumerate(chunks):
                surrounding = _get_surrounding(pos) if self.context_window > 0 else ""
                f = executor.submit(self._generate_context, chunk.text, surrounding)
                ctx_futures[f] = pos

            if not pre_generated:
                combined = summary_future.result()
                summary = combined.get("short_summary", "")
                structured = structured or combined.get("structured_summary", "")
                logger.info("[Enrich] summary ready, has_structured=%s", bool(structured))

            # Notify caller so it can flush pending chunks waiting for summary
            if on_summary:
                on_summary(summary)

            # Pre-fill summary metadata for all chunks (so on_chunk_ready sees it)
            for chunk in chunks:
                chunk.metadata["summary"] = summary
                if structured:
                    chunk.metadata["_structured_summary"] = structured

            # Wait for contexts — notify per chunk as they complete
            # (retries handled inside _generate_context)
            results: dict[int, str] = {}
            for future in as_completed(ctx_futures):
                from src.tasks.task_manager import check_cancelled
                check_cancelled()
                pos = ctx_futures[future]
                chunk = chunks[pos]
                try:
                    ctx = future.result()
                    if ctx:
                        results[pos] = ctx
                        chunk.metadata["context"] = ctx
                        if on_chunk_ready:
                            on_chunk_ready(chunk, ctx)
                except Exception:
                    logger.warning("[Enrich] chunk %d context failed", pos)

            logger.info("[Enrich] %d/%d chunk contexts ready", len(results), len(chunks))

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

        # Detect section headers like ===DATA=== or ===data===
        if stripped.startswith("===") and stripped.endswith("==="):
            header = stripped[3:-3].strip().lower()
            if header in section_map:
                current_key = section_map[header]
            else:
                current_key = None
            continue

        # Parse bullet items
        if current_key is not None and stripped.startswith("-"):
            item = stripped.lstrip("-").strip()
            if item and item.lower() != "none identified":
                result[current_key].append(item)

    return result


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
