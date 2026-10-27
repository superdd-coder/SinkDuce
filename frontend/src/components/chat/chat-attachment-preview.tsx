import { lazy, Suspense, useEffect, useState, type RefObject } from "react"
import { Download, Loader2, RotateCw } from "lucide-react"
import { toast } from "sonner"
import { getChatAttachmentPreview, getChatAttachmentSource, type ChatAttachment } from "@/api/client"
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { chatAttachmentLabel, isChatAttachmentImage } from "@/lib/chat-attachments"
import { useT } from "@/i18n/use-t"

const RawFileViewer = lazy(() => import("@/components/file-mgmt/raw-file-viewer").then((module) => ({ default: module.RawFileViewer })))

export function ChatAttachmentPreview({ attachment, triggerRef, onClose, initialPage }: {
  attachment: ChatAttachment
  triggerRef: RefObject<HTMLButtonElement | null>
  onClose: () => void
  initialPage?: number
}) {
  const t = useT()
  const [open, setOpen] = useState(true)
  const [resource, setResource] = useState<{ url: string; filename: string; original: Blob | null } | null>(null)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [downloading, setDownloading] = useState(false)
  const image = isChatAttachmentImage(attachment.filename)
  const presentation = /\.pptx?$/i.test(attachment.filename)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    let objectUrl: string | undefined
    setResource(null)
    setFailed(false)
    const load = image || presentation ? getChatAttachmentPreview : getChatAttachmentSource
    void load(attachment.session_id, attachment.id).then((blob) => {
      if (cancelled) return
      objectUrl = URL.createObjectURL(blob)
      const converted = presentation && blob.type === "application/pdf"
      setResource({ url: objectUrl, original: image || converted ? null : blob,
        filename: converted ? attachment.filename.replace(/\.pptx?$/i, ".pdf") : attachment.filename })
    }).catch(() => { if (!cancelled) setFailed(true) })
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [attachment.session_id, attachment.id, attachment.filename, image, presentation, attempt, open])

  const download = async () => {
    if (downloading) return
    setDownloading(true)
    try {
      const blob = resource?.original ?? await getChatAttachmentSource(attachment.session_id, attachment.id)
      const url = URL.createObjectURL(blob)
      try {
        const link = document.createElement("a")
        link.href = url
        link.download = attachment.filename
        document.body.appendChild(link)
        link.click()
        link.remove()
      } finally { setTimeout(() => URL.revokeObjectURL(url), 1000) }
    } catch { toast.error(t("chat.attachments.downloadFailed")) }
    finally { setDownloading(false) }
  }

  const loading = <div className="pm-attachment-preview-state" role="status">
    <Loader2 className="size-5 animate-spin" aria-hidden="true" />{t("chat.attachments.loadingPreview")}
  </div>

  return <Dialog open={open} onOpenChange={setOpen} onOpenChangeComplete={(opened) => { if (!opened) onClose() }}>
    <DialogContent className="pm-attachment-dialog" finalFocus={triggerRef} forceOverlay
      onMouseDown={(event) => event.stopPropagation()} onClick={(event) => event.stopPropagation()}>
      <div className="pm-attachment-preview-header">
        <div className="pm-attachment-preview-title">
          <DialogTitle title={attachment.filename}>{attachment.filename}</DialogTitle>
          <DialogDescription>{chatAttachmentLabel(attachment)}</DialogDescription>
        </div>
        <Button type="button" variant="outline" size="sm" disabled={downloading}
          aria-label={t("chat.attachments.download")} onClick={download}>
          {downloading ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
          {t("chat.attachments.download")}
        </Button>
      </div>
      <div className="pm-attachment-preview-body">
        {failed ? <div className="pm-attachment-preview-state" role="alert">
          <p>{t("chat.attachments.previewFailed")}</p>
          <Button variant="outline" size="sm" onClick={() => setAttempt((value) => value + 1)}>
            <RotateCw className="size-3.5" />{t("chat.attachments.retry")}
          </Button>
        </div> : !resource ? loading : image ?
          <img className="pm-attachment-preview-image" src={resource.url} alt={attachment.filename} onError={() => setFailed(true)} /> :
          <Suspense fallback={loading}><RawFileViewer url={resource.url} filename={resource.filename} initialPage={initialPage} hideChrome /></Suspense>}
      </div>
    </DialogContent>
  </Dialog>
}
