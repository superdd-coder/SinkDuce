import { request } from "./http"

/** Approve or deny a pending Chat web-search HITL request. */
export const confirmWebSearch = (confirmId: string, approved: boolean) =>
  request<{ ok: boolean; confirm_id: string; approved: boolean }>(
    "/chat/web-search-confirm",
    {
      method: "POST",
      body: JSON.stringify({ confirm_id: confirmId, approved }),
    },
  )

/** Approve or deny a pending Chat todo-delete HITL request. */
export const confirmTodoDelete = (confirmId: string, approved: boolean) =>
  request<{ ok: boolean; confirm_id: string; approved: boolean }>(
    "/chat/todo-delete-confirm",
    {
      method: "POST",
      body: JSON.stringify({ confirm_id: confirmId, approved }),
    },
  )

/** Approve or deny a pending Chat note-create HITL request.
 *  `collectionOverride` carries the card's dropdown re-pick (collection id). */
export const confirmNoteCreate = (
  confirmId: string,
  approved: boolean,
  collectionOverride?: string,
) =>
  request<{ ok: boolean; confirm_id: string; approved: boolean }>(
    "/chat/note-create-confirm",
    {
      method: "POST",
      body: JSON.stringify({
        confirm_id: confirmId,
        approved,
        ...(collectionOverride ? { collection: collectionOverride } : {}),
      }),
    },
  )

// ── Sessions ──

export interface NoteChange {
  id: string
  collection: string
  title: string
}

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

export interface SessionConfig {
  provider_id?: string | null
  model?: string | null
  web_search?: boolean
  thinking?: boolean
  collections?: string[]
}

export interface SessionDetail extends SessionItem {
  messages: SessionMessage[]
  /** 该会话自己的一套配置（模型/Web/思考/知识库）。空对象 = 没存过。 */
  config?: SessionConfig
}

export async function listSessions(): Promise<SessionItem[]> {
  return request<SessionItem[]>("/sessions")
}

export async function createSession(title = "", collections?: string[], id?: string): Promise<SessionItem> {
  return request<SessionItem>("/sessions", {
    method: "POST",
    body: JSON.stringify({ title, collections, id }),
  })
}

export async function getSession(id: string): Promise<SessionDetail> {
  return request<SessionDetail>(`/sessions/${id}`)
}

export async function updateSession(id: string, title: string): Promise<SessionItem> {
  return request<SessionItem>(`/sessions/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  })
}

export async function deleteSession(id: string): Promise<void> {
  await request<void>(`/sessions/${id}`, { method: "DELETE" })
}

export async function generateSessionTitle(id: string): Promise<{ title: string }> {
  return request<{ title: string }>(`/sessions/${id}/generate-title`, { method: "POST" })
}

export {
  createSessionSseParser,
  iterateSessionSse,
  postSessionMessage,
  type SessionMessagePayload,
  type SessionSseMessage,
} from "./session-sse"
