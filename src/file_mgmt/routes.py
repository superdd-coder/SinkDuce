"""FastAPI routes for file-management metadata CRUD (Phase 2–5).

All routes are mounted under /api/file-mgmt by main.py.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile

from src.file_mgmt import service
from src.file_mgmt.models import (
    ArchiveToggle,
    ChainCreate,
    ChainUpdate,
    EndChainRequest,
    FolderArchiveToggle,
    FolderCreate,
    FolderUpdate,
    GroupCreate,
    GroupUpdate,
    MessageCreate,
    MessageUpdate,
    NodeCreate,
    NodeFileAttach,
    NodeReorder,
    NodeUpdate,
    TodoCreate,
    TodoLinkNode,
    TodoUpdate,
)

from src.api.routes.identity import require_module

router = APIRouter(dependencies=[Depends(require_module("database"))])

#: Agent 视图端点（批次 4）：模块闸门与 MCP 工具**同语义**（chat OR database）。
#: 不能挂在 `router` 上——router 级的 database 依赖会与路由级 AND，
#: chat-only 定制角色就会出现「本地工具放行、跨盒子转发被拒」的裂缝
#: （审查 #4；FastAPI 路由级依赖与 router 级是相加不是相或）。
agent_router = APIRouter(dependencies=[Depends(require_module("chat", "database"))])


# === Folder ===


@router.get("/{collection_id}/folders")
def get_folders(
    collection_id: str,
    include_unassigned: bool = Query(True),
):
    """Return the folder tree (list of root FolderTree nodes).

    ``include_unassigned=False`` 隐藏「未分配」系统文件夹（v2 R6：未分配池对**库浏览**
    不可见）。**MCP 工具面用 False**，网页保持默认 True —— 这个参数是为了让远端盒子能
    **忠实执行** MCP 工具的语义；否则跨盒子读文件夹会把「未分配」暴露出来，
    而那是用户明确要求不可浏览的东西（更糟的是：它不会报错，只是结果不对）。
    """
    return service.get_folder_tree(collection_id, include_unassigned=include_unassigned)


@router.get("/{collection_id}/folders/{folder_id}")
def get_folder(collection_id: str, folder_id: str):
    return service.get_folder(collection_id, folder_id)


@router.post("/{collection_id}/folders", status_code=201)
def create_folder(collection_id: str, req: FolderCreate):
    return service.create_folder(collection_id, req)


@router.patch("/{collection_id}/folders/{folder_id}")
def update_folder(collection_id: str, folder_id: str, req: FolderUpdate):
    return service.update_folder(collection_id, folder_id, req)


@router.patch("/{collection_id}/folders/{folder_id}/archive")
def toggle_folder_archive(
    collection_id: str, folder_id: str, req: FolderArchiveToggle
):
    """Archive or restore a plain folder (subtree path-archive + snapshot)."""
    return service.toggle_folder_archive(collection_id, folder_id, req)


@router.delete("/{collection_id}/folders/{folder_id}")
def delete_folder(collection_id: str, folder_id: str):
    """Delete a folder: structure + mounts only. Files left with no other
    mount move to the Unassigned folder — folder deletion never destroys
    file bodies (burn files individually instead)."""
    return service.delete_folder(collection_id, folder_id)


@router.get("/{collection_id}/folders/{folder_id}/files")
def get_folder_files(collection_id: str, folder_id: str):
    """List files in a folder (with greyed status)."""
    return service.list_files_in_folder(collection_id, folder_id)


@router.get("/{collection_id}/folders/{folder_id}/messages")
def get_folder_messages(
    collection_id: str,
    folder_id: str,
    include_node_msgs: bool = Query(True),
    include_file_msgs: bool = Query(True),
    recursive: bool = Query(False),
):
    """Aggregated message stream for a folder.

    - Default aggregation still includes node/file when those flags are true.
    - ``recursive=true`` expands to all descendant folders (and their files
      when ``include_file_msgs`` is true).
    """
    return service.list_folder_messages(
        collection_id,
        folder_id,
        include_node_msgs=include_node_msgs,
        include_file_msgs=include_file_msgs,
        recursive=recursive,
    )


@router.get("/{collection_id}/messages")
def get_collection_messages(
    collection_id: str,
    include_node_msgs: bool = Query(False),
    include_file_msgs: bool = Query(False),
    recursive: bool = Query(False),
):
    """List messages at the collection (root) level.

    Without flags: collection-owned messages only (backward compatible).
    With ``include_file_msgs`` / ``recursive``: same scope rules as folder view.
    """
    if not include_node_msgs and not include_file_msgs and not recursive:
        return service.list_messages(collection_id, "collection", collection_id)
    return service.list_root_messages(
        collection_id,
        include_node_msgs=include_node_msgs,
        include_file_msgs=include_file_msgs,
        recursive=recursive,
    )


@router.post("/{collection_id}/messages", status_code=201)
def create_collection_message(collection_id: str, req: MessageCreate):
    """Add a message at the collection (root) level."""
    req.owner_type = "collection"
    req.owner_id = collection_id
    return service.create_message(collection_id, req)


@router.post("/{collection_id}/folders/{folder_id}/messages", status_code=201)
def create_folder_message(collection_id: str, folder_id: str, req: MessageCreate):
    """Add a message to a folder. Sets owner_type='folder', owner_id=folder_id."""
    req.owner_type = "folder"
    req.owner_id = folder_id
    return service.create_message(collection_id, req)


@router.get("/{collection_id}/archived")
def get_archived_files(collection_id: str):
    """/Archived virtual view — all files with archived=1."""
    return service.list_archived_files(collection_id)


# === NodeGroup ===


@router.get("/{collection_id}/groups")
def list_groups(collection_id: str):
    return service.list_groups(collection_id)


@router.post("/{collection_id}/groups", status_code=201)
def create_group(collection_id: str, req: GroupCreate):
    return service.create_group(collection_id, req)


@router.patch("/{collection_id}/groups/{group_id}")
def update_group(collection_id: str, group_id: str, req: GroupUpdate):
    return service.update_group(collection_id, group_id, req)


@router.delete("/{collection_id}/groups/{group_id}", status_code=204)
def delete_group(collection_id: str, group_id: str):
    service.delete_group(collection_id, group_id)


# === Chain ===


@router.get("/{collection_id}/chains")
def list_chains(collection_id: str):
    return service.list_chains(collection_id)


@router.post("/{collection_id}/chains", status_code=201)
def create_chain(collection_id: str, req: ChainCreate):
    return service.create_chain(collection_id, req)


@router.patch("/{collection_id}/chains/{chain_id}")
def update_chain(collection_id: str, chain_id: str, req: ChainUpdate):
    return service.update_chain(collection_id, chain_id, req)


@router.delete("/{collection_id}/chains/{chain_id}", status_code=204)
def delete_chain(collection_id: str, chain_id: str):
    service.delete_chain(collection_id, chain_id)


@router.post("/{collection_id}/chains/{chain_id}/reopen")
def reopen_chain(collection_id: str, chain_id: str):
    return service.reopen_chain(collection_id, chain_id)


# === Node ===


@router.get("/{collection_id}/chains/{chain_id}/nodes")
def list_nodes(collection_id: str, chain_id: str):
    return service.list_nodes(collection_id, chain_id)


@router.post("/{collection_id}/chains/{chain_id}/nodes", status_code=201)
def create_node(collection_id: str, chain_id: str, req: NodeCreate):
    return service.create_node(collection_id, chain_id, req)


@router.patch("/{collection_id}/nodes/{node_id}")
def update_node(collection_id: str, node_id: str, req: NodeUpdate):
    return service.update_node(collection_id, node_id, req)


@router.delete("/{collection_id}/nodes/{node_id}")
def delete_node(collection_id: str, node_id: str):
    """Delete a node. Attached files always survive (sole-path files are
    routed to the Unassigned folder) — the response is always 204."""
    service.delete_node(collection_id, node_id)
    from fastapi.responses import Response

    return Response(status_code=204)


@router.post("/{collection_id}/nodes/{node_id}/reorder")
def reorder_node(collection_id: str, node_id: str, req: NodeReorder):
    return service.reorder_node(collection_id, node_id, req)


@router.get("/{collection_id}/nodes/by-external-ref")
def get_node_by_external_ref(
    collection_id: str,
    ref: str,
    chain_id: Optional[str] = Query(None),
):
    """Resolve a timeline node by external_ref (e.g. meeting:{meeting_id})."""
    node = service.get_node_by_external_ref(collection_id, ref, chain_id=chain_id)
    if not node:
        raise HTTPException(404, f"No node with external_ref={ref!r}")
    return node


@router.get("/{collection_id}/nodes/{node_id}/meeting-todo-candidates")
def get_node_meeting_todo_candidates(collection_id: str, node_id: str):
    """Todo candidates from all meeting sections attached to this node."""
    from src.meeting import store as meeting_store

    detail = service.get_node_detail(collection_id, node_id)
    if not isinstance(detail, dict):
        detail = detail.model_dump() if hasattr(detail, "model_dump") else {}
    ext = (detail.get("external_ref") or "") or ""
    if not str(ext).startswith("meeting:"):
        return {"node_id": node_id, "groups": [], "candidates": []}
    meeting_id = str(ext).split(":", 1)[1].strip()
    if not meeting_id:
        return {"node_id": node_id, "groups": [], "candidates": []}

    file_ids: set[str] = set()
    for att in detail.get("attachments") or []:
        if isinstance(att, dict):
            fid = att.get("file_id")
        else:
            fid = getattr(att, "file_id", None)
        if fid:
            file_ids.add(str(fid))

    meeting = meeting_store.get_meeting(meeting_id)
    if meeting is None:
        return {
            "node_id": node_id,
            "meeting_id": meeting_id,
            "groups": [],
            "candidates": [],
        }

    groups: list[dict] = []
    flat: list[dict] = []
    for t in meeting.tabs or []:
        td = t if isinstance(t, dict) else t.model_dump()
        fid = (td.get("allocated_file_id") or "").strip()
        if not fid or fid not in file_ids:
            continue
        if (td.get("associated_collection_id") or "").strip() != collection_id:
            continue
        tab_id = td.get("tab_id")
        section_name = td.get("name") or tab_id
        cands = list(td.get("todo_candidates") or [])
        # Legacy allocate: no stored candidates — extract from section MD now
        if not cands and tab_id:
            try:
                from src.meeting.service import meeting_service

                cands = meeting_service.extract_section_todo_candidates(
                    meeting_id,
                    str(tab_id),
                    persist=True,
                    use_llm=True,
                )
            except Exception:
                cands = []
        for c in cands:
            item = dict(c) if isinstance(c, dict) else {}
            item["section_tab_id"] = tab_id
            item["section_name"] = section_name
            flat.append(item)
        groups.append(
            {
                "tab_id": tab_id,
                "section_name": section_name,
                "candidates": cands,
            }
        )
    return {
        "node_id": node_id,
        "meeting_id": meeting_id,
        "collection_id": collection_id,
        "chain_id": detail.get("chain_id"),
        "groups": groups,
        "candidates": flat,
    }


@router.get("/{collection_id}/nodes/{node_id}")
def get_node_detail(collection_id: str, node_id: str):
    return service.get_node_detail(collection_id, node_id)


@router.get("/{collection_id}/nodes/{node_id}/messages")
def get_node_messages(collection_id: str, node_id: str):
    """List messages for a node."""
    return service.list_messages(collection_id, "node", node_id)


@router.post("/{collection_id}/nodes/{node_id}/messages", status_code=201)
def create_node_message(collection_id: str, node_id: str, req: MessageCreate):
    """Add a message to a node."""
    req.owner_type = "node"
    req.owner_id = node_id
    return service.create_message(collection_id, req)


# === Node attachments ===


@router.post("/{collection_id}/nodes/{node_id}/files", status_code=201)
def attach_file_to_node(collection_id: str, node_id: str, req: NodeFileAttach):
    """Attach a file to a node (existing file or upload new).

    Generates derived paths automatically.
    """
    return service.attach_file_to_node(collection_id, node_id, file_id=req.file_id)


@router.delete("/{collection_id}/nodes/{node_id}/files/{file_id}", status_code=204)
def detach_file_from_node(collection_id: str, node_id: str, file_id: str):
    """Remove a file attachment from a node."""
    service.detach_file_from_node(collection_id, node_id, file_id)


# === File ===


@router.get("/{collection_id}/files")
def list_files(
    collection_id: str,
    folder_id: Optional[str] = Query(None),
    archived: Optional[bool] = Query(None),
    is_definitive: Optional[bool] = Query(None),
    scope: Optional[str] = Query(None),
    lean: bool = Query(False),
):
    """List files, optionally filtered by folder, archive, or definitive flag.

    ``is_definitive=true`` returns all definitive files collection-wide
    (sources that feed Collection Summary).

    ``lean=true``：返回 **Agent 信封形态**（`files.list_files_lean`，与 MCP
    ``list_files`` 工具共用）——条目带 mounts/folder_ids、未分配池不可见
    （v2 R6）、外带 ``{total, summary.by_doc_kind}`` 聚合信封；``scope``
    支持 ``all``/``root``（工具语义）。不带 lean 时行为与从前完全一致。
    """
    if lean:
        return service.list_files_lean(
            collection_id,
            folder_id=(folder_id or "").strip() or None,
            archived=archived,
            is_definitive=is_definitive,
            scope=scope or "all",
        )
    return service.list_files(
        collection_id,
        folder_id=folder_id,
        archived=archived,
        is_definitive=is_definitive,
    )


@router.get("/{collection_id}/files/{file_id}")
def get_file_detail(collection_id: str, file_id: str):
    """File detail: paths, versions, nodes, messages."""
    return service.get_file_detail(collection_id, file_id)


@router.get("/{collection_id}/files/{file_id}/ingest-trace")
def get_ingest_trace(
    collection_id: str,
    file_id: str,
    version_id: Optional[str] = Query(None),
):
    """Per-version ingest steps + LLM returns (ingest_trace.json sidecar)."""
    from src.rag.ingest_trace import find_trace, normalize_ingest_trace

    data, _directory = find_trace(collection_id, file_id, version_id)
    if data is None:
        raise HTTPException(status_code=404, detail="No ingest trace for this file/version")
    return normalize_ingest_trace(data)


@router.post("/{collection_id}/files/{file_id}/paths", status_code=201)
def add_file_path(collection_id: str, file_id: str, body: dict):
    """Add a persistent path to a file. Body: {folder_id, is_primary?}."""
    return service.add_file_path(
        collection_id, file_id,
        folder_id=body["folder_id"],
        is_primary=body.get("is_primary", False),
    )


@router.delete("/{collection_id}/files/{file_id}/paths/{path_id}", status_code=204)
def remove_file_path(collection_id: str, file_id: str, path_id: str):
    """Remove a file path."""
    service.remove_file_path(collection_id, file_id, path_id)


@router.post("/{collection_id}/files/{file_id}/promote-path")
def promote_file_path(collection_id: str, file_id: str, body: dict):
    """Promote a derived path to persistent path. Body: {path_id}."""
    return service.promote_file_path(collection_id, file_id, body["path_id"])


@router.post("/{collection_id}/files/{file_id}/demote-path")
def demote_file_path(collection_id: str, file_id: str, body: dict):
    """Revert a persistent (pinned) path to derived mode. Body: {path_id}.

    Restores source_node_id from a linked timeline node for the same folder.
    Does not delete the path.
    """
    return service.demote_file_path(collection_id, file_id, body["path_id"])


@router.get("/{collection_id}/files/{file_id}/messages")
def get_file_messages(collection_id: str, file_id: str):
    """List messages for a file."""
    return service.list_messages(collection_id, "file", file_id)


@router.post("/{collection_id}/files/{file_id}/messages", status_code=201)
def create_file_message(collection_id: str, file_id: str, req: MessageCreate):
    """Add a message to a file."""
    req.owner_type = "file"
    req.owner_id = file_id
    return service.create_message(collection_id, req)


# === Message (shared) ===


@router.patch("/{collection_id}/messages/{message_id}")
def update_message(collection_id: str, message_id: str, req: MessageUpdate):
    """Edit a message (user messages only)."""
    return service.update_message(collection_id, message_id, req)


@router.delete("/{collection_id}/messages/{message_id}", status_code=204)
def delete_message(collection_id: str, message_id: str):
    """Delete a message (user messages only)."""
    service.delete_message(collection_id, message_id)


# ════════════════════════════════════════════════════════════════════
# Phase 4: File Upload / Version / Delete / Update
# ════════════════════════════════════════════════════════════════════


@router.post("/{collection_id}/files/upload", status_code=201)
def upload_file_to_folder(
    collection_id: str,
    file: UploadFile = File(...),
    folder_id: Optional[str] = Form(None),
    source_node_id: Optional[str] = Form(None),
    on_name_conflict: str = Form("error"),
):
    """Upload a file to a folder (creates persistent path).

    Empty / omitted ``folder_id`` uploads to collection root (orphan file).
    If source_node_id is provided, creates a derived path instead.

    ``on_name_conflict``（批次 5）：``error``（默认，409）/ ``auto_rename`` ——
    与 MCP ``upload_file_from_staging`` 工具同语义；跨盒子转发需要它，
    缺了会把 auto_rename 静默变成 409。

    刻意是 **sync（threadpool）路由**：曾经是 async + ``await file.read()``
    全量进内存再同步处理，大文件上传期间整个事件循环被冻结，所有设备的
    所有请求一起卡（与 upload-folder 同病，同修）。流式版本从 SpooledTemporaryFile
    读，RAM 有界。
    """
    return service.upload_file_to_folder(
        collection_id,
        folder_id,
        file.file,
        file.filename or "unnamed",
        source_node_id=source_node_id,
        on_name_conflict=(on_name_conflict or "error").strip() or "error",
    )


@router.post("/{collection_id}/files/upload-folder", status_code=201)
def upload_entire_folder(
    collection_id: str,
    files: list[UploadFile] = File(...),
    parent_folder_id: Optional[str] = Form(None),
):
    """Upload an entire folder preserving relative paths.

    Deliberately a sync (threadpool) route with per-file streaming: the
    whole folder used to be buffered as bytes and processed on the event
    loop, which on large folders spiked RAM by the folder size and froze
    every other request until the upload finished.

    Empty / omitted ``parent_folder_id`` uses collection root.
    """

    def _stream_files():
        for f in files:
            name = getattr(f, "filename", None) or "unnamed"
            yield f.file.read(), name

    return service.upload_folder(collection_id, parent_folder_id, _stream_files())


@router.get("/{collection_id}/old-versions")
def list_old_versions(collection_id: str):
    """List non-current file versions (history; not the Archive folder)."""
    return service.list_old_versions(collection_id)


@router.post("/{collection_id}/files/{file_id}/versions", status_code=201)
def upload_new_version(
    collection_id: str,
    file_id: str,
    file: UploadFile = File(...),
    commit_message: str = Form(""),
):
    """Upload a new version of an existing file.

    Previous version rows stay on disk with ``archived=1`` (version-level
    history — **not** the system Archive folder). Returns immediately with
    optional ``task_id``; ingest uses the same async upload pipeline as folder
    upload (MinerU / collection chunk config / embed).

    sync（threadpool）+ 流式，理由同单文件上传（大文件不再冻结事件循环）。
    """
    return service.upload_file_version(
        collection_id, file_id, file.file, file.filename or "unnamed",
        commit_message=commit_message,
    )


@router.delete(
    "/{collection_id}/files/{file_id}/versions/{version_id}",
    status_code=200,
)
def delete_file_version(
    collection_id: str, file_id: str, version_id: str
):
    """Permanently delete a non-current version (blob + Qdrant + log message)."""
    return service.delete_file_version(collection_id, file_id, version_id)


@router.post(
    "/{collection_id}/files/{file_id}/versions/{version_id}/rollback",
    status_code=200,
)
def rollback_file_version(
    collection_id: str, file_id: str, version_id: str
):
    """Roll back to this version: make it current and hard-delete all later versions.

    Later versions are permanently removed (blob + vectors + log), not archived.
    Earlier history (lower version_no) is kept as archived history.
    """
    return service.rollback_file_version(collection_id, file_id, version_id)


@router.patch("/{collection_id}/files/{file_id}")
def update_file(collection_id: str, file_id: str, req: dict):
    """Update file metadata (is_definitive, filename). Archive via PATCH .../archive."""
    return service.update_file(collection_id, file_id, req)


@router.delete("/{collection_id}/files/{file_id}", status_code=204)
def delete_file(collection_id: str, file_id: str):
    """Permanently delete a file and all associated records."""
    service.delete_file(collection_id, file_id)


@router.post("/{collection_id}/nodes/{node_id}/files/upload", status_code=201)
async def attach_upload_file_to_node(
    collection_id: str,
    node_id: str,
    file: UploadFile = File(...),
):
    """Upload a new file and attach it to a node (generates derived paths)."""
    file_bytes = await file.read()
    return service.attach_file_to_node(
        collection_id, node_id, file_id=None, upload_file=(file_bytes, file.filename or "unnamed"),
    )


# ════════════════════════════════════════════════════════════════════
# Phase 5: Archive / End Chain / Manual Archive
# ════════════════════════════════════════════════════════════════════


@router.post("/{collection_id}/nodes/{node_id}/end-chain")
def end_chain_endpoint(collection_id: str, node_id: str, req: EndChainRequest):
    """End a branch chain: grey attachments, compute archive candidates."""
    return service.end_chain(collection_id, node_id, req)


@router.patch("/{collection_id}/files/{file_id}/archive")
def toggle_file_archive(collection_id: str, file_id: str, req: ArchiveToggle):
    """Archive or unarchive a file (path-level and/or file-level).

    - archived=True, scope=file: exclude from search (global)
    - archived=True, scope=path + path_ids: archive those mounts only
    - archived=True, scope=path + folder_id: archive all mounts in folder
      (auto file-level if no active paths remain)
    - archived=False: clear file-level; also clear path_ids or folder_id paths
      (neither = file-level only, e.g. /Archived view)
    Requires version for optimistic locking.
    """
    return service.toggle_archive(collection_id, file_id, req)


# ════════════════════════════════════════════════════════════════════
# Collection To-do list
# ════════════════════════════════════════════════════════════════════


@router.get("/{collection_id}/todos")
def list_todos(
    collection_id: str,
    done: Optional[bool] = Query(None),
    chain_id: Optional[str] = Query(None),
    mine: bool = Query(False),
):
    """List to-dos. ``mine=true`` keeps only items assigned to the People **Me** person.

    与 MCP 工具面同一语义（未分配的不算"我的"）；Me 未设置时 400 —— 与工具本体的
    错误文案一致，这样跨盒子调用与本地调用给出**同一个原因**。
    加这个参数是为了让转发能忠实执行 ``list_todos(mine=True)``：少了它，
    转发会静默返回**全部**待办，而调用方以为拿到的是"我的"。
    """
    todos = service.list_todos(collection_id, done=done, chain_id=chain_id)
    if mine:
        from src.speakers.store import get_me_person_id

        me = get_me_person_id()
        if not me:
            raise HTTPException(
                400,
                "No Me person is set. Mark yourself in People before filtering to mine.",
            )
        todos = [t for t in todos if getattr(t, "assignee_person_id", None) == me]
    # 增富形态与 MCP 工具共用一份（见 todos.todo_public_dict），跨盒子转发
    # 才能拿到与本地工具同形的列表。
    return [service.todo_public_dict(t, collection_id) for t in todos]


@router.get("/{collection_id}/chains/{chain_id}/todo-suggestions")
def get_chain_todo_suggestions(collection_id: str, chain_id: str):
    """Cached smart to-do suggestions for a timeline chain (chain-end bubble)."""
    from src.file_mgmt.todo_suggestions import get_todo_suggestions

    return get_todo_suggestions(collection_id, chain_id)


@router.get("/{collection_id}/db-status")
def get_db_status(collection_id: str):
    """file-mgmt DB 是否已初始化 —— **纯读**（绝不触发 ``_open_db`` 的自动建库）。

    存在的意义：REST 写端点对未初始化的库会**静默自动建库**，而跨盒子转发必须
    先知道「这个库还没初始化」才能像 MCP 本地工具一样**拒绝**（而不是悄悄替
    远端建库写入）。用别的读端点探测会顺带把库建出来——探测本身就改了状态。
    """
    from src.file_mgmt.access import _validate_collection

    _validate_collection(collection_id)
    from src.file_mgmt.store import db_initialized

    return {"collection_id": collection_id, "initialized": db_initialized(collection_id)}


# ════════════════════════════════════════════════════════════════════
# Agent views (批次 4)：与 MCP 工具共用同一份信封（src/file_mgmt/views.py）
# ════════════════════════════════════════════════════════════════════


@agent_router.get("/{collection_id}/library-tree")
def library_tree(
    collection_id: str,
    max_depth: int = Query(0),
    include_archived_files: bool = Query(True),
    fields: str = Query("summary"),
):
    """Agent 形状的库树（`list_library_tree` 工具同款信封：计数恒真 +
    files 只含本层 + sparse 旗标）。网页继续用自己的树接口，互不影响。"""
    from src.file_mgmt.views import library_tree_view

    return library_tree_view(
        collection_id,
        max_depth=max_depth,
        include_archived_files=include_archived_files,
        fields=fields,
    )


@agent_router.get("/{collection_id}/timeline")
def timeline(collection_id: str, depth: str = Query("summary")):
    """Agent 形状的时间线（`get_timeline` 工具同款信封：嵌套主链 + 分支 +
    detached_branches 警示）。"""
    from src.file_mgmt.views import timeline_view

    return timeline_view(collection_id, depth=depth)


@agent_router.get("/{collection_id}/chains/{chain_id}/view")
def chain_detail(collection_id: str, chain_id: str):
    """Agent 形状的单链视图（`get_chain` 工具同款信封：链骨架 + 富化节点）。
    与既有 ``/chains/{cid}/nodes``（网页用）并存、互不影响。"""
    from src.file_mgmt.views import chain_view

    return chain_view(collection_id, chain_id)


@agent_router.get("/{collection_id}/files/{file_id}/versions")
def file_versions(collection_id: str, file_id: str):
    """Agent 形状的版本史（`list_file_versions` 工具同款信封：blob_available
    旗标 + gaps 检测 + 计数摘要）。GET；同路径的 POST 是「上传新版本」（网页），
    方法不同不冲突。未分配-only 文件按 v2 R6 不可见。"""
    from src.file_mgmt.views import file_versions_view

    return file_versions_view(collection_id, file_id)


@agent_router.post("/{collection_id}/files/{file_id}/definitive")
def set_file_definitive(collection_id: str, file_id: str, body: dict = Body()):
    """设置/清除文件的 definitive 旗标（`set_file_definitive` 工具同款信封）。
    body: ``{"is_definitive": true|false}``（缺省 true）。"""
    from src.file_mgmt.views import set_file_definitive_view

    return set_file_definitive_view(
        collection_id, file_id,
        is_definitive=bool(body.get("is_definitive", True)),
    )


@router.post("/{collection_id}/todos", status_code=201)
def create_todo(collection_id: str, req: TodoCreate):
    out = service.create_todo(collection_id, req)
    # 增富形态与 MCP 工具/列表端点共用（todos.todo_public_dict）
    return service.todo_public_dict(out, collection_id)


@router.patch("/{collection_id}/todos/{todo_id}")
def update_todo(collection_id: str, todo_id: str, req: TodoUpdate):
    out = service.update_todo(collection_id, todo_id, req)
    return service.todo_public_dict(out, collection_id)


@router.delete("/{collection_id}/todos/{todo_id}", status_code=204)
def delete_todo(collection_id: str, todo_id: str):
    service.delete_todo(collection_id, todo_id)


@router.post("/{collection_id}/todos/{todo_id}/link-node")
def link_todo_node(collection_id: str, todo_id: str, req: TodoLinkNode):
    return service.link_todo_node(collection_id, todo_id, req)


# ════════════════════════════════════════════════════════════════════
# Definitive suggestions (post-upload judgment; Library Overview badge)
# ════════════════════════════════════════════════════════════════════


@router.get("/{collection_id}/definitive-suggestions")
def get_definitive_suggestions(collection_id: str):
    """Pending "mark as Definitive" suggestions (post-upload LLM judgment)."""
    from src.file_mgmt.definitive_suggestions import get_definitive_suggestions as _get

    return _get(collection_id)


@router.post("/{collection_id}/definitive-suggestions/{file_id}/apply")
def apply_definitive_suggestion(collection_id: str, file_id: str):
    """Accept a suggestion: set is_definitive through the normal update path."""
    from src.file_mgmt.definitive_suggestions import apply_suggestion

    return apply_suggestion(collection_id, file_id)


@router.post("/{collection_id}/definitive-suggestions/{file_id}/dismiss")
def dismiss_definitive_suggestion(collection_id: str, file_id: str):
    """Ignore a suggestion (the file is never suggested again)."""
    from src.file_mgmt.definitive_suggestions import dismiss_suggestion

    return dismiss_suggestion(collection_id, file_id)


@router.post("/{collection_id}/definitive-suggestions/dismiss-all")
def dismiss_all_definitive_suggestions(collection_id: str):
    """Ignore every pending suggestion at once (each keeps its content-hash
    anchor, same as a single dismissal)."""
    from src.file_mgmt.definitive_suggestions import dismiss_all_suggestions

    return dismiss_all_suggestions(collection_id)
