import { useEffect, useRef, useState } from "react"
import { FileText, Paperclip, RotateCw, X, Loader2, Eye, ChevronRight } from "lucide-react"
import { getChatAttachmentPreview, type ChatAttachment } from "@/api/client"
import type { ChatAttachmentsManager } from "@/hooks/use-chat-attachments"
import { chatAttachmentLabel, isChatAttachmentImage } from "@/lib/chat-attachments"
import { useT } from "@/i18n/use-t"
import { ChatAttachmentPreview } from "./chat-attachment-preview"
import "./chat-attachments.css"

function Thumbnail({ attachment }: { attachment: ChatAttachment }) {
  const [url, setUrl] = useState<string | null>(null)
  const image = isChatAttachmentImage(attachment.filename)
  useEffect(() => {
    if (!image) return
    let cancelled = false
    let objectUrl: string | undefined
    void getChatAttachmentPreview(attachment.session_id, attachment.id).then((blob) => {
      if (cancelled) return
      objectUrl = URL.createObjectURL(blob)
      setUrl(objectUrl)
    }).catch(() => {})
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [attachment.session_id, attachment.id, image])
  return url ? <img className="pm-attachment-thumbnail" src={url} alt="" /> :
    <span className="pm-attachment-file-icon" aria-hidden="true"><FileText className="size-4" /></span>
}

function AttachmentParseDetails({ attachment }: { attachment?: ChatAttachment }) {
  const t = useT()
  if (!attachment?.warnings?.length) return null
  const notices: Record<string, string> = {
    "MinerU failed; local parsing is used when available.": attachment.status === "ready" ? "localFallback" : "cloudFailed",
    "OCR unavailable; the original image remains available to vision models.": "ocrFallback",
    "The local table parser is limited to 10,000 rows per sheet/file; later rows may be omitted.": "tableLimit",
    "Whole-slide rendering is unavailable; presentation text and embedded images are available, but charts and layout may be incomplete.": "slideRenderUnavailable",
    "Some images have no complete text reading; charts and layout may be incomplete for this model.": "incompleteTextReading",
  }
  return <details className="pm-attachment-parse-details">
    <summary><ChevronRight className="size-3" aria-hidden="true" />{t("chat.attachments.parseDetails")}</summary>
    <div>
      {attachment.status === "ready" && attachment.parser && <p>{t("chat.attachments.parsedWith", {
        parser: attachment.parser === "local" ? t("chat.attachments.localParser") : attachment.parser,
      })}</p>}
      {attachment.warnings.map((warning) => <p key={warning}>{notices[warning] ? t(`chat.attachments.${notices[warning]}`) : warning}</p>)}
    </div>
  </details>
}

export function ChatAttachmentPicker({ manager }: { manager: ChatAttachmentsManager }) {
  const t = useT()
  const ref = useRef<HTMLInputElement>(null)
  return <>
    <input ref={ref} type="file" multiple accept={manager.accept} className="hidden" onChange={(e) => {
      const files = Array.from(e.currentTarget.files ?? [])
      e.currentTarget.value = ""
      void manager.upload(files)
    }} />
    <button type="button" className="pm-attachment-picker" disabled={manager.disabled || !manager.typesReady}
      aria-label={t("chat.attachments.add")} title={t("chat.attachments.add")} onClick={() => ref.current?.click()}>
      <Paperclip className="size-4" />
    </button>
  </>
}

export function ChatAttachmentDrafts({ manager }: { manager: ChatAttachmentsManager }) {
  const t = useT()
  if (!manager.drafts.length) return null
  return <div className="pm-attachments" aria-live="polite">
    {manager.drafts.map((d) => <div className="pm-attachment-item" key={d.key}>
      <div className="pm-attachment-card">
        {d.attachment && d.status === "ready" ? <Thumbnail attachment={d.attachment} /> :
          d.status === "uploading" || d.status === "processing" ? <Loader2 className="size-4 animate-spin shrink-0" /> : <FileText className="size-4 shrink-0" />}
        <div className="pm-attachment-details">
          <span className="pm-attachment-name" title={d.filename}>{d.filename}</span>
          <span className="pm-attachment-status" role={d.status === "failed" ? "alert" : undefined}>
            {d.error || t(`chat.attachments.${d.status}`)}
          </span>
        </div>
        {d.status === "failed" && <button type="button" disabled={manager.disabled} title={t("chat.attachments.retry")}
          aria-label={t("chat.attachments.retry")} onClick={() => void manager.retry(d)}><RotateCw className="size-3.5" /></button>}
        <button type="button" disabled={manager.disabled} title={t("chat.attachments.remove")} aria-label={t("chat.attachments.remove")}
          onClick={() => void manager.remove(d)}><X className="size-3.5" /></button>
      </div>
      <AttachmentParseDetails attachment={d.attachment} />
    </div>)}
  </div>
}

export function ChatAttachmentCards({ attachments }: { attachments?: ChatAttachment[] }) {
  const t = useT()
  const [selected, setSelected] = useState<ChatAttachment | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  if (!attachments?.length) return null
  return <div className="pm-attachments pm-attachments--history">
    {attachments.map((a) => <div className="pm-attachment-item" key={a.id}>
      <button type="button" className="pm-attachment-card pm-attachment-card--preview"
        aria-haspopup="dialog" aria-label={`${t("chat.attachments.preview")}: ${a.filename}`}
        title={t("chat.attachments.preview")} onClick={(event) => { triggerRef.current = event.currentTarget; setSelected(a) }}>
        <Thumbnail attachment={a} />
        <span className="pm-attachment-details"><span className="pm-attachment-name" title={a.filename}>{a.filename}</span>
          <span className="pm-attachment-status">{chatAttachmentLabel(a)}</span>
        </span>
        <Eye className="pm-attachment-open size-3.5 shrink-0" aria-hidden="true" />
      </button>
      <AttachmentParseDetails attachment={a} />
    </div>)}
    {selected && <ChatAttachmentPreview key={`${selected.session_id}/${selected.id}`} attachment={selected}
      triggerRef={triggerRef} onClose={() => setSelected(null)} />}
  </div>
}
