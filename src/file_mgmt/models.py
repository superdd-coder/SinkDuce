"""Pydantic schemas for file management.

All Out models use str for created_at/updated_at/event_time (ISO format).
bool fields are Python bool here; the store layer converts to INTEGER 0/1.
"""

from __future__ import annotations

from pydantic import BaseModel


# ════════════════════════════════════════════════════════════════
# Folder
# ════════════════════════════════════════════════════════════════

class FolderCreate(BaseModel):
    name: str
    parent_folder_id: str | None = None
    kind: str | None = None  # system_group|user_group|branch|plain; defaults to "plain"


class FolderUpdate(BaseModel):
    name: str | None = None
    parent_folder_id: str | None = None
    version: int


class FolderOut(BaseModel):
    folder_id: str
    parent_folder_id: str | None = None
    name: str
    kind: str
    is_system: bool = False
    created_by: str = "local"
    created_at: str
    updated_at: str
    version: int = 1


class FolderTree(FolderOut):
    children: list[FolderTree] = []
    file_count: int = 0  # derived: files directly in this folder


# ════════════════════════════════════════════════════════════════
# NodeGroup
# ════════════════════════════════════════════════════════════════

class GroupCreate(BaseModel):
    name: str
    description: str | None = None
    bind_existing_folder_id: str | None = None


class GroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class GroupOut(BaseModel):
    group_id: str
    folder_id: str | None = None
    name: str
    description: str | None = None
    created_by: str = "local"
    node_count: int = 0  # derived: nodes assigned to this group


# ════════════════════════════════════════════════════════════════
# Chain
# ════════════════════════════════════════════════════════════════

class ChainCreate(BaseModel):
    parent_chain_id: str
    parent_node_id: str
    title: str
    bind_existing_folder_id: str | None = None


class ChainUpdate(BaseModel):
    title: str | None = None


class ChainOut(BaseModel):
    chain_id: str
    parent_chain_id: str | None = None
    parent_node_id: str | None = None
    folder_id: str | None = None
    title: str | None = None
    created_by: str = "local"
    is_main: bool = False       # derived: parent_chain_id is NULL
    has_end_node: bool = False   # derived: chain has an end-type node
    node_count: int = 0          # derived: nodes on this chain


# ════════════════════════════════════════════════════════════════
# Node
# ════════════════════════════════════════════════════════════════

class NodeCreate(BaseModel):
    group_id: str | None = None
    node_type: str  # event|start|end
    title: str | None = None
    order: int
    event_time: str | None = None


class NodeUpdate(BaseModel):
    title: str | None = None
    group_id: str | None = None
    order: int | None = None
    event_time: str | None = None
    version: int


class NodeReorder(BaseModel):
    new_order: int


class NodeOut(BaseModel):
    node_id: str
    chain_id: str | None = None
    group_id: str | None = None
    node_type: str
    title: str | None = None
    order: int
    event_time: str | None = None
    created_by: str = "local"
    created_at: str
    version: int = 1
    has_definitive_file: bool = False  # derived: any attached file is_definitive


# ════════════════════════════════════════════════════════════════
# File
# ════════════════════════════════════════════════════════════════

class FilePathOut(BaseModel):
    path_id: str
    file_id: str
    folder_id: str | None = None
    is_primary: bool = False
    source_node_id: str | None = None  # None = persistent path
    created_by: str = "local"
    folder_path: str = ""   # derived: breadcrumb path of the folder
    is_greyed: bool = False  # derived: all attachments to this path are greyed


class FileVersionOut(BaseModel):
    version_id: str
    file_id: str
    version_no: int
    storage_file_id: str
    archived: bool = False
    commit_message: str | None = None
    created_by: str = "local"
    created_at: str


class FileOut(BaseModel):
    file_id: str
    current_version_id: str | None = None
    is_definitive: bool = False
    archived: bool = False
    unsupported: bool = False
    created_by: str = "local"
    version: int = 1
    filename: str = ""      # derived: current version original filename
    original_ext: str = ""  # derived: extension from filename (e.g. "pdf", "md", "" for none)
    created_at: str = ""    # derived: first version creation timestamp
    is_greyed: bool = False  # derived: greyed status in current folder context
    task_id: str | None = None  # upload task ID for async ingest polling


# Alias used in service signatures (contract section 8)
FileSummary = FileOut


class FileDetail(FileOut):
    paths: list[FilePathOut] = []
    versions: list[FileVersionOut] = []
    nodes: list[dict] = []  # node associations (shape finalized in Phase 2)
    messages: list[MessageOut] = []


# ════════════════════════════════════════════════════════════════
# Message
# ════════════════════════════════════════════════════════════════

class MessageCreate(BaseModel):
    owner_type: str  # folder|file|node|system_version
    owner_id: str
    source_node_id: str | None = None
    body: str | None = None
    author_type: str = "user"  # user|system


class MessageUpdate(BaseModel):
    body: str
    version: int


class MessageOut(BaseModel):
    message_id: str
    owner_type: str
    owner_id: str
    source_node_id: str | None = None
    body: str | None = None
    author_type: str
    author_id: str = "local"
    created_at: str
    edited_at: str | None = None
    edited_by: str | None = None
    version: int = 1


# ════════════════════════════════════════════════════════════════
# Archive / End-chain / Node-file attach
# ════════════════════════════════════════════════════════════════

class EndChainRequest(BaseModel):
    inherit_node_ids: list[str] = []


class ArchiveToggle(BaseModel):
    archived: bool
    version: int


class NodeFileAttach(BaseModel):
    file_id: str | None = None  # attach existing file; if None, upload new file


# Resolve forward references for recursive models
FolderTree.model_rebuild()
FileDetail.model_rebuild()
