import { apiFetch, parseApiErrorBody, request } from "./http"
import type { Evidence } from "../lib/chat-citations"
import type { ReportReference, ReportBlock } from "../lib/chat-reports"
export type { Evidence, ReportReference, ReportBlock }

export interface ChatReport extends ReportReference {
  history_revision?: number
  suggestions?: import('../lib/report-review').RevisionSuggestion[]
  markdown: string
  citations: Evidence[]
  created_at: string
  blocks?: ReportBlock[]
  edit_log?: import('../lib/report-review').ReportEdit[]
  edit_operations?: import('../lib/report-review').ReportEditOperation[]
  parent_revision?: number
  changes?: import("../lib/report-review").ReportChange[]
  review?: import("../lib/report-review").ReviewBatch
}
export interface ReportHistoryEntry extends ReportReference {
  kind: 'create' | 'edit' | 'review' | 'restore' | 'decision'
  change_count: number
  comment_count: number
  summaries: string[]
  decision?: 'accepted' | 'rejected'
  label?: string
  group_id?: string
  restored_from?: number
}
export type ReportFormat = "html" | "docx" | "pdf"
export interface ReportExport {
  format: ReportFormat
  revision: number
  status: "idle" | "queued" | "running" | "ready" | "failed"
  error: string
}
const root = (sid: string) => `/sessions/${encodeURIComponent(sid)}/reports`
const version = (sid: string, rid: string, revision: number) => `${root(sid)}/${encodeURIComponent(rid)}/revisions/${revision}`
export const getChatReports = (sid: string) => request<ReportReference[]>(root(sid))
export const getChatReportVersions = (sid: string, rid: string) => request<ReportHistoryEntry[]>(`${root(sid)}/${encodeURIComponent(rid)}/history`)
export const restoreChatReport = (sid: string, rid: string, revision: number, expectedRevision: number, operationId: string) =>
  request<ChatReport>(`${root(sid)}/${encodeURIComponent(rid)}/history/${revision}/restore`, {
    method: 'POST', body: JSON.stringify({expected_revision: expectedRevision, operation_id: operationId}),
  })
export const getChatReport = (sid: string, rid: string, revision?: number) =>
  request<ChatReport>(revision === undefined ? `${root(sid)}/${encodeURIComponent(rid)}` : version(sid, rid, revision))
export const createChatReportExport = (sid: string, rid: string, revision: number, format: ReportFormat) =>
  request<ReportExport>(`${version(sid, rid, revision)}/exports`, { method: "POST", body: JSON.stringify({ format }) })
export const getChatReportExport = (sid: string, rid: string, revision: number, format: ReportFormat) =>
  request<ReportExport>(`${version(sid, rid, revision)}/exports/${format}`)
export async function downloadChatReport(sid: string, rid: string, revision: number, format: ReportFormat): Promise<Blob> {
  const response = await apiFetch(`${version(sid, rid, revision)}/exports/${format}/download`)
  if (!response.ok) throw parseApiErrorBody(response.status, await response.text())
  return response.blob()
}

export type { ReportComment, CommentAnchor, ReviewRequest, ReviewBatch, ReviewOutcome, ReportChange } from '../lib/report-review'
import type { ReportComment, CommentAnchor } from '../lib/report-review'
export const getPendingReportComments = (sid: string) => request<ReportComment[]>(`/sessions/${encodeURIComponent(sid)}/report-comments`)
export const getReportComments = (sid: string, rid: string, revision: number) => request<ReportComment[]>(`${version(sid,rid,revision)}/comments`)
export const addReportComment = (sid: string, rid: string, revision: number, text: string, anchor: CommentAnchor) =>
  request<ReportComment>(`${version(sid,rid,revision)}/comments`, {method:'POST',body:JSON.stringify({text,anchor})})
export const editReportComment = (sid: string, rid: string, cid: string, text: string) =>
  request<ReportComment>(`${root(sid)}/${encodeURIComponent(rid)}/comments/${encodeURIComponent(cid)}`, {method:'PATCH',body:JSON.stringify({text})})
export const deleteReportComment = (sid: string, rid: string, cid: string) =>
  request<void>(`${root(sid)}/${encodeURIComponent(rid)}/comments/${encodeURIComponent(cid)}`, {method:'DELETE'})
export const getReportReviewHistory = (sid: string, rid: string) => request<import('../lib/report-review').ReviewBatch[]>(`${root(sid)}/${encodeURIComponent(rid)}/reviews`)
const suggestion = (sid:string,rid:string,gid:string) => `${root(sid)}/${encodeURIComponent(rid)}/suggestions/${encodeURIComponent(gid)}`
export const decideReportSuggestion = (sid:string,rid:string,gid:string,revision:number,decision:'accepted'|'rejected',operationId:string,scope:'location'|'instruction'='location') =>
  request<ChatReport>(`${suggestion(sid,rid,gid)}/decision`,{method:'POST',body:JSON.stringify({expected_revision:revision,decision,operation_id:operationId,scope})})
export interface ReportFollowUpContext { current_text:string; deleted:boolean; anchor:CommentAnchor }
export const getReportFollowUpContext = (sid:string,rid:string,gid:string,revision:number) =>
  request<ReportFollowUpContext>(`${suggestion(sid,rid,gid)}/follow-up-context?expected_revision=${revision}`)
export const followUpReportSuggestion = (sid:string,rid:string,gid:string,revision:number,text:string,operationId:string) =>
  request<ReportComment>(`${suggestion(sid,rid,gid)}/follow-up`,{method:'POST',body:JSON.stringify({expected_revision:revision,text,operation_id:operationId})})
