const BASE = "/api"

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`API ${res.status}: ${body}`)
  }
  return res.json()
}

// ── Health ──

export const getHealth = () =>
  fetch("/health").then((r) => r.json()) as Promise<{ status: string }>

// ── Collections ──

export const getCollections = () =>
  request<{ name: string; points_count: number }[]>("/collections")

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
}

export const createCollection = (name: string, dimensions?: number, chunkConfig?: ChunkConfig) =>
  request<{ message?: string; error?: string; dimensions?: number }>("/collections", {
    method: "POST",
    body: JSON.stringify({ name, dimensions, ...chunkConfig }),
  })

export const deleteCollection = (name: string) =>
  request<{ message?: string; error?: string }>(`/collections/${name}`, {
    method: "DELETE",
  })

export const getCollectionConfig = (name: string) =>
  request<Record<string, unknown>>(`/collections/${name}/config`)

export const updateCollectionConfig = (name: string, config: Record<string, unknown>) =>
  request<{ message?: string; error?: string; config?: Record<string, unknown> }>(
    `/collections/${name}/config`,
    {
      method: "PUT",
      body: JSON.stringify(config),
    }
  )

// ── Documents ──

export const uploadFiles = async (files: FileList | File[], collection: string) => {
  const formData = new FormData()
  for (const file of Array.from(files)) {
    formData.append("files", file)
  }
  const res = await fetch(`${BASE}/documents/upload?collection=${encodeURIComponent(collection)}`, {
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
  chunk_count: number
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
}

export const getFileChunks = (collection: string, source: string, limit = 100) =>
  request<{ collection: string; source: string; chunks: ChunkDetail[]; total: number }>(
    `/documents/${collection}/files/${encodeURIComponent(source)}/chunks?limit=${limit}`
  )

export const getFilePreviewUrl = (source: string) =>
  `/api/documents/preview/${encodeURIComponent(source)}`

export const isPreviewable = (filename: string) => {
  const ext = filename.split(".").pop()?.toLowerCase() ?? ""
  return ["pdf", "txt", "md", "csv", "tsv", "docx", "xlsx", "xls", "pptx", "html", "htm", "json", "jsonl"].includes(ext)
}

// ── Config ──

export type ConfigData = Record<string, Record<string, unknown>>

export const getConfig = () => request<ConfigData>("/config")

export const updateConfig = (section: string, data: Record<string, unknown>) =>
  request<{ message?: string; error?: string }>("/config", {
    method: "PUT",
    body: JSON.stringify({ section, data }),
  })

// ── Local model management ──

export interface ModelStatus {
  id: string
  display_name: string
  source: string
  category: string
  size_mb: number
  downloaded: boolean
  status: string
  progress: number
  message: string
}

export const getModelStatus = () =>
  request<ModelStatus[]>("/models/status")

export const downloadModels = (hf_token?: string, model_ids?: string[]) =>
  request<{ success: boolean; message?: string }>("/models/download", {
    method: "POST",
    body: JSON.stringify({ hf_token, model_ids }),
  })

export const toggleModelLoad = (model_id: string) =>
  request<{ success: boolean; model_id: string; loaded: boolean }>(
    `/models/${model_id}/toggle-load`,
    { method: "POST" }
  )

export interface ModelState {
  llm_loaded: boolean
  embedding_loaded: boolean
  reranker_loaded: boolean
  config_unloaded: string[]
  load_states: Record<string, string>
}

export const getModelState = () =>
  request<ModelState>("/models/state")

export interface SetupStatus {
  setup_completed: boolean
  models: ModelStatus[]
  categories: string[]
}

export const getSetupStatus = () =>
  request<SetupStatus>("/models/setup-status")

export const markSetupComplete = () =>
  request<{ success: boolean; message?: string }>("/models/setup-complete", {
    method: "POST",
  })

export const getAvailableModels = (section: string, data?: Record<string, unknown>) =>
  request<{ models: string[]; error?: string; cached?: boolean }>(
    `/config/models/${section}`,
    {
      method: "POST",
      body: data ? JSON.stringify(data) : undefined,
    }
  )

// ── Provider types (dynamic dropdowns) ──

export interface ProviderTypeInfo {
  name: string
  display_name: string
}

export interface ProviderTypesResponse {
  embedding: ProviderTypeInfo[]
  reranker: ProviderTypeInfo[]
  llm: ProviderTypeInfo[]
  file_transcription: ProviderTypeInfo[]
  realtime_transcription: ProviderTypeInfo[]
}

export const fetchProviderTypes = () =>
  request<ProviderTypesResponse>("/config/provider-types")

// ── LLM Providers ──

export interface LLMProvider {
  id: string
  name: string
  provider: string
  model: string
  base_url: string
  api_key: string
  max_tokens: number
  max_concurrent_requests: number
  is_default: boolean
  selected_models?: string[]
  default_model?: string
}

export const getLLMProviders = () =>
  request<LLMProvider[]>("/llm/providers")

export const createLLMProvider = (data: Partial<LLMProvider>) =>
  request<LLMProvider>("/llm/providers", {
    method: "POST",
    body: JSON.stringify(data),
  })

export const updateLLMProvider = (id: string, data: Partial<LLMProvider>) =>
  request<LLMProvider>(`/llm/providers/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  })

export const deleteLLMProvider = (id: string) =>
  request<{ message?: string; error?: string }>(`/llm/providers/${id}`, {
    method: "DELETE",
  })

export const testLLMProvider = (id: string) =>
  request<{ success: boolean; message?: string; error?: string }>(
    `/llm/providers/${id}/test`,
    { method: "POST" }
  )

export const setDefaultLLMProvider = (id: string) =>
  request<{ message?: string; error?: string }>(
    `/llm/providers/${id}/set-default`,
    { method: "POST" }
  )

// ── Embedding Providers ──

export interface EmbeddingProvider {
  id: string
  name: string
  provider: string
  model: string
  base_url: string
  api_key: string
  dimensions: number
  batch_size: number
  is_default: boolean
}

export const getEmbeddingProviders = () =>
  request<EmbeddingProvider[]>("/embedding/providers")

export const createEmbeddingProvider = (data: Partial<EmbeddingProvider>) =>
  request<EmbeddingProvider>("/embedding/providers", {
    method: "POST",
    body: JSON.stringify(data),
  })

export const updateEmbeddingProvider = (id: string, data: Partial<EmbeddingProvider>) =>
  request<EmbeddingProvider>(`/embedding/providers/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  })

export const deleteEmbeddingProvider = (id: string) =>
  request<{ message?: string; error?: string }>(`/embedding/providers/${id}`, {
    method: "DELETE",
  })

export const testEmbeddingProvider = (id: string) =>
  request<{ success: boolean; message?: string; error?: string }>(
    `/embedding/providers/${id}/test`,
    { method: "POST" }
  )

export const setDefaultEmbeddingProvider = (id: string) =>
  request<{ message?: string; error?: string }>(
    `/embedding/providers/${id}/set-default`,
    { method: "POST" }
  )

// ── Rerank Providers ──

export interface RerankProvider {
  id: string
  name: string
  provider: string
  model: string
  base_url: string
  api_key: string
  top_k: number
  is_default: boolean
}

export const getRerankProviders = () =>
  request<RerankProvider[]>("/rerank/providers")

export const createRerankProvider = (data: Partial<RerankProvider>) =>
  request<RerankProvider>("/rerank/providers", {
    method: "POST",
    body: JSON.stringify(data),
  })

export const updateRerankProvider = (id: string, data: Partial<RerankProvider>) =>
  request<RerankProvider>(`/rerank/providers/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  })

export const deleteRerankProvider = (id: string) =>
  request<{ message?: string; error?: string }>(`/rerank/providers/${id}`, {
    method: "DELETE",
  })

export const testRerankProvider = (id: string) =>
  request<{ success: boolean; message?: string; error?: string }>(
    `/rerank/providers/${id}/test`,
    { method: "POST" }
  )

export const setDefaultRerankProvider = (id: string) =>
  request<{ message?: string; error?: string }>(
    `/rerank/providers/${id}/set-default`,
    { method: "POST" }
  )

// ── Collection Info ──

export interface ConflictItem {
  content1: string
  source1: string
  content2: string
  source2: string
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
}

export const getCollectionSummary = (collection: string) =>
  request<{ content: string }>(`/collections/${collection}/info/summary`)
    .catch((err) => {
      if (err instanceof Error && err.message.includes("404")) return null
      throw err
    })

export const getProjectDescription = (collection: string) =>
  request<{ content: string }>(`/collections/${collection}/info/project-description`)
    .catch((err) => {
      if (err instanceof Error && err.message.includes("404")) return null
      throw err
    })

export const getCollectionConflicts = (collection: string) =>
  request<{ conflicts: ConflictItem[] }>(`/collections/${collection}/info/conflicts`)

export const getDocSummary = (collection: string, source: string) =>
  request<DocSummary>(`/collections/${collection}/info/doc-summaries/${encodeURIComponent(source)}`)
    .catch((err) => {
      if (err instanceof Error && err.message.includes("404")) return null
      throw err
    })

export const setDocSummaryInclude = (collection: string, source: string, include: boolean) =>
  request<{ source: string; include_in_summary: boolean }>(
    `/collections/${collection}/info/doc-summaries/${encodeURIComponent(source)}/include`,
    { method: "PUT", body: JSON.stringify({ include }) }
  )

export const generateDocSummary = (collection: string, source: string) =>
  request<DocSummary>(
    `/collections/${collection}/info/doc-summaries/${encodeURIComponent(source)}/generate`,
    { method: "POST" }
  )

export const triggerConsolidation = (collection: string) =>
  request<{ message: string; task: TaskInfo }>(`/collections/${collection}/info/consolidate`, {
    method: "POST",
  })

export const getMeetingLog = (collection: string) =>
  request<{ meetings: MeetingLogItem[] }>(`/collections/${collection}/info/meeting-log`)

export interface ActiveTasksResult {
  active_tasks: Array<{ id: string; task_type: string; status: string; message: string; progress: number }>
  consolidating: boolean
  uploading: boolean
}

export const getActiveCollectionTasks = (collection: string) =>
  request<ActiveTasksResult>(`/collections/${collection}/info/active-tasks`)

// ── Recall ──

export interface RecallResult {
  id: string
  text: string
  score: number
  source: string
  collection: string
  chunk_index: number
  chunk_type: string
  context?: string
  parent_id?: string
  children?: RecallResult[]
}

export const recallSearch = (params: {
  query: string
  collections: string[]
  search_mode?: string
  top_k: number
  rerank_top_k: number
  use_reranker?: boolean
  use_agent?: boolean
  min_score?: number
  rerank_provider_id?: string
}) =>
  request<{ results: RecallResult[]; time_ms: number }>("/recall/search", {
    method: "POST",
    body: JSON.stringify(params),
  })

// ── Recall Evaluation ──

export interface EvalTestCase {
  id: string
  query: string
  target_chunk_id: string
  target_source: string
  created_at?: string
}

export interface EvalChunkJudgment {
  id: string
  source: string
  chunk_index: number
  score: number
  judgment: number   // -1, 0, +1
  reason: string
  is_target: boolean
}

export interface EvalRetrievedChunk {
  id: string
  text: string
  score: number
  source: string
  chunk_index: number
  chunk_type: string
  context?: string
  children?: { id: string; text: string; score: number; chunk_index: number }[]
}

export interface EvalResultRow {
  test_case_id: string
  query: string
  target_source: string
  hard_recall: number      // 0 or 1 — target_chunk_id in top K
  holistic_can_answer: number  // 0 or 1 — LLM holistic "can the user get a correct answer?"
  holistic_reason: string
  recalled: number         // 0 or 1 — hard OR holistic
  quality_score: number    // coverage-dominant on per-chunk judgments, range [-1, 1]
  mrr: number
  target_position: number  // 0 if not found, else 1-based
  chunk_judgments: EvalChunkJudgment[]
  retrieved_chunks: EvalRetrievedChunk[]
  time_ms: number
}

export interface EvalReport {
  collection: string
  config_snapshot: Record<string, unknown>
  total_cases: number
  avg_hard_recall: number
  avg_holistic_recall: number
  avg_recall: number       // avg of (hard OR holistic)
  avg_quality_score: number
  avg_mrr: number
  hit_rate: number
  avg_time_ms: number
  per_query: EvalResultRow[]
  timestamp: string
}

export const getEvalCases = (collection: string) =>
  request<{ cases: EvalTestCase[] }>(`/recall/eval/${collection}/cases`)

export const deleteEvalCase = (collection: string, caseId: string) =>
  request<{ message: string }>(`/recall/eval/${collection}/cases/${caseId}`, {
    method: "DELETE",
  })

export const generateEvalCases = (collection: string, regenerate = false) =>
  request<{ message: string; total: number }>(
    regenerate
      ? `/recall/eval/${collection}/cases/generate?regenerate=true`
      : `/recall/eval/${collection}/cases/generate`,
    { method: "POST" }
  )

export const runEval = (collection: string, params: {
  top_k?: number
  search_mode?: string
  use_reranker?: boolean
  rerank_top_k?: number
  min_score?: number
  rerank_provider_id?: string
}) =>
  request<EvalReport>(`/recall/eval/${collection}/run`, {
    method: "POST",
    body: JSON.stringify({ collection, ...params }),
  })

export const getEvalHistory = (collection: string) =>
  request<{ history: EvalReport[] }>(`/recall/eval/${collection}/history`)

export interface ChunkContent {
  id: string
  text: string
  source: string
  chunk_index: number
}

export const getChunkContent = (collection: string, chunkId: string) =>
  request<ChunkContent>(`/recall/eval/${collection}/chunk/${chunkId}`)

// ── Meetings ──

export type MeetingStatus = "created" | "recording" | "transcribing" | "completed"
export type MeetingMode = "upload" | "record"

export interface TodoItem {
  text: string
  assignee?: string
  priority?: string
}

export interface TranscriptSegment {
  start: number
  end: number
  text: string
  speaker_id?: string
}

export interface Meeting {
  id: string
  title: string
  status: MeetingStatus
  mode?: MeetingMode
  audio_path?: string
  notes_path?: string
  transcript_path?: string
  detail?: string
  summary?: string
  todos?: TodoItem[]
  notes_content?: string
  transcription_error?: string
  summarizing?: boolean
  allocated_collections: string[]
  allocated_file_ids: string[]
  speaker_names?: Record<string, string>
  hot_words_library_id?: string | null
  created_at: string
  updated_at: string
}

export const getMeetings = () =>
  request<Meeting[]>("/meetings")

export const getMeeting = (id: string) =>
  request<Meeting>(`/meetings/${id}`)

export const createMeeting = (title?: string) =>
  request<Meeting>("/meetings", {
    method: "POST",
    body: JSON.stringify(title ? { title } : {}),
  })

export const updateMeeting = (id: string, data: Partial<Pick<Meeting, "title" | "detail" | "summary" | "todos" | "speaker_names" | "hot_words_library_id"> & { notes?: string }>) =>
  request<Meeting>(`/meetings/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  })

