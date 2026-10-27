"""Agent 专用视图端点（批次 4.5）：与 MCP 工具共用同一份信封。

本批的 13 个工具里，多数的**网页版 REST 端点与工具契约形状不同**（如
`/api/collections` 是轻列表、工具要富信封；`/documents/{c}/files/{s}/chunks`
的版本钉住语义与工具的 chunk_type 过滤不同）。按批次 4 定式：信封构造
下沉 service 层 view 函数，这里挂**与工具同语义的闸门**（模块 OR / 域 read）
——挂网页 router 会叠上 router 级依赖（AND），制造「本地放行、转发被拒」
的裂缝。

路径统一在 `/api/agent/…` 前缀下——一眼可辨「这是给 Agent 的接口契约」，
与网页端点互不干扰。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query

from src.api.routes.identity import require_domain, require_module

#: 库/文件/文档域工具的闸门：chat OR database（与 @module_gated("chat","database") 同语义）
agent_router = APIRouter(dependencies=[Depends(require_module("chat", "database"))])

#: 会议域工具的闸门：meeting 域 read 档（与 @domain_gated(DOMAIN_MEETING) 同语义）
meeting_router = APIRouter(dependencies=[Depends(require_domain("meeting"))])


# ── collections ─────────────────────────────────────────────


@agent_router.get("/agent/collections")
def collections_list():
    """`list_collections` 工具同款富信封（notes_count / last_active /
    catalog_definition / 可见性收窄）。"""
    from src.collections_views import collections_list_view

    return collections_list_view()


@agent_router.get("/agent/collections/{collection}")
def collection_info(collection: str):
    """`get_collection` 工具同款信封（元数据 + 脱敏 config + 统计）。"""
    from src.collections_views import collection_info_view

    return collection_info_view(collection)


@agent_router.get("/agent/collections/{collection}/documents")
def documents_list(collection: str):
    """`list_documents` 工具同款信封。"""
    from src.documents_views import documents_list_view

    return documents_list_view(collection)


@agent_router.get("/agent/collections/{collection}/chunks")
def file_chunks(
    collection: str,
    file_id: str = Query(""),
    source: str = Query(""),
    offset: int = Query(0),
    limit: int = Query(50),
    include_context: bool = Query(True),
    chunk_type: str = Query("*"),
):
    """`get_file_chunks` 工具同款信封（chunk_type 过滤/排序/R6-R5 闸门）。"""
    from src.documents_views import file_chunks_view

    return file_chunks_view(
        collection,
        file_id=file_id,
        source=source,
        offset=offset,
        limit=limit,
        include_context=include_context,
        chunk_type=chunk_type,
    )


@agent_router.get("/agent/collections/{collection}/doc-summary/{source:path}")
def doc_summary(collection: str, source: str):
    """`get_doc_summary` 工具同款信封（含未分配闸门）。"""
    from src.documents_views import doc_summary_view

    return doc_summary_view(collection, source)


# ── file-mgmt ───────────────────────────────────────────────


@agent_router.get("/agent/file-mgmt/{collection}/files/{file_id}")
def file_detail(collection: str, file_id: str):
    """`get_file` 工具同款信封（未分配闸门 + 完整 FileDetail）。"""
    from src.file_mgmt.views import file_detail_view

    return file_detail_view(collection, file_id)


@agent_router.get("/agent/file-mgmt/{collection}/nodes/{node_id}")
def node_detail(collection: str, node_id: str):
    """`get_node` 工具同款信封。"""
    from src.file_mgmt.views import node_view

    return node_view(collection, node_id)


@agent_router.get("/agent/file-mgmt/{collection}/chains")
def chains_list(collection: str):
    """`list_chains` 工具同款信封（Main 标题归一）。"""
    from src.file_mgmt.views import chains_view

    return chains_view(collection)


# ── meetings（域闸门） ──────────────────────────────────────


@meeting_router.get("/agent/meetings/{meeting_id}/transcript")
def meeting_transcript(
    meeting_id: str,
    offset: int = Query(0),
    limit: int = Query(100),
):
    """`get_meeting_transcript` 工具同款信封（分页 + 双源回退 + 归档拒读）。"""
    from src.meeting.agent_views import transcript_view

    return transcript_view(meeting_id, offset=offset, limit=limit)


@meeting_router.get("/agent/meetings/{meeting_id}/sections/{tab_id}")
def meeting_section(meeting_id: str, tab_id: str):
    """`get_section` 工具同款信封（general=总体摘要）。"""
    from src.meeting.agent_views import section_view

    return section_view(meeting_id, tab_id)


@meeting_router.get("/agent/meeting-catalog")
def meeting_catalog(collection: str = Query("")):
    """`list_meeting_catalog` 工具同款信封。"""
    from src.meeting.agent_views import catalog_view

    return catalog_view(collection)


@meeting_router.post("/agent/meeting-transcript-lookup")
def meeting_transcript_lookup(body: dict = Body()):
    """`lookup_meeting_transcript` 工具同款信封。检索较慢，Hub 侧已加长超时。"""
    from src.meeting.agent_views import lookup_view

    return lookup_view(
        str(body.get("query") or ""),
        meeting_id=str(body.get("meeting_id") or ""),
        meeting_ids=body.get("meeting_ids"),
        speaker_scope=str(body.get("speaker_scope") or "auto"),
        collection=str(body.get("collection") or ""),
    )


@meeting_router.post("/agent/meetings/{meeting_id}/start-summary")
def meeting_start_summary(meeting_id: str):
    """`start_meeting_summary` 工具同款信封（写：状态机 + 任务创建）。"""
    from src.meeting.agent_views import start_summary_view

    return start_summary_view(meeting_id)
