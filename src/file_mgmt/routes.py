"""FastAPI routes for file-management metadata CRUD (Phase 2).

All routes are mounted under /api/file-mgmt by main.py.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.file_mgmt import service
from src.file_mgmt.models import (
    ChainCreate,
    ChainUpdate,
    FolderCreate,
    FolderUpdate,
    GroupCreate,
    GroupUpdate,
    NodeCreate,
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