export const deleteMeeting = (id: string) =>
  request<{ message?: string; error?: string }>(`/meetings/${id}`, {
    method: "DELETE",
  })

export const uploadMeetingAudio = async (id: string, file: File) => {
  const formData = new FormData()
  formData.append("file", file)
  const res = await fetch(`${BASE}/meetings/${id}/upload-audio`, {
    method: "POST",
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`Upload failed (${res.status}): ${body}`)
  }
  return res.json() as Promise<Meeting>
}

export const uploadMeetingNotes = async (id: string, file: File) => {
  const formData = new FormData()
  formData.append("file", file)
  const res = await fetch(`${BASE}/meetings/${id}/upload-notes`, {
    method: "POST",
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`Upload failed (${res.status}): ${body}`)
  }
  return res.json() as Promise<{ notes_content: string }>
}

export const transcribeMeeting = (id: string, languageHints?: string[]) =>
  request<{ message: string; task_id?: string }>(`/meetings/${id}/transcribe`, {
    method: "POST",
    body: JSON.stringify({ language_hints: languageHints }),
  })

export const cancelTranscribeMeeting = (id: string) =>
  request<{ message: string }>(`/meetings/${id}/cancel-transcribe`, {
    method: "POST",
  })

// ── Meeting Multi-Ingest ──

