"""FastAPI routes for file-management metadata CRUD (Phase 2–5).

All routes are mounted under /api/file-mgmt by main.py.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from src.file_mgmt import service
from src.file_mgmt.models import (
    ArchiveToggle,
    ChainCreate,
    ChainUpdate,
    EndChainRequest,
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

router = APIRouter()


# === Folder ===


@router.get("/{collection_id}/folders")
def get_folders(collection_id: str):
    """Return the folder tree (list of root FolderTree nodes)."""
    return service.get_folder_tree(collection_id)


@router.get("/{collection_id}/folders/{folder_id}")
def get_folder(collection_id: str, folder_id: str):
    return service.get_folder(collection_id, folder_id)


@router.post("/{collection_id}/folders", status_code=201)
def create_folder(collection_id: str, req: FolderCreate):
    return service.create_folder(collection_id, req)


@router.patch("/{collection_id}/folders/{folder_id}")
def update_folder(collection_id: str, folder_id: str, req: FolderUpdate):
    return service.update_folder(collection_id, folder_id, req)


@router.delete("/{collection_id}/folders/{folder_id}", status_code=204)
def delete_folder(collection_id: str, folder_id: str):
    service.delete_folder(collection_id, folder_id)


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
    """Delete a node. Returns affected_files dict if files need UI confirmation."""
    result = service.delete_node(collection_id, node_id)
    if result and result.get("affected_files"):
        return result
    from fastapi.responses import Response
    return Response(status_code=204)


@router.post("/{collection_id}/nodes/{node_id}/reorder")
def reorder_node(collection_id: str, node_id: str, req: NodeReorder):
    return service.reorder_node(collection_id, node_id, req)


@router.get("/{collection_id}/nodes/by-external-ref")
def get_node_by_external_ref(collection_id: str, ref: str):
    """Resolve a timeline node by external_ref (e.g. meeting:{meeting_id})."""
    node = service.get_node_by_external_ref(collection_id, ref)
    if not node:
        raise HTTPException(404, f"No node with external_ref={ref!r}")
    return node


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
):
    """List files, optionally filtered by folder, archive, or definitive flag.

    ``is_definitive=true`` returns all definitive files collection-wide
    (sources that feed Collection Summary).
    """
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
async def upload_file_to_folder(
    collection_id: str,
    file: UploadFile = File(...),
    folder_id: Optional[str] = Form(None),
    source_node_id: Optional[str] = Form(None),
):
    """Upload a file to a folder (creates persistent path).

    Empty / omitted ``folder_id`` uploads to collection root (orphan file).
    If source_node_id is provided, creates a derived path instead.
    """
    file_bytes = await file.read()
    return service.upload_file_to_folder(
        collection_id,
        folder_id,
        file_bytes,
        file.filename or "unnamed",
        source_node_id=source_node_id,
    )


@router.post("/{collection_id}/files/upload-folder", status_code=201)
async def upload_entire_folder(
    collection_id: str,
    files: list[UploadFile] = File(...),
    parent_folder_id: Optional[str] = Form(None),
):
    """Upload an entire folder preserving relative paths.

    Empty / omitted ``parent_folder_id`` uses collection root.
    """
    files_data: list[tuple[bytes, str]] = []
    for f in files:
        content = await f.read()
        # Prefer webkitRelativePath when present (browser folder pick)
        name = getattr(f, "filename", None) or "unnamed"
        files_data.append((content, name))
    return service.upload_folder(collection_id, parent_folder_id, files_data)


@router.get("/{collection_id}/old-versions")
def list_old_versions(collection_id: str):
    """List non-current file versions (history; not the Archive folder)."""
    return service.list_old_versions(collection_id)


@router.post("/{collection_id}/files/{file_id}/versions", status_code=201)
async def upload_new_version(
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
    """
    file_bytes = await file.read()
    return service.upload_file_version(
        collection_id, file_id, file_bytes, file.filename or "unnamed",
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
):
    return service.list_todos(collection_id, done=done, chain_id=chain_id)


@router.post("/{collection_id}/todos", status_code=201)
def create_todo(collection_id: str, req: TodoCreate):
    return service.create_todo(collection_id, req)


@router.patch("/{collection_id}/todos/{todo_id}")
def update_todo(collection_id: str, todo_id: str, req: TodoUpdate):
    return service.update_todo(collection_id, todo_id, req)


@router.delete("/{collection_id}/todos/{todo_id}", status_code=204)
def delete_todo(collection_id: str, todo_id: str):
    service.delete_todo(collection_id, todo_id)


@router.post("/{collection_id}/todos/{todo_id}/link-node")
def link_todo_node(collection_id: str, todo_id: str, req: TodoLinkNode):
    return service.link_todo_node(collection_id, todo_id, req)
