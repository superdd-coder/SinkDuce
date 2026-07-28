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
}

export interface FolderTreeNode extends Folder {
  children: FolderTreeNode[]
  file_count: number
}

export interface NodeGroup {
  group_id: string
  folder_id: string | null
  name: string
  description: string | null
  created_by: string
  node_count: number
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
  created_at: string
  is_greyed: boolean
  task_id: string | null
}

export interface FilePath {
  path_id: string
  file_id: string
  folder_id: string | null
  is_primary: boolean
  source_node_id: string | null
  created_by: string
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
}

export interface FileDetail extends FileSummary {
  paths: FilePath[]
  versions: FileVersion[]
  nodes: Record<string, unknown>[]
  messages: Message[]
}

export interface Message {
  message_id: string
  owner_type: "folder" | "file" | "node" | "system_version"
  owner_id: string
  source_node_id: string | null
  body: string | null
  author_type: "user" | "system"
  author_id: string
  created_at: string
  edited_at: string | null
  edited_by: string | null
  version: number
}

// Request bodies
export interface FolderCreateRequest {
  name: string
  parent_folder_id?: string | null
  kind?: string
}

export interface FolderUpdateRequest {
  name?: string | null
  parent_folder_id?: string | null
  version: number
}

export interface GroupCreateRequest {
  name: string
  description?: string | null
  bind_existing_folder_id?: string | null
}

export interface GroupUpdateRequest {
  name?: string | null
  description?: string | null
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
