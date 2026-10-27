import type { ChatAttachment } from "../api/chat-attachments.ts"
import type { ChatAttachmentDraft } from "./chat-attachments.ts"
import type { Evidence } from "./chat-citations.ts"
import type { ReportReference } from "./chat-reports.ts"
import { sourceEvidence } from './source-evidence.ts'

export interface AssetMessage {
  attachments?: ChatAttachment[]
  citations?: Evidence[]
  sources?: { text: string; metadata?: Record<string, unknown> }[]
  reportChanges?: ReportReference[]
}
export interface UploadedAsset {
  id: string
  filename: string
  status: string
  attachment?: ChatAttachment
}
export interface RetrievedAsset { id: string; title: string; evidence: Evidence }

function displayTitle(value: string): string {
  return /^(?:__file__:|__note__:|__meeting__:|file:)/.test(value) || /^[0-9a-f]{32,64}$/i.test(value) ? "" : value
}

function sourceIdentity(evidence: Evidence): string | null {
  const meta = evidence.source
  if (meta.attachment_id || evidence.kind === "attachment") return null
  const source = String(meta.file_id || meta.source || meta.meeting_id || meta.url || "")
    .replace(/^(?:__file__:|file:)/, "")
  if (!source) return null
  return JSON.stringify([meta.collection || meta.collection_name || "", source, meta.version_id || meta.content_hash || ""])
}

/** A source is one file/version, not one retrieval chunk. Saved citations win over legacy snippets. */
export function conversationSources(sessionId: string, messages: readonly AssetMessage[],
  serverFiles: readonly ChatAttachment[], drafts: readonly ChatAttachmentDraft[]) {
  const uploads = new Map<string, UploadedAsset>()
  const retrieved = new Map<string, RetrievedAsset>()
  const addFile = (file: ChatAttachment) => {
    if (file.session_id !== sessionId) return
    uploads.set(file.id, { id: file.id, filename: file.filename, status: file.status, attachment: file })
  }
  for (const message of messages) for (const file of message.attachments || []) addFile(file)
  for (const file of serverFiles) addFile(file)
  for (const draft of drafts) {
    if (draft.attachment) {
      addFile(draft.attachment)
    } else uploads.set(draft.key, { id: draft.key, filename: draft.filename, status: draft.status })
  }
  const addEvidence = (evidence: Evidence) => {
    const id = sourceIdentity(evidence)
    if (id) retrieved.set(id, { id, title: displayTitle(evidence.title), evidence: evidence.id ? evidence : { ...evidence, id: `asset:${id}` } })
  }
  for (const message of messages) {
    for (const source of message.sources || []) {
      const meta = source.metadata || {}
      const raw = String(meta.source_label || meta.display_name || meta.filename || meta.title || meta.source || "")
      const title = displayTitle(raw).split("/").pop() || ""
      addEvidence({ ...sourceEvidence(source, 0), id: '', title })
    }
    for (const evidence of message.citations || []) addEvidence(evidence)
  }
  return { uploads: [...uploads.values()], retrieved: [...retrieved.values()] }
}
