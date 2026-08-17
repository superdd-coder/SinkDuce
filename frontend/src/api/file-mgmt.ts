// Phase 6: Centralized API client for /api/file-mgmt endpoints.
// Per project convention: all API calls go through this file, no direct fetch in components.

import type {
  FolderTreeNode,
  Folder,
  NodeGroup,
  FileSummary,
  FileDetail,
  FilePath,
  Message,
  OldVersion,
  Chain,
  Node,
  NodeDetail,
  EndChainResult,
  FolderCreateRequest,
  FolderUpdateRequest,
  GroupCreateRequest,
  GroupUpdateRequest,
  MessageCreateRequest,
  MessageUpdateRequest,
  ChainCreateRequest,
  ChainUpdateRequest,
  NodeCreateRequest,
  NodeUpdateRequest,
  EndChainRequest,
  TodoItem,
  TodoCreateRequest,
  TodoUpdateRequest,
  TodoLinkNodeRequest,
  TodoSuggestionsResponse,
  NodeMeetingTodoCandidatesResponse,
} from "@/types/file-mgmt"

import {
  FILE_MGMT_BASE,
  FileMgmtApiError,
  getNameConflict,
  request,
  type NameConflictDetail,
} from "./http"

export { FileMgmtApiError, getNameConflict, type NameConflictDetail }

const BASE = FILE_MGMT_BASE

async function req<T>(path: string, options?: RequestInit): Promise<T> {
  return request<T>(path, { ...options, flavor: "file-mgmt" })
}

// ── Folder ──

export const getFolderTree = (collectionId: string) =>
  req<FolderTreeNode[]>(`/${collectionId}/folders`)

export const getFolder = (collectionId: string, folderId: string) =>
  req<Folder>(`/${collectionId}/folders/${folderId}`)

export const createFolder = (collectionId: string, body: FolderCreateRequest) =>
  req<Folder>(`/${collectionId}/folders`, {
    method: "POST",
    body: JSON.stringify(body),
  })

export const updateFolder = (collectionId: string, folderId: string, body: FolderUpdateRequest) =>
  req<Folder>(`/${collectionId}/folders/${folderId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  })

export const deleteFolder = (collectionId: string, folderId: string) =>
  req<void>(`/${collectionId}/folders/${folderId}`, { method: "DELETE" })

export const getFolderFiles = (collectionId: string, folderId: string) =>
  req<FileSummary[]>(`/${collectionId}/folders/${folderId}/files`)

export const getFolderMessages = (
  collectionId: string,
  folderId: string,
  includeNodeMsgs = true,
  includeFileMsgs = true,
  recursive = false
) =>
  req<Message[]>(
    `/${collectionId}/folders/${folderId}/messages?include_node_msgs=${includeNodeMsgs}&include_file_msgs=${includeFileMsgs}&recursive=${recursive}`
  )

export const createFolderMessage = (collectionId: string, folderId: string, body: MessageCreateRequest) =>
  req<Message>(`/${collectionId}/folders/${folderId}/messages`, {
    method: "POST",
    body: JSON.stringify({ ...body, owner_type: "folder", owner_id: folderId }),
  })

// ── Root-level files ──

export const getRootFiles = (collectionId: string) =>
  req<FileSummary[]>(`/${collectionId}/files`)

export const getArchivedFiles = (collectionId: string) =>
  req<FileSummary[]>(`/${collectionId}/archived`)

/** Files marked definitive — feed Collection Summary consolidate. */
export const getDefinitiveFiles = (collectionId: string) =>
  req<FileSummary[]>(`/${collectionId}/files?is_definitive=true`)

// ── NodeGroup ──

export const listGroups = (collectionId: string) =>
  req<NodeGroup[]>(`/${collectionId}/groups`)

export const createGroup = (collectionId: string, body: GroupCreateRequest) =>
  req<NodeGroup>(`/${collectionId}/groups`, {
    method: "POST",
    body: JSON.stringify(body),
  })

export const updateGroup = (collectionId: string, groupId: string, body: GroupUpdateRequest) =>
  req<NodeGroup>(`/${collectionId}/groups/${groupId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  })

export const deleteGroup = (collectionId: string, groupId: string) =>
  req<void>(`/${collectionId}/groups/${groupId}`, { method: "DELETE" })

// ── File ──

