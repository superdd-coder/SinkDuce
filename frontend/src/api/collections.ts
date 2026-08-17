import { API_BASE, request } from "./http"

// ── Collections ──

export interface CollectionItem {
  id: string
  name: string
  points_count: number
}

export const getCollections = () =>
  request<CollectionItem[]>("/collections")

export interface ChunkConfig {
  chunk_mode?: string
  parent_strategy?: string
  chunk_size?: number
  chunk_overlap?: number
  buffer_ratio?: number
  parent_chunk_size?: number
  parent_chunk_overlap?: number
  child_chunk_size?: number
  child_chunk_overlap?: number
  allowed_file_types?: string[]
  /** MinerU cloud parsing — default ON (matches Collection Config UI) */
  cloud_parsing?: boolean
}

export const createCollection = (name: string, dimensions?: number, chunkConfig?: ChunkConfig) =>
  request<{ id?: string; message?: string; error?: string; dimensions?: number }>("/collections", {
    method: "POST",
    // Always send cloud_parsing so new collections persist the UI default (ON)
    // even when the create dialog does not expose the checkbox.
    body: JSON.stringify({
      name,
      dimensions,
      cloud_parsing: true,
      ...chunkConfig,
    }),
  })

export const deleteCollection = (collectionId: string) =>
  request<{ message?: string; error?: string }>(`/collections/${collectionId}`, {
    method: "DELETE",
  })

export const renameCollection = (collectionId: string, newName: string) =>
  request<{ message?: string; error?: string }>(`/collections/${collectionId}/rename`, {
    method: "PUT",
    body: JSON.stringify({ name: newName }),
  })

export const getCollectionConfig = (collectionId: string) =>
  request<Record<string, unknown>>(`/collections/${collectionId}/config`)

export const updateCollectionConfig = (collectionId: string, config: Record<string, unknown>) =>
  request<{ message?: string; error?: string; config?: Record<string, unknown> }>(
    `/collections/${collectionId}/config`,
    {
      method: "PUT",
      body: JSON.stringify(config),
    }
  )

export const triggerSparseRecalc = (collectionId: string) =>
  request<{ message?: string; task_id?: string; error?: string }>(
    `/collections/${collectionId}/sparse-recalc`,
    { method: "POST" }
  )

// ── Documents ──

export const uploadFiles = async (files: FileList | File[], collection: string) => {
  const formData = new FormData()
  for (const file of Array.from(files)) {
    formData.append("files", file)
  }
  const res = await fetch(`${API_BASE}/documents/upload?collection=${encodeURIComponent(collection)}`, {
    method: "POST",
    body: formData,
  })
  if (!res.ok) {
    let msg = `Upload failed (${res.status})`
    try {
      const body = await res.json()
      msg = body.detail || body.message || msg
    } catch { /* use default */ }
    throw new Error(msg)
  }
  return res.json() as Promise<{ message: string; tasks: TaskInfo[] }>
}

// ── Tasks ──

export interface TaskInfo {
  id: string
  filename: string
  status: "pending" | "processing" | "completed" | "failed"
  progress: number
  message: string
  result?: { filename: string; chunks_count: number; message: string }
  error?: string
  created_at: string
  started_at?: string
  completed_at?: string
}

export const getTasks = (collection?: string) =>
  request<{ tasks: TaskInfo[]; pending: number; processing: number }>(
    collection ? `/documents/tasks?collection=${encodeURIComponent(collection)}` : "/documents/tasks"
  )

export const getTask = (taskId: string) =>
  request<TaskInfo>(`/documents/tasks/${taskId}`)

export const clearCompletedTasks = () =>
  request<{ message: string }>("/documents/tasks/completed", { method: "DELETE" })

export const cancelTask = (taskId: string) =>
  request<{ message: string }>(`/documents/tasks/${taskId}/cancel`, { method: "POST" })

export const retryTask = (taskId: string) =>
  request<{ message: string; task?: TaskInfo }>(`/documents/tasks/${taskId}/retry`, { method: "POST" })

export const deleteDocument = (collection: string, source: string) =>
  request<{ message?: string; error?: string }>(
    `/documents/${collection}/${encodeURIComponent(source)}`,
    { method: "DELETE" }
  )

export interface FileListItem {
  source: string
  /** Managed file-mgmt id (index / SQLite key) when available */
  file_id?: string
  chunk_count: number
  file_type?: string
  original_ext?: string
  note_title?: string
  has_meeting?: boolean
  display_name?: string
  has_summary?: boolean | null
  include_in_summary?: boolean | null
}

export const getFiles = (collection: string) =>
  request<{ collection: string; files: FileListItem[] }>(
    `/documents/${collection}/files`
  )

export interface ChunkDetail {
  id: string
  text: string
  chunk_index: number
  file_type: string
  context: string
  chunk_type?: string
  parent_id?: string
  collection?: string
  summary?: string
  // Position fields for source navigation
  char_offset?: number
  page_number?: number
  slide_number?: number
  section_label?: string
  heading_path?: string
  note_id?: string
  meeting_id?: string
  sheet_name?: string
  label?: string
  version_id?: string
  archived?: boolean
  file_id?: string
  source?: string
  source_label?: string
  chunk_id?: string
  total_chunks?: number
  ingested_at?: number
  created_by?: string
  is_current?: boolean
  meeting_date?: string
  [key: string]: unknown
}

