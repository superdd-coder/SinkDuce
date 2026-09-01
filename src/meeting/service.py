"""Meeting service -- transcription task handler, summary generation, and collection allocation."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.config import DATA_DIR, get_config, LIVE_TRANSLATE_ADAPTER
from src.meeting import store
from src.meeting.models import (
    GenerationState,
    Meeting,
    MeetingStatus,
    ProcessingState,
    TranscriptionResult,
)
from src.meeting.transcription.base import (
    FileTranscriptionProvider,
    RealtimeTranscriptionProvider,
)
from src.meeting.transcription import (
    create_file_transcription_provider,
    create_realtime_transcription_provider,
)
from src.providers.cache import get_or_create as cached_provider
from src.services import services
from src.tasks.task_manager import task_manager, Task, TaskStatus
from src.meeting.refs import (
    clean_refs as _clean_refs,
    finalize_summary_markdown as _finalize_summary_markdown,
    normalize_brackets as _normalize_brackets,
    normalize_refs as _normalize_refs,
    parse_tagger_response as _parse_tagger_response,
)
from src.meeting.allocate import MeetingAllocateMixin
from src.meeting.generation import MeetingGenerationMixin
from src.meeting.translation import MeetingTranslationMixin, _TranslationStream

logger = logging.getLogger(__name__)

# Serialize per-section todo extract (allocate bg + GET refresh must not double-hit LLM)
_todo_extract_locks: dict[str, threading.Lock] = {}
_todo_extract_locks_guard = threading.Lock()


def _todo_extract_lock(meeting_id: str, tab_id: str) -> threading.Lock:
    key = f"{meeting_id}:{tab_id}"
    with _todo_extract_locks_guard:
        lock = _todo_extract_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _todo_extract_locks[key] = lock
        return lock


def _resolve_meeting_llm(ref: str | None = None) -> "LLMProvider":
    """Resolve the LLM for meeting summary (blueprint/tagger/summarizer).

    Priority: ``ref`` (or meeting_model config when ref is None/empty) →
    default LLM provider.
    """
    from src.config import get_config
    from src.providers.llm import create_llm_for_provider

    cfg = get_config()
    meeting_model = ref or cfg.enrichment.meeting_model
    if meeting_model and cfg.llm.providers:
        # meeting_model format: "providerId|modelName" or just "providerId"
        parts = meeting_model.split("|", 1)
        target_pid = parts[0]
        target_model = parts[1] if len(parts) > 1 else None
        for p in cfg.llm.providers:
            if p.id == target_pid:
                return create_llm_for_provider(p, model=target_model)

    llm = services.llm
    if llm is not None:
        return llm

    if cfg.llm.providers:
        default_p = next(
            (p for p in cfg.llm.providers if p.is_default),
            cfg.llm.providers[0],
        )
        return create_llm_for_provider(default_p)

    raise RuntimeError("No LLM provider configured. Add one in Settings first.")


def _notes_for_meeting_llm(notes_text: str) -> str:
    """If Meeting Summary cannot see images, flatten figures via 图片描述."""
    from src.config import get_config
    from src.parsers.image_utils import prepare_text_for_non_visual_llm
    from src.rag.contextual import provider_model_is_visual, resolve_named_slot

    ref = getattr(get_config().enrichment, "meeting_model", "") or ""
    if provider_model_is_visual(*resolve_named_slot(ref)):
        return notes_text
    return prepare_text_for_non_visual_llm(notes_text)


def _detect_embedding_dim() -> int:
    """Detect actual embedding dimension by test embedding."""
    dim = getattr(services.embedding, 'dimensions', 0) if services.embedding else 0
    if not dim or dim <= 0:
        try:
            test = services.embedding.embed_texts(["test"])
            dim = len(test[0])
        except Exception:
            dim = 1024
            logger.warning(
                "Could not detect embedding dimension, falling back to %d. "
                "SummaryManager vector size may be incorrect.",
                dim,
            )
    return dim if dim > 0 else 1024

COLLECTIONS_DIR = DATA_DIR / "collections"

def _files_dir(collection_id: str) -> Path:
    return COLLECTIONS_DIR / collection_id / "files"


def _num_id(sentence_id: str) -> str:
    """Extract numeric part for compact transcript representation.

    '756f0b7c_stt_0001' → '1'
    '756f0b7c_stt_0123' → '123'
    Fallback: return as-is if format doesn't match.
    """
    parts = sentence_id.rsplit("_stt_", 1)
    if len(parts) == 2:
        return str(int(parts[1]))  # int() strips leading zeros
    return sentence_id


def _rebuild_allocation_arrays(tabs: list) -> tuple[list[str], list[str]]:
    """Rebuild allocated_collections / allocated_file_ids from tabs.

    This is the single source of truth for meeting-level allocation tracking.
    All allocate/delete/cancel paths must call this instead of incremental
    append/delete to avoid parallel-array ghost entries (P0-04).
    """
    cols: list[str] = []
    fids: list[str] = []
    for t in (tabs or []):
        td = t if isinstance(t, dict) else t.model_dump()
        cid = td.get("associated_collection_id", "")
        fid = td.get("allocated_file_id", "")
        if cid and fid:
            cols.append(cid)
            fids.append(fid)
    return cols, fids

# ---------------------------------------------------------------------------
# Task handler for file transcription
# ---------------------------------------------------------------------------

async def transcribe_handler(task: Task, meeting_id: str, **kwargs) -> dict:
    """Task handler for file transcription. Registered with task_manager."""
    def update(progress: float, msg: str):
        task.progress = progress
        task.message = msg
        logger.info("[TRANSCRIBE-HANDLER] Meeting %s progress=%.0f%% %s", meeting_id, progress, msg)

    update(0, "Starting transcription...")

    # 1. Get the meeting from store
    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        logger.error("[TRANSCRIBE-HANDLER] Meeting %s NOT FOUND", meeting_id)
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    if not meeting.audio_path:
        logger.error("[TRANSCRIBE-HANDLER] Meeting %s has NO AUDIO FILE", meeting_id)
        raise ValueError(f"Meeting {meeting_id} has no audio file")

    logger.info("[TRANSCRIBE-HANDLER] Meeting %s audio_path=%s", meeting_id, meeting.audio_path)
    # Ensure re-tx never leaves stale tabs/blueprint that would skip Speakers UI.
    try:
        store.delete_pipeline_data(meeting_id)
    except Exception as exc:
        logger.warning("[TRANSCRIBE-HANDLER] delete_pipeline_data failed (non-fatal): %s", exc)
    try:
        from src.meeting.transcript_index import purge_meeting_transcripts

        purge_meeting_transcripts(meeting_id)
    except Exception:
        logger.warning("[TRANSCRIBE-HANDLER] transcript purge skipped: %s", meeting_id)
    store.update_meeting(
        meeting_id,
        status=MeetingStatus.transcribing,
        transcription_error=None,
        transcript_index_status="",
        transcript_index_error="",
        processing_state=ProcessingState.idle.value,
        summary_gen_state=GenerationState.idle.value,
        blueprint_gen_state=GenerationState.idle.value,
        summary=None,
        detail=None,
        blueprint=None,
        blueprint_taxonomy=None,
        tabs=None,
        speaker_names=None,
        speaker_people=None,
        speaker_matches=None,
        speaker_slots=None,
        speaker_slots_status=None,
        speaker_slots_ms=None,
    )
    update(5, "Loading transcription provider...")

    # 2. Get the active file transcription provider
    config = get_config()
    provider_cfg = config.transcription.active_file_provider
    if provider_cfg is None:
        provider_cfg = config.transcription.get_local_file_provider()

    # Auto-load the provider if its model is downloaded but not yet loaded.
    # If the model is NOT downloaded, raise a clear error — do NOT auto-download.
    from src.meeting.transcription import is_local_file_adapter

    if provider_cfg and is_local_file_adapter(provider_cfg.adapter):
        from src.services import _is_builtin_model_downloaded, reload_provider
        from src.providers.load_state import get_state
        model_id = provider_cfg.id
        if not _is_builtin_model_downloaded(model_id):
            raise RuntimeError(
                "Local transcription model is not downloaded. "
                "Please download it first via Settings → Local Models → Download."
            )
        if get_state(model_id) not in ("loaded", "loading"):
            logger.info("[TRANSCRIBE-HANDLER] Auto-loading transcription provider: %s", model_id)
            reload_provider(model_id, loading=True)
            # Wait briefly for load to complete
            import time
            waited = 0
            while get_state(model_id) == "loading" and waited < 60:
                time.sleep(0.5)
                waited += 0.5
            if get_state(model_id) == "error":
                raise RuntimeError(
                    "Failed to load local transcription model. "
                    "Check the model files and try again via Settings → Local Models → Load."
                )

    provider = cached_provider(
        f"file_trans:{provider_cfg.id}",
        lambda: create_file_transcription_provider(provider_cfg),
    )
    logger.info("[TRANSCRIBE-HANDLER] Provider created: %s (adapter=%s)", type(provider).__name__, provider_cfg.adapter)
    update(10, "Transcribing audio...")

    # Use local file mode — DashScope Recognition.call() reads files
    # directly via WebSocket, no public URL needed.
    logger.info("[TRANSCRIBE-HANDLER] Using local file mode: %s", meeting.audio_path)
    source = meeting.audio_path

    # Load hot words only when the active file adapter can apply them
    hot_words = None
    language_hints = kwargs.get("language_hints")  # user-selected from frontend
    # "auto" means auto-detect — strip it so the provider doesn't receive it
    if language_hints:
        language_hints = [h for h in language_hints if h != "auto"] or None
    if getattr(provider, "supports_hot_words", False):
        from src.hot_words.store import collect_meeting_hot_words
        hot_words = collect_meeting_hot_words(meeting) or None
        if hot_words:
            logger.info("[TRANSCRIBE-HANDLER] Loaded %d hot words", len(hot_words))

    if language_hints:
        logger.info("[TRANSCRIBE-HANDLER] Using language_hints=%s", language_hints)

    try:
        result: TranscriptionResult = await provider.transcribe(source, hot_words=hot_words, language_hints=language_hints)
    except Exception as exc:
        logger.error("[TRANSCRIBE-HANDLER] Transcription FAILED for meeting %s: %s", meeting_id, exc, exc_info=True)
        err_text = str(exc)
        if "ASR_RESPONSE_HAVE_NO_WORDS" in err_text or "HAVE_NO_WORDS" in err_text:
            err_text = (
                "No speech detected in the audio (ASR_RESPONSE_HAVE_NO_WORDS). "
                "Check that the recording has clear speech, then try again."
            )
        store.update_meeting(
            meeting_id,
            status=MeetingStatus.created,
            transcription_error=err_text,
        )
        raise

    logger.info("[TRANSCRIBE-HANDLER] Got %d segments, %d chars of text", len(result.segments), len(result.text))

    # 3b. Check for empty result
    if len(result.segments) == 0:
        error_msg = (
            "Transcription returned 0 segments. The audio file may be empty or in an unsupported format."
        )
        logger.error("[TRANSCRIBE-HANDLER] %s", error_msg)
        store.update_meeting(meeting_id, status=MeetingStatus.created, transcription_error=error_msg)
        raise ValueError(error_msg)

    update(80, "Saving transcript...")

    # 4. Save the transcription result
    store.save_transcript(meeting_id, result)
    update(80, "Normalizing sentences...")

    # 4b. Pipeline Node 0.0: clean old pipeline data, normalize sentences
    from src.meeting.pipeline import normalize_sentences

    store.delete_pipeline_data(meeting_id)

    sentences = normalize_sentences(meeting_id, result.segments)
    store.save_sentences(meeting_id, [s.model_dump() for s in sentences])

    logger.info(
        "[PIPELINE] Node 0.0 done: %d sentences for meeting %s",
        len(sentences), meeting_id,
    )
    update(90, "Matching speakers...")
    try:
        from src.speakers.service import attach_after_transcription

        extra_embs = getattr(provider, "last_segment_embeddings", None)
        attach_after_transcription(meeting_id, segment_embeddings=extra_embs)
    except Exception:
        logger.warning(
            "[TRANSCRIBE-HANDLER] Speaker match skipped for %s",
            meeting_id,
            exc_info=True,
        )
    update(95, "Updating meeting status...")

    # 5. Mark meeting as completed — Speakers gate first; user starts Summary.
    store.update_meeting(
        meeting_id,
        status=MeetingStatus.completed,
        # Keep Studio fields empty until user passes Speakers → Summarize
        tabs=None,
        blueprint=None,
        blueprint_taxonomy=None,
        summary=None,
        detail=None,
    )
    update(100, "Transcription complete")

    logger.info(
        "[TRANSCRIBE-HANDLER] Transcript ready for %s — Speakers gate, then Summary from client",
        meeting_id,
    )
    logger.info("[TRANSCRIBE-HANDLER] DONE for meeting %s", meeting_id)
    return {
        "message": "Transcription complete",
        "meeting_id": meeting_id,
        "segments": len(result.segments),
        "text_length": len(result.text),
    }


# Register at module import time
task_manager.register_handler("transcribe", transcribe_handler)
from src.tasks.handlers import meeting_summary_handler, meeting_extract_handler
task_manager.register_handler("meeting_summary", meeting_summary_handler)
task_manager.register_handler("meeting_extract", meeting_extract_handler)


async def reset_stale_processing_states():
    """Scan all meetings on startup and reset stale processing states.

    If the server crashed/restarted while a meeting was in ``summarizing`` or
    ``extracting`` state, the meeting would be permanently stuck because the
    backend task no longer exists.  This function finds such meetings and
    resets them to ``idle`` so the user can retry.

    Failures on individual meetings are logged and skipped so a single
    corrupted meta.json cannot prevent the entire app from starting.
    """
    from src.meeting.models import ProcessingState

    try:
        meetings = store.list_meetings()
    except Exception as exc:
        logger.error("Failed to list meetings during startup: %s", exc)
        return

    active_task_ids = {t.id for t in task_manager.get_all_tasks()
                       if t.status.value in ("pending", "processing")}

    reset_count = 0
    for m in meetings:
        try:
            if m.processing_state in (ProcessingState.summarizing, ProcessingState.extracting):
                # Check if there's an active task for this meeting
                has_active = any(
                    task_manager._task_args.get(tid, ("", {}))[1].get("meeting_id") == m.id
                    for tid in active_task_ids
                )
                if not has_active:
                    logger.warning(
                        "Resetting stale processing_state=%s → idle for meeting %s",
                        m.processing_state, m.id,
                    )
                    store.update_meeting(m.id, processing_state=ProcessingState.idle.value)
                    # Also reset per-tab processing states
                    if m.tabs:
                        updated_tabs: list[dict] = []
                        for t in m.tabs:
                            td = t if isinstance(t, dict) else t.model_dump()
                            if td.get("processing_state") == "generating":
                                td["processing_state"] = "idle"
                            updated_tabs.append(td)
                        store.update_meeting(m.id, tabs=updated_tabs)
                    reset_count += 1
        except Exception as exc:
            logger.warning(
                "Failed to reset stale state for meeting %s: %s", m.id, exc,
            )

    if reset_count:
        logger.info("Reset %d meeting(s) with stale processing states", reset_count)


# ---------------------------------------------------------------------------
# MeetingService
# ---------------------------------------------------------------------------

from src.prompts import (
    MEETING_BLUEPRINT_SYSTEM, MEETING_BLUEPRINT_PROMPT,
    MEETING_GENERAL_SUMMARY_PROMPT,
    MEETING_TAGGER_V3_SYSTEM, MEETING_TAGGER_V3_PROMPT,
    MEETING_SUMMARIZER_V3_SYSTEM, MEETING_SUMMARIZER_V3_PROMPT,
    MEETING_TRANSLATION_SYSTEM, MEETING_TRANSLATION_PROMPT,
    TRANSLATION_LANG_NAMES,
)


def _meeting_thinking_effort() -> str | None:
    """Meeting calls do not send reasoning_effort."""
    return None


def _thinking_for_meeting_call(kind: str) -> bool:
    """Meeting LLM calls do not use thinking (no Settings switch)."""
    return False


def _thinking_max_tokens(base: int, thinking: bool | None) -> int:
    """Scale max_tokens when thinking mode is active.

    Thinking tokens and output tokens share the same max_tokens budget.
    When thinking is enabled, the model may consume thousands of tokens
    on reasoning before producing any output.  Scale the budget up so
    the actual content is not truncated.
    """
    if not thinking:
        return base
    # At minimum give 2× the base; cap at 32768 to avoid excessive latency.
    return min(base * 2, 32768)




class MeetingService(
    MeetingGenerationMixin,
    MeetingTranslationMixin,
    MeetingAllocateMixin,
):
    """High-level meeting operations: transcription providers, summary, allocation (v3)."""

    def __init__(self) -> None:
        import threading as _threading
        # Per-(meeting,tab) active section-stream tasks.
        # Maps "meeting_id:tab_id" → (event_queue, thread).
        # Prevents duplicate LLM calls when SSE reconnects on page refresh.
        self._section_stream_lock = _threading.Lock()
        self._active_section_streams: dict[str, tuple] = {}
        # Per-meeting active blueprint-stream tasks.
        # Maps "meeting_id" → (event_queue, thread).
        self._blueprint_stream_lock = _threading.Lock()
        self._active_blueprint_streams: dict[str, tuple] = {}
        # Per-(meeting,tab,lang) active translation-stream tasks.
        # Maps "meeting_id:tab_id:LANG" → _TranslationStream (broadcast state).
        # Unlike the queue-based streams above, consumers tail a shared
        # `accumulated` buffer so reconnects replay missed tokens.
        self._translation_stream_lock = _threading.Lock()
        self._active_translation_streams: dict[str, _TranslationStream] = {}

    # -- Provider accessors -------------------------------------------------

    @staticmethod
    def _provider_matches_adapter(instance: object, adapter: str) -> bool:
        """Return False if a cached instance is clearly the wrong backend family."""
        name = type(instance).__name__.lower()
        ad = (adapter or "").lower()
        if "dashscope" in ad:
            return "dashscope" in name
        if "funasr" in ad:
            return "funasr" in name and "dashscope" not in name
        if "openrouter" in ad:
            return "openrouter" in name or "openai" in name
        if "openai" in ad:
            return "openai" in name or "compat" in name
        return True

    def get_active_file_provider(self) -> FileTranscriptionProvider | None:
        """Get the active file transcription provider from config.

        Always re-reads config so Settings “Default” switches take effect
        without restarting the process. Drops stale cache entries that do not
        match the active adapter (e.g. DashScope under builtin-local-file).
        """
        from src.providers.cache import invalidate as cache_invalidate
        from src.providers.cache import peek as cache_peek

        config = get_config()
        provider_cfg = config.transcription.active_file_provider
        if provider_cfg is None:
            provider_cfg = config.transcription.get_local_file_provider()
        # Builtin id must always resolve to FunASR ONNX factory config
        if provider_cfg.id == "builtin-local-file":
            provider_cfg = config.transcription.get_local_file_provider()
            provider_cfg = provider_cfg.model_copy(update={"is_active": True})
        cache_key = f"file_trans:{provider_cfg.id}"
        cached = cache_peek(cache_key)
        if cached is not None and not self._provider_matches_adapter(
            cached, provider_cfg.adapter
        ):
            logger.warning(
                "Dropping stale file provider cache %s (%s) for adapter %s",
                cache_key,
                type(cached).__name__,
                provider_cfg.adapter,
            )
            cache_invalidate(cache_key)
        logger.info(
            "Active file transcription provider: id=%s adapter=%s model=%s",
            provider_cfg.id,
            provider_cfg.adapter,
            provider_cfg.model,
        )
        return cached_provider(
            cache_key,
            lambda: create_file_transcription_provider(provider_cfg),
        )

    def get_active_realtime_provider(self) -> RealtimeTranscriptionProvider | None:
        """Get the active realtime transcription provider from config.

        Always re-reads config so Settings “Default” switches take effect.
        Drops stale cache entries that do not match the active adapter.
        """
        from src.providers.cache import invalidate as cache_invalidate
        from src.providers.cache import peek as cache_peek

        config = get_config()
        provider_cfg = config.transcription.active_realtime_provider
        if provider_cfg is None:
            provider_cfg = config.transcription.get_local_realtime_provider()
        if provider_cfg.id == "builtin-local-rt":
            provider_cfg = config.transcription.get_local_realtime_provider()
            provider_cfg = provider_cfg.model_copy(update={"is_active": True})
        cache_key = f"rt_trans:{provider_cfg.id}"
        cached = cache_peek(cache_key)
        if cached is not None and not self._provider_matches_adapter(
            cached, provider_cfg.adapter
        ):
            logger.warning(
                "Dropping stale realtime provider cache %s (%s) for adapter %s",
                cache_key,
                type(cached).__name__,
                provider_cfg.adapter,
            )
            cache_invalidate(cache_key)
        logger.info(
            "Active realtime transcription provider: id=%s adapter=%s model=%s",
            provider_cfg.id,
            provider_cfg.adapter,
            provider_cfg.model,
        )
        return cached_provider(
            cache_key,
            lambda: create_realtime_transcription_provider(provider_cfg),
        )

    def get_active_realtime_provider_meta(self) -> dict:
        """Lightweight active realtime provider identity (no model load)."""
        config = get_config()
        cfg = config.transcription.active_realtime_provider
        if cfg is None:
            return {"id": None, "adapter": None, "name": None, "model": None}
        return {
            "id": cfg.id,
            "adapter": cfg.adapter,
            "name": cfg.name,
            "model": cfg.model,
        }

    # -- Realtime translation (LiveTranslate) --------------------------------

    _LIVE_TRANSLATE_ADAPTER = LIVE_TRANSLATE_ADAPTER

    def _resolve_translation_provider_cfg(self):
        """Config for the LiveTranslate session, or None when not configured.

        Only an explicitly configured LiveTranslate provider (Settings →
        Live translation) enables realtime translation — no key borrowing
        from the realtime transcription provider.
        """
        from src.meeting.transcription import resolve_realtime_adapter

        config = get_config()
        providers = list(config.transcription.realtime_providers or [])
        explicit = [
            p
            for p in providers
            if resolve_realtime_adapter(p.adapter or "")
            == self._LIVE_TRANSLATE_ADAPTER
        ]
        if not explicit:
            return None
        return next((p for p in explicit if p.is_active), explicit[0])

    def get_realtime_translation_provider(self) -> RealtimeTranscriptionProvider:
        """LiveTranslate provider for meetings that request a target language.

        Raises ValueError when no LiveTranslate provider is configured.
        """
        cfg = self._resolve_translation_provider_cfg()
        if cfg is None:
            raise ValueError(
                "Realtime translation is not configured. Add a LiveTranslate "
                "provider in Settings → Live translation."
            )
        from src.meeting.transcription.dashscope_livetranslate_realtime import (
            resolve_live_translate_model,
        )
        from src.providers.cache import invalidate as cache_invalidate
        from src.providers.cache import peek as cache_peek

        expected_model = resolve_live_translate_model(cfg.model)
        key_suffix = cfg.api_key[-6:] if cfg.api_key else "nokey"
        cache_key = f"rt_trans_lt:{cfg.id}:{key_suffix}"
        cached = cache_peek(cache_key)
        if cached is not None and (
            not self._provider_matches_adapter(cached, cfg.adapter)
            or getattr(cached, "_model", None) != expected_model
        ):
            # Provider edited in Settings (model swap etc.) — the generic
            # `rt_trans:<id>` invalidation misses this key, so drop it here.
            logger.warning(
                "Dropping stale LiveTranslate provider cache %s", cache_key
            )
            cache_invalidate(cache_key)
        return cached_provider(
            cache_key,
            lambda: create_realtime_transcription_provider(cfg),
        )

    def get_realtime_translation_provider_meta(self) -> dict:
        """Lightweight LiveTranslate identity + availability (no instance)."""
        cfg = self._resolve_translation_provider_cfg()
        if cfg is None:
            return {
                "id": None,
                "adapter": self._LIVE_TRANSLATE_ADAPTER,
                "name": None,
                "model": None,
                "supports_realtime_translation": False,
            }
        from src.meeting.transcription.dashscope_livetranslate_realtime import (
            _DEFAULT_LIVE_TRANSLATE_MODEL,
        )

        return {
            "id": cfg.id,
            "adapter": self._LIVE_TRANSLATE_ADAPTER,
            "name": cfg.name,
            "model": cfg.model or _DEFAULT_LIVE_TRANSLATE_MODEL,
            "supports_realtime_translation": True,
        }

    # -- Summary generation (v3 Blueprint) ----------------------------------



    # -- Extract (v3: full-transcript, no chunk loop) -------------------------

    async def start_extract(
        self, meeting_id: str, receipts: list[dict]
    ) -> Meeting:
        """Create section tabs and return immediately.

        Section generation is driven by the SSE streaming endpoint
        (``GET /meetings/{id}/sections/{tab_id}/generate-stream``).
        """
        import re as _re
        from src.meeting.models import ProcessingState

        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")
        if not store.get_sentences(meeting_id):
            raise ValueError(
                "Meeting has no sentence data. Transcription completed?"
            )
        if meeting.processing_state != ProcessingState.idle.value:
            raise RuntimeError(
                f"Meeting is busy: {meeting.processing_state}"
            )

        store.update_meeting(
            meeting_id,
            processing_state=ProcessingState.extracting.value,
        )

        # ── Allocate tab_ids & create tabs ───────────────────
        existing_tabs: list[dict] = list(meeting.tabs or [])
        blueprint = meeting.blueprint or []
        blueprint_by_id = {b.get("blueprint_id", ""): b for b in blueprint}

        # Find max existing tab_XX number
        max_tab = 0
        for t in existing_tabs:
            tid = t["tab_id"] if isinstance(t, dict) else (
                t.tab_id if hasattr(t, "tab_id") else ""
            )
            m = _re.match(r"tab_(\d+)", tid)
            if m:
                max_tab = max(max_tab, int(m.group(1)))
        next_tab = max_tab + 1

        new_tabs: list[dict] = []
        for r in receipts:
            source = r.get("source", "custom")
            if source == "blueprint":
                bp_id = r.get("blueprint_id", "")
                if bp_id not in blueprint_by_id:
                    raise ValueError(
                        f"blueprint_id '{bp_id}' not found in blueprint"
                    )
                # Check if tab already exists (regenerate mode)
                existing_tab_id = r.get("tab_id", "")
                if existing_tab_id and any(
                    (t["tab_id"] if isinstance(t, dict) else t.tab_id) == existing_tab_id
                    for t in existing_tabs
                ):
                    continue
                tab_id = f"tab_{next_tab:02d}"
                next_tab += 1
                bp = blueprint_by_id[bp_id]
                tab_entry = {
                    "tab_id": tab_id,
                    "type": "section",
                    "blueprint_id": bp_id,
                    "name": r.get("name", bp.get("tab_name", "")),
                    "description": r.get("description", "") or bp.get("tab_description", ""),
                    "processing_state": "generating",
                    "associated_collection_id": bp.get("associated_collection_id", ""),
                    "associated_collection_name": bp.get("associated_collection_name", ""),
                    "allocated_file_id": "",
                    "is_dirty": False,
                    "md_file_path": "",
                    "payload_ref": [],
                }
            else:  # custom
                existing_tab_id = r.get("tab_id", "")
                if existing_tab_id and any(
                    (t["tab_id"] if isinstance(t, dict) else t.tab_id) == existing_tab_id
                    for t in existing_tabs
                ):
                    continue
                tab_id = f"tab_{next_tab:02d}"
                next_tab += 1
                tab_entry = {
                    "tab_id": tab_id,
                    "type": "section",
                    "blueprint_id": "",
                    "name": r.get("name", ""),
                    "description": r.get("description", ""),
                    "processing_state": "generating",
                    "associated_collection_id": "",
                    "associated_collection_name": "",
                    "allocated_file_id": "",
                    "is_dirty": False,
                    "md_file_path": "",
                    "payload_ref": [],
                }
            new_tabs.append(tab_entry)
            existing_tabs.append(tab_entry)

        # Persist tabs immediately so they're visible in UI
        if new_tabs:
            store.update_meeting(meeting_id, tabs=existing_tabs)

        return store.get_meeting(meeting_id)

    def extract_sections(
        self, meeting_id: str, receipts: list[dict]
    ) -> None:
        """v3 Extract: full-transcript Tagger + Summarizer (2 LLM calls/section).

        Each receipt: {source, name, description, blueprint_id?}.
        No chunk loop — Tagger runs once on the full transcript per section.
        """
        import json as _json
        import re as _re
        from concurrent.futures import ThreadPoolExecutor

        from src.meeting.models import ProcessingState
        from src.meeting.pipeline import build_payload
        from src.meeting.schemas import Sentence

        logger.info("[EXTRACT] Starting for meeting %s (%d receipts)", meeting_id, len(receipts))
        try:
            meeting = store.get_meeting(meeting_id)
            if meeting is None:
                return

            sentences_data = store.get_sentences(meeting_id)
            if sentences_data is None:
                raise ValueError("No sentences data")

            sentences = [
                Sentence(**s) if isinstance(s, dict) else s
                for s in sentences_data
            ]

            id_to_sentence: dict[str, Sentence] = {
                s.sentence_id: s for s in sentences
            }

            # ── Build full transcript text (shared across all sections) ──
            speaker_names: dict[str, str] = getattr(meeting, "speaker_names", None) or {}
            transcript_lines = []
            for s in sentences_data:
                sid = s.get("sentence_id", "")
                speaker = s.get("speaker", "")
                text = s.get("original_text", "")
                # Strip speaker name prefix from text (STT may include it)
                spk_name = speaker_names.get(speaker, "")
                if spk_name:
                    text = text.removeprefix(spk_name).strip()
                    text = text.removeprefix(":").strip()
                spk_part = f"[spk:{speaker}] " if speaker else ""
                transcript_lines.append(f"[{_num_id(sid)}] {spk_part}{text}")
            full_transcript = "\n".join(transcript_lines)
            logger.info("[EXTRACT] Full transcript: %d chars, %d sentences",
                        len(full_transcript), len(transcript_lines))

            # ── Resolve LLM ──────────────────────────────────────
            llm = _resolve_meeting_llm()
            think_tagger = _thinking_for_meeting_call("tagger")
            think_summarizer = _thinking_for_meeting_call("summarizer")
            think_effort = _meeting_thinking_effort()

            # ── Allocate tab_ids & create tabs ───────────────────
            existing_tabs: list[dict] = list(meeting.tabs or [])
            blueprint = meeting.blueprint or []
            blueprint_by_id = {b.get("blueprint_id", ""): b for b in blueprint}

            # Find max existing tab_XX number
            max_tab = 0
            for t in existing_tabs:
                tid = t["tab_id"] if isinstance(t, dict) else (
                    t.tab_id if hasattr(t, "tab_id") else ""
                )
                m = _re.match(r"tab_(\d+)", tid)
                if m:
                    max_tab = max(max_tab, int(m.group(1)))
            next_tab = max_tab + 1

            new_tabs: list[dict] = []
            receipt_tab_ids: list[str] = []

            for r in receipts:
                source = r.get("source", "custom")
                if source == "blueprint":
                    bp_id = r.get("blueprint_id", "")
                    if bp_id not in blueprint_by_id:
                        raise ValueError(
                            f"blueprint_id '{bp_id}' not found in blueprint"
                        )
                    # Check if tab already exists (regenerate mode)
                    existing_tab_id = r.get("tab_id", "")
                    existing = next(
                        (t for t in existing_tabs
                         if (t["tab_id"] if isinstance(t, dict) else t.tab_id) == existing_tab_id),
                        None,
                    ) if existing_tab_id else None
                    if existing:
                        receipt_tab_ids.append(existing_tab_id)
                        continue
                    tab_id = f"tab_{next_tab:02d}"
                    next_tab += 1
                    bp = blueprint_by_id[bp_id]
                    tab_entry = {
                        "tab_id": tab_id,
                        "type": "section",
                        "blueprint_id": bp_id,
                        "name": r.get("name", bp.get("tab_name", "")),
                        "description": r.get("description", "") or bp.get("tab_description", ""),
                        "processing_state": "generating",
                        "associated_collection_id": bp.get("associated_collection_id", ""),
                        "associated_collection_name": bp.get("associated_collection_name", ""),
                        "allocated_file_id": "",
                        "is_dirty": False,
                        "md_file_path": "",
                        "payload_ref": [],
                    }
                else:  # custom
                    # Check if tab already exists (regenerate mode — start_section_regenerate
                    # always passes tab_id; Add Section does not)
                    existing_tab_id = r.get("tab_id", "")
                    existing = next(
                        (t for t in existing_tabs
                         if (t["tab_id"] if isinstance(t, dict) else t.tab_id) == existing_tab_id),
                        None,
                    ) if existing_tab_id else None
                    if existing:
                        receipt_tab_ids.append(existing_tab_id)
                        continue
                    tab_id = f"tab_{next_tab:02d}"
                    next_tab += 1
                    tab_entry = {
                        "tab_id": tab_id,
                        "type": "section",
                        "blueprint_id": "",
                        "name": r.get("name", ""),
                        "description": r.get("description", ""),
                        "processing_state": "generating",
                        "associated_collection_id": "",
                        "associated_collection_name": "",
                        "allocated_file_id": "",
                        "is_dirty": False,
                        "md_file_path": "",
                        "payload_ref": [],
                    }
                # Shared: append only for NEW tabs (both branches `continue` on existing)
                new_tabs.append(tab_entry)
                existing_tabs.append(tab_entry)
                receipt_tab_ids.append(tab_id)

            # Persist tabs immediately so they're visible in UI
            if new_tabs:
                store.update_meeting(meeting_id, tabs=existing_tabs)

            # ── Build other-sections text (shared across sections) ──
            # Collect all known tab names (existing + newly created in this batch)
            _all_tab_names: set[str] = set()
            for t in existing_tabs:
                nm = t.get("name", "") if isinstance(t, dict) else getattr(t, "name", "")
                if nm:
                    _all_tab_names.add(nm)

            def _other_sections_text(exclude_tab_id: str) -> str:
                others = []
                # Only tabs that have ALREADY been extracted (have md_file_path).
                # Blueprint entries not yet created are excluded.
                for t in existing_tabs:
                    tid = t["tab_id"] if isinstance(t, dict) else t.tab_id
                    if tid == exclude_tab_id or tid == "tab_general":
                        continue
                    md = t.get("md_file_path", "") if isinstance(t, dict) else getattr(t, "md_file_path", "")
                    if not md:
                        continue  # not yet extracted — skip
                    nm = t.get("name", "") if isinstance(t, dict) else getattr(t, "name", "")
                    dc = t.get("description", "") if isinstance(t, dict) else getattr(t, "description", "")
                    others.append(f"- {nm}: {dc}" if dc else f"- {nm}")
                return "\n".join(others) if others else "(No other sections)"

            # ── Hot words ─────────────────────────────────────────
            try:
                from src.hot_words.store import hot_words_prompt_text
                hot_words_text = hot_words_prompt_text(meeting)
            except Exception:
                logger.warning("[EXTRACT] Failed to load hot words", exc_info=True)
                hot_words_text = "(None)"

            # ── Short-ID → full-ID lookup ────────────────────────
            short_to_full: dict[str, str] = {}
            for fid in id_to_sentence:
                parts = fid.rsplit("_stt_", 1)
                if len(parts) == 2:
                    short_to_full["stt_" + parts[1]] = fid

            # ── Process each section (pipelined for KV cache) ────
            topic_tagged: dict[str, set[str]] = {}
            topic_payload: dict[str, set[str]] = {}  # FOCUS + NEARBY (expanded)
            _merged_texts: dict[str, str] = {}        # merged FOCUS+NEARBY for summarizer
            topic_summaries: dict[str, dict] = {}
            topic_errors: dict[str, str] = {}

            def _run_tagger_phase(tab_id: str, receipt: dict) -> None:
                """Step A+B: Tagger → Payload.  Populates topic_tagged, topic_payload, _merged_texts."""
                section_name = receipt.get("name", "")
                section_desc = receipt.get("description", "")
                other_secs = _other_sections_text(tab_id)

                # ── Step A: Tagger (1 LLM call, full transcript) ──
                tagger_prompt = MEETING_TAGGER_V3_PROMPT.format(
                    transcript=full_transcript,
                    hot_words=hot_words_text,
                    other_sections=other_secs,
                    section_name=section_name,
                    section_description=section_desc,
                )
                tagged_short_ids: list[str] = []
                for attempt in range(3):
                    try:
                        raw = llm.generate(
                            tagger_prompt,
                            system=MEETING_TAGGER_V3_SYSTEM,
                            max_tokens=_thinking_max_tokens(16384, think_tagger),
                            temperature=0.0,
                            thinking=think_tagger,
                            thinking_effort=think_effort if think_tagger else None,
                            response_format={"type": "json_object"},
                        )
                        logger.info(
                            "[EXTRACT] Tagger raw response (first 500 chars): %s",
                            raw[:500],
                        )
                        parsed = _parse_tagger_response(raw)
                        tagged_short_ids = parsed.get("sentence_ids", [])
                        logger.info(
                            "[EXTRACT] Tagger for '%s': %d sentences tagged (prompt_len=%d)",
                            section_name, len(tagged_short_ids), len(tagger_prompt),
                        )
                        break
                    except Exception as exc:
                        logger.warning(
                            "[EXTRACT] Tagger attempt %d/3 for '%s': %s",
                            attempt + 1, section_name, exc,
                        )
                        if attempt < 2:
                            import time
                            time.sleep(2 ** attempt)
                else:
                    logger.error("[EXTRACT] Tagger FAILED for '%s'", section_name)
                    topic_errors[tab_id] = "Tagger failed"
                    topic_tagged[tab_id] = set()
                    return

                if not tagged_short_ids:
                    logger.warning("[EXTRACT] No sentences tagged for '%s'", section_name)
                    topic_tagged[tab_id] = set()
                    return

                # Convert short IDs → full IDs
                full_tagged_ids: set[str] = set()
                for sid in tagged_short_ids:
                    full = short_to_full.get(sid, sid)
                    full_tagged_ids.add(full)

                topic_tagged[tab_id] = full_tagged_ids  # FOCUS (Tagger output)

                # ── Step B: Build payload ────────────────────────
                payload_ids = build_payload(
                    full_tagged_ids, sentences, radius=3, gap_threshold=10.0,
                )
                topic_payload[tab_id] = set(payload_ids)  # FOCUS + NEARBY (expanded)
                if not payload_ids:
                    logger.warning("[EXTRACT] Empty payload for '%s'", section_name)
                    return

                # Merge FOCUS + NEARBY in chronological order with [FOCUS] prefix on anchors
                merged_lines = []
                for pid in payload_ids:
                    sent = id_to_sentence.get(pid)
                    if sent is None:
                        continue
                    spk = sent.speaker
                    line = f"[{_num_id(pid)}] [spk:{spk}] {sent.original_text}"
                    if pid in full_tagged_ids:
                        merged_lines.append(f"[FOCUS] {line}")
                    else:
                        merged_lines.append(line)

                _merged_texts[tab_id] = "\n".join(merged_lines) if merged_lines else "(No sentences)"

            def _run_summarizer_phase(tab_id: str, receipt: dict) -> None:
                """Step C: Summarizer.  Reads topic_tagged, topic_payload, _merged_texts."""
                section_name = receipt.get("name", "")
                section_desc = receipt.get("description", "")
                other_secs = _other_sections_text(tab_id)

                payload_ids = topic_payload.get(tab_id, set())
                full_tagged_ids = topic_tagged.get(tab_id, set())
                merged_text = _merged_texts.get(tab_id, "(No sentences)")

                # ── Step C: Summarizer (1 LLM call) ──────────────
                summarizer_prompt = MEETING_SUMMARIZER_V3_PROMPT.format(
                    transcript=full_transcript,
                    hot_words=hot_words_text,
                    other_sections=other_secs,
                    section_name=section_name,
                    section_description=section_desc,
                    merged_sentences=merged_text,
                )

                for attempt in range(3):
                    try:
                        raw = llm.generate(
                            summarizer_prompt,
                            system=MEETING_SUMMARIZER_V3_SYSTEM,
                            max_tokens=_thinking_max_tokens(8192, think_summarizer),
                            thinking=think_summarizer,
                            thinking_effort=think_effort if think_summarizer else None,
                        )
                        validated = _finalize_summary_markdown(raw, list(payload_ids))
                        md_path = store.save_section_md(
                            meeting_id, tab_id, validated
                        )
                        topic_summaries[tab_id] = {
                            "md": validated,
                            "md_path": md_path,
                            "payload_ids": list(payload_ids),  # FOCUS + NEARBY
                        }
                        logger.info(
                            "[EXTRACT] Summarizer for '%s': %d chars, %d focus, %d payload",
                            section_name, len(validated), len(full_tagged_ids), len(payload_ids),
                        )
                        return
                    except Exception as exc:
                        logger.warning(
                            "[EXTRACT] Summarizer attempt %d/3 for '%s': %s",
                            attempt + 1, section_name, exc,
                        )
                        if attempt < 2:
                            import time
                            time.sleep(2 ** attempt)
                else:
                    placeholder = f"# {section_name}\n\nSummary generation failed after 3 attempts."
                    md_path = store.save_section_md(
                        meeting_id, tab_id, placeholder
                    )
                    topic_summaries[tab_id] = {
                        "md": placeholder,
                        "md_path": md_path,
                        "payload_ids": list(full_tagged_ids),
                    }
                    topic_errors[tab_id] = "Summary generation failed"

            # ── Pipeline execution ───────────────────────────────
            # Three-phase pipeline designed for KV-cache reuse:
            #   Phase 1 — First section's Tagger runs alone to warm
            #     the cache (System + transcript prefix).
            #   Phase 2 — First section's Summarizer + all remaining
            #     sections (Tagger → Summarizer chain) run in parallel.
            #     Remaining Taggers hit the cached System + transcript
            #     prefix because Phase 1 has already completed.
            # This avoids the concurrent-cache-miss problem where
            # parallel Taggers simultaneously start and all pay full
            # cost for the shared prefix.
            if receipt_tab_ids:
                first_id = receipt_tab_ids[0]
                first_rec = receipts[0]
                _run_tagger_phase(first_id, first_rec)
                logger.info("[EXTRACT] Phase 1 done: first Tagger complete (cache warm)")

                with ThreadPoolExecutor(max_workers=min(len(receipt_tab_ids), 10)) as executor:
                    import concurrent.futures
                    futures: dict[str, concurrent.futures.Future] = {}

                    # First section Summarizer
                    if first_id in topic_tagged and topic_tagged[first_id]:
                        futures["summarizer|" + first_id] = executor.submit(
                            _run_summarizer_phase, first_id, first_rec,
                        )

                    # Remaining sections: Tagger → Summarizer chain
                    # (Taggers hit cache from Phase 1, Summarizers follow
                    #  immediately so per-section latency stays lowest)
                    def _run_remaining(tab_id: str, receipt: dict) -> None:
                        _run_tagger_phase(tab_id, receipt)
                        if tab_id in topic_tagged and topic_tagged[tab_id]:
                            _run_summarizer_phase(tab_id, receipt)

                    for tab_id, receipt in zip(receipt_tab_ids[1:], receipts[1:]):
                        futures["section|" + tab_id] = executor.submit(
                            _run_remaining, tab_id, receipt,
                        )

                    # ── Process results as they complete ──────────
                    # Use as_completed so each section's tab is updated
                    # immediately when its Summarizer finishes — no waiting
                    # for the slowest section.
                    import threading as _threading
                    _persist_lock = _threading.Lock()

                    def _persist_one_tab(tab_id: str) -> None:
                        """Persist a single tab's completion to the meeting store."""
                        with _persist_lock:
                            _meeting = store.get_meeting(meeting_id)
                            if _meeting is None:
                                return
                            _tabs = list(_meeting.tabs or [])
                            for _t in _tabs:
                                _tid = _t["tab_id"] if isinstance(_t, dict) else _t.tab_id
                                if _tid == tab_id:
                                    info = topic_summaries.get(tab_id)
                                    if info:
                                        _t["md_file_path"] = info["md_path"]
                                        _t["payload_ref"] = info["payload_ids"]
                                        _t["processing_state"] = "idle"
                                        _t["is_dirty"] = False
                                    else:
                                        _t["processing_state"] = "idle"
                                    break
                            store.update_meeting(meeting_id, tabs=_tabs)

                    future_map = {fut: key for key, fut in futures.items()}
                    for fut in concurrent.futures.as_completed(future_map):
                        key = future_map[fut]
                        try:
                            fut.result()
                        except Exception as exc:
                            logger.error("[EXTRACT] Future '%s' failed: %s", key, exc)
                            parts = key.split("|", 1)
                            if len(parts) == 2:
                                topic_errors[parts[1]] = str(exc)

                        # Persist this section's result immediately
                        parts = key.split("|", 1)
                        if len(parts) == 2:
                            tab_id = parts[1]
                            _persist_one_tab(tab_id)

            # ── Apply tags to sentences ─────────────────────────
            batch_tab_ids: set[str] = set(receipt_tab_ids)
            for s in sentences:
                s.section_tags = [
                    t for t in s.section_tags
                    if t not in batch_tab_ids
                ]

            # Apply tags from expanded payload (FOCUS + NEARBY)
            # Falls back to topic_tagged if payload was never built (Tagger failed)
            for tab_id in receipt_tab_ids:
                expanded = topic_payload.get(tab_id) or topic_tagged.get(tab_id, set())
                for sid in expanded:
                    sent = id_to_sentence.get(sid)
                    if sent and tab_id not in sent.section_tags:
                        sent.section_tags.append(tab_id)

            store.save_sentences(
                meeting_id,
                [s.model_dump() if hasattr(s, "model_dump") else s for s in sentences],
            )

            # ── Update tabs with results ─────────────────────────
            # Re-read meeting to get latest tabs (some may have been
            # persisted incrementally by _persist_one_tab during the
            # as_completed loop above).
            _latest = store.get_meeting(meeting_id)
            updated_tabs: list[dict] = list(_latest.tabs or []) if _latest else existing_tabs
            for t in updated_tabs:
                tid = t["tab_id"] if isinstance(t, dict) else t.tab_id
                if tid in topic_summaries:
                    info = topic_summaries[tid]
                    if isinstance(t, dict):
                        t["md_file_path"] = info["md_path"]
                        t["payload_ref"] = info["payload_ids"]
                        t["processing_state"] = "idle"
                        t["is_dirty"] = False  # regenerate resets dirty flag
                    else:
                        t.md_file_path = info["md_path"]
                        t.payload_ref = info["payload_ids"]
                        t.processing_state = "idle"
                        t.is_dirty = False
                elif tid in topic_errors:
                    if isinstance(t, dict):
                        t["processing_state"] = "idle"
                    else:
                        t.processing_state = "idle"

            # Safety net: force all tabs in this batch to idle (covers Tagger-empty,
            # unexpected exceptions, and any other edge case that skips the normal reset)
            for t in updated_tabs:
                tid = t["tab_id"] if isinstance(t, dict) else t.tab_id
                if tid in receipt_tab_ids and tid not in topic_summaries and tid not in topic_errors:
                    if isinstance(t, dict):
                        t["processing_state"] = "idle"
                    else:
                        t.processing_state = "idle"

            store.update_meeting(meeting_id, tabs=updated_tabs)
            store.update_meeting(
                meeting_id,
                processing_state=ProcessingState.idle.value,
            )
            logger.info(
                "[EXTRACT] Done for meeting %s: %d sections, %d errors",
                meeting_id, len(receipt_tab_ids), len(topic_errors),
            )

        except Exception as e:
            logger.error("[EXTRACT] Failed for meeting %s: %s", meeting_id, e, exc_info=True)
            store.update_meeting(
                meeting_id,
                processing_state=ProcessingState.idle.value,
            )

    # -- Section management (v3) --------------------------------------------

    async def delete_section(self, meeting_id: str, tab_id: str) -> Meeting:
        """v3: remove a section, its tags, md file, and allocated ingest data."""
        from src.meeting.models import ProcessingState

        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")
        if meeting.processing_state != ProcessingState.idle.value:
            raise RuntimeError(f"Meeting is busy: {meeting.processing_state}")
        if tab_id in ("tab_general", "general"):
            raise ValueError("Cannot delete the General summary")

        # Find the tab and check for allocated_file_id
        tab_meta: dict | None = None
        for t in (meeting.tabs or []):
            tid = t["tab_id"] if isinstance(t, dict) else t.tab_id
            if tid == tab_id:
                tab_meta = t if isinstance(t, dict) else t.model_dump()
                break

        # If section was ingested, clean up the allocation
        if tab_meta:
            col_id = tab_meta.get("associated_collection_id", "")
            file_id = tab_meta.get("allocated_file_id", "")
            if col_id and file_id:
                try:
                    self._delete_allocation(
                        col_id, file_id, meeting_id=meeting_id, detach_anchor=True
                    )
                    logger.info("[DELETE-SECTION] Cleaned ingest for %s/%s", meeting_id, tab_id)
                except Exception as exc:
                    logger.warning("[DELETE-SECTION] Failed to clean ingest: %s", exc)

        # Clean tags
        sentences_data = store.get_sentences(meeting_id)
        if sentences_data:
            from src.meeting.schemas import Sentence

            sentences = [
                Sentence(**s) if isinstance(s, dict) else s
                for s in sentences_data
            ]
            for s in sentences:
                if tab_id in s.section_tags:
                    s.section_tags.remove(tab_id)
            store.save_sentences(
                meeting_id,
                [
                    s.model_dump() if hasattr(s, "model_dump") else s
                    for s in sentences
                ],
            )

        # Remove from tabs and delete md file
        updated_tabs = [
            t for t in (meeting.tabs or [])
            if (t["tab_id"] if isinstance(t, dict) else t.tab_id) != tab_id
        ]
        store.update_meeting(meeting_id, tabs=updated_tabs)

        # Rebuild meeting-level tracking arrays from remaining tabs
        alloc_cols, alloc_fids = _rebuild_allocation_arrays(updated_tabs)
        store.update_meeting(
            meeting_id,
            allocated_collections=alloc_cols,
            allocated_file_ids=alloc_fids,
        )

        md_path = store.section_md_path(meeting_id, tab_id)
        if md_path.exists():
            md_path.unlink()
            logger.info("[DELETE-SECTION] Removed md for %s/%s", meeting_id, tab_id)

        return store.get_meeting(meeting_id)

    async def start_section_regenerate(
        self, meeting_id: str, tab_id: str
    ) -> Meeting:
        """Prepare a section for regeneration (v3).

        Cleans old tags, sets processing_state, then the frontend
        connects to the SSE streaming endpoint to drive generation.
        """
        from src.meeting.models import ProcessingState

        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")
        if meeting.processing_state != ProcessingState.idle.value:
            raise RuntimeError(f"Meeting is busy: {meeting.processing_state}")
        if tab_id in ("tab_general", "general"):
            raise ValueError("Re-summarize the meeting to refresh General")

        # Find this tab's metadata
        tab_meta: dict | None = None
        for t in (meeting.tabs or []):
            tid = t["tab_id"] if isinstance(t, dict) else t.tab_id
            if tid == tab_id:
                tab_meta = t if isinstance(t, dict) else t.model_dump()
                break

        if tab_meta is None:
            raise ValueError(f"Tab '{tab_id}' not found")

        # Clean old tags for this section
        sentences_data = store.get_sentences(meeting_id)
        if sentences_data:
            from src.meeting.schemas import Sentence

            sentences = [
                Sentence(**s) if isinstance(s, dict) else s
                for s in sentences_data
            ]
            for s in sentences:
                if tab_id in s.section_tags:
                    s.section_tags.remove(tab_id)
            store.save_sentences(
                meeting_id,
                [
                    s.model_dump() if hasattr(s, "model_dump") else s
                    for s in sentences
                ],
            )

        # Set tab processing_state to "generating"
        updated_tabs: list[dict] = []
        for t in (meeting.tabs or []):
            td = t if isinstance(t, dict) else t.model_dump()
            if td.get("tab_id") == tab_id:
                td["processing_state"] = "generating"
            updated_tabs.append(td)
        store.update_meeting(meeting_id, tabs=updated_tabs)

        store.update_meeting(
            meeting_id,
            processing_state=ProcessingState.extracting.value,
        )
        return store.get_meeting(meeting_id)

    def schedule_section_todo_extract(
        self,
        meeting_id: str,
        tab_id: str,
        *,
        use_llm: bool = True,
    ) -> None:
        """Run todo-candidate extract in a daemon thread (non-blocking)."""

        def _run() -> None:
            try:
                items = self.extract_section_todo_candidates(
                    meeting_id,
                    tab_id,
                    persist=True,
                    use_llm=use_llm,
                )
                logger.info(
                    "Background todo extract done meeting=%s tab=%s count=%d",
                    meeting_id,
                    tab_id,
                    len(items),
                )
            except Exception:
                logger.warning(
                    "Background todo extract failed meeting=%s tab=%s",
                    meeting_id,
                    tab_id,
                    exc_info=True,
                )

        threading.Thread(
            target=_run,
            name=f"todo-extract-{meeting_id[:8]}-{tab_id}",
            daemon=True,
        ).start()

    def extract_section_todo_candidates(
        self,
        meeting_id: str,
        tab_id: str,
        *,
        persist: bool = True,
        use_llm: bool = True,
        enrich_ddl: bool | None = None,
    ) -> list[dict]:
        """Extract todos from section MD (ingest snapshot + one LLM call).

        Snapshot matches library allocate: resolve speaker display names and
        strip stt_ref tags. LLM returns short title, body, priority, ddl.
        Falls back to deterministic ``## Todo`` parse if LLM is unavailable.

        Used when:
        - allocate/re-ingest (background thread, non-blocking for allocate)
        - GET candidates when none stored (legacy allocate)
        """
        # Back-compat: old callers passed enrich_ddl=
        if enrich_ddl is not None:
            use_llm = bool(enrich_ddl) or use_llm

        lock = _todo_extract_lock(meeting_id, tab_id)
        with lock:
            return self._extract_section_todo_candidates_locked(
                meeting_id,
                tab_id,
                persist=persist,
                use_llm=use_llm,
            )

    def _extract_section_todo_candidates_locked(
        self,
        meeting_id: str,
        tab_id: str,
        *,
        persist: bool,
        use_llm: bool,
    ) -> list[dict]:
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")

        tab_meta: dict | None = None
        for t in (meeting.tabs or []):
            td = t if isinstance(t, dict) else t.model_dump()
            if td.get("tab_id") == tab_id:
                tab_meta = td
                break
        if tab_meta is None:
            raise ValueError(f"Tab '{tab_id}' not found")

        raw_md = store.get_section_md(meeting_id, tab_id) or ""
        speaker_names: dict[str, str] = (
            getattr(meeting, "speaker_names", None) or {}
        )

        # Same LLM as section summary / blueprint: Settings → Enrichment → Meeting model
        # (falls back to default LLM when meeting_model is empty).
        llm = None
        if use_llm:
            try:
                llm = _resolve_meeting_llm()
            except Exception:
                logger.debug(
                    "No meeting LLM for todo extract; will fall back to parse",
                    exc_info=True,
                )
                llm = None

        from src.meeting.todo_candidates import extract_todo_candidates

        candidates = extract_todo_candidates(
            raw_md,
            speaker_names=speaker_names,
            meeting_created_at=getattr(meeting, "created_at", None),
            llm=llm,
            use_llm=use_llm and llm is not None,
        )

        # Preserve prior created_todo_id / ddl when candidate_id matches
        prev_by_id: dict[str, dict] = {}
        for old in tab_meta.get("todo_candidates") or []:
            if not isinstance(old, dict):
                continue
            cid = str(old.get("candidate_id") or "").strip()
            if cid:
                prev_by_id[cid] = old
        for c in candidates:
            cid = str(c.get("candidate_id") or "").strip()
            prev = prev_by_id.get(cid)
            if not prev:
                continue
            if prev.get("created_todo_id"):
                c["created_todo_id"] = prev["created_todo_id"]
            if prev.get("ddl") and not c.get("ddl"):
                c["ddl"] = prev["ddl"]

        if persist:
            # Re-read meeting so concurrent tab field updates are not wiped
            latest = store.get_meeting(meeting_id) or meeting
            updated_tabs: list[dict] = []
            for t in (latest.tabs or []):
                td = t if isinstance(t, dict) else t.model_dump()
                if td.get("tab_id") == tab_id:
                    td["todo_candidates"] = candidates
                updated_tabs.append(td)
            store.update_meeting(meeting_id, tabs=updated_tabs)

        return candidates

    def mark_todo_candidates_created(
        self,
        meeting_id: str,
        items: list[dict],
    ) -> Meeting:
        """Write ``created_todo_id`` onto matching section todo candidates.

        *items*: ``{tab_id, candidate_id, todo_id}`` (tab_id optional when unique).
        Used after confirm-create so the same candidate is not offered again.
        """
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")

        # (tab_id|*, candidate_id) → todo_id
        by_key: dict[tuple[str, str], str] = {}
        for it in items or []:
            if not isinstance(it, dict):
                continue
            cid = str(it.get("candidate_id") or "").strip()
            tid = str(it.get("todo_id") or "").strip()
            tab = str(it.get("tab_id") or "").strip()
            if not cid or not tid:
                continue
            by_key[(tab or "*", cid)] = tid

        if not by_key:
            return meeting

        updated_tabs: list[dict] = []
        for t in (meeting.tabs or []):
            td = t if isinstance(t, dict) else t.model_dump()
            tab_id = str(td.get("tab_id") or "")
            cands = list(td.get("todo_candidates") or [])
            changed = False
            new_cands: list[dict] = []
            for c in cands:
                if not isinstance(c, dict):
                    new_cands.append(c)
                    continue
                c = dict(c)
                cid = str(c.get("candidate_id") or "").strip()
                todo_id = (
                    by_key.get((tab_id, cid))
                    or by_key.get(("*", cid))
                )
                if todo_id:
                    c["created_todo_id"] = todo_id
                    changed = True
                new_cands.append(c)
            if changed:
                td["todo_candidates"] = new_cands
            updated_tabs.append(td)

        store.update_meeting(meeting_id, tabs=updated_tabs)
        updated = store.get_meeting(meeting_id)
        assert updated is not None
        return updated


    async def generate_section_description(
        self, meeting_id: str, section_name: str,
    ) -> dict:
        """Generate a section description via LLM from section name + General Summary."""
        import json as _json

        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")
        general_summary = store.get_section_md(meeting_id, "tab_general") or ""
        if not general_summary.strip():
            raise ValueError("No General Summary available. Generate it first.")

        # Build existing sections context: only tabs that have been extracted
        existing_lines: list[str] = []
        for t in (meeting.tabs or []):
            td = t if isinstance(t, dict) else t.model_dump()
            if td.get("tab_id") == "tab_general":
                continue
            nm = td.get("name", "")
            dc = td.get("description", "")
            if nm:
                existing_lines.append(f"- {nm}: {dc}" if dc else f"- {nm}")
        existing_sections = "\n".join(existing_lines) if existing_lines else "(No other sections yet)"

        # ── Taxonomy ───────────────────────────────────────────
        taxonomy_text = "(Unknown)"
        bt = meeting.blueprint_taxonomy
        if bt and isinstance(bt, dict):
            dim = bt.get("dimension", "")
            expl = bt.get("explanation", "")
            if dim:
                taxonomy_text = f"Dimension: {dim}. {expl}" if expl else f"Dimension: {dim}."

        # ── Hot words ─────────────────────────────────────────
        try:
            from src.hot_words.store import hot_words_prompt_text
            hot_words_text = hot_words_prompt_text(meeting)
        except Exception:
            logger.warning("[SECTION-DESC] Failed to load hot words", exc_info=True)
            hot_words_text = "(None)"

        from src.prompts import SECTION_DESC_PROMPT

        prompt = SECTION_DESC_PROMPT.format(
            section_name=section_name,
            general_summary=general_summary,
            hot_words=hot_words_text,
            taxonomy=taxonomy_text,
            existing_sections=existing_sections,
        )
        llm = _resolve_meeting_llm()
        raw = llm.generate(
            prompt,
            max_tokens=1024,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        try:
            data = _json.loads(raw.strip())
            if isinstance(data, dict) and "found" in data:
                return data
        except _json.JSONDecodeError:
            pass
        return {"found": False}


def _parse_json_response(raw: str, expected_keys: list[str] | None = None) -> dict:
    """Parse a JSON object from LLM response, robust to thinking-mode preamble.

    Scans for every '{' position and tries JSONDecoder.raw_decode() at each,
    accepting the first valid dict that contains at least one expected key
    (if expected_keys is provided).  This is safe even when the preamble
    contains brace characters because raw_decode() validates the full JSON
    structure before returning.
    """
    import json as _json

    raw_stripped = raw.strip()
    decoder = _json.JSONDecoder()

    # Collect all '{' positions
    positions = [i for i, c in enumerate(raw_stripped) if c == "{"]

    for idx in positions:
        try:
            data, _ = decoder.raw_decode(raw_stripped[idx:])
            if isinstance(data, dict):
                if expected_keys is None or any(k in data for k in expected_keys):
                    return data
        except (_json.JSONDecodeError, ValueError):
            continue

    logger.warning(
        "[JSON] Failed to parse response (raw=%d chars, expected=%s)",
        len(raw_stripped),
        expected_keys,
    )
    return {}



class _HostAttr:
    """Resolve a service.py global at call time so unittest patches still apply."""

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)

    def _target(self):
        import src.meeting.service as host
        return getattr(host, self._name)

    def __call__(self, *args, **kwargs):
        return self._target()(*args, **kwargs)

    def __getattr__(self, item):
        return getattr(self._target(), item)

    def __bool__(self):
        return bool(self._target())