export interface ProjectSplit {
  name: string
  summary: string
  detail: string
  todos: any[]
}

export interface CollectionRecommendation {
  collection: string
  score: number
}

export const splitMeetingByProject = (meetingId: string) =>
  request<{ projects: ProjectSplit[] }>(`/meetings/${meetingId}/split-by-project`, {
    method: "POST",
  })

export const recommendCollectionsForText = (text: string) =>
  request<{ recommendations: CollectionRecommendation[] }>(`/recommend-collections-for-text`, {
    method: "POST",
    body: JSON.stringify({ text }),
  })

export const allocateMulti = (meetingId: string, allocations: { collection: string; content: string }[]) =>
  request<any>(`/meetings/${meetingId}/allocate-multi`, {
    method: "POST",
    body: JSON.stringify({ allocations }),
  })

export const deleteAllAllocations = (meetingId: string) =>
  request<{ message: string }>(`/meetings/${meetingId}/allocations`, {
    method: "DELETE",
  })

export const generateMeetingSummary = (id: string) =>
  request<Meeting>(`/meetings/${id}/generate-summary`, {
    method: "POST",
  })

export const getMeetingTranscript = (id: string) =>
  request<{ segments: TranscriptSegment[] }>(`/meetings/${id}/transcript`)

export const saveMeetingTranscript = (
  id: string,
  payload: { segments: TranscriptSegment[]; text?: string },
) =>
  request<{ message: string; segments: number }>(`/meetings/${id}/save-transcript`, {
    method: "POST",
    body: JSON.stringify(payload),
  })

