import { API_BASE, request } from "./http"

// ── Notes ──

export interface NoteListItem {
  id: string
  title: string
  collection: string
  created_at: string
  updated_at: string
  is_extracted: boolean
  extracted_into: string[]
  is_ingested: boolean
}

export interface NoteDetail {
  id: string
  title: string
  collection: string
  created_at: string
  updated_at: string
  content: string
  references: NoteReference[]
  is_extracted: boolean
  extracted_into: string[]
  is_ingested: boolean
  /** Managed file under Notes folder when ingested */
  file_id?: string | null
  /** SHA-256 of content at last successful ingest (for REINGEST dirty state) */
  ingested_content_hash?: string | null
  /** Server: content differs from last ingest */
  needs_reingest?: boolean
}

export interface NoteReference {
  block_id: string
  source_note_id: string
  source_title: string
}

export interface PropagationPreview {
  origin_id: string
  origin_title: string
  links: PropagationLink[]
  total_affected: number
}

export interface PropagationLink {
  source_id: string
  source_title: string
  target_id: string
  target_title: string
}

export const getNotes = (collection: string) =>
  request<{ collection: string; notes: NoteListItem[] }>(`/notes/${encodeURIComponent(collection)}`)

export const getNote = (collection: string, noteId: string) =>
  request<NoteDetail>(`/notes/${encodeURIComponent(collection)}/${noteId}`)

export const createNote = (collection: string, title: string) =>
  request<{ id: string; title: string; collection: string; created_at: string; updated_at: string }>(
    `/notes/${encodeURIComponent(collection)}`,
    { method: "POST", body: JSON.stringify({ title }) }
  )

export const updateNote = (collection: string, noteId: string, data: { title?: string; content?: string }) =>
  request<{ message: string; id: string }>(
    `/notes/${encodeURIComponent(collection)}/${noteId}`,
    { method: "PUT", body: JSON.stringify(data) }
  )

export const deleteNote = (collection: string, noteId: string) =>
  request<{ message: string }>(
    `/notes/${encodeURIComponent(collection)}/${noteId}`,
    { method: "DELETE" }
  )

export const uploadNoteImage = async (collection: string, noteId: string, file: File) => {
  const formData = new FormData()
  formData.append("file", file)
  const res = await fetch(
    `${API_BASE}/notes/${encodeURIComponent(collection)}/${noteId}/images`,
    { method: "POST", body: formData }
  )
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`Image upload failed (${res.status}): ${body}`)
  }
  return res.json() as Promise<{ url: string; filename: string }>
}

export const distillNote = (collection: string, targetNoteId: string, sourceNoteId: string) =>
  request<{ message: string; block_id: string; source_note_id: string; source_title: string; distilled_content: string }>(
    `/notes/${encodeURIComponent(collection)}/${targetNoteId}/distill`,
    { method: "POST", body: JSON.stringify({ source_note_id: sourceNoteId }) }
  )

/** Distill one meeting summary file (General or a Section) into a note distill-block. */
export const distillMeetingIntoNote = (
  collection: string,
  targetNoteId: string,
  meetingId: string,
  tabId: string = "tab_general",
) =>
  request<{
    message: string
    block_id: string
    source_note_id: string
    source_title: string
    source_type?: string
    meeting_id: string
    tab_id: string
    distilled_content: string
  }>(
    `/notes/${encodeURIComponent(collection)}/${targetNoteId}/distill-meeting`,
    {
      method: "POST",
      body: JSON.stringify({ meeting_id: meetingId, tab_id: tabId || "tab_general" }),
    }
  )

/** Distill-block source id for one meeting tab (matches backend meeting_source_id). */
export const meetingDistillSourceId = (meetingId: string, tabId: string = "tab_general") =>
  `meeting:${meetingId}:${tabId || "tab_general"}`

/** Parse ``meeting:{id}:{tab}`` or legacy ``meeting:{id}``. */
export const parseMeetingDistillSource = (
  sourceId: string,
): { meetingId: string; tabId: string } | null => {
  if (!sourceId.startsWith("meeting:")) return null
  const rest = sourceId.slice("meeting:".length).trim()
  if (!rest) return null
  const colon = rest.indexOf(":")
  if (colon === -1) return { meetingId: rest, tabId: "tab_general" }
  const meetingId = rest.slice(0, colon).trim()
  const tabId = rest.slice(colon + 1).trim() || "tab_general"
  return meetingId ? { meetingId, tabId } : null
}

export const getPropagationPreview = (collection: string, noteId: string) =>
  request<PropagationPreview>(`/notes/${encodeURIComponent(collection)}/${noteId}/propagation-preview`)

export const triggerPropagation = (collection: string, noteId: string) =>
  request<{ message: string; updated_note_ids: string[] }>(
    `/notes/${encodeURIComponent(collection)}/${noteId}/propagate`,
    { method: "POST" }
  )

export const ingestNote = (collection: string, noteId: string) =>
  request<{ message: string; status: string; file_id?: string | null; task_id?: string | null }>(
    `/notes/${encodeURIComponent(collection)}/${noteId}/ingest`,
    { method: "POST" }
  )

/** Re-ingest as a new version on the managed Notes-folder file. */
export const reingestNote = (collection: string, noteId: string) =>
  request<{
    message: string
    status: string
    file_id?: string | null
    task_id?: string | null
  }>(`/notes/${encodeURIComponent(collection)}/${noteId}/reingest`, {
    method: "POST",
  })

export const removeNoteIngestion = (collection: string, noteId: string) =>
  request<{ message: string; is_ingested: boolean }>(
    `/notes/${encodeURIComponent(collection)}/${noteId}/ingest`,
    { method: "DELETE" }
  )
