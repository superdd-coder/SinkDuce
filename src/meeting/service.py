"""Meeting service -- transcription task handler, summary generation, and collection allocation."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from src.config import get_config
from src.meeting import store
from src.meeting.models import Meeting, MeetingStatus, TranscriptionResult
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

logger = logging.getLogger(__name__)


def _resolve_meeting_llm() -> "LLMProvider":
    """Resolve the LLM for meeting summary (blueprint/tagger/summarizer).

    Priority: meeting_model config → default LLM provider.
    """
    from src.config import get_config
    from src.providers.llm import create_llm_for_provider

    cfg = get_config()
    meeting_model = cfg.enrichment.meeting_model
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

COLLECTIONS_DIR = Path("data").resolve() / "collections"

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


def _num_to_stt(num: int | str) -> str:
    """Convert numeric ID back to stt_XXXX format.

    1 → 'stt_0001', 123 → 'stt_0123'
    """
    return f"stt_{int(num):04d}"


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
    store.update_meeting(meeting_id, status=MeetingStatus.transcribing, transcription_error=None)
    update(5, "Loading transcription provider...")

    # 2. Get the active file transcription provider
    config = get_config()
    provider_cfg = config.transcription.active_file_provider
    if provider_cfg is None:
        provider_cfg = config.transcription.get_local_file_provider()

    # Auto-load the provider if its model is downloaded but not yet loaded.
    # If the model is NOT downloaded, raise a clear error — do NOT auto-download.
    if provider_cfg and provider_cfg.adapter.startswith("funasr_local"):
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

    # Load hot words if meeting has a library assigned
    hot_words = None
    language_hints = kwargs.get("language_hints")  # user-selected from frontend
    # "auto" means auto-detect — strip it so the provider doesn't receive it
    if language_hints:
        language_hints = [h for h in language_hints if h != "auto"] or None
    if meeting.hot_words_library_id:
        from src.hot_words.store import get_library
        lib = get_library(meeting.hot_words_library_id)
        if lib and lib.words:
            hot_words = [w.model_dump() for w in lib.words]
            logger.info("[TRANSCRIBE-HANDLER] Loaded %d hot words from library %s", len(hot_words), lib.name)

    if language_hints:
        logger.info("[TRANSCRIBE-HANDLER] Using language_hints=%s", language_hints)

    try:
        result: TranscriptionResult = await provider.transcribe(source, hot_words=hot_words, language_hints=language_hints)
    except Exception as exc:
        logger.error("[TRANSCRIBE-HANDLER] Transcription FAILED for meeting %s: %s", meeting_id, exc, exc_info=True)
        store.update_meeting(meeting_id, status=MeetingStatus.created, transcription_error=str(exc))
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
    update(95, "Updating meeting status...")

    # 5. Mark meeting as completed
    store.update_meeting(meeting_id, status=MeetingStatus.completed)
    update(95, "Starting summary generation...")

    # 6. Auto-trigger summary generation (same path as the Summarize button)
    try:
        task_manager.create_task(
            filename=f"meeting_summary:{meeting_id}",
            task_type="meeting_summary",
            meeting_id=meeting_id,
        )
        logger.info("[TRANSCRIBE-HANDLER] Auto-triggered meeting_summary for %s", meeting_id)
    except Exception as e:
        logger.warning("[TRANSCRIBE-HANDLER] Failed to auto-trigger summary (non-fatal): %s", e)

    update(100, "Transcription complete")

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
)


class MeetingService:
    """High-level meeting operations: transcription providers, summary, allocation (v3)."""

    def __init__(self) -> None:
        pass

    # -- Provider accessors -------------------------------------------------

    def get_active_file_provider(self) -> FileTranscriptionProvider | None:
        """Get the active file transcription provider from config."""
        config = get_config()
        provider_cfg = config.transcription.active_file_provider
        if provider_cfg is None:
            provider_cfg = config.transcription.get_local_file_provider()
        return cached_provider(
            f"file_trans:{provider_cfg.id}",
            lambda: create_file_transcription_provider(provider_cfg),
        )

    def get_active_realtime_provider(self) -> RealtimeTranscriptionProvider | None:
        """Get the active realtime transcription provider from config."""
        config = get_config()
        provider_cfg = config.transcription.active_realtime_provider
        if provider_cfg is None:
            provider_cfg = config.transcription.get_local_realtime_provider()
        return cached_provider(
            f"rt_trans:{provider_cfg.id}",
            lambda: create_realtime_transcription_provider(provider_cfg),
        )

    # -- Summary generation (v3 Blueprint) ----------------------------------

    def generate_blueprint_stream(self, meeting_id: str):
        """Stream blueprint generation as SSE event dicts.

        Two-pass pipeline with per-phase generation-state tracking:

        Pass 1 — General Summary (streaming):
          Uses generate_stream_tagged() to yield thinking and content
          tokens in real-time.  Thinking tokens are emitted as
          ``{"event": "thinking", ...}`` so the frontend can show them
          in a collapsible section that auto-hides when real content
          begins.  Generation states transition:
          idle → prefilling → streaming → idle.

        Pass 2 — Blueprint Decomposition (non-streaming):
          Runs after Pass 1 completes.  Emits ``blueprint_start`` and
          ``blueprint_done`` events.  State: idle → prefilling → idle.

        Yields dicts::

          {"event": "state", "data": {"summary": "prefilling"}}
          {"event": "thinking", "data": "..."}
          {"event": "token", "data": "## Summary\\n..."}
          {"event": "state", "data": {"summary": "streaming"}}
          {"event": "summary_done", "data": {"title": "...", "general_md": "..."}}
          {"event": "state", "data": {"blueprint": "prefilling"}}
          {"event": "blueprint_done", "data": {"taxonomy": {...}, "blueprint": [...]}}
          {"event": "state", "data": {"blueprint": "idle"}}
          {"event": "error", "data": {"message": "..."}}
        """
        from src.meeting.models import ProcessingState, GenerationState

        logger.info("[STREAM] Starting blueprint stream for meeting %s", meeting_id)

        # ── Build context (shared with _do_blueprint_summary) ──────
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            yield {"event": "error", "data": {"message": "Meeting not found"}}
            return

        # Transcript
        sentences_data = store.get_sentences(meeting_id)
        if sentences_data:
            speaker_names: dict[str, str] = getattr(meeting, "speaker_names", None) or {}
            lines = []
            for s in sentences_data:
                sid = s.get("sentence_id", "")
                speaker = s.get("speaker", "")
                text = s.get("original_text", "")
                spk_name = speaker_names.get(speaker, "")
                if spk_name:
                    text = text.removeprefix(spk_name).strip()
                    text = text.removeprefix(":").strip()
                spk_part = f"[spk:{speaker}] " if speaker else ""
                lines.append(f"[{_num_id(sid)}] {spk_part}{text}")
            transcript_text = "\n".join(lines)
        else:
            transcript_result = store.get_transcript(meeting_id)
            transcript_text = (
                transcript_result.text if transcript_result else "(No transcript available)"
            )

        notes_text = store.get_notes(meeting_id) or "(No notes)"

        # Collection catalog
        alias_to_real: dict[str, str] = {}
        real_to_alias: dict[str, str] = {}
        collection_catalog = "No existing collections."
        try:
            from src.rag.summary_manager import SummaryManager
            sm = SummaryManager(db=services.db, vector_size=_detect_embedding_dim())
            sm.ensure_collection()
            project_descs = sm.get_all_project_descriptions()
            if project_descs:
                from src.collections.store import get_collection_meta, list_collections_meta
                existing_ids = {c["id"] for c in list_collections_meta()}
                catalog_lines = []
                stale_ids: list[str] = []
                alias_idx = 0
                for pd in project_descs:
                    cid = pd.get("collection_id", "")
                    cnt = pd.get("content", "")
                    if cid not in existing_ids:
                        stale_ids.append(cid)
                        continue
                    meta = get_collection_meta(cid)
                    display_name = meta.get("name", cid) if meta else cid
                    alias_idx += 1
                    alias = f"col_{alias_idx}"
                    alias_to_real[alias] = cid
                    real_to_alias[cid] = alias
                    catalog_lines.append(
                        f"- id: {alias}  |  name: {display_name}  |  description: {cnt}"
                    )
                for stale_cid in stale_ids:
                    try:
                        sm.delete_project_description(stale_cid)
                    except Exception:
                        pass
                catalog_lines.sort(key=lambda ln: ln)
                collection_catalog = "\n".join(catalog_lines)
        except Exception as e:
            logger.warning("[STREAM] Failed to build catalog: %s", e)

        # Hot words
        hot_words_text = "(None)"
        if meeting.hot_words_library_id:
            try:
                from src.hot_words.store import get_library
                lib = get_library(meeting.hot_words_library_id)
                if lib and lib.words:
                    hot_words_text = ", ".join(w.text for w in lib.words)
            except Exception:
                logger.warning("[STREAM] Failed to load hot words", exc_info=True)

        llm = _resolve_meeting_llm()
        meeting_thinking = get_config().enrichment.meeting_thinking

        # ── Build prompts ────────────────────────────────────
        summary_prompt = MEETING_GENERAL_SUMMARY_PROMPT.format(
            transcript=transcript_text,
            notes=notes_text,
            hot_words=hot_words_text,
        )
        blueprint_prompt = MEETING_BLUEPRINT_PROMPT.format(
            transcript=transcript_text,
            notes=notes_text,
            hot_words=hot_words_text,
            collection_catalog=collection_catalog,
        )
        all_sids = [s.get("sentence_id", "") for s in (sentences_data or [])]

        logger.info("[STREAM] Call 1 prompt: %d chars, Call 2 prompt: %d chars",
                    len(summary_prompt), len(blueprint_prompt))

        # ── Set initial state ─────────────────────────────────
        store.update_meeting(
            meeting_id,
            processing_state=ProcessingState.summarizing.value,
            summary_gen_state=GenerationState.prefilling.value,
        )

        # ── Helper: parse blueprint LLM response ──────────────
        def _process_raw_blueprint(raw_bp: str) -> dict:
            bp_data = _parse_json_response(raw_bp, ["taxonomy", "blueprint"])
            parsed_title = bp_data.get("title", "")
            blueprint_raw = bp_data.get("blueprint", [])
            taxonomy = bp_data.get("taxonomy", None)
            if not isinstance(blueprint_raw, list):
                blueprint_raw = []
            blueprint_raw.sort(key=lambda item: item.get("tab_name", ""))
            bp_list: list[dict] = []
            for idx, item in enumerate(blueprint_raw):
                bp_name = item.get("tab_name", f"Section {idx + 1}")
                if bp_name.strip().lower() == "other":
                    continue
                real_cid = alias_to_real.get(item.get("associated_collection_id", ""), "")
                bp_list.append({
                    "blueprint_id": f"bp_{idx + 1:02d}",
                    "tab_name": bp_name,
                    "tab_description": item.get("section_description", "")[:600],
                    "associated_collection_id": real_cid,
                    "associated_collection_name": item.get("associated_collection_name", ""),
                })
            return {
                "bp_data": bp_data,
                "blueprint": bp_list,
                "taxonomy": taxonomy,
                "title": parsed_title,
            }

        # ── Background thread: runs LLM, persists results ─────
        import queue
        import threading
        import concurrent.futures

        event_queue: queue.Queue = queue.Queue()

        def _run_llm() -> None:
            """All LLM interaction + persistence.  Survives SSE disconnect."""
            bp_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                # ── Pass 1: General Summary ────────────────
                event_queue.put(("state", {"summary": "prefilling"}))
                logger.info("[STREAM-THREAD] Call 1 starting for meeting %s", meeting_id)

                accumulated = ""
                in_thinking = True
                bp_future: concurrent.futures.Future | None = None
                _bp_emitted = False
                _bp_cache: dict | None = None

                for text, is_thinking in llm.generate_stream_tagged(
                    summary_prompt,
                    system=MEETING_BLUEPRINT_SYSTEM,
                    max_tokens=32768,
                    temperature=0.0,
                    thinking=meeting_thinking,
                ):
                    if is_thinking:
                        event_queue.put(("thinking", text))
                    else:
                        if in_thinking:
                            in_thinking = False
                            store.update_meeting(
                                meeting_id,
                                summary_gen_state=GenerationState.streaming.value,
                                blueprint_gen_state=GenerationState.prefilling.value,
                            )
                            event_queue.put(("state", {"summary": "streaming", "blueprint": "prefilling"}))
                            logger.info("[STREAM-THREAD] Call 1 streaming — launching Call 2 in parallel")
                            bp_future = bp_executor.submit(
                                llm.generate,
                                prompt=blueprint_prompt,
                                system=MEETING_BLUEPRINT_SYSTEM,
                                temperature=0.0,
                                max_tokens=8192,
                                response_format={"type": "json_object"},
                                thinking=meeting_thinking,
                            )
                        accumulated += text
                        event_queue.put(("token", text))

                    # Early-completion check
                    if bp_future is not None and bp_future.done() and not _bp_emitted:
                        raw_bp = bp_future.result()
                        parsed = _process_raw_blueprint(raw_bp)
                        bp_count = len(parsed.get("blueprint", []))
                        if bp_count == 0:
                            logger.warning(
                                "[STREAM-THREAD] Call 2 early — EMPTY blueprint "
                                "(raw=%d chars, first 400: %.400r)",
                                len(raw_bp), raw_bp[:400],
                            )
                        else:
                            logger.info(
                                "[STREAM-THREAD] Call 2 finished early — %d blueprint items",
                                bp_count,
                            )
                        _bp_cache = parsed
                        _bp_emitted = True
                        store.update_meeting(
                            meeting_id,
                            blueprint=parsed["blueprint"],
                            blueprint_taxonomy=parsed["taxonomy"],
                            blueprint_gen_state=GenerationState.idle.value,
                        )
                        event_queue.put(("blueprint_done", {
                            "taxonomy": parsed["taxonomy"],
                            "blueprint": parsed["blueprint"],
                        }))
                        event_queue.put(("state", {"blueprint": "idle"}))

                logger.info("[STREAM-THREAD] Call 1 done: %d chars", len(accumulated))
                general_md = accumulated.strip()
                general_md = _clean_refs(
                    _normalize_refs(_normalize_brackets(general_md)), all_sids,
                )

                # Persist Call 1 result (content lives in tab_general.md, not meta.json)
                general_tab_path = store.save_section_md(meeting_id, "tab_general", general_md)
                store.update_meeting(
                    meeting_id,
                    summary_gen_state=GenerationState.idle.value,
                )
                event_queue.put(("summary_done", {"general_md": general_md}))
                event_queue.put(("state", {"summary": "idle"}))

                # ── Call 2 result ───────────────────────────
                if not _bp_emitted:
                    try:
                        if bp_future is not None:
                            logger.info("[STREAM-THREAD] Waiting for Call 2...")
                            raw_blueprint = bp_future.result()
                        else:
                            raw_blueprint = llm.generate(
                                blueprint_prompt,
                                system=MEETING_BLUEPRINT_SYSTEM,
                                max_tokens=8192,
                                temperature=0.0,
                                thinking=meeting_thinking,
                                response_format={"type": "json_object"},
                            )
                        logger.info("[STREAM-THREAD] Call 2 done: %d chars", len(raw_blueprint))
                        parsed = _process_raw_blueprint(raw_blueprint)
                        if not parsed.get("blueprint"):
                            logger.warning(
                                "[STREAM-THREAD] Call 2 produced empty blueprint "
                                "(raw=%d chars, first 300: %.300r)",
                                len(raw_blueprint), raw_blueprint[:300],
                            )
                    except Exception as bp_exc:
                        logger.exception(
                            "[STREAM-THREAD] Call 2 FAILED (summary preserved): %s", bp_exc,
                        )
                        parsed = {"bp_data": {}, "blueprint": [], "taxonomy": None, "title": ""}
                else:
                    parsed = _bp_cache or {}
                    logger.info("[STREAM-THREAD] Call 2 was emitted early — reusing cache")

                bp_data = parsed.get("bp_data", {})
                parsed_title = parsed.get("title", "")
                blueprint = parsed.get("blueprint", [])
                taxonomy = parsed.get("taxonomy", None)

                # ── Build tabs ──────────────────────────────
                old_tabs: list[dict] = list(meeting.tabs or [])
                is_re_summarize = any(
                    (t["tab_id"] if isinstance(t, dict) else t.tab_id) != "tab_general"
                    for t in old_tabs
                )
                tabs: list[dict] = [{
                    "tab_id": "tab_general", "type": "general",
                    "blueprint_id": "", "name": "General", "description": "",
                    "processing_state": "idle",
                    "associated_collection_id": "", "associated_collection_name": "",
                    "allocated_file_id": "", "is_dirty": False,
                    "md_file_path": general_tab_path, "payload_ref": [],
                }]
                old_section_tabs: list[dict] = []
                for t in old_tabs:
                    td = t if isinstance(t, dict) else (
                        t.model_dump() if hasattr(t, "model_dump") else dict(t)
                    )
                    tid = td.get("tab_id", "")
                    if tid == "tab_general":
                        continue
                    if is_re_summarize:
                        td["blueprint_id"] = ""
                    td.setdefault("blueprint_id", "")
                    td.setdefault("description", td.get("description", ""))
                    td.setdefault("processing_state", "idle")
                    td.setdefault("allocated_file_id", "")
                    td.setdefault("is_dirty", False)
                    old_section_tabs.append(td)
                matched_old: set[int] = set()
                for bp_entry in blueprint:
                    bp_cid = bp_entry["associated_collection_id"]
                    for ot in old_section_tabs:
                        if id(ot) in matched_old:
                            continue
                        if bp_cid and ot.get("associated_collection_id") == bp_cid:
                            ot["blueprint_id"] = bp_entry["blueprint_id"]
                            ot["name"] = bp_entry["tab_name"]
                            ot["description"] = bp_entry["tab_description"]
                            matched_old.add(id(ot))
                            break
                tabs.extend(old_section_tabs)

                # ── Final persist ───────────────────────────
                update_fields: dict = dict(
                    blueprint=blueprint,
                    blueprint_taxonomy=taxonomy,
                    tabs=tabs,
                    processing_state=ProcessingState.idle.value,
                    blueprint_gen_state=GenerationState.idle.value,
                )
                if parsed_title:
                    update_fields["title"] = parsed_title
                store.update_meeting(meeting_id, **update_fields)

                if not _bp_emitted:
                    event_queue.put(("blueprint_done", {
                        "taxonomy": taxonomy,
                        "blueprint": blueprint,
                    }))
                    event_queue.put(("state", {"blueprint": "idle"}))
                logger.info("[STREAM-THREAD] Complete for meeting %s", meeting_id)

            except Exception as e:
                logger.exception("[STREAM-THREAD] Failed for meeting %s: %s", meeting_id, e)
                store.update_meeting(
                    meeting_id,
                    processing_state=ProcessingState.idle.value,
                    summary_gen_state=GenerationState.idle.value,
                    blueprint_gen_state=GenerationState.idle.value,
                )
                event_queue.put(("error", {"message": str(e)}))
            finally:
                bp_executor.shutdown(wait=False)
                event_queue.put(("done", None))

        # ── Launch thread ────────────────────────────────────────
        thread = threading.Thread(target=_run_llm, daemon=True)
        thread.start()

        # ── Read queue → SSE events ──────────────────────────────
        try:
            while True:
                try:
                    event_type, event_data = event_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if event_type == "done":
                    break
                yield {"event": event_type, "data": event_data}
        except GeneratorExit:
            logger.info(
                "[STREAM] SSE client disconnected for meeting %s — LLM continues in background",
                meeting_id,
            )

    def _do_blueprint_summary(self, meeting_id: str) -> None:
        """Node 0.3 v3: generate General summary + decomposition blueprint.

        Single-pass LLM call → {title, general_md_content, blueprint[]}.
        Blueprint IDs are code-assigned (bp_01, bp_02, ...).
        On re-summarize, existing section tabs keep their tab_id but
        clear their blueprint_id linkage.
        """
        from src.meeting.models import ProcessingState

        logger.info("[BLUEPRINT] Starting for meeting %s", meeting_id)
        try:
            meeting = store.get_meeting(meeting_id)
            if meeting is None:
                return

            # ── Build transcript: [stt_XXXX] [spk:ID] {text} ──────
            sentences_data = store.get_sentences(meeting_id)

            if sentences_data:
                # Resolve speaker names to strip them from original_text
                speaker_names: dict[str, str] = getattr(meeting, "speaker_names", None) or {}
                lines = []
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
                    lines.append(f"[{_num_id(sid)}] {spk_part}{text}")
                transcript_text = "\n".join(lines)
            else:
                transcript_result = store.get_transcript(meeting_id)
                transcript_text = (
                    transcript_result.text
                    if transcript_result
                    else "(No transcript available)"
                )

            notes_text = store.get_notes(meeting_id) or "(No notes)"
            logger.info(
                "[BLUEPRINT] Transcript: %d chars, Notes: %d chars",
                len(transcript_text),
                len(notes_text),
            )

            # ── Build collection catalog ──────────────────────────
            # Alias real collection IDs → col_1, col_2, ... so the LLM
            # never sees UUIDs that it might hallucinate or truncate.
            alias_to_real: dict[str, str] = {}
            real_to_alias: dict[str, str] = {}
            collection_catalog = "No existing collections."
            try:
                from src.rag.summary_manager import SummaryManager

                sm = SummaryManager(
                    db=services.db, vector_size=_detect_embedding_dim()
                )
                sm.ensure_collection()
                project_descs = sm.get_all_project_descriptions()
                if project_descs:
                    from src.collections.store import get_collection_meta, list_collections_meta

                    existing_ids = {c["id"] for c in list_collections_meta()}
                    catalog_lines = []
                    stale_ids: list[str] = []
                    alias_idx = 0
                    for pd in project_descs:
                        cid = pd.get("collection_id", "")
                        cnt = pd.get("content", "")
                        if cid not in existing_ids:
                            stale_ids.append(cid)
                            continue
                        meta = get_collection_meta(cid)
                        display_name = meta.get("name", cid) if meta else cid
                        alias_idx += 1
                        alias = f"col_{alias_idx}"
                        alias_to_real[alias] = cid
                        real_to_alias[cid] = alias
                        catalog_lines.append(
                            f"- id: {alias}  |  name: {display_name}  |  description: {cnt}"
                        )
                    for stale_cid in stale_ids:
                        try:
                            sm.delete_project_description(stale_cid)
                        except Exception:
                            pass
                    # Sort for deterministic ordering — Qdrant scroll is unordered
                    catalog_lines.sort(key=lambda ln: ln)
                    collection_catalog = "\n".join(catalog_lines)
                    logger.info(
                        "[BLUEPRINT] Catalog contents (%d aliases):\n%s",
                        len(alias_to_real), collection_catalog)
                    logger.info(
                        "[BLUEPRINT] Found %d collections for catalog (%d stale cleaned)",
                        len(catalog_lines),
                        len(stale_ids),
                    )
            except Exception as e:
                logger.warning("[BLUEPRINT] Failed to build collection catalog: %s", e)

            # ── Hot words ─────────────────────────────────────────
            hot_words_text = "(None)"
            if meeting.hot_words_library_id:
                try:
                    from src.hot_words.store import get_library

                    lib = get_library(meeting.hot_words_library_id)
                    if lib and lib.words:
                        hot_words_text = ", ".join(w.text for w in lib.words)
                except Exception:
                    logger.warning("[BLUEPRINT] Failed to load hot words", exc_info=True)

            llm = _resolve_meeting_llm()
            meeting_thinking = get_config().enrichment.meeting_thinking

            # ── Call 1: General Summary (no collection catalog) ───
            # Isolated from catalog so collection descriptions cannot
            # bias the Summary wording.
            summary_prompt = MEETING_GENERAL_SUMMARY_PROMPT.format(
                transcript=transcript_text,
                notes=notes_text,
                hot_words=hot_words_text,
            )
            logger.info("[SUMMARY] Calling LLM with %d char prompt...", len(summary_prompt))

            raw_summary = llm.generate(
                summary_prompt,
                system=MEETING_BLUEPRINT_SYSTEM,
                max_tokens=32768,
                temperature=0.0,
                thinking=meeting_thinking,
            )
            logger.info("[SUMMARY] LLM returned %d chars", len(raw_summary))

            # Call 1 outputs raw markdown directly — no JSON wrapper
            general_md = raw_summary.strip()
            parsed_title = ""  # title comes from Call 2
            logger.info("[SUMMARY] general_md=%d chars", len(general_md))

            # ── Call 2: Blueprint Decomposition (with catalog) ────
            # Focused purely on classification — no Summary task
            # competing for attention.  Shares transcript prefix with
            # Call 1 for prefix-cache hits.
            blueprint_prompt = MEETING_BLUEPRINT_PROMPT.format(
                transcript=transcript_text,
                notes=notes_text,
                hot_words=hot_words_text,
                collection_catalog=collection_catalog,
            )
            logger.info("[BLUEPRINT] Calling LLM with %d char prompt...", len(blueprint_prompt))

            raw_blueprint = llm.generate(
                blueprint_prompt,
                system=MEETING_BLUEPRINT_SYSTEM,
                max_tokens=8192,
                temperature=0.0,
                thinking=meeting_thinking,
                response_format={"type": "json_object"},
            )
            logger.info("[BLUEPRINT] LLM returned %d chars", len(raw_blueprint))

            bp_data = _parse_json_response(raw_blueprint, ["taxonomy", "blueprint"])
            parsed_title = bp_data.get("title", "") or parsed_title
            blueprint_raw = bp_data.get("blueprint", [])
            taxonomy = bp_data.get("taxonomy", None)
            if not isinstance(blueprint_raw, list):
                blueprint_raw = []
            logger.info(
                "[BLUEPRINT] Parsed: blueprint=%d sections, taxonomy=%s, title='%s'",
                len(blueprint_raw),
                taxonomy.get("dimension", "") if taxonomy else "",
                parsed_title,
            )

            # ── Validate sentence refs ────────────────────────────
            all_sids = [s.get("sentence_id", "") for s in (sentences_data or [])]
            general_md = _clean_refs(_normalize_refs(_normalize_brackets(general_md)), all_sids)

            # ── Build blueprint entries (v3: blueprint_id = bp_XX) ──
            # Sort by tab_name for deterministic bp_XX assignment
            blueprint_raw.sort(key=lambda item: item.get("tab_name", ""))
            blueprint: list[dict] = []
            for idx, item in enumerate(blueprint_raw):
                bp_id = f"bp_{idx + 1:02d}"
                bp_name = item.get("tab_name", f"Section {idx + 1}")
                # Skip "Other" from blueprint entirely
                if bp_name.strip().lower() == "other":
                    continue
                # Map alias back to real collection ID (safe: unknown aliases → "")
                raw_cid = item.get("associated_collection_id", "")
                real_cid = alias_to_real.get(raw_cid, "")
                if raw_cid and not real_cid:
                    logger.warning(
                        "[BLUEPRINT] Unknown collection alias '%s' in LLM response — cleared",
                        raw_cid,
                    )
                bp_entry = {
                    "blueprint_id": bp_id,
                    "tab_name": bp_name,
                    "tab_description": item.get("section_description", "")[:600],
                    "associated_collection_id": real_cid,
                    "associated_collection_name": item.get(
                        "associated_collection_name", ""
                    ),
                }
                blueprint.append(bp_entry)

            # ── Build tabs: preserve existing section tabs ────────
            old_tabs: list[dict] = list(meeting.tabs or [])
            is_re_summarize = any(
                (t["tab_id"] if isinstance(t, dict) else t.tab_id) != "tab_general"
                for t in old_tabs
            )

            tabs: list[dict] = []
            general_tab_path = store.save_section_md(
                meeting_id, "tab_general", general_md
            )
            tabs.append(
                {
                    "tab_id": "tab_general",
                    "type": "general",
                    "blueprint_id": "",
                    "name": "General",
                    "description": "",
                    "processing_state": "idle",
                    "associated_collection_id": "",
                    "associated_collection_name": "",
                    "allocated_file_id": "",
                    "is_dirty": False,
                    "md_file_path": general_tab_path,
                    "payload_ref": [],
                }
            )

            for t in old_tabs:
                tid = t["tab_id"] if isinstance(t, dict) else (
                    t.tab_id if hasattr(t, "tab_id") else ""
                )
                if tid == "tab_general":
                    continue
                td = t if isinstance(t, dict) else (
                    t.model_dump() if hasattr(t, "model_dump") else dict(t)
                )
                # On re-summarize: clear blueprint_id, keep everything else
                if is_re_summarize:
                    td["blueprint_id"] = ""
                # Ensure v3 fields exist for legacy tabs
                td.setdefault("blueprint_id", "")
                td.setdefault("description", td.get("description", ""))
                td.setdefault("processing_state", "idle")
                td.setdefault("allocated_file_id", "")
                td.setdefault("is_dirty", False)
                tabs.append(td)

            # ── Persist (content lives in tab_general.md, not meta.json) ─
            update_fields: dict = dict(
                blueprint=blueprint,
                blueprint_taxonomy=taxonomy,
                tabs=tabs,
                processing_state=ProcessingState.idle.value,
            )
            if parsed_title:
                prefix = meeting.created_at.strftime("%Y-%m-%d %H:%M")
                update_fields["title"] = f"{prefix} {parsed_title}"

            store.update_meeting(meeting_id, **update_fields)
            logger.info(
                "[BLUEPRINT] Done for meeting %s: %d blueprint items, %d tabs",
                meeting_id, len(blueprint), len(tabs),
            )

        except Exception as e:
            logger.error("[BLUEPRINT] Failed for meeting %s: %s", meeting_id, e, exc_info=True)
            store.update_meeting(
                meeting_id,
                processing_state=ProcessingState.idle.value,
            )

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
            meeting_thinking = get_config().enrichment.meeting_thinking

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
            hot_words_text = "(None)"
            if meeting.hot_words_library_id:
                try:
                    from src.hot_words.store import get_library
                    lib = get_library(meeting.hot_words_library_id)
                    if lib and lib.words:
                        hot_words_text = ", ".join(w.text for w in lib.words)
                except Exception:
                    logger.warning("[EXTRACT] Failed to load hot words", exc_info=True)

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
                            max_tokens=16384,
                            temperature=0.0,
                            thinking=meeting_thinking,
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
                    full_tagged_ids, sentences, radius=2, gap_threshold=10.0,
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
                            max_tokens=8192,
                            thinking=meeting_thinking,
                        )
                        validated = _clean_refs(_normalize_refs(_normalize_brackets(raw)), list(payload_ids))
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

    # -- Section streaming generation (v3) ---------------------------------

    def generate_section_stream(self, meeting_id: str, tab_id: str):
        """Stream single-section generation as SSE event dicts (Tagger → Summarizer).

        Runs Tagger (non-streaming) first, then streams Summarizer output via
        ``generate_stream_tagged``.  All LLM work happens in a background thread;
        the main generator reads from a queue so the SSE connection can drop
        without cancelling the work.

        Yields dicts::

          {"event": "state", "data": {"section_gen": "prefilling"}}
          {"event": "thinking", "data": "..."}
          {"event": "token", "data": "..."}
          {"event": "state", "data": {"section_gen": "streaming"}}
          {"event": "section_done", "data": {"tab_id": "...", "md": "..."}}
          {"event": "error", "data": {"message": "..."}}
        """
        import queue
        import threading
        import re as _re

        from src.meeting.pipeline import build_payload
        from src.meeting.schemas import Sentence

        event_queue: queue.Queue = queue.Queue()

        def _run() -> None:
            try:
                # ── Load context ──────────────────────────────────
                meeting = store.get_meeting(meeting_id)
                if meeting is None:
                    event_queue.put(("error", {"message": "Meeting not found"}))
                    return

                # Find tab metadata
                tab_meta: dict | None = None
                for t in (meeting.tabs or []):
                    td = t if isinstance(t, dict) else t.model_dump()
                    if td.get("tab_id") == tab_id:
                        tab_meta = td
                        break
                if tab_meta is None:
                    event_queue.put(("error", {"message": f"Tab '{tab_id}' not found"}))
                    return

                section_name = tab_meta.get("name", "")
                section_desc = tab_meta.get("description", "")

                sentences_data = store.get_sentences(meeting_id)
                if not sentences_data:
                    event_queue.put(("error", {"message": "No sentences data"}))
                    return

                sentences = [
                    Sentence(**s) if isinstance(s, dict) else s
                    for s in sentences_data
                ]
                id_to_sentence: dict[str, Sentence] = {
                    s.sentence_id: s for s in sentences
                }

                # ── Build full transcript ─────────────────────────
                speaker_names: dict[str, str] = getattr(meeting, "speaker_names", None) or {}
                transcript_lines = []
                for s in sentences_data:
                    sid = s.get("sentence_id", "")
                    speaker = s.get("speaker", "")
                    text = s.get("original_text", "")
                    spk_name = speaker_names.get(speaker, "")
                    if spk_name:
                        text = text.removeprefix(spk_name).strip()
                        text = text.removeprefix(":").strip()
                    spk_part = f"[spk:{speaker}] " if speaker else ""
                    transcript_lines.append(f"[{_num_id(sid)}] {spk_part}{text}")
                full_transcript = "\n".join(transcript_lines)

                # ── Other sections text ───────────────────────────
                existing_tabs: list[dict] = list(meeting.tabs or [])
                blueprint = meeting.blueprint or []
                _all_tab_names: set[str] = set()
                for t in existing_tabs:
                    nm = t.get("name", "") if isinstance(t, dict) else getattr(t, "name", "")
                    if nm:
                        _all_tab_names.add(nm)

                def _other_sections_text(exclude_tid: str) -> str:
                    others = []
                    for t in existing_tabs:
                        tid = t["tab_id"] if isinstance(t, dict) else t.tab_id
                        if tid == exclude_tid or tid == "tab_general":
                            continue
                        md = t.get("md_file_path", "") if isinstance(t, dict) else getattr(t, "md_file_path", "")
                        if not md:
                            continue  # not yet extracted — skip
                        nm = t.get("name", "") if isinstance(t, dict) else getattr(t, "name", "")
                        dc = t.get("description", "") if isinstance(t, dict) else getattr(t, "description", "")
                        others.append(f"- {nm}: {dc}" if dc else f"- {nm}")
                    return "\n".join(others) if others else "(No other sections)"

                # ── Hot words ─────────────────────────────────
                hot_words_text = "(None)"
                if meeting.hot_words_library_id:
                    try:
                        from src.hot_words.store import get_library
                        lib = get_library(meeting.hot_words_library_id)
                        if lib and lib.words:
                            hot_words_text = ", ".join(w.text for w in lib.words)
                    except Exception:
                        logger.warning("[SECTION-STREAM] Failed to load hot words", exc_info=True)

                other_secs = _other_sections_text(tab_id)

                # ── Short-ID → full-ID lookup ─────────────────────
                short_to_full: dict[str, str] = {}
                for fid in id_to_sentence:
                    parts = fid.rsplit("_stt_", 1)
                    if len(parts) == 2:
                        short_to_full["stt_" + parts[1]] = fid

                llm = _resolve_meeting_llm()
                meeting_thinking = get_config().enrichment.meeting_thinking

                # ── Phase 1: Tagger (non-streaming) ───────────────
                event_queue.put(("state", {"section_gen": "prefilling"}))
                logger.info("[SECTION-STREAM] Tagger starting for %s/%s", meeting_id, tab_id)

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
                            max_tokens=16384,
                            temperature=0.0,
                            thinking=meeting_thinking,
                            response_format={"type": "json_object"},
                        )
                        parsed = _parse_tagger_response(raw)
                        tagged_short_ids = parsed.get("sentence_ids", [])
                        logger.info(
                            "[SECTION-STREAM] Tagger for '%s': %d sentences tagged",
                            section_name, len(tagged_short_ids),
                        )
                        break
                    except Exception as exc:
                        logger.warning(
                            "[SECTION-STREAM] Tagger attempt %d/3 for '%s': %s",
                            attempt + 1, section_name, exc,
                        )
                        if attempt < 2:
                            import time
                            time.sleep(2 ** attempt)
                else:
                    event_queue.put(("error", {"message": f"Tagger failed for '{section_name}'"}))
                    # Persist idle state so UI unsticks
                    self._persist_section_idle(meeting_id, tab_id)
                    return

                if not tagged_short_ids:
                    logger.warning("[SECTION-STREAM] No sentences tagged for '%s'", section_name)
                    # Persist empty result so UI unsticks
                    placeholder = f"# {section_name}\n\nNo relevant sentences found in the transcript."
                    md_path = store.save_section_md(meeting_id, tab_id, placeholder)
                    self._persist_section_done(meeting_id, tab_id, md_path, [])
                    event_queue.put(("section_done", {"tab_id": tab_id, "md": placeholder}))
                    return

                # Convert short IDs → full IDs
                full_tagged_ids: set[str] = set()
                for sid in tagged_short_ids:
                    full = short_to_full.get(sid, sid)
                    full_tagged_ids.add(full)

                # ── Build payload ─────────────────────────────────
                payload_ids = build_payload(
                    full_tagged_ids, sentences, radius=2, gap_threshold=10.0,
                )
                if not payload_ids:
                    logger.warning("[SECTION-STREAM] Empty payload for '%s'", section_name)
                    placeholder = f"# {section_name}\n\nNo relevant context found."
                    md_path = store.save_section_md(meeting_id, tab_id, placeholder)
                    self._persist_section_done(meeting_id, tab_id, md_path, [])
                    event_queue.put(("section_done", {"tab_id": tab_id, "md": placeholder}))
                    return

                # Merge FOCUS + NEARBY in chronological order
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
                merged_text = "\n".join(merged_lines) if merged_lines else "(No sentences)"

                # ── Phase 2: Summarizer (streaming) ───────────────
                summarizer_prompt = MEETING_SUMMARIZER_V3_PROMPT.format(
                    transcript=full_transcript,
                    hot_words=hot_words_text,
                    other_sections=other_secs,
                    section_name=section_name,
                    section_description=section_desc,
                    merged_sentences=merged_text,
                )
                logger.info(
                    "[SECTION-STREAM] Summarizer starting for '%s' (prompt=%d chars, payload=%d)",
                    section_name, len(summarizer_prompt), len(payload_ids),
                )

                streaming_started = False
                accumulated = ""
                for text, is_thinking in llm.generate_stream_tagged(
                    summarizer_prompt,
                    system=MEETING_SUMMARIZER_V3_SYSTEM,
                    max_tokens=8192,
                    thinking=meeting_thinking,
                ):
                    if is_thinking:
                        event_queue.put(("thinking", text))
                    else:
                        if not streaming_started:
                            streaming_started = True
                            event_queue.put(("state", {"section_gen": "streaming"}))
                        accumulated += text
                        event_queue.put(("token", text))

                logger.info(
                    "[SECTION-STREAM] Summarizer done for '%s': %d chars",
                    section_name, len(accumulated),
                )

                # ── Validate & persist ────────────────────────────
                validated = _clean_refs(
                    _normalize_refs(_normalize_brackets(accumulated.strip())),
                    list(payload_ids),
                )
                md_path = store.save_section_md(meeting_id, tab_id, validated)

                # Apply section tags to sentences
                for s in sentences:
                    if tab_id in s.section_tags:
                        s.section_tags.remove(tab_id)
                for sid in payload_ids:
                    sent = id_to_sentence.get(sid)
                    if sent and tab_id not in sent.section_tags:
                        sent.section_tags.append(tab_id)
                store.save_sentences(
                    meeting_id,
                    [s.model_dump() if hasattr(s, "model_dump") else s for s in sentences],
                )

                self._persist_section_done(meeting_id, tab_id, md_path, list(payload_ids))
                event_queue.put(("section_done", {"tab_id": tab_id, "md": validated}))
                logger.info("[SECTION-STREAM] Complete for %s/%s", meeting_id, tab_id)

            except Exception as e:
                logger.exception("[SECTION-STREAM] Failed for %s/%s: %s", meeting_id, tab_id, e)
                self._persist_section_idle(meeting_id, tab_id)
                event_queue.put(("error", {"message": str(e)}))
            finally:
                event_queue.put(("done", None))

        # ── Launch background thread ──────────────────────────────
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        # ── Read queue → SSE events ──────────────────────────────
        try:
            while True:
                try:
                    event_type, event_data = event_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if event_type == "done":
                    break
                yield {"event": event_type, "data": event_data}
        except GeneratorExit:
            logger.info(
                "[SECTION-STREAM] SSE client disconnected for %s/%s — LLM continues in background",
                meeting_id, tab_id,
            )

    def _persist_section_done(
        self, meeting_id: str, tab_id: str, md_path: str, payload_ids: list[str],
    ) -> None:
        """Persist completed section result to meeting store."""
        from src.meeting.models import ProcessingState
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            return
        tabs: list[dict] = list(meeting.tabs or [])
        all_idle = True
        for t in tabs:
            tid = t["tab_id"] if isinstance(t, dict) else t.tab_id
            if tid == tab_id:
                t["md_file_path"] = md_path
                t["payload_ref"] = payload_ids
                t["processing_state"] = "idle"
                t["is_dirty"] = False
            elif t.get("processing_state") == "generating":
                all_idle = False
        if all_idle:
            store.update_meeting(
                meeting_id, tabs=tabs,
                processing_state=ProcessingState.idle.value,
            )
        else:
            store.update_meeting(meeting_id, tabs=tabs)

    def _persist_section_idle(self, meeting_id: str, tab_id: str) -> None:
        """Reset a section's processing_state to idle (on error)."""
        from src.meeting.models import ProcessingState
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            return
        tabs: list[dict] = list(meeting.tabs or [])
        all_idle = True
        for t in tabs:
            tid = t["tab_id"] if isinstance(t, dict) else t.tab_id
            if tid == tab_id:
                t["processing_state"] = "idle"
            elif t.get("processing_state") == "generating":
                all_idle = False
        if all_idle:
            store.update_meeting(
                meeting_id, tabs=tabs,
                processing_state=ProcessingState.idle.value,
            )
        else:
            store.update_meeting(meeting_id, tabs=tabs)

    # -- Section management (v3) --------------------------------------------

    async def delete_section(self, meeting_id: str, tab_id: str) -> Meeting:
        """v3: remove a section, its tags, md file, and allocated ingest data."""
        from src.meeting.models import ProcessingState

        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")
        if meeting.processing_state != ProcessingState.idle.value:
            raise RuntimeError(f"Meeting is busy: {meeting.processing_state}")

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
                    self._delete_allocation(col_id, file_id)
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

    # -- Collection allocation ----------------------------------------------

    @staticmethod
    def _delete_allocation(collection: str, file_id: str) -> None:
        """Delete an allocation's chunks and file snapshot from a collection."""
        try:
            from src.collections.file_index import load as load_file_index, remove as remove_file_index

            # Look up source from file index
            idx = load_file_index(collection)
            entry = idx.get(file_id, {})
            source = entry.get("source", "")
            if source:
                services.db.delete_by_filter(collection=collection, key="source", value=source)

            # Delete file snapshot
            file_dir = _files_dir(collection) / file_id
            if file_dir.exists():
                shutil.rmtree(file_dir)

            # Remove from index
            remove_file_index(collection, file_id)
            logger.info("Deleted allocation file_id=%s source=%s from collection '%s'", file_id, source, collection)
        except Exception as exc:
            logger.warning("Failed to delete allocation file_id=%s: %s", file_id, exc)

    async def allocate_section_to_collection(
        self, meeting_id: str, tab_id: str, collection_id: str,
    ) -> Meeting:
        """Allocate a single section's .md content to a collection.

        Resolves [spk:ID] → speaker names, strips [stt_XXXX] refs,
        then uploads as a single file via the document pipeline.
        """
        import re as _re

        from src.collections.store import get_collection_meta
        from src.tasks.handlers import upload_handler

        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")

        # Find the tab
        tab_meta: dict | None = None
        for t in (meeting.tabs or []):
            tid = t["tab_id"] if isinstance(t, dict) else t.tab_id
            if tid == tab_id:
                tab_meta = t if isinstance(t, dict) else t.model_dump()
                break

        if tab_meta is None:
            raise ValueError(f"Tab '{tab_id}' not found")

        # ── Idempotency: clean up previous allocation (if any) ────
        old_fid = tab_meta.get("allocated_file_id", "")
        old_col = tab_meta.get("associated_collection_id", "")
        if old_col and old_fid:
            self._delete_allocation(old_col, old_fid)
            logger.info(
                "Cleaned previous allocation %s/%s for tab %s (re-ingest)",
                old_col, old_fid, tab_id,
            )

        # Read section .md content
        content = store.get_section_md(meeting_id, tab_id)
        if not content:
            raise ValueError(f"No content for tab '{tab_id}'")

        # ── Process content ────────────────────────────────────
        # 1. Resolve [spk:ID] → speaker names
        speaker_names: dict[str, str] = getattr(meeting, "speaker_names", None) or {}
        for spk_id, name in speaker_names.items():
            content = content.replace(f"[spk:{spk_id}]", name)
            content = _re.sub(rf"\bSpeaker {_re.escape(spk_id)}\b", name, content)

        # 2. Remove sentence refs: [stt_0001,stt_0002-0005] and bare stt_XXXX
        content = _re.sub(
            r"\[(?:ref:)?\s*(?:stt_\d+(?:\s*[-–]\s*\d+)?"
            r"(?:\s*,\s*stt_\d+(?:\s*[-–]\s*\d+)?)*)\s*\]",
            "", content,
        )
        content = _re.sub(r"\bstt_\d{4}\b", "", content)
        content = _re.sub(r"\n{3,}", "\n\n", content)
        content = content.strip()

        section_label = tab_meta.get("name", tab_id)
        full_content = f"# {section_label}\n\n{content}"

        # ── Upload to collection ────────────────────────────────
        alloc_file_id = uuid.uuid4().hex
        file_dir = _files_dir(collection_id) / alloc_file_id
        file_dir.mkdir(parents=True, exist_ok=True)
        file_path = file_dir / f"{tab_id}.md"
        file_path.write_text(full_content, encoding="utf-8")

        section_source = f"__meeting__:{meeting_id}:{tab_id}"

        upload_task = Task(
            id=str(uuid.uuid4()),
            filename=f"meeting_{meeting_id}_{tab_id}",
            collection=collection_id,
            status=TaskStatus.PROCESSING,
            created_at=datetime.now(),
        )

        await upload_handler(
            upload_task, str(file_path), collection_id, section_source,
            source_label=f"Meeting: {meeting.title} / {section_label}",
            file_id=alloc_file_id,
            meeting_id=meeting_id,
        )

        # ── Update tab metadata ─────────────────────────────────
        col_meta = get_collection_meta(collection_id)
        col_name = col_meta.get("name", collection_id) if col_meta else collection_id

        updated_tabs: list[dict] = []
        for t in (meeting.tabs or []):
            td = t if isinstance(t, dict) else t.model_dump()
            if td.get("tab_id") == tab_id:
                td["associated_collection_id"] = collection_id
                td["associated_collection_name"] = col_name
                td["allocated_file_id"] = alloc_file_id
            updated_tabs.append(td)

        store.update_meeting(meeting_id, tabs=updated_tabs)

        # Rebuild meeting-level tracking arrays from tabs (single source of truth)
        alloc_cols, alloc_fids = _rebuild_allocation_arrays(updated_tabs)
        store.update_meeting(
            meeting_id,
            allocated_collections=alloc_cols,
            allocated_file_ids=alloc_fids,
        )

        updated = store.get_meeting(meeting_id)
        assert updated is not None

        logger.info(
            "Allocated section %s/%s to collection '%s'",
            meeting_id, tab_id, collection_id,
        )

        return updated

    async def delete_section_allocation(
        self, meeting_id: str, tab_id: str,
    ) -> Meeting:
        """Remove a section's allocation: delete file snapshot and clear tab metadata."""
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

        col_id = tab_meta.get("associated_collection_id", "")
        file_id = tab_meta.get("allocated_file_id", "")
        if col_id and file_id:
            self._delete_allocation(col_id, file_id)

        # Clear tab metadata (set to empty string, not pop — frontend expects string)
        updated_tabs: list[dict] = []
        for t in (meeting.tabs or []):
            td = t if isinstance(t, dict) else t.model_dump()
            if td.get("tab_id") == tab_id:
                td["associated_collection_id"] = ""
                td["associated_collection_name"] = ""
                td["allocated_file_id"] = ""
            updated_tabs.append(td)

        store.update_meeting(meeting_id, tabs=updated_tabs)

        # Rebuild meeting-level tracking arrays from tabs (single source of truth)
        alloc_cols, alloc_fids = _rebuild_allocation_arrays(updated_tabs)
        store.update_meeting(
            meeting_id,
            allocated_collections=alloc_cols,
            allocated_file_ids=alloc_fids,
        )

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
        hot_words_text = "(None)"
        if meeting.hot_words_library_id:
            try:
                from src.hot_words.store import get_library
                lib = get_library(meeting.hot_words_library_id)
                if lib and lib.words:
                    hot_words_text = ", ".join(w.text for w in lib.words)
            except Exception:
                logger.warning("[SECTION-DESC] Failed to load hot words", exc_info=True)

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


