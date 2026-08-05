// Phase 6: TypeScript types for file-management module.
// Mirrors Pydantic schemas in src/file_mgmt/models.py per contract §5.

export interface Folder {
  folder_id: string
  parent_folder_id: string | null
  name: string
  kind: "system_group" | "user_group" | "branch" | "plain"
  is_system: boolean
  created_by: string
  created_at: string
  updated_at: string
  version: number
  icon_type?: string | null
  icon_value?: string | null
  icon_color?: string | null
}

export interface FolderTreeNode extends Folder {
  children: FolderTreeNode[]
  file_count: number
  /**
   * Latest update among contained files (subtree). Empty folder falls back
   * to folder.updated_at / created_at from API.
   */
  content_updated_at?: string
}

export interface NodeGroup {
  group_id: string
  folder_id: string | null
  name: string
  description: string | null
  created_by: string
  node_count: number
  icon_type?: string | null
  icon_value?: string | null
  icon_color?: string | null
  is_system?: boolean
}

export interface Chain {
  chain_id: string
  parent_chain_id: string | null
  parent_node_id: string | null
  folder_id: string | null
  title: string | null
  created_by: string
  is_main: boolean
  has_end_node: boolean
  node_count: number
  /** Merge rejoin node on the parent chain (closed-loop end). */
  merge_node_id?: string | null
}

export interface Node {
  node_id: string
  chain_id: string | null
  group_id: string | null
  node_type: "event" | "start" | "end"
  title: string | null
  order: number
  event_time: string | null
  created_by: string
  created_at: string
  version: number
  has_definitive_file: boolean
  /** e.g. meeting:{meetingId} for auto-created anchors */
  external_ref?: string | null
}

export interface FileSummary {
  file_id: string
  current_version_id: string | null
  is_definitive: boolean
  archived: boolean
  unsupported: boolean
  created_by: string
  version: number
  filename: string
  original_ext: string
  /** First version creation time. */
  created_at: string
  /** Latest version creation time (update). */
  updated_at?: string
  is_greyed: boolean
  task_id: string | null
  /** Human label from files.json (e.g. "Meeting: Title / Section") */
  display_name?: string
  /** Document source key (__file__:…, __meeting__:…, …) */
  source?: string
  /** Backend kind for badges: meeting | note | file */
  doc_kind?: "meeting" | "note" | "file" | string
}

export interface FilePath {
  path_id: string
  file_id: string
  folder_id: string | null
  is_primary: boolean
  source_node_id: string | null
  created_by: string
  /** Path-level archive (this folder only). */
  archived?: boolean
  folder_path: string
  is_greyed: boolean
}

export interface FileVersion {
  version_id: string
  file_id: string
  version_no: number
  storage_file_id: string
  archived: boolean
  commit_message: string | null
  created_by: string
  created_at: string
  /** False when the version blob is missing on disk (Source/extract unavailable). */
  blob_available?: boolean
}

/** Non-current version row for All Files collapsible history. */
export interface OldVersion {
  version_id: string
  file_id: string
  version_no: number
  storage_file_id: string
  archived: boolean
  commit_message: string | null
  created_at: string
  current_filename: string
  current_display_name: string
  filename: string
  original_ext: string
  /** False when the version blob is missing on disk (cannot preview Raw). */
  blob_available?: boolean
}

/** Node association on a file (from GET file detail). */
export interface FileNodeRef {
  node_id: string
  title: string | null
  node_type: string
  group_id?: string | null
  chain_id?: string | null
  group_name?: string | null
  chain_title?: string | null
  greyed: boolean
}

export interface FileDetail extends FileSummary {
  paths: FilePath[]
  versions: FileVersion[]
  nodes: FileNodeRef[]
  messages: Message[]
}

export interface Message {
  message_id: string
  owner_type: "folder" | "file" | "node" | "system_version" | "collection" | string
  owner_id: string
  source_node_id: string | null
  body: string | null
  author_type: "user" | "system"
  author_id: string
  created_at: string
  edited_at: string | null
  edited_by: string | null
  version: number
  /** Backend-resolved owner display name (folder/file/node title). */
  source_name?: string | null
}

// Request bodies
export interface FolderCreateRequest {
  name: string
  parent_folder_id?: string | null
  kind?: string
  icon_type?: string | null
  icon_value?: string | null
  icon_color?: string | null
}

export interface FolderUpdateRequest {
  name?: string | null
  parent_folder_id?: string | null
  icon_type?: string | null
  icon_value?: string | null
  icon_color?: string | null
  version: number
}

export interface GroupCreateRequest {
  name: string
  description?: string | null
  bind_existing_folder_id?: string | null
  icon_type?: string | null
  icon_value?: string | null
  icon_color?: string | null
}

export interface GroupUpdateRequest {
  name?: string | null
  description?: string | null
  icon_type?: string | null
  icon_value?: string | null
  icon_color?: string | null
  /** Rebind to another plain unbound folder (F-b move attachment paths). */
  rebind_folder_id?: string | null
}

export interface MessageCreateRequest {
  owner_type: string
  owner_id: string
  source_node_id?: string | null
  body?: string | null
  author_type?: string
}

export interface MessageUpdateRequest {
  body: string
  version: number
}

// ── Timeline View Types ──

export interface NodeDetail extends Node {
  attachments: NodeAttachment[]
  messages: Message[]
}

export interface NodeAttachment {
  file_id: string
  is_definitive: boolean
  /** True if file-level or path-level archived (unified UI). */
  archived: boolean
  filename: string
  /** File row version for optimistic locking (definitive toggle, etc.). */
  version?: number
}

export interface EndChainResult {
  /** @deprecated alias of path_archived_files */
  greyed_files?: string[]
  archive_candidates?: string[]
  inherited_files: string[]
  /** Merge node created on the parent chain (closed-loop rejoin). */
  merged_node_id?: string | null
  path_archived_files?: string[]
  path_archived_path_ids?: string[]
  file_archived?: string[]
}

// ── Request bodies (additional) ──

export interface ChainCreateRequest {
  parent_chain_id: string
  parent_node_id: string
  title: string
  bind_existing_folder_id?: string | null
}

export interface ChainUpdateRequest {
  title?: string | null
}

export interface NodeCreateRequest {
  group_id?: string | null
  node_type: string
  title?: string | null
  order: number
  event_time?: string | null
}

export interface NodeUpdateRequest {
  chain_id?: string | null
  title?: string | null
  group_id?: string | null
  order?: number | null
  node_type?: string | null
  event_time?: string | null
  version: number
}

export interface EndChainRequest {
  /** Files to keep active on the branch (path not archived). */
  inherit_file_ids?: string[]
  /** Legacy: inherit all files on these nodes. */
  inherit_node_ids?: string[]
  title?: string | null
  group_id?: string | null
  event_time?: string | null
  message_body?: string | null
  attachment_file_ids?: string[]
}

// ── Collection To-do ──

export interface TodoItem {
  todo_id: string
  title: string
  body: string | null
  done: boolean
  ddl: string | null
  target_chain_id: string | null
  chain_id: string
  chain_title: string
  is_main_chain: boolean
  completed_node_id: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface TodoCreateRequest {
  title: string
  body?: string | null
  ddl?: string | null
  target_chain_id?: string | null
}

export interface TodoUpdateRequest {
  title?: string | null
  body?: string | null
  ddl?: string | null
  target_chain_id?: string | null
  done?: boolean | null
  clear_ddl?: boolean
  clear_chain?: boolean
  clear_body?: boolean
}

export interface TodoLinkNodeRequest {
  node_id: string
}
