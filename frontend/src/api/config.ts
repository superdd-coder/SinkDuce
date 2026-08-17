import { request } from "./http"

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

/** Download ONNX ASR packs from the official GitHub Release (no HuggingFace). */
export const downloadModels = (model_ids?: string[]) =>
  request<{ success: boolean; message?: string }>("/models/download", {
    method: "POST",
    body: JSON.stringify({ model_ids }),
  })

export const deleteLocalModel = (model_id: string) =>
  request<{
    success: boolean
    model_id?: string
    display_name?: string
    removed?: boolean
    freed_mb?: number
    unloaded_providers?: string[]
    error?: string
  }>(`/models/${encodeURIComponent(model_id)}`, { method: "DELETE" })

export const deleteLocalModels = (model_ids: string[]) =>
  request<{
    success: boolean
    freed_mb?: number
    unloaded_providers?: string[]
    results?: Array<{ model_id: string; success: boolean; freed_mb?: number; error?: string }>
    error?: string
  }>("/models/delete", {
    method: "POST",
    body: JSON.stringify({ model_ids }),
  })

export const toggleModelLoad = (
  model_id: string,
  action?: "load" | "unload",
) =>
  request<{
    success: boolean
    model_id: string
    loaded: boolean
    status?: string
    message?: string
    error?: string
  }>(`/models/${model_id}/toggle-load`, {
    method: "POST",
    body: JSON.stringify(action ? { action } : {}),
  })

export interface ModelLoadDetail {
  state?: string
  message?: string
  error?: string
  started_at?: number
  load_s?: number
}

export interface ModelState {
  llm_loaded: boolean
  embedding_loaded: boolean
  reranker_loaded: boolean
  config_unloaded?: string[]
  load_states: Record<string, string>
  load_details?: Record<string, ModelLoadDetail>
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
  /** Present on file/realtime transcription adapters */
  supports_hot_words?: boolean
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

  is_default: boolean
  function_call_model_ids: string[]
  selected_models?: string[]
  default_model?: string
  visual_model_ids?: string[]
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
  request<{
    message?: string
    error?: string
    provider_id?: string
    adapter?: string
    name?: string
  }>(`/transcription/file-providers/${id}/set-active`, {
    method: "POST",
  })

export const testFileTranscriptionProvider = (id: string) =>
  request<{ success: boolean; message?: string; error?: string; code?: string }>(
    `/transcription/file-providers/${id}/test`,
    { method: "POST" },
  )


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
  request<{
    message?: string
    error?: string
    provider_id?: string
    adapter?: string
    name?: string
  }>(`/transcription/realtime-providers/${id}/set-active`, {
    method: "POST",
  })

export const testRealtimeTranscriptionProvider = (id: string) =>
  request<{ success: boolean; message?: string; error?: string; code?: string }>(
    `/transcription/realtime-providers/${id}/test`,
    { method: "POST" },
  )



export interface LanguageHintOption {
  code: string
  label: string
}

export interface ActiveProviderSideInfo {
  supports_hot_words: boolean
  supported_language_hints: LanguageHintOption[]
  /** Official language_hints cap for the active adapter+model (1 or 4). */
  max_language_hints?: number
  /** Resolved adapter name, e.g. funasr_onnx / dashscope_funasr */
  adapter?: string | null
  id?: string | null
  name?: string | null
  model?: string | null
  /** Registry display name for UI captions */
  display_name?: string | null
}

export interface ActiveProviderInfo {
  file: ActiveProviderSideInfo
  realtime: ActiveProviderSideInfo
}

export const getActiveProviderInfo = () =>
  request<ActiveProviderInfo>("/transcription/active-provider-info")
