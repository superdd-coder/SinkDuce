import { apiFetch, parseApiErrorBody, request } from "./http"

export interface ChatAttachment {
  id: string
  session_id: string
  filename: string
  size: number
  status: "processing" | "ready" | "failed"
  parser: string
  error: string
  warnings: string[]
  characters: number
  lines: number
  image_count: number
  bound: boolean
  created_at: string
}

export interface ChatAttachmentTypes {
  extensions: string[]
  cloud_only: string[]
  max_file_bytes: number
  max_attachments: number
}

const path = (sid: string) => `/sessions/${encodeURIComponent(sid)}/attachments`
export const getChatAttachmentTypes = () => request<ChatAttachmentTypes>("/chat/attachment-types")
export const getChatAttachments = (sid: string) => request<ChatAttachment[]>(path(sid))
export const getChatAttachmentText = (sid: string, id: string) => request<{ text: string; native_length?: number; position_map?: { char_offset: number; page_number?: number; slide_number?: number }[] }>(`${path(sid)}/${encodeURIComponent(id)}/text`)
export async function uploadChatAttachment(sid: string, file: File): Promise<ChatAttachment> {
  const body = new FormData()
  body.append("file", file)
  const res = await apiFetch(path(sid), { method: "POST", body })
  if (!res.ok) throw parseApiErrorBody(res.status, await res.text())
  return res.json()
}
export const retryChatAttachment = (sid: string, id: string) =>
  request<ChatAttachment>(`${path(sid)}/${encodeURIComponent(id)}/retry`, { method: "POST" })
export const removeChatAttachment = (sid: string, id: string) =>
  request<void>(`${path(sid)}/${encodeURIComponent(id)}`, { method: "DELETE" })
export async function getChatAttachmentSource(sid: string, id: string): Promise<Blob> {
  const res = await apiFetch(`${path(sid)}/${encodeURIComponent(id)}/source`)
  if (!res.ok) throw parseApiErrorBody(res.status, await res.text())
  return res.blob()
}

export async function getChatAttachmentPreview(sid: string, id: string): Promise<Blob> {
  const res = await apiFetch(`${path(sid)}/${encodeURIComponent(id)}/preview`)
  if (!res.ok) throw parseApiErrorBody(res.status, await res.text())
  return res.blob()
}
