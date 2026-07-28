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
  FolderCreateRequest,
  FolderUpdateRequest,
  GroupCreateRequest,
  GroupUpdateRequest,
  MessageCreateRequest,
  MessageUpdateRequest,
} from "@/types/file-mgmt"

const BASE = "/api/file-mgmt"

async function req<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`API ${res.status}: ${body}`)
  }
  // 204 No Content
  if (res.status === 204) return undefined as unknown as T
  return res.json()
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
  includeFileMsgs = true
) =>
  req<Message[]>(
    `/${collectionId}/folders/${folderId}/messages?include_node_msgs=${includeNodeMsgs}&include_file_msgs=${includeFileMsgs}`
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
  formData.append("folder_id", folderId)
  if (sourceNodeId) formData.append("source_node_id", sourceNodeId)
  const res = await fetch(`${BASE}/${collectionId}/files/upload`, {
    method: "POST",
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`API ${res.status}: ${body}`)
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
    formData.append("files", f)
  }
  formData.append("parent_folder_id", parentFolderId)
  const res = await fetch(`${BASE}/${collectionId}/files/upload-folder`, {
    method: "POST",
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`API ${res.status}: ${body}`)
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

export const toggleFileArchive = (collectionId: string, fileId: string, archived: boolean, version: number) =>
  req<FileSummary>(`/${collectionId}/files/${fileId}/archive`, {
    method: "PATCH",
    body: JSON.stringify({ archived, version }),
  })

export const deleteFile = (collectionId: string, fileId: string) =>
  req<void>(`/${collectionId}/files/${fileId}`, { method: "DELETE" })

export const getFileDetail = (collectionId: string, fileId: string) =>
  req<FileDetail>(`/${collectionId}/files/${fileId}`)

// ── Message (shared) ──

// ── Collection-level (root) messages ──

export const getCollectionMessages = (collectionId: string) =>
  req<Message[]>(`/${collectionId}/messages`)

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
