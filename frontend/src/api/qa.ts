import { apiFetch, request } from "./http"
import { readQaDraftStream, type QaDraftProgress } from "../lib/qa-draft-progress"

export type { QaDraftProgress } from "../lib/qa-draft-progress"

export type QaStatus = string
export type QaType = string
export interface QaOption { id: string; label: string; color: string }
export interface QaOptions { statuses: QaOption[]; types: QaOption[] }
export interface QaExtraField { id: string; label: string; value: string | number | boolean | null; kind: string }
export interface QaVersion { id: string; number: number; state: "working" | "frozen"; created_at: string }
export interface QaColumn { id: string; label: string; included: boolean; position: number; absent_since: number | null; source_column?: number }
export interface QaReply { sources: QaSource[]; column_id: string; label: string; body: string; included: boolean; modified: boolean; historical_body: string; cleared_since: number | null; absent_since: number | null; text_revision_id: string }
export interface QaQuestion { display_number?: string; type_origin?: "default" | "ai" | "manual"; fields?: QaExtraField[]; group?: string; group_id?: string; parent_id?: string; summary: string; reply_summary: string; id: string; text: string; included: boolean; position: number; status: QaStatus; type: QaType; external_number: string; updated_at: string; absent_since: number | null; replies: QaReply[]; latest_reply: string }
export interface QaListSummary { id: string; title: string; revision: number; archived: boolean; question_count: number; version_number: number; updated_at: string }
export interface QaOutlineItem { display_number?: string; id: string; kind: "group" | "question" | "note"; text?: string; group_id?: string; parent_id?: string }
export interface QaListDetail { annotation_pending?: number; annotation_state?: "idle" | "running" | "partial" | "ready"; outline?: QaOutlineItem[]; options?: QaOptions; original_layout?: boolean; template_id: string | null; id: string; title: string; revision: number; archived: boolean; current_version_id: string; version: QaVersion; columns: QaColumn[]; questions: QaQuestion[]; can_edit: boolean }
export interface QaProjectQuestion { display_number?: string; type_origin?: "default" | "ai" | "manual"; status_label: string; status_color: string; type_label: string; id: string; text: string; included: boolean; status: QaStatus; type: QaType; updated_at: string; list_id: string; list_title: string }
export interface QaReplyRevision { id: string; body: string; created_at: string; created_by: string }

export const qaBase = (collection: string) => `/collections/${encodeURIComponent(collection)}/qa`
export const getQaLists = (collection: string, signal?: AbortSignal) => request<QaListSummary[]>(`${qaBase(collection)}/lists`, { signal })
export const getQaList = (collection: string, id: string, version?: string, signal?: AbortSignal) => request<QaListDetail>(`${qaBase(collection)}/lists/${id}${version ? `?version_id=${encodeURIComponent(version)}` : ""}`, { signal })
export const createQaList = (collection: string, title: string, columns?: string[]) => request<QaListDetail>(`${qaBase(collection)}/lists`, { method: "POST", body: JSON.stringify({ title, columns }) })
export const mutateQaList = (collection: string, list: QaListDetail, path: string, payload: Record<string, unknown>, method = "POST") => request<QaListDetail>(`${qaBase(collection)}/lists/${list.id}${path}`, { method, body: JSON.stringify({ expected_revision: list.revision, version_id: list.version.id, ...payload }) })
export const getQaVersions = (collection: string, id: string) => request<QaVersion[]>(`${qaBase(collection)}/lists/${id}/versions`)
export interface QaDeleteResult { ok: boolean; cleanup_pending: boolean }
export const deleteQaList = (collection: string, list: QaListDetail, confirm_title: string) => request<QaDeleteResult>(`${qaBase(collection)}/lists/${list.id}`, { method: "DELETE", body: JSON.stringify({ expected_revision: list.revision, confirm_title }) })
export const getQaProjectQuestions = (collection: string, includeOmitted: boolean, signal?: AbortSignal) => request<QaProjectQuestion[]>(`${qaBase(collection)}/questions?include_omitted=${includeOmitted}`, { signal })
export const getQaReplyHistory = (collection: string, list: QaListDetail, question: string, column: string) => request<QaReplyRevision[]>(`${qaBase(collection)}/lists/${list.id}/questions/${question}/replies/${column}/history?version_id=${list.version.id}`)

