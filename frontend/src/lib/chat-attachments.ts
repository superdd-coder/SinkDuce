import type { ChatAttachment } from "../api/chat-attachments"

export function isChatAttachmentImage(filename: string): boolean {
  return /\.(png|jpe?g|webp|gif|bmp|jp2|tiff?)$/i.test(filename)
}

export function chatAttachmentLabel(attachment: Pick<ChatAttachment, "filename" | "size">): string {
  const type = attachment.filename.split(".").pop()?.toUpperCase() ?? ""
  const size = attachment.size < 1024 ? `${attachment.size} B`
    : attachment.size < 1024 * 1024 ? `${Math.ceil(attachment.size / 1024)} KB`
      : `${Number((attachment.size / (1024 * 1024)).toFixed(1))} MB`
  return `${type} · ${size}`
}

export interface ChatAttachmentDraft {
  key: string
  filename: string
  status: "uploading" | "processing" | "ready" | "failed"
  attachment?: ChatAttachment
  file?: File
  error?: string
}

export function canSendAttachments(text: string, drafts: readonly { status: string }[]): boolean {
  return drafts.every((d) => d.status === "ready") && (!!text.trim() || drafts.length > 0)
}

export function settleAttachmentUpload(drafts: ChatAttachmentDraft[], key: string, a: ChatAttachment): ChatAttachmentDraft[] {
  return drafts.filter((d) => d.key === key || d.attachment?.id !== a.id).map((d) => d.key === key
    ? { ...d, attachment: a, status: a.status, error: a.error } : d)
}

export function mergeAttachmentDrafts(local: ChatAttachmentDraft[], rows: ChatAttachment[], consumed: string[]): ChatAttachmentDraft[] {
  const ignored = new Set(consumed)
  for (const a of rows) if (a.bound) ignored.add(a.id)
  const incoming = rows.filter((a) => !a.bound && !ignored.has(a.id))
  const result = local.filter((d) => !d.attachment || !ignored.has(d.attachment.id))
  for (const a of incoming) {
    const i = result.findIndex((d) => d.attachment?.id === a.id)
    const draft: ChatAttachmentDraft = { ...(i >= 0 ? result[i] : {}), key: i >= 0 ? result[i].key : a.id,
      filename: a.filename, status: a.status, attachment: a, error: a.error }
    if (i >= 0) result[i] = draft
    else result.push(draft)
  }
  return result
}
