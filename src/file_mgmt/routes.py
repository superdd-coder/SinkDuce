"""FastAPI routes for file-management metadata CRUD (Phase 2 + Phase 3).

All routes are mounted under /api/file-mgmt by main.py.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from src.file_mgmt import service
from src.file_mgmt.models import (
    ChainCreate,
    ChainUpdate,
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
):
    """Aggregated message stream for a folder."""
    return service.list_folder_messages(
        collection_id, folder_id,
        include_node_msgs=include_node_msgs,
        include_file_msgs=include_file_msgs,
    )


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


@router.delete("/{collection_id}/nodes/{node_id}", status_code=204)
def delete_node(collection_id: str, node_id: str):
    service.delete_node(collection_id, node_id)


@router.post("/{collection_id}/nodes/{node_id}/reorder")
def reorder_node(collection_id: str, node_id: str, req: NodeReorder):
    return service.reorder_node(collection_id, node_id, req)


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
):
    """List all files, optionally filtered by folder or archive status."""
    return service.list_files(collection_id, folder_id=folder_id, archived=archived)


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
