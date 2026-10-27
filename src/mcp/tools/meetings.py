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

from mcp.server.fastmcp import Context

from src.identity.acl import DOMAIN_MEETING
from src.mcp.common import domain_gated, err, ok, run_sync, to_json
from src.hub.confirm import confirm_destructive
from src.hub.routing import hub_routed
from src.hub.upload_forwards import upload_meeting_audio_forward

logger = logging.getLogger(__name__)


# ── list_meetings ──────────────────────────────────────────────


@domain_gated(DOMAIN_MEETING)
@hub_routed(
    method="GET",
    path="/meetings",
    # lean=true：REST 侧的 Agent 精简形态（src/meeting/lean.py，与本工具
    # 本地实现共用同一份投影）——不带 lean 的 REST 每项 40+ 字段，
    # 直接转发等于把网页载荷灌进 Agent 上下文（功能倒退）。
    query=lambda a: {
        "lean": True,
        "limit": a.get("limit", 100),
        "status": a.get("status"),
        "search": a.get("search") or None,
        "include_archived": a.get("include_archived") or None,
    },
    forwards=("limit", "status", "search", "include_archived"),
)
async def list_meetings(
    limit: int = 100,
    status: str | None = None,
    search: str = "",
    include_archived: bool = False,
    ctx: Context | None = None,
) -> str:
    """List meetings sorted by ``updated_at`` descending.

    Returns lightweight metadata only (no transcript, summary, tabs).
    Use :func:`get_meeting` for tabs list and :func:`get_section` for content.

    Args:
        limit: Max meetings to return (default 100).
        status: Filter by status — ``"created"`` / ``"recording"`` /
            ``"transcribing"`` / ``"completed"``. Omit for all.
        search: Case-insensitive title substring filter. Omit for all.
        include_archived: Also return archived meetings (default false).
    """
    def _run() -> dict[str, Any]:
        from src.meeting import store as mstore
        from src.meeting.lean import meeting_light_dict

        meetings = mstore.list_meetings()
        if not include_archived:
            meetings = [m for m in meetings if getattr(m, "archived", False) is not True]
        if status:
            meetings = [m for m in meetings if m.status.value == status]
        if search:
            q = search.lower()
            meetings = [m for m in meetings if q in (m.title or "").lower()]
        total = len(meetings)
        items = [meeting_light_dict(m) for m in meetings[:limit]]
        return ok(meetings=items, total=total, returned=len(items))

    return to_json(await run_sync(_run))


# ── get_meeting (lightweight metadata) ──────────────────────────


@domain_gated(DOMAIN_MEETING)
@hub_routed(
    method="GET",
    path="/meetings/{meeting_id}",
    # lean=true：REST 不内联 transcript（非 lean 是给网页的，直接转发会把
    # 转录全文灌进 Agent 上下文）；形状与本地共用 meeting_meta_dict。
    query=lambda _a: {"lean": True},
    forwards=("meeting_id",),
)
async def get_meeting(meeting_id: str, ctx: Context | None = None) -> str:
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
        from src.meeting.lean import meeting_meta_dict

        m = mstore.get_meeting(meeting_id)
        if not m:
            return err(f"Meeting '{meeting_id}' not found")
        return meeting_meta_dict(m)

    return to_json(await run_sync(_run))


# ── get_section ─────────────────────────────────────────────────


@domain_gated(DOMAIN_MEETING)
@hub_routed(
    method="GET",
    path="/agent/meetings/{meeting_id}/sections/{tab_id}",
    forwards=("meeting_id", "tab_id"),
)
async def get_section(
    meeting_id: str, tab_id: str, ctx: Context | None = None,
) -> str:
    """Get a single section's markdown content.

    ``tab_id="general"`` returns the General Summary (``meeting.summary``).
    Other ``tab_id`` values return the corresponding section markdown
    (read from ``data/meetings/{meeting_id}/sections/{tab_id}.md``).

    Use :func:`get_meeting` first to discover available tab IDs.
    """
    def _run() -> dict[str, Any]:
        from src.meeting.agent_views import section_view

        return section_view(meeting_id, tab_id)

    return to_json(await run_sync(_run))


# ── get_meeting_transcript ──────────────────────────────────────