export interface QaMapping { question: number; number?: number | null; status?: number | null; type?: number | null; group?: number | null; replies: number[]; extras?: number[]; ignored?: number[] }
export interface QaRegion { start_column: number; end_column: number; header_end_row: number; end_row: number }
export type QaImportContentChoice = "keep" | "incoming"
export interface QaImportConflict {
  id: string; row_key: string; question_id: string; column_key: string | null;
  kind: "question" | "reply" | "status" | "type" | "extra" | "structure" | "membership";
  label: string; current: string; incoming: string; baseline: string | null; choice: QaImportContentChoice | null;
}
export interface QaImportContentReview {
  conflicts: QaImportConflict[]; unresolved: number;
  missing_questions: { id: string; text: string; kept: boolean; reason: "local_changes" | "draft" | "not_in_file" }[];
  kept_columns: { id: string; label: string }[];
  preview?: Record<string, { text?: string; replies?: Record<string, string>; included?: boolean }>;
  counts: { kept_replies: number; updated_replies: number; new_replies: number; conflicts: number; cleared_replies?: number; blank_kept?: number };
}
export interface QaImportMergeChanges {
  content_choices?: Record<string, QaImportContentChoice>;
  blank_columns?: string[]; retained_questions?: string[]; removed_questions?: string[];
  retained_replies?: Record<string, string[]>;
}
export interface QaImportPlan {
  review?: QaImportContentReview; stale?: boolean;
  content_choices?: Record<string, QaImportContentChoice>;
  blank_columns?: string[]; retained_questions?: string[]; removed_questions?: string[];
  retained_replies?: Record<string, string[]>;
  region?: QaRegion; row_actions?: Record<string, string>; parents?: Record<string, string>; structure_status?: string; analysis_complete?: boolean;
  candidates?: { sheet: string; header_row: number; start_column: number; end_column: number }[];
  table_rows?: { key: string; values: string[]; kind: string; group?: string; group_key?: string; parent_key?: string }[];
  header_values?: string[][];
  preview?: { row: number; values: string[] }[];
  mapping_valid: boolean; mapping_confirmed?: boolean; ai_status: string; suggestions?: Record<string, { target: string; reason: string }>;
  id: string; filename: string; plan_revision: number; base_revision: number; sheets: string[]; sheet: string; header_row: number;
  headers: string[]; rows: { key: string; values: string[]; text: string; group?: string; group_key?: string; formatted_values?: Record<string, string> }[];
  mapping: QaMapping;
  matches: Record<string, string | null>; column_matches: Record<string, string | null>;
}
export const uploadQaImport = async (collection: string, list: QaImportContext, file: File): Promise<QaImportPlan> => {
  const form = new FormData(); form.append("file", file); form.append("expected_revision", String(list.revision))
  const response = await apiFetch(`${qaBase(collection)}/lists/${list.id}/imports`, { method: "POST", body: form })
  if (!response.ok) throw new Error(await response.text())
  return response.json()
}
export const patchQaImport = (collection: string, id: string, plan: QaImportPlan, changes: Record<string, unknown>) => request<QaImportPlan>(`${qaBase(collection)}/lists/${id}/imports/${plan.id}`, { method: "PATCH", body: JSON.stringify({ plan_revision: plan.plan_revision, ...changes }) })
export const commitQaImport = (collection: string, id: string, plan: QaImportPlan) => request<QaListDetail>(`${qaBase(collection)}/lists/${id}/imports/${plan.id}/commit`, { method: "POST", body: JSON.stringify({ plan_revision: plan.plan_revision }) })
export const exportQaFile = async (collection: string, list: QaListDetail, options: Record<string, boolean>): Promise<Blob> => {
  const record = await request<{ id: string }>(`${qaBase(collection)}/lists/${list.id}/exports`, { method: "POST", body: JSON.stringify({ expected_revision: list.revision, ...options }) })
  const response = await apiFetch(`${qaBase(collection)}/lists/${list.id}/exports/${record.id}/download`)
  if (!response.ok) throw new Error(await response.text())
  return response.blob()
}
export const downloadQaOriginal = async (collection: string, list: QaListDetail): Promise<Blob> => {
  const response = await apiFetch(`${qaBase(collection)}/lists/${list.id}/versions/${list.version.id}/original`)
  if (!response.ok) throw new Error(await response.text())
  return response.blob()
}

export type QaImportContext = Pick<QaListDetail, "id" | "title" | "revision" | "questions" | "columns">
export const createQaImportList = (collection: string, title: string) => request<QaImportContext>(`${qaBase(collection)}/lists`, { method: "POST", body: JSON.stringify({ title, mode: "import" }) })
export const analyzeQaImport = (collection: string, id: string, plan: QaImportPlan) => request<QaImportPlan>(`${qaBase(collection)}/lists/${id}/imports/${plan.id}/analyze`, { method: "POST", body: JSON.stringify({ plan_revision: plan.plan_revision }) })