def _bind_meeting_mixins() -> None:
    """Share service globals with mixins so test patches on this module still apply."""
    import src.meeting.allocate as _allocate
    import src.meeting.generation as _generation
    import src.meeting.translation as _translation

    live = (
        "store",
        "logger",
        "services",
        "get_config",
        "_resolve_meeting_llm",
        "_notes_for_meeting_llm",
        "_thinking_max_tokens",
        "_thinking_for_meeting_call",
        "_meeting_thinking_effort",
        "_parse_json_response",
        "_detect_embedding_dim",
        "_rebuild_allocation_arrays",
        "_files_dir",
        "_num_id",
        "_clean_refs",
        "_finalize_summary_markdown",
        "_normalize_brackets",
        "_normalize_refs",
        "_parse_tagger_response",
    )
    frozen = {
        "GenerationState": GenerationState,
        "Meeting": Meeting,
        "MeetingStatus": MeetingStatus,
        "ProcessingState": ProcessingState,
        "TranscriptionResult": TranscriptionResult,
        "uuid": uuid,
        "json": json,
        "asyncio": asyncio,
        "threading": threading,
        "shutil": shutil,
        "Path": Path,
        "datetime": datetime,
        "timezone": timezone,
        "task_manager": task_manager,
        "Task": Task,
        "TaskStatus": TaskStatus,
    }
    for mod in (_allocate, _generation, _translation):
        vars(mod).update(frozen)
        for name in live:
            setattr(mod, name, _HostAttr(name))


_bind_meeting_mixins()

# Module-level singleton
meeting_service = MeetingService()