def _parse_blueprint_response(raw: str) -> tuple[str, list[dict], str, dict | None]:
    """Parse Node 0.3 LLM response into (general_md_content, blueprint, title, taxonomy).

    Scans for the first '{' then uses JSONDecoder.raw_decode() for proper
    nested-brace handling.  Robust to preamble text and markdown fences.
    Falls back to treating the entire response as markdown with an empty
    blueprint.
    """
    import json as _json

    raw_stripped = raw.strip()

    # Find the blueprint JSON object.  With thinking mode enabled the LLM
    # may emit reasoning text that contains brace characters, so we try
    # multiple strategies in order of reliability.
    decoder = _json.JSONDecoder()
    strategies = [
        # Strategy 1: look for {"title"  (most reliable anchor)
        ("{\"title\"", raw_stripped.find('{"title"')),
        # Strategy 2: look for "general_md_content" and backtrack to the
        #             nearest opening brace
        ("\"general_md_content\"", raw_stripped.rfind('"general_md_content"')),
        # Strategy 3: first '{' (original behaviour, fragile with thinking)
        ("{", raw_stripped.index("{")),
    ]

    for _label, idx in strategies:
        if idx < 0:
            continue
        # For strategy 2, backtrack to the nearest '{' before the key
        if _label == '"general_md_content"':
            search_from = idx - 1
            brace_idx = raw_stripped.rfind("{", 0, search_from)
            if brace_idx < 0:
                continue
            idx = brace_idx
        try:
            data, _ = decoder.raw_decode(raw_stripped[idx:])
            general_md = data.get("general_md_content", "")
            blueprint = data.get("blueprint", [])
            title = data.get("title", "")
            taxonomy = data.get("taxonomy", None)
            if isinstance(blueprint, list):
                logger.info("[BLUEPRINT] JSON parsed via strategy '%s'", _label)
                return general_md, blueprint, title, taxonomy
        except (_json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue

    # Fallback: treat raw as plain markdown, infer single-general section
    logger.warning(
        "[BLUEPRINT] JSON parse failed, falling back to plain markdown "
        "(raw=%d chars)",
        len(raw_stripped),
    )
    return raw_stripped, [], "", None


def _normalize_brackets(md: str) -> str:
    """Convert CJK fullwidth brackets and other Unicode bracket variants to ASCII.

    LLMs occasionally emit Chinese brackets 【】 or other fullwidth forms
    instead of plain [].  This normalizes them so downstream regex-based
    ref/priority parsing works correctly.

    Mappings:
      【 → [    】 → ]
      〔 → [    〕 → ]
      ［ → [    ］ → ]
      ｛ → {    ｝ → }
    """
    return (
        md.replace("【", "[")   # 【
           .replace("】", "]")   # 】
           .replace("〔", "[")   # 〔
           .replace("〕", "]")   # 〕
           .replace("［", "[")   # ［
           .replace("］", "]")   # ］
           .replace("｛", "{")   # ｛
           .replace("｝", "}")   # ｝
    )


def _normalize_refs(md: str) -> str:
    """Convert numeric refs [67] → [stt_0067] in LLM output markdown.

    Handles plain numbers, comma-separated lists, and ranges:
      [67]       → [stt_0067]
      [67,70]    → [stt_0067,stt_0070]
      [67-70]    → [stt_0067-stt_0070]
      [67,70-73] → [stt_0067,stt_0070-stt_0073]

    Also handles Chinese brackets 【67】 via prior _normalize_brackets pass.
    Already-normalized stt_XXXX refs pass through unchanged.
    """
    import re as _re

    def _convert(m: _re.Match) -> str:
        inner = m.group(1)
        tokens = [t.strip() for t in inner.split(",") if t.strip()]
        converted: list[str] = []
        for token in tokens:
            # Range: 67-70 or 67–70 → stt_0067-0070
            rm = _re.match(r"^(\d+)\s*[-–]\s*(\d+)$", token)
            if rm:
                converted.append(
                    f"stt_{int(rm.group(1)):04d}-{int(rm.group(2)):04d}"
                )
                continue
            # Plain number: 67
            nm = _re.match(r"^(\d+)$", token)
            if nm:
                converted.append(f"stt_{int(nm.group(1)):04d}")
                continue
            # Already stt_XXXX — pass through
            converted.append(token)
        return "[" + ",".join(converted) + "]"

    return _re.sub(
        r"\[(\d+(?:\s*[-–]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-–]\s*\d+)?)*)\]",
        _convert,
        md,
    )


def _clean_refs(md: str, valid_ids: list[str]) -> str:
    """Strip [stt_XXX] tags whose sentence IDs are not in *valid_ids*.

    Supports range notation: [stt_0019-0036] expands to all IDs in the range,
    and mixed forms like [stt_0001-0005,stt_0010,stt_0100-0105].
    """
    import re as _re

    valid_set = set(valid_ids)

    def _expand_range(start_str: str, end_str: str) -> list[str]:
        """Expand stt_0019-0036 → [stt_0019, stt_0020, ..., stt_0036]."""
        try:
            s = int(start_str)
            e = int(end_str)
            if e < s or e - s > 50:  # sanity cap
                return [f"stt_{start_str}"]
            return [f"stt_{n:04d}" for n in range(s, e + 1)]
        except ValueError:
            return [f"stt_{start_str}"]

    def _clean_one(m: _re.Match) -> str:
        inner = m.group(1) or m.group(2)
        # Split on commas (ranges are kept as single tokens like stt_0019-0036)
        tokens = [t.strip() for t in inner.split(",") if t.strip()]
        expanded: list[str] = []
        for token in tokens:
            # Range: stt_0019-0036 or stt_0019–0036
            rm = _re.match(r"^stt_(\d+)\s*[-–]\s*(\d+)$", token)
            if rm:
                expanded.extend(_expand_range(rm.group(1), rm.group(2)))
                continue
            # Concatenated IDs: stt_004144465356
            cm = _re.match(r"^stt_(\d{5,})$", token)
            if cm:
                digits = cm.group(1)
                # Truncate to multiple of 4 to avoid malformed last chunk
                digits = digits[:len(digits) - len(digits) % 4]
                chunks = [digits[j:j+4] for j in range(0, len(digits), 4)]
                expanded.extend([f"stt_{c}" for c in chunks])
                continue
            # Plain stt_XXXX
            if _re.match(r"^stt_\d+$", token):
                expanded.append(token)
                continue

        kept = [i for i in expanded if any(v.endswith(i) for v in valid_set)]
        if not kept:
            return ""
        return "[" + ",".join(kept) + "]"

    # Match bracketed [stt_XXXX,…] (with optional ranges) and bare stt_XXXX
    return _re.sub(
        r"\[(?:ref:)?\s*(stt_\d+(?:\s*[-–]\s*\d+)?(?:\s*,\s*stt_\d+(?:\s*[-–]\s*\d+)?)*)\s*\]"
        r"|(?<!\w)(stt_\d+)(?!\w)",
        _clean_one,
        md,
    )


def _resolve_default_llm():
    """Resolve the default LLM from config. Returns None if none found."""
    try:
        from src.config import get_config
        from src.providers.llm import create_llm_for_provider

        config = get_config()
        if config.llm.providers:
            default_p = next(
                (p for p in config.llm.providers if p.is_default),
                config.llm.providers[0],
            )
            return create_llm_for_provider(default_p)
    except Exception:
        logger.warning("Failed to resolve default LLM", exc_info=True)
    return None


def _parse_tagger_response(raw: str) -> dict[str, list[str]]:
    """Parse v3 Tagger LLM response into {"sentence_ids": [...]}.

    Scans for the first '{' then uses JSONDecoder.raw_decode() for proper
    nested-brace handling.  Falls back to regex + json.loads if raw_decode
    fails (handles trailing text after the JSON close brace).

    Converts numeric IDs from the LLM (integers) back to stt_XXXX format.
    Also handles legacy string IDs ("stt_0001") for backward compatibility.
    """
    import json as _json
    import re as _re

    raw_stripped = raw.strip()
    # Search for {"sentence_ids" specifically (not bare '{' which
    # appears in reasoning text).  Look from the end — the JSON
    # should be the last thing in the response.
    idx = raw_stripped.rfind('{"sentence_ids"')
    if idx < 0:
        # Fallback: try any '{' from the end
        idx = raw_stripped.rfind("{")
    if idx < 0:
        # Last resort: try to extract bare integer IDs from the text
        fallback_ids = _re.findall(r"\b(\d{1,4})\b", raw_stripped)
        if fallback_ids:
            logger.warning(
                "[TAGGER] No JSON found, fallback extracted %d numeric IDs from text",
                len(fallback_ids),
            )
            return {"sentence_ids": _normalize_ids([int(x) for x in fallback_ids])}
        logger.warning("[TAGGER] No JSON object found in LLM response (%d chars)", len(raw_stripped))
        return {"sentence_ids": []}

    def _normalize_ids(raw_ids: list) -> list[str]:
        """Convert integer IDs → 'stt_XXXX', pass strings through unchanged."""
        result: list[str] = []
        for i in raw_ids:
            if isinstance(i, (int, float)):
                result.append(_num_to_stt(int(i)))
            elif isinstance(i, str) and i.isdigit():
                result.append(_num_to_stt(int(i)))
            else:
                result.append(str(i))  # legacy: "stt_0001" etc.
        return result

    # Try raw_decode first (proper nested-brace handling)
    last_err = ""
    try:
        decoder = _json.JSONDecoder()
        data, _ = decoder.raw_decode(raw_stripped[idx:])
        raw_ids = data.get("sentence_ids", [])
        if isinstance(raw_ids, list):
            return {"sentence_ids": _normalize_ids(raw_ids)}
    except _json.JSONDecodeError as e:
        last_err = str(e)

    # Fallback: regex extraction + json.loads (handles trailing text/markdown)
    json_match = _re.search(r"\{[\s\S]*?\}", raw_stripped[idx:])
    if json_match:
        try:
            data = _json.loads(json_match.group())
            raw_ids = data.get("sentence_ids", [])
            if isinstance(raw_ids, list) and raw_ids:
                ids = _normalize_ids(raw_ids)
                logger.info("[TAGGER] Recovered via regex fallback (%d ids)", len(ids))
                return {"sentence_ids": ids}
        except _json.JSONDecodeError:
            pass

    logger.warning(
        "[TAGGER] Failed to parse LLM response (%d chars, starts: %.200r, err: %s, ends: %.200r)",
        len(raw_stripped), raw_stripped[:200], last_err, raw_stripped[-200:],
    )
    return {"sentence_ids": []}


# Backward compat
_parse_tagging_response = _parse_tagger_response


# Module-level singleton
meeting_service = MeetingService()