export const getFileChunks = (
  collection: string,
  source: string,
  limit = 100,
  opts?: { versionId?: string; includeArchived?: boolean }
) => {
  const q = new URLSearchParams()
  q.set("limit", String(limit))
  if (opts?.versionId) q.set("version_id", opts.versionId)
  if (opts?.includeArchived) q.set("include_archived", "true")
  return request<{
    collection: string
    source: string
    chunks: ChunkDetail[]
    total: number
  }>(
    `/documents/${collection}/files/${encodeURIComponent(source)}/chunks?${q.toString()}`
  )
}

export const getFilePreviewUrl = (
  source: string,
  opts?: { collection?: string; storageFile?: string; versionId?: string }
) => {
  const q = new URLSearchParams()
  if (opts?.collection) q.set("collection", opts.collection)
  if (opts?.storageFile) q.set("storage_file", opts.storageFile)
  if (opts?.versionId) q.set("version_id", opts.versionId)
  const qs = q.toString()
  return `/api/documents/preview/${encodeURIComponent(source)}${qs ? `?${qs}` : ""}`
}

export const getExtractedText = (
  source: string,
  collection?: string,
  opts?: { storageFile?: string; versionId?: string }
) => {
  const q = new URLSearchParams()
  if (collection) q.set("collection", collection)
  if (opts?.storageFile) q.set("storage_file", opts.storageFile)
  if (opts?.versionId) q.set("version_id", opts.versionId)
  const qs = q.toString()
  return request<{ text: string; format: string }>(
    `/documents/extracted/${encodeURIComponent(source)}${qs ? `?${qs}` : ""}`
  )
}

export const isPreviewable = (filename: string) => {
  const ext = filename.split(".").pop()?.toLowerCase() ?? ""
  return ["pdf", "txt", "md", "csv", "tsv", "docx", "xlsx", "xls", "pptx", "html", "htm", "json", "jsonl"].includes(ext)
}

// ── Collection Info ──

export interface ConflictItem {
  content1: string
  source1: string
  source1_label?: string
  content2: string
  source2: string
  source2_label?: string
}

export interface DocSummary {
  data: string[]
  facts: string[]
  insights: string[]
  include_in_summary?: boolean
}

export interface MeetingLogItem {
  id: string
  title: string
  created_at: string
  file_ids?: string[]
  file_labels?: Record<string, string>
}

export const getCollectionSummary = (collectionId: string) =>
  request<{ content: string }>(`/collections/${collectionId}/info/summary`)
    .catch((err) => {
      if (err instanceof Error && err.message.includes("404")) return null
      throw err
    })

export const getProjectDescription = (collectionId: string) =>
  request<{ content: string }>(`/collections/${collectionId}/info/project-description`)
    .catch((err) => {
      if (err instanceof Error && err.message.includes("404")) return null
      throw err
    })

export const getCollectionConflicts = (collectionId: string) =>
  request<{ conflicts: ConflictItem[] }>(`/collections/${collectionId}/info/conflicts`)

export const getDocSummary = (
  collectionId: string,
  source: string,
  opts?: { versionId?: string }
) => {
  const q = new URLSearchParams()
  if (opts?.versionId) q.set("version_id", opts.versionId)
  const qs = q.toString()
  return request<DocSummary>(
    `/collections/${collectionId}/info/doc-summaries/${encodeURIComponent(source)}${qs ? `?${qs}` : ""}`
  ).catch((err) => {
    if (err instanceof Error && err.message.includes("404")) return null
    throw err
  })
}

export const setDocSummaryInclude = (collectionId: string, source: string, include: boolean) =>
  request<{ source: string; include_in_summary: boolean }>(
    `/collections/${collectionId}/info/doc-summaries/${encodeURIComponent(source)}/include`,
    { method: "PUT", body: JSON.stringify({ include }) }
  )

/** Always targets current version. Optional versionId must be current or API returns 400. */
export const generateDocSummary = (
  collectionId: string,
  source: string,
  opts?: { versionId?: string }
) => {
  const q = new URLSearchParams()
  if (opts?.versionId) q.set("version_id", opts.versionId)
  const qs = q.toString()
  return request<{ message: string; task?: TaskInfo; source?: string } & Partial<DocSummary>>(
    `/collections/${collectionId}/info/doc-summaries/${encodeURIComponent(source)}/generate${qs ? `?${qs}` : ""}`,
    { method: "POST" }
  )
}

export const triggerConsolidation = (collectionId: string) =>
  request<{ message: string; task: TaskInfo }>(`/collections/${collectionId}/info/consolidate`, {
    method: "POST",
  })

export const getMeetingLog = (collectionId: string) =>
  request<{ meetings: MeetingLogItem[] }>(`/collections/${collectionId}/info/meeting-log`)

export interface ActiveTasksResult {
  active_tasks: Array<{ id: string; task_type: string; status: string; message: string; progress: number }>
  consolidating: boolean
  uploading: boolean
}

export const getActiveCollectionTasks = (collectionId: string) =>
  request<ActiveTasksResult>(`/collections/${collectionId}/info/active-tasks`)

