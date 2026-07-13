"""MCP Meeting management tools.

6 atomic tools wrapping ``src.meeting`` (audio + transcript + summary):

- :func:`list_meetings` — list all meetings (lightweight)
- :func:`get_meeting` — full meeting info (metadata + transcript + summary
  + notes + sections + task status)
- :func:`create_meeting` — create a new meeting shell
- :func:`update_meeting` — update editable fields (title, status, mode,
  speaker_names, hot_words_library_id, notes content)
- :func:`delete_meeting` — delete meeting + clean up ingested chunks
- :func:`start_meeting_summary` — kick off async summary generation
  (returns a ``task_id`` — poll with :func:`list_tasks`)

Notes
-----
- **Audio upload** (``upload-audio``) is not exposed via MCP because audio
  bytes are large; use the API/UI for that, then call
  ``start_meeting_summary`` once the transcript is ready.
- **Real-time transcription** is a WebSocket-only flow; it's not exposed
  via MCP.
- **Section-level operations** (allocate, regenerate, etc.) are also UI-
  driven because they require interactive review of the blueprint.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp.common import err, ok, run_sync, to_json

logger = logging.getLogger(__name__)


# ── list_meetings ──────────────────────────────────────────────


async def list_meetings(
    limit: int = 100,
    status: str | None = None,
    search: str = "",
) -> str:
    """List meetings sorted by ``updated_at`` descending.

    Returns lightweight metadata only (no transcript, summary, detail,
    blueprint, or tabs). Use :func:`get_meeting` for full content.

    Args:
        limit: Max meetings to return (default 100).
        status: Filter by status — ``"created"`` / ``"recording"`` /
            ``"transcribing"`` / ``"completed"``. Omit for all.
        search: Case-insensitive title substring filter. Omit for all.
    """
    _LIGHT_FIELDS = {
        "id", "title", "status", "mode",
        "processing_state", "summary_gen_state", "blueprint_gen_state",
        "speaker_names", "hot_words_library_id",
        "allocated_collections", "allocated_file_ids",
        "created_at", "updated_at",
    }

    def _run() -> dict[str, Any]:
        from src.meeting import store as mstore

        meetings = mstore.list_meetings()
        if status:
            meetings = [m for m in meetings if m.status.value == status]
        if search:
            q = search.lower()
            meetings = [m for m in meetings if q in (m.title or "").lower()]
        total = len(meetings)
        meetings = meetings[:limit]
        items = []
        for m in meetings:
            d = {k: v for k, v in m.model_dump().items() if k in _LIGHT_FIELDS}
            d["created_at"] = m.created_at.isoformat()
            d["updated_at"] = m.updated_at.isoformat()
            items.append(d)
        return ok(meetings=items, total=total, returned=len(items))

    return to_json(await run_sync(_run))


# ── get_meeting ────────────────────────────────────────────────


async def get_meeting(meeting_id: str) -> str:
    """Get full meeting info: metadata + transcript + summary + notes + sections.

    Includes:
    - All Meeting model fields (status, mode, audio_path, etc.)
    - ``notes_content`` (raw markdown if notes.md exists)
    - ``transcript`` (segments with speaker_id, start, end, text)
    - ``summary`` (general summary markdown)
    - ``blueprint`` (section tabs with their descriptions)
    - ``tasks`` (active async tasks for this meeting)
    """
    def _run() -> dict[str, Any]:
        from src.meeting import store as mstore
        from src.tasks import task_manager

        m = mstore.get_meeting(meeting_id)
        if not m:
            return err(f"Meeting '{meeting_id}' not found")

        data = m.model_dump()
        data["created_at"] = m.created_at.isoformat()
        data["updated_at"] = m.updated_at.isoformat()

        # Embed transcript (prefer sentences.json when available for richer data)
        sentences = mstore.get_sentences(meeting_id)
        if sentences:
            data["transcript"] = {
                "segments": [
                    {
                        "start": s.get("start_time", 0),
                        "end": s.get("end_time", 0),
                        "text": s.get("original_text", ""),
                        "speaker_id": s.get("speaker", ""),
                        "sentence_id": s.get("sentence_id", ""),
                        "section_tags": s.get("section_tags", []),
                    }
                    for s in sentences
                ],
                "source": "sentences",
            }
        else:
            t = mstore.get_transcript(meeting_id)
            if t:
                data["transcript"] = {
                    "segments": [s.model_dump() for s in t.segments],
                    "text": t.text,
                    "language": t.language,
                    "source": "transcript",
                }
            else:
                data["transcript"] = None

        # Embed notes content
        notes = mstore.get_notes(meeting_id)
        data["notes_content"] = notes

        # Embed section markdown (for each tab if available)
        if m.tabs:
            sections = []
            for tab in m.tabs:
                if isinstance(tab, dict):
                    tid = tab.get("tab_id", "")
                    tname = tab.get("name", "")
                else:
                    tid = getattr(tab, "tab_id", "")
                    tname = getattr(tab, "name", "")
                if not tid:
                    continue
                md = mstore.get_section_md(meeting_id, tid)
                sections.append({"tab_id": tid, "name": tname, "content": md})
            data["sections"] = sections
        else:
            data["sections"] = []

        # Embed active tasks
        tasks = []
        for t in task_manager.get_all_tasks():
            args = task_manager._task_args.get(t.id)
            if args and args[1].get("meeting_id") == meeting_id:
                tasks.append(t.to_dict_with_type(args[0]))
        data["tasks"] = tasks

        return ok(**data)

    return to_json(await run_sync(_run))


# ── create_meeting ─────────────────────────────────────────────


async def create_meeting(
    title: str = "",
    mode: str | None = None,
) -> str:
    """Create a new empty meeting shell.

    Args:
        title: Display title (defaults to current timestamp).
        mode: ``"upload"`` for audio file upload, ``"record"`` for realtime
            recording. If ``None``, mode can be set later via
            :func:`update_meeting`.
    """
    from datetime import datetime
    from src.meeting.models import MeetingMode

    def _run() -> dict[str, Any]:
        from src.meeting import store as mstore

        final_title = title.strip() or datetime.now().strftime("%Y-%m-%d %H:%M")
        meeting_mode = MeetingMode(mode) if mode else None
        m = mstore.create_meeting(title=final_title, mode=meeting_mode)
        d = m.model_dump()
        d["created_at"] = m.created_at.isoformat()
        d["updated_at"] = m.updated_at.isoformat()
        return ok(**d)

    return to_json(await run_sync(_run))


# ── update_meeting ─────────────────────────────────────────────


async def update_meeting(
    meeting_id: str,
    title: str | None = None,
    status: str | None = None,
    speaker_names: dict[str, str] | None = None,
    hot_words_library_id: str | None = None,
    notes_content: str | None = None,
) -> str:
    """Update editable meeting fields.

    Args:
        meeting_id: Target meeting.
        title: New title (optional).
        status: New status — ``created`` / ``recording`` / ``transcribing``
            / ``completed``.
        speaker_names: Map ``speaker_id`` → ``display_name`` (e.g.
            ``{"S1": "Alice"}``).
        hot_words_library_id: ID of a hot words library to use for
            transcription.
        notes_content: New notes content (Markdown).
    """
    def _run() -> dict[str, Any]:
        from src.meeting import store as mstore

        m = mstore.get_meeting(meeting_id)
        if not m:
            return err(f"Meeting '{meeting_id}' not found")

        updated_fields: list[str] = []
        kwargs: dict[str, Any] = {}

        if title is not None:
            kwargs["title"] = title
            updated_fields.append("title")
        if status is not None:
            kwargs["status"] = status
            updated_fields.append("status")
        if speaker_names is not None:
            kwargs["speaker_names"] = speaker_names
            updated_fields.append("speaker_names")
        if hot_words_library_id is not None:
            kwargs["hot_words_library_id"] = hot_words_library_id
            updated_fields.append("hot_words_library_id")

        if kwargs:
            m = mstore.update_meeting(meeting_id, **kwargs)

        if notes_content is not None:
            mstore.save_notes(meeting_id, notes_content)
            updated_fields.append("notes_content")

        if not updated_fields:
            return err("No valid fields to update")

        m = mstore.get_meeting(meeting_id) or m
        d = m.model_dump()
        d["created_at"] = m.created_at.isoformat()
        d["updated_at"] = m.updated_at.isoformat()
        return ok(message=f"Meeting '{meeting_id}' updated", updated_fields=updated_fields, **d)

    return to_json(await run_sync(_run))


# ── delete_meeting ─────────────────────────────────────────────


async def delete_meeting(meeting_id: str) -> str:
    """Delete a meeting and clean up everything that references it.

    Removes:
    - the meeting directory (``data/meetings/{meeting_id}/``)
    - any chunks ingested into allocated collections (via file_index lookup)
    - corresponding file snapshots and ``files.json`` entries
    """
    def _run() -> dict[str, Any]:
        from src.meeting import store as mstore
        from src.services import services
        from src.collections.file_index import (
            load as load_file_index,
            remove as remove_file_index,
            COLLECTIONS_DIR as _CDIR,
        )
        import shutil as _shutil

        m = mstore.get_meeting(meeting_id)
        if not m:
            return err(f"Meeting '{meeting_id}' not found")

        cleaned_allocations: list[dict[str, str]] = []
        if m.allocated_collections and m.allocated_file_ids:
            for col, fid in zip(m.allocated_collections, m.allocated_file_ids):
                try:
                    idx = load_file_index(col)
                    entry = idx.get(fid, {})
                    source = entry.get("source", "")
                    if source:
                        services.db.delete_by_filter(col, key="source", value=source)
                        snap_dir = _CDIR / col / "files" / fid
                        if snap_dir.exists():
                            _shutil.rmtree(snap_dir)
                        remove_file_index(col, fid)
                        cleaned_allocations.append({
                            "collection": col, "file_id": fid, "source": source,
                        })
                except Exception as e:
                    logger.warning(
                        "Failed to clean Qdrant points for meeting %s in %s: %s",
                        meeting_id, col, e,
                    )

        deleted = mstore.delete_meeting(meeting_id)
        if not deleted:
            return err(f"Meeting '{meeting_id}' could not be deleted")
        return ok(
            message=f"Meeting '{meeting_id}' deleted",
            cleaned_allocations=cleaned_allocations,
        )

    return to_json(await run_sync(_run))


# ── start_meeting_summary ──────────────────────────────────────


async def start_meeting_summary(meeting_id: str) -> str:
    """Trigger async summary generation for a meeting.

    Returns a ``task_id`` that you can poll with :func:`list_tasks` or
    :func:`get_task_status`. Fails if:
    - the meeting doesn't exist
    - there's no transcript yet
    - the meeting is already busy (summarizing / extracting)
    """
    def _run() -> dict[str, Any]:
        from src.meeting import store as mstore
        from src.meeting.models import ProcessingState
        from src.tasks import task_manager

        m = mstore.get_meeting(meeting_id)
        if not m:
            return err(f"Meeting '{meeting_id}' not found")
        if m.processing_state != ProcessingState.idle.value:
            return err(
                f"Meeting is busy: {m.processing_state}",
                processing_state=m.processing_state,
            )
        transcript = mstore.get_transcript(meeting_id)
        if not transcript:
            return err("No transcript available")

        mstore.update_meeting(
            meeting_id, processing_state=ProcessingState.summarizing.value,
        )
        task = task_manager.create_task(
            filename=f"summary:{meeting_id}",
            task_type="meeting_summary",
            meeting_id=meeting_id,
        )
        return ok(
            message="Summary generation started",
            meeting_id=meeting_id,
            task_id=task.id,
        )

    return to_json(await run_sync(_run))


__all__ = [
    "list_meetings",
    "get_meeting",
    "create_meeting",
    "update_meeting",
    "delete_meeting",
    "start_meeting_summary",
]