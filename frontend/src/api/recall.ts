import { request } from "./http"

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
  /** Current human-readable name (rename / multi-version aware) */
  display_name?: string
  /** Ingest-time label snapshot (fallback only) */
  source_label?: string
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
  sparse_llm_tokenize?: boolean
  rerank_provider_id?: string
}) =>
  request<{ results: RecallResult[]; time_ms: number; total: number; search_params?: Record<string, unknown>; context?: string }>("/recall/search", {
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
  sparse_llm_tokenize?: boolean
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

