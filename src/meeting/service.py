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
    update(90, "Updating meeting status...")

    # 5. Mark meeting as completed
    store.update_meeting(meeting_id, status=MeetingStatus.completed)
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

from src.prompts import MEETING_SUMMARY_SYSTEM, MEETING_SUMMARY_PROMPT


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
        """Start background summary generation. Returns immediately with summarizing=True."""
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")
        meeting.summarizing = True
        store.update_meeting(meeting_id, summarizing=True)
        import threading
        t = threading.Thread(target=self._do_generate_summary, args=(meeting_id,), daemon=True)
        t.start()
        return meeting

    def _do_generate_summary(self, meeting_id: str) -> None:
        """Background: generate summary via LLM and save to meeting store."""
        logger.info("[SUMMARY] Starting generation for meeting %s", meeting_id)
        try:
            meeting = store.get_meeting(meeting_id)
            if meeting is None:
                return

            transcript_result = store.get_transcript(meeting_id)
            notes = store.get_notes(meeting_id)
            speaker_names = meeting.speaker_names or {}

            transcript_text = transcript_result.text if transcript_result else "(No transcript available)"
            if speaker_names and transcript_result:
                lines = []
                for seg in transcript_result.segments:
                    name = speaker_names.get(seg.speaker_id, f"Speaker {seg.speaker_id}") if seg.speaker_id else ""
                    prefix = f"[{name}] " if name else ""
                    lines.append(f"{prefix}{seg.text}")
                transcript_text = "\n".join(lines)

            notes_text = notes if notes else "(No notes)"

            if speaker_names:
                speakers_text = "\n".join(f"- Speaker {sid}: {name}" for sid, name in speaker_names.items())
            else:
                speakers_text = "(No speaker names assigned)"
            logger.info("[SUMMARY] Transcript: %d chars, Notes: %d chars", len(transcript_text), len(notes_text))

            from src.rag.summary_manager import SummaryManager
            db_section_names: list[str] = []
            db_grouping_instruction = (
                "Group content into sections by project/topic. "
                "Each section goes into the 'sections' array with its own heading, detail, summary, and todos."
            )
            try:
                sm = SummaryManager(db=services.db, vector_size=_detect_embedding_dim())
                sm.ensure_collection()
                project_descs = sm.get_all_project_descriptions()
                if project_descs:
                    desc_map = {d.get("collection_id", ""): d.get("content", "") for d in project_descs}
                    db_section_names = [d.get("collection_id", "") for d in project_descs if d.get("collection_id")]
                    if db_section_names:
                        existing_lines = []
                        for name in db_section_names:
                            desc = desc_map.get(name, "")
                            if desc:
                                existing_lines.append(f"- **{name}**: {desc}")
                            else:
                                existing_lines.append(f"- **{name}**")
                        existing_block = "\n".join(existing_lines)
                        db_grouping_instruction = (
                            "Existing projects in the database (match meeting content to these if relevant):\n\n"
                            f"{existing_block}\n\n"
                            "Use these project names ONLY as reference for creating sections. "
                            "Create one section per matching project. "
                            "Content that doesn't match any project goes into an 'Other Topics' section. "
                            "If the meeting introduces a new project/initiative, create a section for it. "
                            "Do NOT mention in the output which projects matched or didn't match — just organize the content into the appropriate sections."
                        )
                    logger.info("[SUMMARY] Found %d project descriptions for grouping", len(project_descs))
            except Exception as e:
                logger.warning("[SUMMARY] Failed to fetch project descriptions: %s", e)

            hot_words_text = "(None)"
            if meeting.hot_words_library_id:
                try:
                    from src.hot_words.store import get_library
                    lib = get_library(meeting.hot_words_library_id)
                    if lib and lib.words:
                        hot_words_text = ", ".join(w.text for w in lib.words)
                        logger.info("[SUMMARY] Loaded %d hot words from library %s", len(lib.words), lib.name)
                except Exception:
                    logger.warning("[SUMMARY] Failed to load hot words", exc_info=True)

            prompt = MEETING_SUMMARY_PROMPT.format(
                transcript=transcript_text, notes=notes_text, speakers=speakers_text,
                database_grouping_instruction=db_grouping_instruction,
                hot_words=hot_words_text,
            )
            logger.info("[SUMMARY] Calling LLM with %d char prompt...", len(prompt))

            llm = services.llm
            if llm is None:
                from src.config import get_config
                from src.providers.llm import create_llm_for_provider
                config = get_config()
                if config.llm.providers:
                    default_p = next((p for p in config.llm.providers if p.is_default), config.llm.providers[0])
                    llm = create_llm_for_provider(default_p)
            if llm is None:
                raise RuntimeError("No LLM provider configured. Add one in Settings first.")

            raw_response = llm.generate(
                prompt,
                system=MEETING_SUMMARY_SYSTEM,
                max_tokens=32768,
                thinking=True,
            )
            logger.info("[SUMMARY] LLM returned %d chars", len(raw_response))

            title, detail, summary, todos, sections = _parse_summary_response(raw_response)
            logger.info("[SUMMARY] Parsed: title='%s' detail=%d chars summary=%d chars todos=%d sections=%d",
                        title, len(detail), len(summary), len(todos), len(sections) if sections else 0)

            if not summary and detail:
                lines = [l.strip() for l in detail.split("\n") if l.strip() and not l.strip().startswith("#")]
                if lines:
                    summary = "\n".join(lines[:5])
                    logger.info("[SUMMARY] Summary was empty, extracted %d chars from detail as fallback", len(summary))

            update_fields: dict = dict(detail=detail, summary=summary, todos=todos, sections=sections, summarizing=False)
            if title:
                prefix = meeting.created_at.strftime("%Y-%m-%d %H:%M")
                update_fields["title"] = f"{prefix} {title}"
            store.update_meeting(meeting_id, **update_fields)
            logger.info("[SUMMARY] Done for meeting %s", meeting_id)
        except Exception as e:
            logger.error("[SUMMARY] Failed for meeting %s: %s", meeting_id, e)
            store.update_meeting(meeting_id, summarizing=False)

    async def generate_summary(self, meeting_id: str) -> Meeting:
        """Legacy: kept for backward compat. Use start_generate_summary for async."""
        return await self.start_generate_summary(meeting_id)

    # -- Collection allocation ----------------------------------------------

    async def allocate_to_collection(self, meeting_id: str, collection: str) -> dict:
        """Allocate meeting content to a Database collection via the upload pipeline.

        For same-collection re-ingest: deletes old allocation first, then creates new.
        For cross-collection migrate: creates new allocation first, deletes old only after success.
        """
        import re as _re

        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")

        old_collection = meeting.allocated_collections[0] if meeting.allocated_collections else None
        old_file_id = meeting.allocated_file_ids[0] if meeting.allocated_file_ids else None
        is_migrate = old_collection and old_file_id and old_collection != collection

        # For re-ingest (same collection): delete old first
        if old_collection and old_file_id and not is_migrate:
            self._delete_allocation(old_collection, old_file_id)

        # 2. Build combined content
        content_parts: list[str] = []
        if meeting.detail:
            content_parts.append(f"# Detail\n\n{meeting.detail}")
        if meeting.summary:
            content_parts.append(f"# Summary\n\n{meeting.summary}")
        if meeting.todos:
            todos_md = "\n".join(f"- {t.get('text', str(t))}" for t in meeting.todos)
            content_parts.append(f"# TODO\n\n{todos_md}")
        notes = store.get_notes(meeting_id)
        if notes:
            content_parts.append(f"# Notes\n\n{notes}")

        if not content_parts:
            raise ValueError(
                f"Meeting {meeting_id} has no content to allocate. "
                "Add notes or generate a summary first."
            )

        combined = "\n\n---\n\n".join(content_parts)

        # 3. Save meeting content with section-based identity
        section_source = f"__meeting__:{meeting_id}_0"
        alloc_file_id = uuid.uuid4().hex
        file_dir = _files_dir(collection) / alloc_file_id
        file_dir.mkdir(parents=True, exist_ok=True)
        file_path = file_dir / "meeting.md"
        file_path.write_text(combined, encoding="utf-8")

        # 4. Call upload pipeline directly
        from src.tasks.handlers import upload_handler

        upload_task = Task(
            id=str(uuid.uuid4()),
            filename=f"meeting_{meeting_id}",
            collection=collection,
            status=TaskStatus.PROCESSING,
            created_at=datetime.now(),
        )

        result = await upload_handler(upload_task, str(file_path), collection, section_source,
                                      source_label=f"Meeting: {meeting.title}", file_id=alloc_file_id)

        # 5. Track allocation in meeting metadata
        store.update_meeting(
            meeting_id,
            allocated_collections=[collection],
            allocated_file_ids=[alloc_file_id],
        )

        updated = store.get_meeting(meeting_id)
        assert updated is not None

        logger.info(
            "Allocated meeting %s to collection '%s' (%d chunks)",
            meeting_id,
            collection,
            result.get("chunks_count", 0),
        )

        # For migrate (different collection): delete old allocation AFTER success
        if is_migrate and old_collection and old_file_id:
            self._delete_allocation(old_collection, old_file_id)

        return updated

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

    async def delete_all_allocations(self, meeting_id: str) -> None:
        """Delete all meeting allocations from all collections and clear meeting allocation fields."""
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")

        collections = meeting.allocated_collections or []
        file_ids = meeting.allocated_file_ids or []

        for col, fid in zip(collections, file_ids):
            self._delete_allocation(col, fid)

        # Also check old single-collection format
        if not collections and meeting.allocated_collection and meeting.allocated_file_id:
            self._delete_allocation(meeting.allocated_collection, meeting.allocated_file_id)

        # Clear allocation fields
        store.update_meeting(meeting_id, allocated_collections=[], allocated_file_ids=[])
        logger.info("[RE-INGEST] Deleted all allocations for meeting %s", meeting_id)

    # -- Project splitting --------------------------------------------------

    async def split_by_project(self, meeting_id: str) -> list[dict]:
        """Extract project sections from the meeting's structured summary sections."""
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")

        if meeting.sections:
            projects = []
            for s in meeting.sections:
                heading = s.get("heading", "")
                projects.append({
                    "name": heading.strip(),
                    "summary": s.get("summary", ""),
                    "detail": s.get("detail", ""),
                    "todos": s.get("todos", []),
                })
            return projects

        if not meeting.detail and not meeting.summary:
            raise ValueError(
                f"Meeting {meeting_id} has no content to split. "
                "Generate a summary first."
            )

        # Fallback: treat entire meeting as a single project
        title = meeting.title or "Meeting"
        name = title.split(" ", 1)[-1] if title[:10].count("-") >= 2 else title
        return [{
            "name": name.strip(),
            "summary": meeting.summary or "",
            "detail": meeting.detail or "",
            "todos": meeting.todos or [],
        }]

    # -- Collection recommendation -----------------------------------------

    @staticmethod
    def _get_collection_docs(sm) -> tuple[list[str], list[str]]:
        """Return (collection_names, collection_docs) using project descriptions.

        Uses the 2-sentence project descriptions generated during
        consolidation. Falls back to doc summaries if no project
        description exists for a collection.
        """
        project_descs = sm.get_all_project_descriptions()
        if project_descs:
            names = [d.get("collection_id", "") for d in project_descs]
            # Prepend project name so reranker can match on name + description
            docs = [f"{d.get('collection_id', '')}: {d.get('content', '')}" for d in project_descs]
            return names, docs

        # Fallback: collections with summaries but no project descriptions yet
        summaries = sm.get_all_collection_summaries()
        names = [s.get("collection_id", "") for s in summaries]
        docs = []
        for name in names:
            doc_summaries = sm.get_doc_summaries(name, included_only=True)
            if doc_summaries:
                lines = [f"Project: {name}"]
                for ds in doc_summaries:
                    for item in ds.get("data", []) + ds.get("facts", []):
                        candidate = f"- {item}"
                        if len("\n".join(lines + [candidate])) <= 1500:
                            lines.append(candidate)
                docs.append("\n".join(lines))
            else:
                cs = sm.get_collection_summary(name)
                docs.append(cs["content"][:1500] if cs and cs.get("content") else name)
        return names, docs

    async def recommend_collections(self, meeting_id: str) -> list[dict]:
        """Recommend collections based on reranker scoring against project descriptions."""
        from src.rag.summary_manager import SummaryManager

        logger.info("[RECOMMEND] Starting recommendation for meeting %s", meeting_id)
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")

        parts = []
        if meeting.detail:
            parts.append(meeting.detail)
        meeting_text = "\n\n".join(parts)

        if not meeting_text:
            logger.info("[RECOMMEND] No meeting text available, returning empty")
            return []

        if not services.reranker_provider:
            logger.warning("[RECOMMEND] No reranker configured, cannot recommend collections")
            return []

        logger.info("[RECOMMEND] Meeting text: %d chars", len(meeting_text))

        sm = SummaryManager(db=services.db, vector_size=_detect_embedding_dim())
        sm.ensure_collection()
        collection_names, collection_docs = self._get_collection_docs(sm)
        if not collection_names:
            return []

        logger.info("[RECOMMEND] Found %d collections to score", len(collection_names))

        loop = asyncio.get_running_loop()
        ranked = await loop.run_in_executor(
            None,
            lambda: services.reranker_provider.rerank(
                query=meeting_text,
                documents=collection_docs,
                top_k=len(collection_docs),
            ),
        )

        results = [
            {"collection": collection_names[idx], "score": round(score, 4)}
            for idx, score in ranked
        ]
        results.sort(key=lambda r: r["score"], reverse=True)
        logger.info("[RECOMMEND] Meeting %s: %d recommendations (top=%s score=%.4f)",
                    meeting_id, len(results),
                    results[0]["collection"] if results else "none",
                    results[0]["score"] if results else 0)
        return results

    async def recommend_collections_for_text(self, text: str) -> list[dict]:
        """Recommend collections based on reranker scoring for arbitrary text."""
        from src.rag.summary_manager import SummaryManager

        if not text.strip():
            return []

        if not services.reranker_provider:
            logger.warning("[RECOMMEND] No reranker configured, cannot recommend collections")
            return []

        vec_size = _detect_embedding_dim()
        sm = SummaryManager(db=services.db, vector_size=vec_size)
        sm.ensure_collection()
        collection_names, collection_docs = self._get_collection_docs(sm)
        if not collection_names:
            return []

        loop = asyncio.get_running_loop()
        ranked = await loop.run_in_executor(
            None,
            lambda: services.reranker_provider.rerank(
                query=text,
                documents=collection_docs,
                top_k=len(collection_docs),
            ),
        )

        results = [
            {"collection": collection_names[idx], "score": round(score, 4)}
            for idx, score in ranked
        ]
        results.sort(key=lambda r: r["score"], reverse=True)
        logger.info("[RECOMMEND] Reranker scored %d collections, top='%s' (%.4f)",
                    len(results), results[0]["collection"] if results else "none",
                    results[0]["score"] if results else 0)
        return results

    # -- Multi-collection allocation ---------------------------------------

    async def allocate_to_multiple_collections(
        self, meeting_id: str, allocations: list[dict]
    ) -> list[dict]:
        """Allocate meeting content to multiple collections.

        allocations: [{"collection": "name", "content": "markdown content", "project_name": "..."}]
        """
        import re as _re

        logger.info("[ALLOCATE-MULTI] Starting multi-allocation for meeting %s (%d allocations)", meeting_id, len(allocations))
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")

        if not allocations:
            raise ValueError("allocations list is empty")

        results = []
        allocated_collections: list[str] = []
        allocated_file_ids: list[str] = []

        for section_idx, alloc in enumerate(allocations):
            collection = alloc["collection"]
            content = alloc["content"]

            # Each section gets a unique source and file_id
            section_source = f"__meeting__:{meeting_id}_{section_idx}"
            alloc_file_id = uuid.uuid4().hex
            file_dir = _files_dir(collection) / alloc_file_id
            file_dir.mkdir(parents=True, exist_ok=True)
            file_path = file_dir / "meeting.md"
            file_path.write_text(content, encoding="utf-8")

            # Call upload pipeline
            from src.tasks.handlers import upload_handler

            upload_task = Task(
                id=str(uuid.uuid4()),
                filename=f"meeting_{meeting_id}",
                collection=collection,
                status=TaskStatus.PROCESSING,
                created_at=datetime.now(),
            )
            result = await upload_handler(upload_task, str(file_path), collection, section_source,
                                          meeting_id=meeting_id,
                                          source_label=f"Meeting: {meeting.title}",
                                          file_id=alloc_file_id)

            allocated_collections.append(collection)
            allocated_file_ids.append(alloc_file_id)

            results.append({
                "collection": collection,
                "chunks_count": result.get("chunks_count", 0),
            })

            logger.info(
                "Allocated meeting %s to collection '%s' (%d chunks)",
                meeting_id, collection, result.get("chunks_count", 0),
            )

        # Track all allocations in meeting metadata
        store.update_meeting(
            meeting_id,
            allocated_collections=allocated_collections,
            allocated_file_ids=allocated_file_ids,
        )

        return results


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_summary_response(raw: str) -> tuple[str, str, str, list[dict], list[dict] | None]:
    """Parse LLM response into (title, detail, summary, todos, sections).

    Returns sections=None if JSON parsing fails (fallback to legacy format).
    Each section dict has keys: heading, detail, summary, todos.
    """
    import json as _json
    import re as _re

    # Try JSON first
    raw_stripped = raw.strip()
    json_match = _re.search(r"\{[\s\S]*\}", raw_stripped)
    if json_match:
        try:
            data = _json.loads(json_match.group())
            title = data.get("title", "")[:80]
            sections = data.get("sections", [])
            if sections and isinstance(sections, list):
                # Compute flat fields from sections
                detail_parts = [f"## {s.get('heading', '')}\n\n{s.get('detail', '')}" for s in sections]
                summary_parts = [f"## {s.get('heading', '')}\n\n{s.get('summary', '')}" for s in sections]
                todos = []
                for s in sections:
                    section_todos = s.get("todos", [])
                    if isinstance(section_todos, list):
                        for t in section_todos:
                            if isinstance(t, dict) and t.get("text"):
                                item = {"text": t["text"]}
                                if t.get("assignee"):
                                    item["assignee"] = t["assignee"]
                                if t.get("priority"):
                                    item["priority"] = t["priority"]
                                todos.append(item)
                detail = "\n\n".join(detail_parts)
                summary = "\n\n".join(summary_parts)
                return title, detail, summary, todos, sections
        except (_json.JSONDecodeError, KeyError, TypeError):
            pass

    # Fallback: legacy === delimiter format
    title = ""
    detail_parts: list[str] = []
    summary_parts: list[str] = []
    todos: list[dict] = []

    sections = raw.split("===")

    current_section = None
    for part in sections:
        stripped = part.strip()
        if stripped == "TITLE":
            current_section = "title"
            continue
        elif stripped == "DETAIL":
            current_section = "detail"
            continue
        elif stripped == "SUMMARY":
            current_section = "summary"
            continue
        elif stripped == "TODO":
            current_section = "todo"
            continue

        if not stripped:
            continue

        if current_section == "title" and not title:
            title = stripped.split("\n")[0].strip()[:80]
        elif current_section == "detail":
            detail_parts.append(stripped)
        elif current_section == "summary":
            summary_parts.append(stripped)
        elif current_section == "todo":
            parsed = _parse_todos(stripped)
            todos.extend(parsed)

    detail = "\n".join(detail_parts)
    summary = "\n".join(summary_parts)
    return title, detail, summary, todos, None


def _parse_todos(raw: str) -> list[dict]:
    """Extract JSON arrays from the TODO section of the LLM response.

    Handles multiple JSON arrays (e.g., from PROJECT sub-sections).
    """
    # Try to find ALL JSON arrays in the text and merge them
    all_todos: list[dict] = []
    remaining = raw
    while True:
        start = remaining.find("[")
        if start == -1:
            break
        # Find matching closing bracket
        depth = 0
        end = -1
        for i in range(start, len(remaining)):
            if remaining[i] == "[":
                depth += 1
            elif remaining[i] == "]":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end == -1:
            break
        try:
            parsed = json.loads(remaining[start : end + 1])
            if isinstance(parsed, list):
                all_todos.extend(parsed)
        except json.JSONDecodeError:
            pass
        remaining = remaining[end + 1:]

    if all_todos:
        return all_todos

    # Fallback: treat each non-empty line as a todo item
    items: list[dict] = []
    for line in raw.splitlines():
        line = line.strip().lstrip("-").lstrip("*").strip()
        if line and not line.startswith("PROJECT:"):
            items.append({"text": line})
    return items


# Module-level singleton
meeting_service = MeetingService()