// ── Transcription Providers ──

export interface TranscriptionProvider {
  id: string
  name: string
  adapter: string
  api_key: string
  model?: string
  is_active: boolean
  models_downloaded?: boolean
  language_hints_config?: LanguageHintOption[]
}

// File transcription providers
export const getFileTranscriptionProviders = () =>
  request<TranscriptionProvider[]>("/transcription/file-providers")

export const createFileTranscriptionProvider = (data: Partial<TranscriptionProvider>) =>
  request<TranscriptionProvider>("/transcription/file-providers", {
    method: "POST",
    body: JSON.stringify(data),
  })

export const updateFileTranscriptionProvider = (id: string, data: Partial<TranscriptionProvider>) =>
  request<TranscriptionProvider>(`/transcription/file-providers/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  })

export const deleteFileTranscriptionProvider = (id: string) =>
  request<{ message?: string; error?: string }>(`/transcription/file-providers/${id}`, {
    method: "DELETE",
  })

export const setActiveFileTranscriptionProvider = (id: string) =>
  request<{ message?: string; error?: string }>(`/transcription/file-providers/${id}/set-active`, {
    method: "POST",
  })

export const testFileTranscriptionProvider = (id: string) =>
  request<{ success: boolean; message?: string; error?: string }>(`/transcription/file-providers/${id}/test`, {
    method: "POST",
  })


// Realtime transcription providers
export const getRealtimeTranscriptionProviders = () =>
  request<TranscriptionProvider[]>("/transcription/realtime-providers")

export const createRealtimeTranscriptionProvider = (data: Partial<TranscriptionProvider>) =>
  request<TranscriptionProvider>("/transcription/realtime-providers", {
    method: "POST",
    body: JSON.stringify(data),
  })

export const updateRealtimeTranscriptionProvider = (id: string, data: Partial<TranscriptionProvider>) =>
  request<TranscriptionProvider>(`/transcription/realtime-providers/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  })

