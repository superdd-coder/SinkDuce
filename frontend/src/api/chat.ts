import { API_BASE, request } from "./http"

/** Approve or deny a pending Chat web-search HITL request. */
export const confirmWebSearch = (confirmId: string, approved: boolean) =>
  request<{ ok: boolean; confirm_id: string; approved: boolean }>(
    "/chat/web-search-confirm",
    {
      method: "POST",
      body: JSON.stringify({ confirm_id: confirmId, approved }),
    },
  )

// ── Sessions ──

export interface SessionItem {
  id: string
  title: string
  collections: string[]
  created_at: string
  updated_at: string
  message_count: number
  last_message: string | null
}

export interface SessionMessage {
  id: string
  session_id: string
  role: "user" | "assistant"
  content: string
  sources: { text: string; score: number; metadata: Record<string, unknown> }[] | null
  metadata: Record<string, unknown> | null
  created_at: string
}

export interface SessionDetail extends SessionItem {
  messages: SessionMessage[]
}

export async function listSessions(): Promise<SessionItem[]> {
  const r = await fetch(`${API_BASE}/sessions`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function createSession(title = "", collections?: string[], id?: string): Promise<SessionItem> {
  const r = await fetch(`${API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, collections, id }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function getSession(id: string): Promise<SessionDetail> {
  const r = await fetch(`${API_BASE}/sessions/${id}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function updateSession(id: string, title: string): Promise<SessionItem> {
  const r = await fetch(`${API_BASE}/sessions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function deleteSession(id: string): Promise<void> {
  await fetch(`${API_BASE}/sessions/${id}`, { method: "DELETE" })
}

export async function generateSessionTitle(id: string): Promise<{ title: string }> {
  const r = await fetch(`${API_BASE}/sessions/${id}/generate-title`, { method: "POST" })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export {
  createSessionSseParser,
  iterateSessionSse,
  postSessionMessage,
  type SessionMessagePayload,
  type SessionSseMessage,
} from "./session-sse"