export interface QaSource { kind: "material" | "project"; file_id: string; version_id: string; filename: string; page_number?: number; excerpt: string; chunk_id: string }
export interface QaDraft { body: string; draft_revision: number; base_cell_revision_id: string; sources: QaSource[] }
export interface QaProposal { operation?: "draft" | "polish" | "revise"; id: string; body: string; sources: QaSource[]; base_cell_revision_id: string; insufficient_evidence: boolean; unverified_sources?: string[] }
export interface QaDraftOptions { guidance: string; previous_proposal_id?: string; material_version_ids?: string[] }
const draftBase = (collection: string, list: QaListDetail, question: string, column: string) => `${qaBase(collection)}/lists/${list.id}/questions/${question}/drafts/${column}`
export const getQaDraft = (collection: string, list: QaListDetail, question: string, column: string) => request<QaDraft | null>(`${draftBase(collection, list, question, column)}?version_id=${list.version.id}`)
export const putQaDraft = (collection: string, list: QaListDetail, question: string, column: string, draft: { body: string; draft_revision: number; base_cell_revision_id: string; proposal_id?: string }) => request<QaDraft>(draftBase(collection, list, question, column), { method: "PUT", body: JSON.stringify({ ...draft, version_id: list.version.id }) })
export const discardQaDraft = (collection: string, list: QaListDetail, question: string, column: string, revision: number) => request(`${draftBase(collection, list, question, column)}?version_id=${list.version.id}&draft_revision=${revision}`, { method: "DELETE" })
export const generateQaDraft = (collection: string, list: QaListDetail, question: string, column: string, instructions: string) => request<QaProposal>(`${draftBase(collection, list, question, column)}/generate`, { method: "POST", body: JSON.stringify({ expected_revision: list.revision, version_id: list.version.id, instructions }) })
export async function streamQaDraft(collection: string, list: QaListDetail, question: string, column: string, instructions: string, onProgress: (progress: QaDraftProgress) => void, signal?: AbortSignal, options?: QaDraftOptions): Promise<QaProposal> {
  const response = await apiFetch(`${draftBase(collection, list, question, column)}/generate/stream`, {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" }, signal,
    body: JSON.stringify({ expected_revision: list.revision, version_id: list.version.id, instructions, ...options }),
  })
  if (!response.ok) throw new Error(await response.text())
  if (!response.body) throw new Error("qa_incomplete_stream")
  return readQaDraftStream(response.body, onProgress, value => {
    if (!value || typeof value !== "object" || !("id" in value) || typeof value.id !== "string" || !("body" in value) || typeof value.body !== "string" || !("sources" in value) || !Array.isArray(value.sources)) throw new Error("qa_invalid_stream")
    return value as QaProposal
  }, signal)
}

export interface QaMaterial { id: string; current_version_id: string; version_id?: string; filename: string; revision: number; number: number; archived: boolean; parse_state: string; index_state: string; error_code: string }
export const getQaMaterials = (collection: string, id: string, signal?: AbortSignal) => request<QaMaterial[]>(`${qaBase(collection)}/lists/${id}/materials`, { signal })
export const getQaMaterialVersion = (collection: string, id: string, mid: string, version: string, signal?: AbortSignal) => request<QaMaterial>(`${qaBase(collection)}/lists/${id}/materials/${mid}/versions/${encodeURIComponent(version)}`, { signal })
export const uploadQaMaterial = async (collection: string, id: string, file: File, material?: QaMaterial, signal?: AbortSignal): Promise<QaMaterial> => {
  const form = new FormData(); form.append("file", file)
  if (material) form.append("expected_revision", String(material.revision))
  const res = await apiFetch(`${qaBase(collection)}/lists/${id}/materials${material ? `/${material.id}/versions` : ""}`, { method: "POST", body: form, signal })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}