export const deleteRealtimeTranscriptionProvider = (id: string) =>
  request<{ message?: string; error?: string }>(`/transcription/realtime-providers/${id}`, {
    method: "DELETE",
  })

export const setActiveRealtimeTranscriptionProvider = (id: string) =>
  request<{ message?: string; error?: string }>(`/transcription/realtime-providers/${id}/set-active`, {
    method: "POST",
  })

export const testRealtimeTranscriptionProvider = (id: string) =>
  request<{ success: boolean; message?: string; error?: string }>(`/transcription/realtime-providers/${id}/test`, {
    method: "POST",
  })


// ── Hot Words ──

export interface HotWordItem {
  text: string
  weight: number
  lang?: string
}

export interface HotWordsLibrary {
  id: string
  name: string
  description: string
  words: HotWordItem[]
  created_at: string
  updated_at: string
}

export interface HotWordsLibrarySummary {
  id: string
  name: string
  description: string
  word_count: number
  created_at: string
  updated_at: string
}

export const getHotWordsLibraries = () =>
  request<HotWordsLibrarySummary[]>("/hot-words")

export const getHotWordsLibrary = (id: string) =>
  request<HotWordsLibrary>(`/hot-words/${id}`)

export const createHotWordsLibrary = (data: { name: string; description?: string }) =>
  request<HotWordsLibrary>("/hot-words", {
    method: "POST",
    body: JSON.stringify(data),
  })

export const updateHotWordsLibrary = (id: string, data: Partial<HotWordsLibrary>) =>
  request<HotWordsLibrary>(`/hot-words/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  })

export const deleteHotWordsLibrary = (id: string) =>
  request<{ message?: string; error?: string }>(`/hot-words/${id}`, {
    method: "DELETE",
  })

export interface LanguageHintOption {
  code: string
  label: string
}

export interface ActiveProviderInfo {
  file: { supports_hot_words: boolean; supported_language_hints: LanguageHintOption[] }
  realtime: { supports_hot_words: boolean; supported_language_hints: LanguageHintOption[] }
}

export const getActiveProviderInfo = () =>
  request<ActiveProviderInfo>("/transcription/active-provider-info")
