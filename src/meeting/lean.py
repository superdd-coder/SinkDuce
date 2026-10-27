"""会议的 **Agent 精简形态**（批次 3：REST ``?lean=true`` 与 MCP 工具共用）。

## 为什么要有这个模块

REST 的会议载荷是**给网页的**：``GET /meetings`` 每项 40+ 字段
（permissions / tabs / speaker_matches / transcript_index_*…），
``GET /meetings/{id}`` 更是把**整段 transcript 内联**。而 MCP 工具面是**给
Agent 的**：`list_meetings` 只回 8 个轻字段，`get_meeting` 只回元数据 + 摘要
旗标（``has_transcript`` 之类），正文要用 `get_meeting_transcript` /
`get_section` 另取——**当初就是为了省模型上下文**。

跨盒子转发走 REST，于是同一工具两条路载荷差出一个数量级：直接转发
`GET /meetings/{id}` 会把转录全文灌进 Agent 上下文，是**功能倒退**。
解法（用户拍板的 A 案 (a)）：精简形状做成 **REST 的 ``lean=true`` 模式**，
让接口契约放在 REST 侧、能被测试钉住；MCP 工具的本地实现与 REST 的
lean 路径**共用本模块的同一份投影函数**——两侧永不漂移
（与批次 2 的 ``todo_public_dict`` 同一手法）。
"""

from __future__ import annotations

from typing import Any

#: ``list_meetings`` 的轻字段白名单（工具契约：轻量元数据，无 transcript/summary/tabs）。
#: ``archived`` 是批次 3 审查 #4 补的：``include_archived=true`` 时归档会议会出现，
#: 没有旗标的话 Agent 分不清哪条是归档的（后续 get_section 对归档会议会拒绝）。
MEETING_LIGHT_FIELDS = frozenset({
    "id", "title", "status", "mode", "archived",
    "processing_state", "summary_gen_state", "blueprint_gen_state",
    "speaker_names", "hot_words_library_id", "hot_words_library_ids",
    "allocated_collections", "allocated_file_ids",
    "created_at", "updated_at",
})


def meeting_light_dict(m: Any) -> dict[str, Any]:
    """一个会议 → ``list_meetings`` 的单条轻量条目。"""
    d = {k: v for k, v in m.model_dump().items() if k in MEETING_LIGHT_FIELDS}
    d["created_at"] = m.created_at.isoformat()
    d["updated_at"] = m.updated_at.isoformat()
    return d


def meeting_meta_dict(m: Any) -> dict[str, Any]:
    """一个会议 → ``get_meeting`` 的元数据信封（**不含 transcript/正文**）。

    与 :func:`src.mcp.tools.meetings.get_meeting` 的本地输出逐字段一致：
    轻量 tabs（id+name+description，general 恒排第一）、has_* 旗标、
    notes_content、时间戳。取不到的旗标按 False 处理（探针失败 ≠ 有内容）。
    """
    from src.meeting import store as mstore

    tabs: list[dict[str, str]] = [
        {"tab_id": "general", "name": "总体摘要", "description": ""}
    ]
    for tab in m.tabs or []:
        if isinstance(tab, dict):
            tid = tab.get("tab_id", "")
            tname = tab.get("name", "")
            tdesc = tab.get("description", "")
        else:
            tid = getattr(tab, "tab_id", "")
            tname = getattr(tab, "name", "")
            tdesc = getattr(tab, "description", "")
        if tid:
            tabs.append({"tab_id": tid, "name": tname, "description": tdesc or ""})

    has_transcript = bool(m.transcript_path or mstore.get_sentences(m.id))
    has_summary = bool(m.summary)
    has_notes = bool(mstore.get_notes(m.id))

    return {
        "id": m.id,
        "title": m.title,
        "status": m.status.value,
        "mode": m.mode.value if m.mode else None,
        "processing_state": m.processing_state,
        "summary_gen_state": m.summary_gen_state,
        "blueprint_gen_state": m.blueprint_gen_state,
        "speaker_names": m.speaker_names,
        "hot_words_library_id": m.hot_words_library_id,
        "hot_words_library_ids": list(m.hot_words_library_ids or []),
        "tabs": tabs,
        "allocated_collections": m.allocated_collections,
        "allocated_file_ids": m.allocated_file_ids,
        "has_transcript": has_transcript,
        "has_summary": has_summary,
        "has_notes": has_notes,
        "notes_content": mstore.get_notes(m.id),
        "created_at": m.created_at.isoformat(),
        "updated_at": m.updated_at.isoformat(),
    }


__all__ = ["MEETING_LIGHT_FIELDS", "meeting_light_dict", "meeting_meta_dict"]