export const uploadFileToFolder = async (
  collectionId: string,
  folderId: string,
  file: File,
  sourceNodeId?: string
): Promise<FileSummary> => {
  const formData = new FormData()
  formData.append("file", file)
  // Omit empty folder_id so root upload is accepted (orphan file)
  if (folderId) formData.append("folder_id", folderId)
  if (sourceNodeId) formData.append("source_node_id", sourceNodeId)
  const res = await fetch(`${BASE}/${collectionId}/files/upload`, {
    method: "POST",
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new FileMgmtApiError(res.status, body)
  }
  return res.json()
}

export const uploadFolderToCollection = async (
  collectionId: string,
  parentFolderId: string,
  files: File[]
): Promise<FileSummary[]> => {
  const formData = new FormData()
  for (const f of files) {
    // Preserve relative path for nested folder structure when available
    const rel =
      (f as File & { webkitRelativePath?: string }).webkitRelativePath ||
      f.name
    formData.append("files", f, rel)
  }
  // Empty parent = collection root (optional on API)
  if (parentFolderId) formData.append("parent_folder_id", parentFolderId)
  const res = await fetch(`${BASE}/${collectionId}/files/upload-folder`, {
    method: "POST",
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new FileMgmtApiError(res.status, body)
  }
  return res.json()
}

export const addFilePath = (collectionId: string, fileId: string, folderId: string, isPrimary = false) =>
  req<FilePath>(`/${collectionId}/files/${fileId}/paths`, {
    method: "POST",
    body: JSON.stringify({ folder_id: folderId, is_primary: isPrimary }),
  })

export const removeFilePath = (collectionId: string, fileId: string, pathId: string) =>
  req<void>(`/${collectionId}/files/${fileId}/paths/${pathId}`, { method: "DELETE" })

/** scope: "file" = exclude from search; "path" = archive specific mounts */
export const toggleFileArchive = (
  collectionId: string,
  fileId: string,
  archived: boolean,
  version: number,
  opts?: {
    folderId?: string | null
    /** Precise path rows (preferred over folderId for node context). */
    pathIds?: string[]
    scope?: "file" | "path"
  }
) =>
  req<FileSummary>(`/${collectionId}/files/${fileId}/archive`, {
    method: "PATCH",
    body: JSON.stringify({
      archived,
      version,
      scope: opts?.scope ?? "file",
      ...(opts?.folderId && opts.folderId !== "__archived__"
        ? { folder_id: opts.folderId }
        : {}),
      ...(opts?.pathIds && opts.pathIds.length > 0
        ? { path_ids: opts.pathIds }
        : {}),
    }),
  })

export const deleteFile = (collectionId: string, fileId: string) =>
  req<void>(`/${collectionId}/files/${fileId}`, { method: "DELETE" })

export const getFileDetail = (collectionId: string, fileId: string) =>
  req<FileDetail>(`/${collectionId}/files/${fileId}`)

export interface IngestTraceStep {
  id: string
  title: string
  status: string
  at?: string
  started_at?: string
  ended_at?: string
  started_ms?: number
  ended_ms?: number
  ms?: number
  detail?: string
  data?: Record<string, unknown>
}

export interface IngestTrace {
  schema?: number
  status: string
  started_at?: string
  finished_at?: string | null
  duration_ms?: number | null
  error?: string
  file_id?: string
  version_id?: string
  filename?: string
  collection?: string
  config?: Record<string, unknown>
  steps: IngestTraceStep[]
}

export const getIngestTrace = (
  collectionId: string,
  fileId: string,
  versionId?: string | null,
) => {
  const q = versionId ? `?version_id=${encodeURIComponent(versionId)}` : ""
  return req<IngestTrace>(`/${collectionId}/files/${fileId}/ingest-trace${q}`)
}

export const getFileMessages = (collectionId: string, fileId: string) =>
  req<Message[]>(`/${collectionId}/files/${fileId}/messages`)

export const createFileMessage = (
  collectionId: string,
  fileId: string,
  body: MessageCreateRequest
) =>
  req<Message>(`/${collectionId}/files/${fileId}/messages`, {
    method: "POST",
    body: JSON.stringify({ ...body, owner_type: "file", owner_id: fileId }),
  })

// ── Message (shared) ──

// ── Collection-level (root) messages ──

export const getCollectionMessages = (
  collectionId: string,
  opts?: {
    includeNodeMsgs?: boolean
    includeFileMsgs?: boolean
    recursive?: boolean
  }
) => {
  const includeNodeMsgs = opts?.includeNodeMsgs ?? false
  const includeFileMsgs = opts?.includeFileMsgs ?? false
  const recursive = opts?.recursive ?? false
  if (!includeNodeMsgs && !includeFileMsgs && !recursive) {
    return req<Message[]>(`/${collectionId}/messages`)
  }
  return req<Message[]>(
    `/${collectionId}/messages?include_node_msgs=${includeNodeMsgs}&include_file_msgs=${includeFileMsgs}&recursive=${recursive}`
  )
}

export const createCollectionMessage = (collectionId: string, body: MessageCreateRequest) =>
  req<Message>(`/${collectionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ ...body, owner_type: "collection", owner_id: collectionId }),
  })

export const updateMessage = (collectionId: string, messageId: string, body: MessageUpdateRequest) =>
  req<Message>(`/${collectionId}/messages/${messageId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  })

export const deleteMessage = (collectionId: string, messageId: string) =>
  req<void>(`/${collectionId}/messages/${messageId}`, { method: "DELETE" })

// ── Chain ──

export const listChains = (collectionId: string) =>
  req<Chain[]>(`/${collectionId}/chains`)

export const createChain = (collectionId: string, body: ChainCreateRequest) =>
  req<Chain>(`/${collectionId}/chains`, {
    method: "POST",
    body: JSON.stringify(body),
  })

export const updateChain = (collectionId: string, chainId: string, body: ChainUpdateRequest) =>
  req<Chain>(`/${collectionId}/chains/${chainId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  })

export const deleteChain = (collectionId: string, chainId: string) =>
  req<void>(`/${collectionId}/chains/${chainId}`, { method: "DELETE" })

export const reopenChain = (collectionId: string, chainId: string) =>
  req<Chain>(`/${collectionId}/chains/${chainId}/reopen`, { method: "POST" })

// ── Node ──

export const listNodes = (collectionId: string, chainId: string) =>
  req<Node[]>(`/${collectionId}/chains/${chainId}/nodes`)

export const createNode = (collectionId: string, chainId: string, body: NodeCreateRequest) =>
  req<Node>(`/${collectionId}/chains/${chainId}/nodes`, {
    method: "POST",
    body: JSON.stringify(body),
  })

export const updateNode = (collectionId: string, nodeId: string, body: NodeUpdateRequest) =>
  req<Node>(`/${collectionId}/nodes/${nodeId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  })

export const deleteNode = (collectionId: string, nodeId: string) =>
  req<void>(`/${collectionId}/nodes/${nodeId}`, { method: "DELETE" })

export const reorderNode = (collectionId: string, nodeId: string, newOrder: number) =>
  req<Node[]>(`/${collectionId}/nodes/${nodeId}/reorder`, {
    method: "POST",
    body: JSON.stringify({ new_order: newOrder }),
  })

export const getNodeDetail = (collectionId: string, nodeId: string) =>
  req<NodeDetail>(`/${collectionId}/nodes/${nodeId}`)

/** Resolve timeline node by external_ref (e.g. meeting:{meetingId}). */
export const getNodeByExternalRef = (
  collectionId: string,
  ref: string,
  chainId?: string | null,
) => {
  const qs = new URLSearchParams({ ref })
  if (chainId) qs.set("chain_id", chainId)
  return req<Node>(
    `/${collectionId}/nodes/by-external-ref?${qs.toString()}`
  )
}

/** Meeting section todo candidates attached to a timeline node. */
export const getNodeMeetingTodoCandidates = (
  collectionId: string,
  nodeId: string,
) =>
  req<NodeMeetingTodoCandidatesResponse>(
    `/${collectionId}/nodes/${nodeId}/meeting-todo-candidates`
  )

// ── Node Messages ──

export const getNodeMessages = (collectionId: string, nodeId: string) =>
  req<Message[]>(`/${collectionId}/nodes/${nodeId}/messages`)

export const createNodeMessage = (collectionId: string, nodeId: string, body: MessageCreateRequest) =>
  req<Message>(`/${collectionId}/nodes/${nodeId}/messages`, {
    method: "POST",
    body: JSON.stringify({ ...body, owner_type: "node", owner_id: nodeId }),
  })

// ── Node Attachments ──

export const attachFileToNode = (collectionId: string, nodeId: string, fileId: string) =>
  req<{ file_id: string }>(`/${collectionId}/nodes/${nodeId}/files`, {
    method: "POST",
    body: JSON.stringify({ file_id: fileId }),
  })

/** Upload a new file and attach to node (group + branch derived paths). */
export const uploadFileToNode = async (
  collectionId: string,
  nodeId: string,
  file: File,
): Promise<FileSummary> => {
  const formData = new FormData()
  formData.append("file", file)
  const res = await fetch(`${BASE}/${collectionId}/nodes/${nodeId}/files/upload`, {
    method: "POST",
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new FileMgmtApiError(res.status, body)
  }
  return res.json()
}

export const detachFileFromNode = (collectionId: string, nodeId: string, fileId: string) =>
  req<void>(`/${collectionId}/nodes/${nodeId}/files/${fileId}`, { method: "DELETE" })

// ── End Chain ──

export const endChain = (collectionId: string, nodeId: string, body: EndChainRequest) =>
  req<EndChainResult>(`/${collectionId}/nodes/${nodeId}/end-chain`, {
    method: "POST",
    body: JSON.stringify(body),
  })

// ── File (additional) ──

/** Metadata only (is_definitive). Use toggleFileArchive for archive. */
export const updateFile = (
  collectionId: string,
  fileId: string,
  body: { is_definitive?: boolean; filename?: string; version: number }
) =>
  req<FileSummary>(`/${collectionId}/files/${fileId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  })

/** Upload a new version of an existing file (multipart). */
export const uploadFileVersion = async (
  collectionId: string,
  fileId: string,
  file: File,
  commitMessage = ""
): Promise<FileSummary> => {
  const formData = new FormData()
  formData.append("file", file)
  formData.append("commit_message", commitMessage)
  const res = await fetch(`${BASE}/${collectionId}/files/${fileId}/versions`, {
    method: "POST",
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new FileMgmtApiError(res.status, body)
  }
  return res.json()
}

/**
 * List non-current (old) file versions across the collection.
 * Version-level history only — not the system Archive folder.
 */
export const listOldVersions = (collectionId: string) =>
  req<OldVersion[]>(`/${collectionId}/old-versions`)

/**
 * Permanently delete one non-current version (blob + Qdrant + log message).
 * Refuses if version_id is the file's current version.
 */
export const deleteFileVersion = (
  collectionId: string,
  fileId: string,
  versionId: string
) =>
  req<{ file_id: string; version_id: string; deleted: boolean }>(
    `/${collectionId}/files/${fileId}/versions/${versionId}`,
    { method: "DELETE" }
  )

/**
 * Roll back to a historical version: make it current and permanently delete
 * all later versions (blob + Qdrant + log) — not archive.
 */
export const rollbackFileVersion = (
  collectionId: string,
  fileId: string,
  versionId: string
) =>
  req<{
    file_id: string
    version_id: string
    version_no: number
    storage_file_id: string
    deleted_version_ids: string[]
    deleted_count: number
    restored_chunks: number
    current: boolean
  }>(`/${collectionId}/files/${fileId}/versions/${versionId}/rollback`, {
    method: "POST",
  })

/** Promote a derived path (source_node_id set) to a persistent path. */
export const promoteFilePath = (
  collectionId: string,
  fileId: string,
  pathId: string
) =>
  req<FilePath>(`/${collectionId}/files/${fileId}/promote-path`, {
    method: "POST",
    body: JSON.stringify({ path_id: pathId }),
  })

/**
 * Revert a persistent (pinned) path back to derived mode.
 * Does not delete the only path in a folder — when no timeline node can re-own
 * the folder, the API returns 400 and the path stays (use removeFilePath).
 */
export const demoteFilePath = (
  collectionId: string,
  fileId: string,
  pathId: string
) =>
  req<FilePath>(`/${collectionId}/files/${fileId}/demote-path`, {
    method: "POST",
    body: JSON.stringify({ path_id: pathId }),
  })

// ── Collection To-do ──

export const listTodos = (
  collectionId: string,
  opts?: { done?: boolean; chain_id?: string }
) => {
  const qs = new URLSearchParams()
  if (opts?.done !== undefined) qs.set("done", String(opts.done))
  if (opts?.chain_id) qs.set("chain_id", opts.chain_id)
  const q = qs.toString()
  return req<TodoItem[]>(`/${collectionId}/todos${q ? `?${q}` : ""}`)
}

/** Smart next-todo suggestions for a chain (cached; poll while pending/generating). */
export const getChainTodoSuggestions = (
  collectionId: string,
  chainId: string
) =>
  req<TodoSuggestionsResponse>(
    `/${collectionId}/chains/${chainId}/todo-suggestions`
  )

export const createTodo = (collectionId: string, body: TodoCreateRequest) =>
  req<TodoItem>(`/${collectionId}/todos`, {
    method: "POST",
    body: JSON.stringify(body),
  })

export const updateTodo = (
  collectionId: string,
  todoId: string,
  body: TodoUpdateRequest
) =>
  req<TodoItem>(`/${collectionId}/todos/${todoId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  })

export const deleteTodo = (collectionId: string, todoId: string) =>
  req<void>(`/${collectionId}/todos/${todoId}`, { method: "DELETE" })

export const linkTodoNode = (
  collectionId: string,
  todoId: string,
  body: TodoLinkNodeRequest
) =>
  req<TodoItem>(`/${collectionId}/todos/${todoId}/link-node`, {
    method: "POST",
    body: JSON.stringify(body),
  })
