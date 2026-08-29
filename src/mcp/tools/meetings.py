"""MCP Meeting management tools.

9 atomic tools wrapping ``src.meeting`` (audio + transcript + summary):

- :func:`list_meetings` — list all meetings (lightweight)
- :func:`get_meeting` — metadata + tabs list + notes (no transcript/sections content)
- :func:`get_section` — single section/tab markdown (tab_id="general" for summary)
- :func:`get_meeting_transcript` — paginated transcript segments
- :func:`create_meeting` — create a new meeting shell
- :func:`update_meeting` — update editable fields (title, status, mode,
  speaker_names, hot_words_library_id, notes content)
- :func:`delete_meeting` — delete meeting + clean up ingested chunks
- :func:`start_meeting_summary` — kick off async summary generation
  (returns a ``task_id`` — poll with :func:`list_tasks`)
- :func:`upload_meeting_audio_from_staging` — upload audio via staging
  token (same zero-leak pattern as ``upload_document_from_staging``)

Notes
-----
- **Audio upload** uses the unified staging pattern.
- **Real-time transcription** is a WebSocket-only flow; not exposed via MCP.
- **Section-level operations** (allocate, regenerate, etc.) are UI-driven.
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

    Returns lightweight metadata only (no transcript, summary, tabs).
    Use :func:`get_meeting` for tabs list and :func:`get_section` for content.

    Args:
        limit: Max meetings to return (default 100).
        status: Filter by status — ``"created"`` / ``"recording"`` /
            ``"transcribing"`` / ``"completed"``. Omit for all.
        search: Case-insensitive title substring filter. Omit for all.
    """
    _LIGHT_FIELDS = {
        "id", "title", "status", "mode",
        "processing_state", "summary_gen_state", "blueprint_gen_state",
        "speaker_names", "hot_words_library_id", "hot_words_library_ids",
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


# ── get_meeting (lightweight metadata) ──────────────────────────


async def get_meeting(meeting_id: str) -> str:
    """Get meeting **metadata only** — lightweight, always safe to call.

    Returns:
    - id, title, status, mode, processing_state
    - tabs: list of ``{tab_id, name, description}`` — use :func:`get_section`
      to read the markdown content of any tab.
      The first tab always has ``tab_id="general"`` (the overall summary).
    - notes_content, speaker_names, allocated_collections
    - has_transcript, has_summary, has_notes: quick flags
    - created_at, updated_at

    Does **not** return transcript segments or section markdown content.
    Use :func:`get_meeting_transcript` and :func:`get_section` for those.
    """
    def _run() -> dict[str, Any]:
        from src.meeting import store as mstore

        m = mstore.get_meeting(meeting_id)
        if not m:
            return err(f"Meeting '{meeting_id}' not found")

        # ── Tabs list (id + name + description only, no content) ──
        tabs: list[dict] = []
        # Always put "general" first
        tabs.append({
            "tab_id": "general",
            "name": "总体摘要",
            "description": "",
        })
        if m.tabs:
            for tab in m.tabs:
                if isinstance(tab, dict):
                    tid = tab.get("tab_id", "")
                    tname = tab.get("name", "")
                    tdesc = tab.get("description", "")
                else:
                    tid = getattr(tab, "tab_id", "")
                    tname = getattr(tab, "name", "")
                    tdesc = getattr(tab, "description", "")
                if tid:
                    tabs.append({
                        "tab_id": tid,
                        "name": tname,
                        "description": tdesc or "",
                    })

        # ── Flags ──
        has_transcript = bool(m.transcript_path or mstore.get_sentences(meeting_id))
        has_summary = bool(m.summary)
        has_notes = bool(mstore.get_notes(meeting_id))

        return ok(
            id=m.id,
            title=m.title,
            status=m.status.value,
            mode=m.mode.value if m.mode else None,
            processing_state=m.processing_state,
            summary_gen_state=m.summary_gen_state,
            blueprint_gen_state=m.blueprint_gen_state,
            speaker_names=m.speaker_names,
            hot_words_library_id=m.hot_words_library_id,
            hot_words_library_ids=list(m.hot_words_library_ids or []),
            tabs=tabs,
            allocated_collections=m.allocated_collections,
            allocated_file_ids=m.allocated_file_ids,
            has_transcript=has_transcript,
            has_summary=has_summary,
            has_notes=has_notes,
            notes_content=mstore.get_notes(meeting_id),
            created_at=m.created_at.isoformat(),
            updated_at=m.updated_at.isoformat(),
        )

    return to_json(await run_sync(_run))


# ── get_section ─────────────────────────────────────────────────


async def get_section(meeting_id: str, tab_id: str) -> str:
    """Get a single section's markdown content.

    ``tab_id="general"`` returns the General Summary (``meeting.summary``).
    Other ``tab_id`` values return the corresponding section markdown
    (read from ``data/meetings/{meeting_id}/sections/{tab_id}.md``).

    Use :func:`get_meeting` first to discover available tab IDs.
    """
    from src.meeting import store as mstore

    def _run() -> dict[str, Any]:
        m = mstore.get_meeting(meeting_id)
        if not m:
            return err(f"Meeting '{meeting_id}' not found")

        if tab_id == "general":
            if m.summary:
                return ok(
                    meeting_id=meeting_id,
                    tab_id="general",
                    name="总体摘要",
                    content=m.summary,
                )
            else:
                return err("General summary not yet generated for this meeting")

        # Find tab info from the meeting's tabs list
        tab_name = ""
        if m.tabs:
            for tab in m.tabs:
                tid = tab.get("tab_id", "") if isinstance(tab, dict) else getattr(tab, "tab_id", "")
                if tid == tab_id:
                    tab_name = tab.get("name", "") if isinstance(tab, dict) else getattr(tab, "name", "")
                    break

        if not tab_name:
            return err(f"Tab '{tab_id}' not found in meeting '{meeting_id}'")

        md = mstore.get_section_md(meeting_id, tab_id)
        if md is None:
            return err(f"Section markdown not found for tab '{tab_id}'")

        return ok(
            meeting_id=meeting_id,
            tab_id=tab_id,
            name=tab_name,
            content=md,
        )

    return to_json(await run_sync(_run))


# ── get_meeting_transcript ──────────────────────────────────────


async def get_meeting_transcript(
    meeting_id: str,
    offset: int = 0,
    limit: int = 100,
) -> str:
    """Get paginated transcript segments for a meeting.

    Returns segments with ``start``, ``end``, ``text``, ``speaker_id``,
    ``sentence_id``.  Use ``offset``/``limit`` to page through large
    transcripts (1000+ segments).

    Use :func:`get_meeting` first to check ``has_transcript``.

    Args:
        meeting_id: Target meeting.
        offset: Skip this many segments (default 0).
        limit: Max segments to return (default 100).
    """
    def _run() -> dict[str, Any]:
        from src.meeting import store as mstore

        m = mstore.get_meeting(meeting_id)
        if not m:
            return err(f"Meeting '{meeting_id}' not found")

        # Prefer sentences.json (richer data: speaker diarization, sentence IDs)
        sentences = mstore.get_sentences(meeting_id)
        if sentences:
            total = len(sentences)
            page = sentences[offset:offset + limit]
            segments = [
                {
                    "start": s.get("start_time", 0),
                    "end": s.get("end_time", 0),
                    "text": s.get("original_text", ""),
                    "speaker_id": s.get("speaker", ""),
                    "sentence_id": s.get("sentence_id", ""),
                }
                for s in page
            ]
            return ok(
                meeting_id=meeting_id,
                segments=segments,
                total=total,
                offset=offset,
                limit=limit,
                source="sentences",
            )

        # Fallback: transcript.json
        t = mstore.get_transcript(meeting_id)
        if t:
            total = len(t.segments)
            page = t.segments[offset:offset + limit]
            return ok(
                meeting_id=meeting_id,
                segments=[s.model_dump() for s in page],
                text=t.text,
                language=t.language,
                total=total,
                offset=offset,
                limit=limit,
                source="transcript",
            )

        return err(f"No transcript available for meeting '{meeting_id}'")

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
    hot_words_library_ids: list[str] | None = None,
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
        if hot_words_library_ids is not None:
            kwargs["hot_words_library_ids"] = hot_words_library_ids
            updated_fields.append("hot_words_library_ids")
        elif hot_words_library_id is not None:
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
    - ingested SQLite rows + disk snapshots (``files.json`` leftover only)
    - Qdrant chunks for those sources
    """
    def _run() -> dict[str, Any]:
        from src.meeting import store as mstore
        from src.meeting.service import meeting_service

        m = mstore.get_meeting(meeting_id)
        if not m:
            return err(f"Meeting '{meeting_id}' not found")

        cleaned_allocations = meeting_service.cleanup_meeting_allocations(m)

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


# ── upload_meeting_audio_from_staging ───────────────────────────


async def upload_meeting_audio_from_staging(
    staging_token: str,
    meeting_id: str,
) -> str:
    """Upload audio to a meeting.

    **To upload audio, use Bash — one command**::

        curl -F "file=@/path/to/audio.webm" -F "meeting_id=meet_xxx" {base_url}/api/mcp/meeting-upload

    That's it — one HTTP call, no context leak.

    **Only use this MCP tool when you already have a staging_token**
    (e.g. from a prior ``POST /api/mcp/stage-content`` call).  Most of the
    time you should use the Bash + curl one-shot above instead.

    Tokens expire after 10 minutes.
    """
    from src.meeting import store as mstore
    from src.meeting.models import MeetingMode, MeetingStatus
    from src.mcp.staging import staging_store

    # 1. Retrieve staged audio
    entry = await staging_store.take(staging_token)
    if entry is None:
        return to_json(err(
            f"Staging token '{staging_token}' not found or expired. "
            f"Tokens expire after 10 minutes. Re-stage the audio and try again."
        ))

    def _run() -> dict[str, Any]:
        meeting = mstore.get_meeting(meeting_id)
        if not meeting:
            return err(f"Meeting '{meeting_id}' not found")

        ext = (
            entry.filename.rsplit(".", 1)[-1]
            if entry.filename and "." in entry.filename
            else "webm"
        )
        path = mstore.save_audio(
            meeting_id,
            entry.content,
            ext,
            original_filename=entry.filename,
        )
        updated = mstore.update_meeting(
            meeting_id,
            mode=MeetingMode.upload,
            status=MeetingStatus.created,
        )
        return ok(
            message="Audio uploaded via staging",
            meeting_id=meeting_id,
            audio_path=path,
            filename=entry.filename,
            size_bytes=len(entry.content),
            status=updated.status.value,
        )

    return to_json(await run_sync(_run))


async def list_meeting_catalog(collection: str = "") -> str:
    """List meetings this client may search (id, title, date, index_ready).

    Omit ``collection`` for every meeting. Pass a collection id to list only
    meetings ingested into that library. Not a transcript search — use
    :func:`lookup_meeting_transcript` for spoken content.
    """

    def _run() -> dict[str, Any]:
        from src.meeting.catalog import catalog_tool_json
        import json

        raw = catalog_tool_json(collection=(collection or "").strip() or None)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return err(raw)
        if not isinstance(data, dict):
            return err("catalog failed")
        return ok(**data)

    return to_json(await run_sync(_run))


async def lookup_meeting_transcript(
    query: str,
    meeting_id: str = "",
    meeting_ids: list[str] | None = None,
    speaker_scope: str = "auto",
    collection: str = "",
) -> str:
    """Search spoken transcript packs (not allocated summaries).

    Pass ``meeting_ids`` (or a single ``meeting_id``) to narrow. Omit ids to
    search every indexed meeting in the visible set (all meetings, or those
    ingested into ``collection`` when that is set). Ids outside the visible
    set return an error. ``speaker_scope=auto`` filters packs that contain
    speakers named in ``query`` when a mapping exists. ``all`` searches
    without that filter.

    Args:
        query: Natural-language search (topics, facts, wording).
        meeting_id: Optional single meeting (legacy). Combined with meeting_ids.
        meeting_ids: Optional meeting id list from list_meeting_catalog.
        speaker_scope: ``auto`` (default) or ``all``.
        collection: Optional collection id to restrict the visible set.
    """
    def _run() -> dict[str, Any]:
        from src.meeting.catalog import lookup_tool_json
        import json

        ids: list[str] = []
        if meeting_ids:
            ids.extend(str(x).strip() for x in meeting_ids if str(x).strip())
        one = (meeting_id or "").strip()
        if one and one not in ids:
            ids.append(one)
        raw = lookup_tool_json(
            query,
            meeting_ids=ids or None,
            speaker_scope=speaker_scope,
            user_question=query,
            collection=(collection or "").strip() or None,
        )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return err(raw)
        if not isinstance(data, dict):
            return err("lookup failed")
        if data.get("error"):
            return err(str(data.get("error")), **{k: v for k, v in data.items() if k != "error"})
        return ok(**data)

    return to_json(await run_sync(_run))


__all__ = [
    "list_meetings",
    "list_meeting_catalog",
    "get_meeting",
    "get_section",
    "get_meeting_transcript",
    "create_meeting",
    "update_meeting",
    "delete_meeting",
    "start_meeting_summary",
    "upload_meeting_audio_from_staging",
    "lookup_meeting_transcript",
]