@domain_gated(DOMAIN_MEETING)
@hub_routed(
    method="GET",
    path="/agent/meetings/{meeting_id}/transcript",
    query=lambda a: {
        "offset": a.get("offset", 0),
        "limit": a.get("limit", 100),
    },
    forwards=("meeting_id", "offset", "limit"),
)
async def get_meeting_transcript(
    meeting_id: str,
    offset: int = 0,
    limit: int = 100,
    ctx: Context | None = None,
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
        from src.meeting.agent_views import transcript_view

        return transcript_view(meeting_id, offset=offset, limit=limit)

    return to_json(await run_sync(_run))


# ── create_meeting ─────────────────────────────────────────────


@domain_gated(DOMAIN_MEETING)
@hub_routed(
    method="POST",
    path="/meetings",
    # 已知良性分歧（接受并记录）：REST create 会把**pin 的热词库**应用到新会议
    #（与盒子自家 UI 一致），本地工具不带。响应里 hot_words_library_ids 可见
    #（非静默），模型看得见实际发生了什么。
    body=dict,
    # REST 回 model_dump()（含 iso 时间），工具回 ok(**d) —— 同形透传。
    forwards=("title", "mode"),
)
async def create_meeting(
    title: str = "",
    mode: str | None = None,
    ctx: Context | None = None,
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


def _meeting_update_body(args: dict[str, Any]) -> dict[str, Any]:
    """工具参数 → REST PUT body。``notes_content`` 在 REST 侧叫 ``notes``。"""
    body: dict[str, Any] = {}
    for k in ("title", "status", "speaker_names",
              "hot_words_library_id", "hot_words_library_ids"):
        if args.get(k) is not None:
            body[k] = args[k]
    if args.get("notes_content") is not None:
        body["notes"] = args["notes_content"]
    return body


def _meeting_update_shape(payload: Any, args: dict[str, Any]) -> Any:
    """REST（serialize 后的整会议）→ 工具信封 ok(message, updated_fields, **会议)。

    ``updated_fields`` 按传入参数算，优先级与本地一致（ids 列表压过单个 id）。
    REST 响应是 _serialize_meeting（model_dump 的超集：permissions/notes_content），
    保留超集。
    """
    if not isinstance(payload, dict) or payload.get("error"):
        return payload
    updated: list[str] = []
    for k in ("title", "status", "speaker_names"):
        if args.get(k) is not None:
            updated.append(k)
    if args.get("hot_words_library_ids") is not None:
        updated.append("hot_words_library_ids")
    elif args.get("hot_words_library_id") is not None:
        updated.append("hot_words_library_id")
    if args.get("notes_content") is not None:
        updated.append("notes_content")
    return {
        "message": f"Meeting '{args.get('meeting_id')}' updated",
        "updated_fields": updated,
        **payload,
    }


@domain_gated(DOMAIN_MEETING)
@hub_routed(
    method="PUT",
    path="/meetings/{meeting_id}",
    body=_meeting_update_body,
    shape=_meeting_update_shape,
    # 不存在的会议：REST（本轮对齐后）回 404 —— 与本地 err 同语义。
    forwards=(
        "meeting_id", "title", "status", "speaker_names",
        "hot_words_library_id", "hot_words_library_ids", "notes_content",
    ),
)
async def update_meeting(
    meeting_id: str,
    title: str | None = None,
    status: str | None = None,
    speaker_names: dict[str, str] | None = None,
    hot_words_library_id: str | None = None,
    hot_words_library_ids: list[str] | None = None,
    notes_content: str | None = None,
    ctx: Context | None = None,
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


@domain_gated(DOMAIN_MEETING)
@confirm_destructive()
@hub_routed(
    method="DELETE",
    path="/meetings/{meeting_id}",
    # REST（本轮对齐后）回 {message, cleaned_allocations}；工具契约是
    # ok(message=带 id 文案, cleaned_allocations)。404（不存在）由 REST 报。
    shape=lambda payload, a: (
        payload
        if not isinstance(payload, dict)
        else {
            "message": f"Meeting '{a.get('meeting_id')}' deleted",
            "cleaned_allocations": (payload or {}).get("cleaned_allocations", []),
        }
    ),
    forwards=("meeting_id",),
)
async def delete_meeting(meeting_id: str, ctx: Context | None = None) -> str:
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
        # 与 REST 删除同一道闸门：正在录制的会议不可删（会产生孤儿录制流）。
        if mstore.recording_is_live(meeting_id):
            return err(
                f"Meeting '{meeting_id}' is currently recording"
                " — stop the recording before deleting"
            )

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


@domain_gated(DOMAIN_MEETING)
@hub_routed(
    method="POST",
    path="/agent/meetings/{meeting_id}/start-summary",
    forwards=("meeting_id",),
)
async def start_meeting_summary(
    meeting_id: str, ctx: Context | None = None,
) -> str:
    """Trigger async summary generation for a meeting.

    Returns a ``task_id`` that you can poll with :func:`list_tasks` or
    :func:`get_task_status`. Fails if:
    - the meeting doesn't exist
    - there's no transcript yet
    - the meeting is already busy (summarizing / extracting)
    """
    def _run() -> dict[str, Any]:
        from src.meeting.agent_views import start_summary_view

        return start_summary_view(meeting_id)

    return to_json(await run_sync(_run))


# ── upload_meeting_audio_from_staging ───────────────────────────


@domain_gated(DOMAIN_MEETING)
@hub_routed(
    forward=upload_meeting_audio_forward,
    timeout=300.0,
    forwards=("staging_token", "meeting_id"),
)
async def upload_meeting_audio_from_staging(
    staging_token: str,
    meeting_id: str,
    ctx: Context | None = None,
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


@domain_gated(DOMAIN_MEETING)
@hub_routed(
    method="GET",
    path="/agent/meeting-catalog",
    query=lambda a: {"collection": a.get("collection") or None},
    forwards=("collection",),
)
async def list_meeting_catalog(
    collection: str = "", ctx: Context | None = None,
) -> str:
    """List meetings this client may search (id, title, date, index_ready).

    Omit ``collection`` for every meeting. Pass a collection id to list only
    meetings ingested into that library. Not a transcript search — use
    :func:`lookup_meeting_transcript` for spoken content.
    """

    def _run() -> dict[str, Any]:
        from src.meeting.agent_views import catalog_view

        return catalog_view(collection)

    return to_json(await run_sync(_run))


@domain_gated(DOMAIN_MEETING)
@hub_routed(
    method="POST",
    path="/agent/meeting-transcript-lookup",
    body=dict,
    # 转写包向量检索 + 打包，比普通读慢
    timeout=90.0,
    forwards=("query", "meeting_id", "meeting_ids", "speaker_scope", "collection"),
)
async def lookup_meeting_transcript(
    query: str,
    meeting_id: str = "",
    meeting_ids: list[str] | None = None,
    speaker_scope: str = "auto",
    collection: str = "",
    ctx: Context | None = None,
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
        from src.meeting.agent_views import lookup_view

        return lookup_view(
            query,
            meeting_id=meeting_id,
            meeting_ids=meeting_ids,
            speaker_scope=speaker_scope,
            collection=collection,
        )

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