export const archiveQaMaterial = (collection: string, id: string, material: QaMaterial) => request<QaMaterial[]>(`${qaBase(collection)}/lists/${id}/materials/${material.id}`, { method: "PATCH", body: JSON.stringify({ expected_revision: material.revision, archived: !material.archived }) })
export const retryQaMaterial = (collection: string, id: string, mid: string, version?: string) => request(`${qaBase(collection)}/lists/${id}/materials/${mid}/retry${version ? `?version_id=${encodeURIComponent(version)}` : ""}`, { method: "POST" })
export const getQaSourceFile = async (collection: string, id: string, source: QaSource, signal?: AbortSignal): Promise<Blob> => {
  const path = source.kind === "material" ? `${qaBase(collection)}/lists/${id}/materials/${source.file_id}/versions/${source.version_id}/file` : `${qaBase(collection)}/sources/project/${source.file_id}/versions/${source.version_id}`
  const res = await apiFetch(path, { signal }); if (!res.ok) throw new Error(await res.text()); return res.blob()
}

export interface QaTemplate { id: string; revision: number; name: string; title?: string; metadata_position?: "left" | "right"; logo_position?: "left" | "right"; logo_id?: string; include_number?: boolean; include_status?: boolean; include_type?: boolean; resequence?: boolean }
export const getQaTemplates = (collection: string) => request<QaTemplate[]>(`${qaBase(collection)}/templates`)
export const saveQaTemplate = (collection: string, template: Partial<QaTemplate> & { name: string }) => {
  const { id, revision, ...values } = template
  return request<QaTemplate>(`${qaBase(collection)}/templates${id ? `/${id}` : ""}`, { method: id ? "PATCH" : "POST", body: JSON.stringify({ ...values, ...(id ? { expected_revision: revision } : {}) }) })
}
export const uploadQaLogo = async (collection: string, file: File): Promise<{ id: string }> => {
  const form = new FormData(); form.append("file", file)
  const res = await apiFetch(`${qaBase(collection)}/logos`, { method: "POST", body: form }); if (!res.ok) throw new Error(await res.text()); return res.json()
}
export const getQaLogo = async (collection: string, id: string): Promise<Blob> => { const r = await apiFetch(`${qaBase(collection)}/logos/${id}`); if (!r.ok) throw new Error(await r.text()); return r.blob() }
export interface QaExportRecord { id: string; version_id: string; created_at: string }
export const getQaExports = (collection: string, id: string) => request<QaExportRecord[]>(`${qaBase(collection)}/lists/${id}/exports`)
export const downloadQaExport = async (collection: string, id: string, eid: string): Promise<Blob> => { const r = await apiFetch(`${qaBase(collection)}/lists/${id}/exports/${eid}/download`); if (!r.ok) throw new Error(await r.text()); return r.blob() }

export interface QaImportRecord extends QaImportPlan { committed_version_id: string | null; cancelled?: boolean }
export interface QaPendingImport { list_id: string; title: string; plan: QaImportPlan }
export const getQaImports = (collection: string, id: string) => request<QaImportRecord[]>(`${qaBase(collection)}/lists/${id}/imports`)
export const getQaPendingImports = (collection: string) => request<QaPendingImport[]>(`${qaBase(collection)}/imports/pending`)
export const getQaImportContext = (collection: string, id: string) => request<QaImportContext>(`${qaBase(collection)}/lists/${id}`)
export const cancelQaImport = (collection: string, id: string, plan: QaImportPlan) => request(`${qaBase(collection)}/lists/${id}/imports/${plan.id}?plan_revision=${plan.plan_revision}`, { method: "DELETE" })
export interface QaCorrection { id: string; text: string; target_text: string; replies: { label: string; body: string }[]; draft_count: number; overrides: Record<string, string> }
export const previewQaCorrection = (collection: string, list: QaListDetail, iid: string, row: string, target: string, drafts: "move" | "keep") => request<QaCorrection>(`${qaBase(collection)}/lists/${list.id}/imports/${iid}/corrections`, { method: "POST", body: JSON.stringify({ expected_revision: list.revision, row_key: row, target, drafts }) })
export const applyQaCorrection = (collection: string, list: QaListDetail, iid: string, previewId: string) => request<QaListDetail>(`${qaBase(collection)}/lists/${list.id}/imports/${iid}/corrections/apply`, { method: "POST", body: JSON.stringify({ preview_id: previewId }) })
export const annotateQaQuestion = (collection: string, list: QaListDetail, question: string) => request<QaListDetail>(`${qaBase(collection)}/lists/${list.id}/questions/${question}/annotate`, { method: "POST", body: JSON.stringify({ expected_revision: list.revision, version_id: list.version.id }) })

export const annotateQaList = (collection: string, list: QaListDetail, signal?: AbortSignal) => request<QaListDetail>(`${qaBase(collection)}/lists/${list.id}/annotations`, { method: "POST", signal, body: JSON.stringify({ expected_revision: list.revision, version_id: list.version.id }) })
