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
    # lucide | emoji; optional (plain folders only)
    icon_type: str | None = None
    icon_value: str | None = None
    icon_color: str | None = None


class FolderUpdate(BaseModel):
    name: str | None = None
    parent_folder_id: str | None = None
    icon_type: str | None = None
    icon_value: str | None = None
    icon_color: str | None = None
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
    icon_type: str | None = None
    icon_value: str | None = None
    icon_color: str | None = None


class FolderTree(FolderOut):
    children: list[FolderTree] = []
    file_count: int = 0  # derived: files directly in this folder
    # Max current-version timestamp among files in this folder subtree
    # (direct + nested). Empty folder falls back to folder.updated_at.
    content_updated_at: str = ""


# ════════════════════════════════════════════════════════════════
# NodeGroup
# ════════════════════════════════════════════════════════════════

class GroupCreate(BaseModel):
    name: str
    description: str | None = None
    bind_existing_folder_id: str | None = None
    # lucide | emoji; optional for backward compat
    icon_type: str | None = None
    icon_value: str | None = None
    icon_color: str | None = None


class GroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    icon_type: str | None = None
    icon_value: str | None = None
    icon_color: str | None = None
    # Rebind to another plain unbound folder; F-b moves attachment paths only
    rebind_folder_id: str | None = None


class GroupOut(BaseModel):
    group_id: str
    folder_id: str | None = None
    name: str
    description: str | None = None
    created_by: str = "local"
    node_count: int = 0  # derived: nodes assigned to this group
    icon_type: str | None = None
    icon_value: str | None = None
    icon_color: str | None = None
    is_system: bool = False


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
    # Merge/rejoin node on the parent chain (closed-loop end of a finished branch)
    merge_node_id: str | None = None


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
    node_type: str | None = None
    chain_id: str | None = None
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
    # External identity for get-or-create anchors (e.g. meeting:{id})
    external_ref: str | None = None


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
    archived: bool = False  # path-level archive
    folder_path: str = ""   # derived: breadcrumb path of the folder
    is_greyed: bool = False  # UI: file- or path-level archived in this context


class FileVersionOut(BaseModel):
    version_id: str
    file_id: str
    version_no: int
    storage_file_id: str
    archived: bool = False
    commit_message: str | None = None
    created_by: str = "local"
    created_at: str
    # False when the on-disk blob is missing (legacy overwrite / never stored).
    # Agents should not call get_document_text(version_id=…) when this is False.
    blob_available: bool = True


class FileOut(BaseModel):
    file_id: str
    current_version_id: str | None = None
    is_definitive: bool = False
    archived: bool = False
    unsupported: bool = False
    created_by: str = "local"
    version: int = 1
    filename: str = ""      # derived: current version original filename (storage name)
    original_ext: str = ""  # derived: extension from filename (e.g. "pdf", "md", "" for none)
    created_at: str = ""    # derived: first version creation timestamp
    updated_at: str = ""    # derived: latest version creation timestamp (current upload)
    is_greyed: bool = False  # True if file- or path-level archived (folder view grey)
    task_id: str | None = None  # upload task ID for async ingest polling
    # Human-readable label from files.json (e.g. "Meeting: Title / Section")
    display_name: str = ""
    # Document source key (e.g. __file__:{id}, __meeting__:{id}:{tab})
    source: str = ""
    # Derived kind for UI badges: meeting | note | file
    doc_kind: str = "file"


# Alias used in service signatures (contract section 8)
FileSummary = FileOut


class FileDetail(FileOut):
    paths: list[FilePathOut] = []
    versions: list[FileVersionOut] = []
    nodes: list[dict] = []  # node associations (shape finalized in Phase 2)
    messages: list[MessageOut] = []


class OldVersionOut(BaseModel):
    """A non-current file version (for All Files collapsible history)."""

    version_id: str
    file_id: str
    version_no: int
    storage_file_id: str
    archived: bool = True
    commit_message: str | None = None
    created_at: str
    # Parent file (current) display
    current_filename: str = ""
    current_display_name: str = ""
    # This version's own label (usually storage basename)
    filename: str = ""
    original_ext: str = ""
    # False when the on-disk blob is missing (legacy overwrite / never stored)
    blob_available: bool = True


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
    # Display name of owner (folder/file/node); filled by service layer
    source_name: str | None = None


# ════════════════════════════════════════════════════════════════
# Archive / End-chain / Node-file attach
# ════════════════════════════════════════════════════════════════

class EndChainRequest(BaseModel):
    """End a branch and create a merge node on the parent chain.

    Inherit selection is **file-level**: checked files keep their branch paths;
    unchecked files get path-level archive on the branch folder.
    ``inherit_node_ids`` remains for backward compatibility (inherit all files
    on those nodes when ``inherit_file_ids`` is omitted/empty and nodes given).
    """
    inherit_file_ids: list[str] = []
    inherit_node_ids: list[str] = []
    # Merge node fields (applied to the new parent-chain end/merge node)
    title: str | None = None
    group_id: str | None = None
    event_time: str | None = None
    message_body: str | None = None
    attachment_file_ids: list[str] = []


class ArchiveToggle(BaseModel):
    """Archive / unarchive request.

    **Archive** (``archived=True``):
    - ``scope="file"``: exclude from search (``files.archived=1``). All folder
      views show the file greyed; no per-path writes required.
    - ``scope="path"``: archive specific mounts (``file_paths.archived``).
      Prefer ``path_ids`` (precise, e.g. node group+branch paths without
      touching a native mount in the same folder). Or pass ``folder_id`` to
      archive every mount of this file in that folder. If every mount path is
      then archived, auto-promotes to file-level.

    **Unarchive** (``archived=False``):
    - Always clears file-level archive when set.
    - If ``path_ids`` is set: clears those path archives only.
    - Else if ``folder_id`` is set: also clears path archives in that folder only.
    - If both omitted (e.g. /Archived view): file-level only;
      path archives are left for per-folder Unarchive.
    - Paths that were never path-archived recover automatically once file-level
      is cleared (they were only grey via global file archive).
    """

    archived: bool
    version: int
    folder_id: str | None = None
    scope: str = "file"  # "file" | "path" (only used when archived=True)
    # Precise path-level targets (preferred over folder_id when both set).
    path_ids: list[str] = []


class NodeFileAttach(BaseModel):
    file_id: str | None = None  # attach existing file; if None, upload new file


# ════════════════════════════════════════════════════════════════
# Todo (collection checklist; not a timeline node)
# ════════════════════════════════════════════════════════════════

class TodoCreate(BaseModel):
    title: str
    body: str | None = None  # detailed description (optional)
    ddl: str | None = None  # ISO date or datetime; optional
    target_chain_id: str | None = None  # null = main chain


class TodoUpdate(BaseModel):
    title: str | None = None
    body: str | None = None  # set description; use clear_body to wipe
    ddl: str | None = None
    target_chain_id: str | None = None
    done: bool | None = None
    clear_ddl: bool = False  # when True, set ddl to NULL
    clear_chain: bool = False  # when True, set target_chain_id to NULL (main)
    clear_body: bool = False  # when True, set body to NULL


class TodoLinkNode(BaseModel):
    node_id: str


class TodoOut(BaseModel):
    todo_id: str
    title: str
    body: str | None = None
    done: bool
    ddl: str | None = None
    target_chain_id: str | None = None  # stored; null means main-by-default
    chain_id: str  # resolved
    chain_title: str = ""
    is_main_chain: bool = True
    completed_node_id: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


# Resolve forward references for recursive models
FolderTree.model_rebuild()
FileDetail.model_rebuild()
