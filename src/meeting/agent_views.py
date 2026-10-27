"""会议域的 **Agent 视图函数**（批次 4.5：REST agent 端点与 MCP 工具共用）。

`transcript_view` / `section_view` / `catalog_view` / `lookup_view` /
`start_summary_view` 与对应 MCP 工具的本地输出**同一份**（信封整体迁移）。
网页既有端点不动；这些走 agent 专用路由（域闸门与工具同语义）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.mcp.common import err, ok

logger = logging.getLogger("meeting.agent_views")


def transcript_view(
    meeting_id: str, offset: int = 0, limit: int = 100
) -> dict[str, Any]:
    """`get_meeting_transcript` 工具的信封（归档拒读 + 分页 + 双源回退）。"""
    from src.meeting import store as mstore

    m = mstore.get_meeting(meeting_id)
    if not m:
        return err(f"Meeting '{meeting_id}' not found")
    if getattr(m, "archived", False) is True:
        return err(f"Meeting '{meeting_id}' is archived; its transcript is not available")

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


def section_view(meeting_id: str, tab_id: str) -> dict[str, Any]:
    """`get_section` 工具的信封（general=总体摘要；归档拒读）。"""
    from src.meeting import store as mstore

    m = mstore.get_meeting(meeting_id)
    if not m:
        return err(f"Meeting '{meeting_id}' not found")
    if getattr(m, "archived", False) is True:
        return err(f"Meeting '{meeting_id}' is archived; its summaries are not available")

    if tab_id == "general":
        if m.summary:
            return ok(
                meeting_id=meeting_id,
                tab_id="general",
                name="总体摘要",
                content=m.summary,
            )
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


def catalog_view(collection: str = "") -> dict[str, Any]:
    """`list_meeting_catalog` 工具的信封。"""
    from src.meeting.catalog import catalog_tool_json

    raw = catalog_tool_json(collection=(collection or "").strip() or None)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return err(raw)
    if not isinstance(data, dict):
        return err("catalog failed")
    return ok(**data)


def lookup_view(
    query: str,
    meeting_id: str = "",
    meeting_ids: list[str] | None = None,
    speaker_scope: str = "auto",
    collection: str = "",
) -> dict[str, Any]:
    """`lookup_meeting_transcript` 工具的信封（可见集校验 + 说话人过滤）。"""
    from src.meeting.catalog import lookup_tool_json

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


def start_summary_view(meeting_id: str) -> dict[str, Any]:
    """`start_meeting_summary` 工具的信封（写：状态机 + 任务创建）。"""
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


__all__ = [
    "catalog_view",
    "lookup_view",
    "section_view",
    "start_summary_view",
    "transcript_view",
]
