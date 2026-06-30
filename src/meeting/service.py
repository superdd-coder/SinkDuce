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


def _detect_embedding_dim() -> int:
    """Detect actual embedding dimension by test embedding."""
    dim = getattr(services.embedding, 'dimensions', 0) if services.embedding else 0
    if not dim or dim <= 0:
        try:
            test = services.embedding.embed_texts(["test"])
            dim = len(test[0])
        except Exception:
            dim = 1024
    return dim if dim > 0 else 1024

COLLECTIONS_DIR = Path("data").resolve() / "collections"

def _files_dir(collection_id: str) -> Path:
    return COLLECTIONS_DIR / collection_id / "files"

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

    # 4b. Pipeline Node 0.0 + 0.1: normalize sentences & chunk
    from src.meeting.pipeline import chunk_sentences, normalize_sentences

    sentences = normalize_sentences(meeting_id, result.segments)
    store.save_sentences(meeting_id, [s.model_dump() for s in sentences])

    chunks = chunk_sentences(meeting_id, sentences)
    store.save_chunks(meeting_id, [c.model_dump() for c in chunks])

    logger.info(
        "[PIPELINE] Node 0.0+0.1 done: %d sentences, %d chunks for meeting %s",
        len(sentences), len(chunks), meeting_id,
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
from src.tasks.handlers import meeting_summary_handler
task_manager.register_handler("meeting_summary", meeting_summary_handler)


# ---------------------------------------------------------------------------
# MeetingService
# ---------------------------------------------------------------------------

from src.prompts import MEETING_BLUEPRINT_SYSTEM, MEETING_BLUEPRINT_PROMPT


class MeetingService:
    """High-level meeting operations: transcription providers, summary, allocation."""

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

    # -- Summary generation -------------------------------------------------

    async def start_generate_summary(self, meeting_id: str) -> Meeting:
        """Start background blueprint summary (Node 0.3)."""
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")

        from src.meeting.models import ProcessingState

        store.update_meeting(
            meeting_id,
            processing_state=ProcessingState.summarizing.value,
        )
        import threading

        t = threading.Thread(
            target=self._do_blueprint_summary, args=(meeting_id,), daemon=True
        )
        t.start()
        return store.get_meeting(meeting_id)

    def _do_blueprint_summary(self, meeting_id: str) -> None:
        """Node 0.3: generate General summary + decomposition blueprint.

        Uses the v2 prompt → LLM → parses {general_md_content, blueprint} →
        saves general tab md + updates meta.json with blueprint & tabs.
        """
        from src.meeting.models import ProcessingState

        logger.info("[BLUEPRINT] Starting for meeting %s", meeting_id)
        try:
            meeting = store.get_meeting(meeting_id)
            if meeting is None:
                return

            # ── Build transcript text (with sentence IDs for refs) ────
            notes = store.get_notes(meeting_id)
            speaker_names = meeting.speaker_names or {}
            sentences_data = store.get_sentences(meeting_id)

            if sentences_data:
                lines = []
                for s in sentences_data:
                    sid = s.get("sentence_id", "")
                    speaker = s.get("speaker", "")
                    text = s.get("original_text", "")
                    name = speaker_names.get(speaker, f"Speaker {speaker}") if speaker else ""
                    spk_prefix = f"[{name}] " if name else ""
                    lines.append(f"[{sid}] {spk_prefix}{text}")
                transcript_text = "\n".join(lines)
            else:
                transcript_result = store.get_transcript(meeting_id)
                transcript_text = (
                    transcript_result.text
                    if transcript_result
                    else "(No transcript available)"
                )

            # ── Build speakers list (ALL speakers from transcript) ──────
            # Collect all speaker IDs from sentences, not just renamed ones
            all_speaker_ids: set[str] = set()
            if sentences_data:
                for s in sentences_data:
                    spk = s.get("speaker", "")
                    if spk:
                        all_speaker_ids.add(spk)
            if all_speaker_ids:
                speakers_text = "\n".join(
                    f"- Speaker {sid}: {speaker_names.get(sid, f'Speaker {sid}')}"
                    for sid in sorted(all_speaker_ids, key=lambda x: int(x) if x.isdigit() else 0)
                )
            elif speaker_names:
                speakers_text = "\n".join(
                    f"- Speaker {sid}: {name}" for sid, name in speaker_names.items()
                )
            else:
                speakers_text = "(No speakers identified)"

            notes_text = notes if notes else "(No notes)"
            logger.info(
                "[BLUEPRINT] Transcript: %d chars, Notes: %d chars",
                len(transcript_text),
                len(notes_text),
            )

            # ── Build collection catalog ──────────────────────────────
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
                    lines = []
                    stale_ids: list[str] = []
                    for pd in project_descs:
                        cid = pd.get("collection_id", "")
                        content = pd.get("content", "")
                        # Skip entries for collections that no longer exist
                        if cid not in existing_ids:
                            stale_ids.append(cid)
                            logger.info(
                                "[BLUEPRINT] Skipping stale project_description for '%s'",
                                cid,
                            )
                            continue
                        meta = get_collection_meta(cid)
                        display_name = meta.get("name", cid) if meta else cid
                        lines.append(
                            f"- id: {cid}  |  name: {display_name}  |  description: {content}"
                        )
                    # Clean up stale entries from __summaries__
                    for stale_cid in stale_ids:
                        try:
                            sm.delete_project_description(stale_cid)
                            logger.info(
                                "[BLUEPRINT] Deleted stale project_description for '%s'",
                                stale_cid,
                            )
                        except Exception:
                            pass
                    collection_catalog = "\n".join(lines)
                    logger.info(
                        "[BLUEPRINT] Found %d collections for catalog (%d stale cleaned)",
                        len(lines),
                        len(stale_ids),
                    )
                    logger.info("[BLUEPRINT] Collection catalog:\n%s", collection_catalog)
            except Exception as e:
                logger.warning("[BLUEPRINT] Failed to build collection catalog: %s", e)

            # ── Hot words ─────────────────────────────────────────────
            hot_words_text = "(None)"
            if meeting.hot_words_library_id:
                try:
                    from src.hot_words.store import get_library

                    lib = get_library(meeting.hot_words_library_id)
                    if lib and lib.words:
                        hot_words_text = ", ".join(w.text for w in lib.words)
                except Exception:
                    logger.warning("[BLUEPRINT] Failed to load hot words", exc_info=True)

            # ── Call LLM ──────────────────────────────────────────────
            prompt = MEETING_BLUEPRINT_PROMPT.format(
                transcript=transcript_text,
                notes=notes_text,
                speakers=speakers_text,
                hot_words=hot_words_text,
                collection_catalog=collection_catalog,
            )
            logger.info("[BLUEPRINT] Calling LLM with %d char prompt...", len(prompt))

            llm = services.llm
            if llm is None:
                from src.config import get_config
                from src.providers.llm import create_llm_for_provider

                config = get_config()
                if config.llm.providers:
                    default_p = next(
                        (p for p in config.llm.providers if p.is_default),
                        config.llm.providers[0],
                    )
                    llm = create_llm_for_provider(default_p)
            if llm is None:
                raise RuntimeError(
                    "No LLM provider configured. Add one in Settings first."
                )

            raw_response = llm.generate(
                prompt,
                system=MEETING_BLUEPRINT_SYSTEM,
                max_tokens=32768,
                thinking=True,
                response_format={"type": "json_object"},
            )
            logger.info("[BLUEPRINT] LLM returned %d chars", len(raw_response))
            # DEBUG: dump raw response for ref debugging
            _ref_lines = [l for l in raw_response.split("\n") if "stt_" in l]
            if _ref_lines:
                logger.info("[BLUEPRINT] RAW lines with stt_ refs:\n%s", "\n".join(_ref_lines))

            # ── Parse response ───────────────────────────────────────
            general_md, blueprint_raw, parsed_title = _parse_blueprint_response(
                raw_response
            )
            logger.info(
                "[BLUEPRINT] Parsed: general_md=%d chars, blueprint=%d sections, title='%s'",
                len(general_md),
                len(blueprint_raw),
                parsed_title,
            )

            # ── Validate sentence refs in General content ────────────
            all_sids = [s.get("sentence_id", "") for s in (sentences_data or [])]
            general_md = _clean_refs(general_md, all_sids)

            # ── Clean up old derived data before writing new ────────
            # Reset sentence tags (old blueprint sections no longer exist)
            old_sentences = store.get_sentences(meeting_id)
            if old_sentences:
                for s in old_sentences:
                    s["section_tags"] = []
                store.save_sentences(meeting_id, old_sentences)
            # Delete old section .md files
            if meeting.tabs:
                for old_tab in meeting.tabs:
                    tid = (
                        old_tab["tab_id"]
                        if isinstance(old_tab, dict)
                        else old_tab.tab_id
                    )
                    if tid and tid != "tab_general":
                        p = store.section_md_path(meeting_id, tid)
                        if p.exists():
                            p.unlink()
            logger.info(
                "[BLUEPRINT] Cleaned old sentence tags & section .md files for meeting %s",
                meeting_id,
            )

            # ── Assign tab_ids (code-generated, not LLM) ─────────────
            tabs: list[dict] = []
            # General tab always present
            general_tab_path = store.save_section_md(
                meeting_id, "tab_general", general_md
            )
            tabs.append(
                {
                    "tab_id": "tab_general",
                    "type": "general",
                    "name": "General",
                    "associated_collection_id": "",
                    "associated_collection_name": "",
                    "md_file_path": general_tab_path,
                    "payload_ref": [],
                }
            )

            blueprint: list[dict] = []
            for idx, item in enumerate(blueprint_raw):
                tab_id = f"tab_sec_{idx + 1:02d}"
                bp_entry = {
                    "tab_id": tab_id,
                    "tab_name": item.get("tab_name", f"Section {idx + 1}"),
                    "associated_collection_id": item.get(
                        "associated_collection_id", ""
                    ),
                    "associated_collection_name": item.get(
                        "associated_collection_name", ""
                    ),
                    "section_description": item.get("section_description", "")[:200],
                }
                blueprint.append(bp_entry)
                # Skip "Other" from tabs — needed for tagging but not displayed
                if bp_entry["tab_name"].strip().lower() == "other":
                    continue
                tabs.append(
                    {
                        "tab_id": tab_id,
                        "type": "section",
                        "name": bp_entry["tab_name"],
                        "associated_collection_id": bp_entry[
                            "associated_collection_id"
                        ],
                        "associated_collection_name": bp_entry[
                            "associated_collection_name"
                        ],
                        "md_file_path": "",  # filled by breakdown
                        "payload_ref": [],
                    }
                )

            # ── Persist ───────────────────────────────────────────────
            update_fields: dict = dict(
                detail=general_md,
                summary=general_md[:500] if len(general_md) > 500 else general_md,
                blueprint=blueprint,
                tabs=tabs,

                processing_state=ProcessingState.idle.value,
            )
            if parsed_title:
                prefix = meeting.created_at.strftime("%Y-%m-%d %H:%M")
                update_fields["title"] = f"{prefix} {parsed_title}"

            store.update_meeting(meeting_id, **update_fields)
            logger.info("[BLUEPRINT] Done for meeting %s", meeting_id)

        except Exception as e:
            logger.error("[BLUEPRINT] Failed for meeting %s: %s", meeting_id, e, exc_info=True)
            store.update_meeting(
                meeting_id,

                processing_state=ProcessingState.idle.value,
            )

    # -- Breakdown (Pipeline 2) ---------------------------------------------

    async def start_breakdown(self, meeting_id: str) -> Meeting:
        """Start background breakdown. Returns immediately."""
        from src.meeting.models import ProcessingState

        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")
        if not meeting.blueprint:
            raise ValueError(
                "Meeting has no blueprint. Generate a summary first."
            )
        if not store.get_sentences(meeting_id):
            raise ValueError(
                "Meeting has no sentence data. Transcription completed? "
            )
        if meeting.processing_state != ProcessingState.idle.value:
            raise RuntimeError(
                f"Meeting is busy: {meeting.processing_state}"
            )

        store.update_meeting(
            meeting_id,
            processing_state=ProcessingState.breaking_down.value,
        )
        import threading

        t = threading.Thread(
            target=self._do_breakdown, args=(meeting_id,), daemon=True
        )
        t.start()
        return store.get_meeting(meeting_id)

    def _do_breakdown(self, meeting_id: str) -> None:
        """Node 1.1 → 1.2 → 1.3: full breakdown pipeline.

        1. Tag every chunk via LLM (bounded concurrency)
        2. Write tags to sentences, persist
        3. Build per-section payloads (Node 1.2)
        4. Summarize each section via LLM (concurrent, Node 1.3)
        5. Persist section md files, update meeting metadata
        """
        import json as _json
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from src.meeting.models import ProcessingState
        from src.meeting.pipeline import build_payload
        from src.meeting.schemas import Chunk, Sentence
        from src.prompts import (
            MEETING_TAGGING_PROMPT,
            MEETING_TAGGING_SYSTEM,
            MEETING_SECTION_SUMMARY_PROMPT,
            MEETING_SECTION_SUMMARY_SYSTEM,
        )

        logger.info("[BREAKDOWN] Starting for meeting %s", meeting_id)
        try:
            meeting = store.get_meeting(meeting_id)
            if meeting is None:
                return

            sentences_data = store.get_sentences(meeting_id)
            if sentences_data is None:
                raise ValueError("No sentences data")
            chunks_data = store.get_chunks(meeting_id)
            if chunks_data is None:
                raise ValueError("No chunks data")

            # Convert dicts to objects
            sentences = [
                Sentence(**s) if isinstance(s, dict) else s
                for s in sentences_data
            ]
            chunks = [
                Chunk(**c) if isinstance(c, dict) else c
                for c in chunks_data
            ]
            blueprint = meeting.blueprint or []

            # ── Build id-to-index lookup ─────────────────────────────
            id_to_sentence: dict[str, Sentence] = {
                s.sentence_id: s for s in sentences
            }

            def chunk_json(chunk_sids: list[str]) -> list[dict]:
                return [
                    {
                        "sentence_id": sid,
                        "start_time": id_to_sentence[sid].start_time,
                        "speaker": id_to_sentence[sid].speaker,
                        "original_text": id_to_sentence[sid].original_text,
                    }
                    for sid in chunk_sids
                    if sid in id_to_sentence
                ]

            blueprint_json = _json.dumps(blueprint, ensure_ascii=False)

            # ── Resolve LLM ──────────────────────────────────────────
            llm = services.llm
            if llm is None:
                llm = _resolve_default_llm()
            if llm is None:
                raise RuntimeError("No LLM provider configured.")

            MAX_CONCURRENT = 6
            semaphore = threading.BoundedSemaphore(MAX_CONCURRENT)
            tag_results_lock = threading.Lock()
            tag_results: dict[int, dict[str, list[str]]] = {}  # chunk_idx → mapping
            failed_chunks: list[int] = []

            def tag_one_chunk(idx: int, chunk: Chunk) -> None:
                """Tag a single chunk with retry."""
                with semaphore:
                    target_json = _json.dumps(
                        chunk_json(chunk.sentence_refs), ensure_ascii=False
                    )
                    # Build context: previous 2 chunks
                    ctx_sids: list[str] = []
                    for offset in (2, 1):
                        ci = idx - offset
                        if ci >= 0:
                            ctx_sids.extend(chunks[ci].sentence_refs)
                    context_json = _json.dumps(
                        chunk_json(ctx_sids), ensure_ascii=False
                    ) if ctx_sids else "[]"

                    prompt = MEETING_TAGGING_PROMPT.format(
                        blueprint_json=blueprint_json,
                        context_json=context_json,
                        target_json=target_json,
                    )

                    for attempt in range(3):
                        try:
                            raw = llm.generate(
                                prompt,
                                system=MEETING_TAGGING_SYSTEM,
                                max_tokens=4096,
                                thinking=False,
                            )
                            mapping = _parse_tagging_response(raw)
                            with tag_results_lock:
                                tag_results[idx] = mapping
                            logger.info(
                                "[BREAKDOWN] Chunk %d/%d tagged: %s",
                                idx + 1, len(chunks),
                                {k: len(v) for k, v in mapping.items()},
                            )
                            return
                        except Exception as exc:
                            logger.warning(
                                "[BREAKDOWN] Chunk %d attempt %d/3 failed: %s",
                                idx + 1, attempt + 1, exc,
                            )
                            if attempt < 2:
                                import time
                                time.sleep(2 ** attempt)
                    # All retries exhausted
                    with tag_results_lock:
                        failed_chunks.append(idx)
                        tag_results[idx] = {}
                    logger.error("[BREAKDOWN] Chunk %d FAILED after 3 retries", idx + 1)

            # ── Phase A: Tag all chunks (bounded concurrency) ────────
            logger.info(
                "[BREAKDOWN] Tagging %d chunks (max %d concurrent)",
                len(chunks), MAX_CONCURRENT,
            )
            with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as pool:
                futures = [
                    pool.submit(tag_one_chunk, i, c)
                    for i, c in enumerate(chunks)
                ]
                for f in as_completed(futures):
                    f.result()  # propagate any unexpected errors

            logger.info(
                "[BREAKDOWN] Tagging phase done: %d/%d chunks tagged, %d failed",
                len(tag_results), len(chunks), len(failed_chunks),
            )

            # ── Write tags back to sentences ────────────────────────
            # Build short-ID → full-ID lookup (LLM may return stt_0012
            # instead of 756f0b7c_stt_0012 as shown in prompt examples)
            short_to_full: dict[str, str] = {}
            for fid in id_to_sentence:
                parts = fid.rsplit("_stt_", 1)
                if len(parts) == 2:
                    short_to_full["stt_" + parts[1]] = fid
            for chunk_idx, mapping in tag_results.items():
                for tab_id, sids in mapping.items():
                    for sid in sids:
                        full_sid = short_to_full.get(sid, sid)
                        sent = id_to_sentence.get(full_sid)
                        if sent and tab_id not in sent.section_tags:
                            sent.section_tags.append(tab_id)

            # Persist tagged sentences
            store.save_sentences(
                meeting_id,
                [s.model_dump() if hasattr(s, "model_dump") else s for s in sentences],
            )

            # ── Phase B: Build section payloads (Node 1.2) ──────────
            tagged_sets: dict[str, set[str]] = {}
            for s in sentences:
                for tag in s.section_tags:
                    tagged_sets.setdefault(tag, set()).add(s.sentence_id)

            section_payloads: dict[str, list[str]] = {}
            for tab_id, tagged_ids in tagged_sets.items():
                section_payloads[tab_id] = build_payload(
                    tagged_ids, sentences, radius=3
                )

            # ── Phase C: Summarize sections (Node 1.3) ──────────────
            blueprint_map: dict[str, dict] = {
                b["tab_id"]: b for b in blueprint
            }
            # Build cross-section summary for context injection
            other_descriptions = "\n".join(
                f"- {b['tab_name']}: {b.get('section_description', '')}"
                for b in blueprint
                if b["tab_id"] not in ("other",)
            )

            section_mds: dict[str, str] = {}

            def summarize_one_section(tab_id: str, tagged_ids: set[str]) -> None:
                bp = blueprint_map.get(
                    tab_id,
                    {
                        "tab_name": tab_id,
                        "section_description": "",
                    },
                )
                payload_ids = section_payloads.get(tab_id, [])
                payload_json = _json.dumps(
                    chunk_json(payload_ids), ensure_ascii=False
                )

                prompt = MEETING_SECTION_SUMMARY_PROMPT.format(
                    section_name=bp.get("tab_name", tab_id),
                    section_description=bp.get("section_description", ""),
                    sentences_json=payload_json,
                    other_sections_summary=other_descriptions,
                )

                for attempt in range(3):
                    try:
                        raw = llm.generate(
                            prompt,
                            system=MEETING_SECTION_SUMMARY_SYSTEM,
                            max_tokens=8192,
                            thinking=True,
                        )
                        # DEBUG: dump raw section summary for ref debugging
                        _ref_lines = [l for l in raw.split("\n") if "stt_" in l]
                        if _ref_lines:
                            logger.info(
                                "[BREAKDOWN] Section '%s' RAW lines with stt_:\n%s",
                                bp.get("tab_name", tab_id),
                                "\n".join(_ref_lines),
                            )
                        # Strip invalid [ref:...] tags
                        raw = _clean_refs(raw, payload_ids)
                        with tag_results_lock:
                            section_mds[tab_id] = raw
                        logger.info(
                            "[BREAKDOWN] Section '%s' summarized (%d chars)",
                            bp.get("tab_name", tab_id), len(raw),
                        )
                        return
                    except Exception as exc:
                        logger.warning(
                            "[BREAKDOWN] Section '%s' attempt %d/3 failed: %s",
                            bp.get("tab_name", tab_id), attempt + 1, exc,
                        )
                        if attempt < 2:
                            import time
                            time.sleep(2 ** attempt)

                # Failed — mark as retryable
                with tag_results_lock:
                    section_mds[tab_id] = (
                        f"# {bp.get('tab_name', tab_id)}\n\n"
                        "⚠️ Generation failed. Click retry."
                    )
                logger.error(
                    "[BREAKDOWN] Section '%s' FAILED after 3 retries",
                    bp.get("tab_name", tab_id),
                )

            # All sections (concurrent)
            non_other_sections = {
                k: v for k, v in tagged_sets.items() if k != "other"
            }
            logger.info(
                "[BREAKDOWN] Summarizing %d sections",
                len(non_other_sections),
            )
            with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as pool:
                futures = [
                    pool.submit(summarize_one_section, tid, tids)
                    for tid, tids in non_other_sections.items()
                ]
                for f in as_completed(futures):
                    f.result()

            # ── Persist section mds and update metadata ─────────────
            updated_tabs = meeting.tabs or []
            for tab_entry in updated_tabs:
                tid = (
                    tab_entry["tab_id"]
                    if isinstance(tab_entry, dict)
                    else tab_entry.tab_id
                )
                if tid == "tab_general":
                    continue
                if tid in section_mds:
                    md_path = store.save_section_md(
                        meeting_id, tid, section_mds[tid]
                    )
                    if isinstance(tab_entry, dict):
                        tab_entry["md_file_path"] = md_path
                        if tid in section_payloads:
                            tab_entry["payload_ref"] = section_payloads[tid]
                    else:
                        tab_entry.md_file_path = md_path
                        if tid in section_payloads:
                            tab_entry.payload_ref = section_payloads[tid]

            store.update_meeting(
                meeting_id,
                tabs=updated_tabs,
                processing_state=ProcessingState.idle.value,
            )
            logger.info("[BREAKDOWN] Done for meeting %s", meeting_id)

        except Exception as e:
            logger.error(
                "[BREAKDOWN] Failed for meeting %s: %s",
                meeting_id, e, exc_info=True,
            )
            store.update_meeting(
                meeting_id,
                processing_state=ProcessingState.idle.value,
            )

    # -- Magic Extract (Pipeline 3) -----------------------------------------

    async def start_magic_extract(
        self, meeting_id: str, topics: list[dict], target_tab_id: str | None = None,
    ) -> Meeting:
        """Start background magic extract for one or more custom topics.

        topics: [{"name": "...", "description": "..."}]
        target_tab_id: if set, overwrite this tab instead of creating a new one
        """
        from src.meeting.models import ProcessingState

        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")
        if not topics:
            raise ValueError("At least one topic is required")
        if target_tab_id in ("tab_general", "other"):
            raise ValueError(f"Cannot overwrite '{target_tab_id}' tab")
        if meeting.processing_state != ProcessingState.idle.value:
            raise RuntimeError(f"Meeting is busy: {meeting.processing_state}")

        store.update_meeting(
            meeting_id,
            processing_state=ProcessingState.extracting.value,
        )
        import threading

        t = threading.Thread(
            target=self._do_magic_extract,
            args=(meeting_id, topics, target_tab_id),
            daemon=True,
        )
        t.start()
        return store.get_meeting(meeting_id)

    def _do_magic_extract(
        self, meeting_id: str, topics: list[dict], target_tab_id: str | None = None,
    ) -> None:
        """Node 2.2: concurrent constrained re-scan for multiple topics.

        Each topic re-scans all chunks (respecting existing tags as
        guardrails), then builds payload and summarizes.
        If target_tab_id is set, overwrites that tab instead of creating a new one.
        """
        import json as _json
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from src.meeting.models import ProcessingState
        from src.meeting.pipeline import build_payload
        from src.meeting.schemas import Chunk, Sentence
        from src.prompts import (
            MEETING_EXTRACT_PROMPT,
            MEETING_EXTRACT_SYSTEM,
            MEETING_SECTION_SUMMARY_PROMPT,
            MEETING_SECTION_SUMMARY_SYSTEM,
        )

        logger.info(
            "[EXTRACT] Starting for meeting %s with %d topics",
            meeting_id, len(topics),
        )
        try:
            meeting = store.get_meeting(meeting_id)
            if meeting is None:
                return

            sentences_data = store.get_sentences(meeting_id)
            chunks_data = store.get_chunks(meeting_id)
            if sentences_data is None or chunks_data is None:
                raise ValueError("No sentences or chunks data")

            sentences = [
                Sentence(**s) if isinstance(s, dict) else s
                for s in sentences_data
            ]
            chunks = [
                Chunk(**c) if isinstance(c, dict) else c
                for c in chunks_data
            ]
            existing_tabs = meeting.tabs or []

            # Pre-allocate tab_ids before concurrent processing to avoid
            # collision (all topics saw the same static existing_tabs snapshot).
            import re as _re_tab
            _max_n = 0
            for t in existing_tabs:
                m = _re_tab.match(r"tab_sec_(\d+)", t.get("tab_id", "") if isinstance(t, dict) else "")
                if m:
                    _max_n = max(_max_n, int(m.group(1)))
            _next_n = _max_n + 1
            topic_tab_ids: list[str] = []
            for _t in topics:
                if target_tab_id:
                    topic_tab_ids.append(target_tab_id)
                else:
                    topic_tab_ids.append(f"tab_sec_{_next_n:02d}")
                    _next_n += 1

            id_to_sentence: dict[str, Sentence] = {
                s.sentence_id: s for s in sentences
            }

            def chunk_json(chunk_sids: list[str]) -> list[dict]:
                return [
                    {
                        "sentence_id": sid,
                        "start_time": id_to_sentence[sid].start_time,
                        "speaker": id_to_sentence[sid].speaker,
                        "original_text": id_to_sentence[sid].original_text,
                    }
                    for sid in chunk_sids
                    if sid in id_to_sentence
                ]

            # Existing section summary for guardrail context
            existing_summary = _json.dumps(
                [
                    {
                        "tab_id": t.get("tab_id", ""),
                        "name": t.get("name", ""),
                    }
                    for t in existing_tabs
                    if t.get("type") == "section"
                ],
                ensure_ascii=False,
            )

            llm = services.llm
            if llm is None:
                llm = _resolve_default_llm()
            if llm is None:
                raise RuntimeError("No LLM provider configured.")

            MAX_CONCURRENT = 4

            def _rescan_chunks_for_topic(
                topic: dict, tab_id: str,
            ) -> tuple[set[str], dict[str, str]]:
                """Re-scan all chunks for one topic → (tagged_ids, {sentence_id: tab_id})"""
                topic_name = topic.get("name", "Untitled")
                topic_desc = topic.get("description", "")

                tagged_ids: set[str] = set()
                semaphore = threading.BoundedSemaphore(MAX_CONCURRENT)
                lock = threading.Lock()

                def rescan_one_chunk(idx: int, chunk: Chunk) -> None:
                    with semaphore:
                        target_json = _json.dumps(
                            chunk_json(chunk.sentence_refs),
                            ensure_ascii=False,
                        )
                        ctx_sids: list[str] = []
                        for offset in (2, 1):
                            ci = idx - offset
                            if ci >= 0:
                                ctx_sids.extend(chunks[ci].sentence_refs)
                        context_json = (
                            _json.dumps(chunk_json(ctx_sids), ensure_ascii=False)
                            if ctx_sids
                            else "[]"
                        )

                        prompt = MEETING_EXTRACT_PROMPT.format(
                            target_topic_name=topic_name,
                            target_topic_description=topic_desc,
                            existing_sections_json=existing_summary,
                            context_json=context_json,
                            target_json=target_json,
                        )

                        for attempt in range(3):
                            try:
                                raw = llm.generate(
                                    prompt,
                                    system=MEETING_EXTRACT_SYSTEM,
                                    max_tokens=2048,
                                    thinking=False,
                                )
                                mapping = _parse_tagging_response(raw)
                                sids = mapping.get("extract_target", [])
                                with lock:
                                    tagged_ids.update(sids)
                                logger.info(
                                    "[EXTRACT] '%s' chunk %d: %d sentences matched",
                                    topic_name, idx + 1, len(sids),
                                )
                                return
                            except Exception as exc:
                                logger.warning(
                                    "[EXTRACT] '%s' chunk %d attempt %d: %s",
                                    topic_name, idx + 1, attempt + 1, exc,
                                )
                                if attempt < 2:
                                    import time

                                    time.sleep(2 ** attempt)

                with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as pool:
                    futures = [
                        pool.submit(rescan_one_chunk, i, c)
                        for i, c in enumerate(chunks)
                    ]
                    for f in as_completed(futures):
                        f.result()

                # Build payload and summarize
                payload_ids = build_payload(tagged_ids, sentences, radius=3)

                # Cross-section summary
                other_descriptions = "\n".join(
                    f"- {t.get('name', '')}: {t.get('type', '')}"
                    for t in existing_tabs
                    if t.get("type") == "section"
                )

                payload_json = _json.dumps(
                    chunk_json(payload_ids), ensure_ascii=False
                )

                summary_prompt = MEETING_SECTION_SUMMARY_PROMPT.format(
                    section_name=topic_name,
                    section_description=topic_desc,
                    sentences_json=payload_json,
                    other_sections_summary=other_descriptions,
                )

                md_content = ""
                for attempt in range(3):
                    try:
                        raw = llm.generate(
                            summary_prompt,
                            system=MEETING_SECTION_SUMMARY_SYSTEM,
                            max_tokens=8192,
                            thinking=True,
                        )
                        md_content = _clean_refs(raw, payload_ids)
                        break
                    except Exception as exc:
                        logger.warning(
                            "[EXTRACT] '%s' summary attempt %d: %s",
                            topic_name, attempt + 1, exc,
                        )
                        if attempt < 2:
                            import time

                            time.sleep(2 ** attempt)

                if not md_content:
                    md_content = (
                        f"# {topic_name}\n\n"
                        "⚠️ Generation failed. Click retry."
                    )

                return tagged_ids, {
                    "tab_id": tab_id,
                    "type": "section",
                    "name": topic_name,
                    "md_content": md_content,
                    "payload_ids": payload_ids,
                }

            # ── Process all topics concurrently ─────────────────────
            results: list[tuple[set[str], dict]] = []
            with ThreadPoolExecutor(max_workers=len(topics)) as pool:
                futures = [
                    pool.submit(_rescan_chunks_for_topic, t, tid)
                    for t, tid in zip(topics, topic_tab_ids)
                ]
                for f in as_completed(futures):
                    results.append(f.result())

            # ── Apply tags & persist ────────────────────────────────
            # Build short-ID → full-ID lookup
            short_to_full: dict[str, str] = {}
            for fid in id_to_sentence:
                parts = fid.rsplit("_stt_", 1)
                if len(parts) == 2:
                    short_to_full["stt_" + parts[1]] = fid
            updated_tabs: list[dict] = list(existing_tabs) if existing_tabs else []
            for tagged_ids, meta in results:
                tab_id = meta["tab_id"]
                # Write tags to sentences
                for sid in tagged_ids:
                    full_sid = short_to_full.get(sid, sid)
                    sent = id_to_sentence.get(full_sid)
                    if sent and tab_id not in sent.section_tags:
                        sent.section_tags.append(tab_id)

                # Persist md
                md_path = store.save_section_md(
                    meeting_id, tab_id, meta["md_content"]
                )
                # Preserve existing tab type if overwriting
                existing = next((t for t in existing_tabs if t.get("tab_id") == tab_id), None)
                tab_type = existing.get("type", "section") if existing else "section"
                tab_entry = {
                    "tab_id": tab_id,
                    "type": tab_type,
                    "name": meta["name"],
                    "associated_collection_id": existing.get("associated_collection_id", "") if existing else "",
                    "associated_collection_name": existing.get("associated_collection_name", "") if existing else "",
                    "md_file_path": md_path,
                    "payload_ref": meta["payload_ids"],
                }
                # Replace existing or append
                if existing:
                    idx = updated_tabs.index(existing)
                    updated_tabs[idx] = tab_entry
                else:
                    updated_tabs.append(tab_entry)

            store.save_sentences(
                meeting_id,
                [
                    s.model_dump() if hasattr(s, "model_dump") else s
                    for s in sentences
                ],
            )
            store.update_meeting(
                meeting_id,
                tabs=updated_tabs,
                processing_state=ProcessingState.idle.value,
            )
            logger.info(
                "[EXTRACT] Done for meeting %s: %d new sections",
                meeting_id, len(results),
            )

        except Exception as e:
            logger.error(
                "[EXTRACT] Failed for meeting %s: %s",
                meeting_id, e, exc_info=True,
            )
            store.update_meeting(
                meeting_id,
                processing_state=ProcessingState.idle.value,
            )

    async def delete_section(self, meeting_id: str, tab_id: str) -> Meeting:
        """Node 2.1: remove a section and its tags from all sentences."""
        from src.meeting.models import ProcessingState

        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")
        if meeting.processing_state != ProcessingState.idle.value:
            raise RuntimeError(f"Meeting is busy: {meeting.processing_state}")

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

        md_path = store.section_md_path(meeting_id, tab_id)
        if md_path.exists():
            md_path.unlink()
            logger.info("[DELETE-SECTION] Removed md for %s/%s", meeting_id, tab_id)

        return store.get_meeting(meeting_id)

    async def start_section_regenerate(
        self, meeting_id: str, tab_id: str
    ) -> Meeting:
        """Regenerate one section: clean its tags, then re-scan and re-summarize.

        Uses the section's existing name and type as the extract topic.
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

        # Clean old tags for this section (Node 2.1)
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

        # Re-extract with section's own name + description
        bp_desc = ""
        if meeting.blueprint:
            for b in meeting.blueprint:
                if b.get("tab_id") == tab_id:
                    bp_desc = b.get("section_description", "")
                    break

        topics = [
            {
                "name": tab_meta.get("name", tab_id),
                "description": bp_desc or tab_meta.get("name", "Meeting section"),
            }
        ]
        return await self.start_magic_extract(meeting_id, topics, target_tab_id=tab_id)

    @staticmethod
    def _make_tab_id(existing_tabs: list[dict]) -> str:
        """Generate the next available section tab id (tab_sec_XX)."""
        import re as _re
        max_n = 0
        for t in (existing_tabs or []):
            tid = t.get("tab_id", "") if isinstance(t, dict) else ""
            m = _re.match(r"tab_sec_(\d+)", tid)
            if m:
                max_n = max(max_n, int(m.group(1)))
        return f"tab_sec_{max_n + 1:02d}"

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

        # Also add to meeting-level allocated_collections so Meeting Log can find it
        # Always append — a meeting may have multiple sections in the same collection
        existing_cols = list(meeting.allocated_collections or [])
        existing_fids = list(meeting.allocated_file_ids or [])
        existing_cols.append(collection_id)
        existing_fids.append(alloc_file_id)
        store.update_meeting(
            meeting_id,
            allocated_collections=existing_cols,
            allocated_file_ids=existing_fids,
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

        # Clear tab metadata
        updated_tabs: list[dict] = []
        for t in (meeting.tabs or []):
            td = t if isinstance(t, dict) else t.model_dump()
            if td.get("tab_id") == tab_id:
                td.pop("associated_collection_id", None)
                td.pop("associated_collection_name", None)
                td.pop("allocated_file_id", None)
            updated_tabs.append(td)

        store.update_meeting(meeting_id, tabs=updated_tabs)

        # Clean up meeting-level tracking: remove file_id; remove collection
        # if no other section still references it
        if file_id:
            existing_fids = list(meeting.allocated_file_ids or [])
            existing_cols = list(meeting.allocated_collections or [])
            if file_id in existing_fids:
                idx = existing_fids.index(file_id)
                existing_fids.pop(idx)
                if idx < len(existing_cols):
                    existing_cols.pop(idx)
            # Also check: any other tab still references this collection?
            col_still_used = any(
                td.get("associated_collection_id") == col_id
                for td in updated_tabs
            )
            if not col_still_used and col_id in existing_cols:
                existing_cols.remove(col_id)
            store.update_meeting(
                meeting_id,
                allocated_collections=existing_cols,
                allocated_file_ids=existing_fids,
            )

        updated = store.get_meeting(meeting_id)
        assert updated is not None
        return updated


def _parse_blueprint_response(raw: str) -> tuple[str, list[dict], str]:
    """Parse Node 0.3 LLM response into (general_md_content, blueprint, title).

    Tries JSON first; falls back to treating the entire response as
    markdown with an empty blueprint.
    """
    import json as _json
    import re as _re

    raw_stripped = raw.strip()

    # Try to extract JSON block
    json_match = _re.search(r"\{[\s\S]*\}", raw_stripped)
    if json_match:
        try:
            data = _json.loads(json_match.group())
            general_md = data.get("general_md_content", "")
            blueprint = data.get("blueprint", [])
            title = data.get("title", "")
            if isinstance(blueprint, list):
                return general_md, blueprint, title
        except (_json.JSONDecodeError, KeyError, TypeError):
            pass

    # Fallback: treat raw as plain markdown, infer single-general section
    logger.warning(
        "[BLUEPRINT] JSON parse failed, falling back to plain markdown "
        "(raw=%d chars)",
        len(raw_stripped),
    )
    return raw_stripped, [], ""


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


def _parse_tagging_response(raw: str) -> dict[str, list[str]]:
    """Parse Node 1.1 LLM tagging response into {tab_id: [sentence_ids]}."""
    import json as _json
    import re as _re

    raw_stripped = raw.strip()
    json_match = _re.search(r"\{[\s\S]*\}", raw_stripped)
    if json_match:
        try:
            data = _json.loads(json_match.group())
            mapping = data.get("mapping", {})
            if isinstance(mapping, dict):
                return {k: v for k, v in mapping.items() if isinstance(v, list)}
        except (_json.JSONDecodeError, KeyError, TypeError):
            pass

    logger.warning(
        "[TAGGING] Failed to parse LLM response (%d chars)", len(raw_stripped)
    )
    return {}


# Module-level singleton
meeting_service = MeetingService()
