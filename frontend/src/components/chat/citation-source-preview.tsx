import { useEffect, useState } from "react"
import { ArrowLeftRight, Loader2 } from "lucide-react"
import { getChatAttachments, getChatAttachmentPreview, getChatAttachmentSource, getChatAttachmentText, getFileDetail, getFilePreviewUrl, getExtractedText, getMeetingTranscript, type Evidence } from "@/api/client"
import { RawFileViewer } from "@/components/file-mgmt/raw-file-viewer"
import { ParseTextViewer } from "@/components/file-mgmt/file-detail/parse-text-viewer"
import { useAppStore } from "@/stores/app-store"
import { useT } from "@/i18n/use-t"
import { citationTextRange } from "@/lib/chat-citations"
import { citationFileTarget } from '@/lib/source-evidence'

type Preview = { url?: string; filename: string; text?: string; page?: number; image?: boolean; externalUrl?: string; timedRange?: { offset: number; length: number } }

/** Preview reads retain source permissions and use the exact cited version. */
export function CitationSourcePreview({ evidence, sessionId }: { evidence: Evidence; sessionId: string }) {
  const t = useT()
  const rawCollection = String(evidence.source.collection || evidence.source.collection_name || "")
  const collection = useAppStore((state) => state.collections?.find((item) => item.id === rawCollection || item.name === rawCollection)?.id || rawCollection)
  const [loaded, setLoaded] = useState<{ key: string; value: Preview } | null>(null)
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null)
  const [selection, setSelection] = useState<{ key: string; mode: string } | null>(null)
  const [attempt, setAttempt] = useState(0)
  const key = `${sessionId}/${evidence.id}/${attempt}`
  const preview = loaded?.key === key ? loaded.value : null
  const error = failure?.key === key ? failure.message : ""
  const plain = /\.(?:txt|md|markdown|csv|tsv|json|jsonl|html?)$/i.test(preview?.filename || "") || !preview?.url
  const page = evidence.location.page_number ?? evidence.location.slide_number ?? preview?.page
  const range = preview?.text !== undefined ? citationTextRange(preview.text, evidence.quote, evidence.location.char_offset) ?? preview.timedRange : undefined
  const mode = selection?.key === key ? selection.mode : plain ? "text" : "original"
  useEffect(() => {
    let cancelled = false
    let objectUrl: string | undefined
    async function load(): Promise<Preview> {
      const meta = evidence.source
      if (evidence.kind === "web" && meta.url) {
        const url = new URL(String(meta.url))
        if (url.protocol !== "https:" && url.protocol !== "http:") throw new Error("Unsupported source URL")
        return { filename: evidence.title, externalUrl: url.href }
      }
      if (meta.attachment_id) {
        const aid = String(meta.attachment_id)
        const rows = await getChatAttachments(sessionId)
        const file = rows.find((item) => item.id === aid)
        if (!file) throw new Error(t("chat.reports.sourceUnavailable"))
        const rendition = /\.(?:pptx?|png|jpe?g|webp|gif|bmp|tiff?|heic|heif)$/i.test(file.filename)
        const blob = await (rendition ? getChatAttachmentPreview : getChatAttachmentSource)(sessionId, aid)
        if (cancelled) return { filename: file.filename }
        objectUrl = URL.createObjectURL(blob)
        const parsed = await getChatAttachmentText(sessionId, aid).catch(() => undefined)
        const text = parsed?.text
        const range = text === undefined ? undefined : citationTextRange(text, evidence.quote, evidence.location.char_offset)
        // Offsets in the parse map are Unicode code points; DOM ranges use UTF-16.
        const offset = text !== undefined && range ? Array.from(text.slice(0, range.offset)).length : undefined
        const position = offset === undefined || offset >= (parsed?.native_length ?? Infinity) ? undefined : parsed?.position_map?.filter((p) => p.char_offset <= offset).sort((a, b) => b.char_offset - a.char_offset)[0]
        return { url: objectUrl, filename: blob.type === "application/pdf" && /\.pptx?$/i.test(file.filename) ? file.filename.replace(/\.pptx?$/i, ".pdf") : file.filename, text, page: position?.page_number ?? position?.slide_number, image: blob.type.startsWith("image/") }
      }
      if (meta.meeting_id) {
        const result = await getMeetingTranscript(String(meta.meeting_id))
        const parts = result.segments.map((segment) => `${Math.floor(segment.start / 60)}:${String(Math.floor(segment.start % 60)).padStart(2, "0")} ${segment.text}`)
        const time = evidence.location.start_time
        const sentence = evidence.location.sentence_id
        const index = typeof time === "number" && Number.isFinite(time) ? result.segments.findIndex((segment) => segment.end >= time)
          : typeof sentence === 'string' ? result.segments.findIndex(segment => segment.sentence_id === sentence) : -1
        const timedRange = index >= 0 ? { offset: parts.slice(0, index).reduce((sum, part) => sum + part.length + 2, 0), length: parts[index].length } : undefined
        return { filename: evidence.title, text: parts.join("\n\n"), timedRange }
      }
      const target = citationFileTarget(evidence, [])
      const fileId = target?.fileId || ''
      const source = target?.source || ''
      if (!collection || !source) throw new Error(t("chat.reports.sourceUnavailable"))
      const detail = fileId ? await getFileDetail(collection, fileId) : null
      const versionId = typeof meta.version_id === "string" ? meta.version_id : undefined
      const filename = detail?.filename || evidence.title
      const url = source.startsWith("__note__:") ? undefined : getFilePreviewUrl(source, { collection, versionId }) || undefined
      const text = await getExtractedText(source, collection, { versionId }).then((result) => result.text).catch(() => undefined)
      if (!url && text === undefined) throw new Error(t("chat.reports.sourceUnavailable"))
      return { url, filename, text }
    }
    void load().then((value) => { if (!cancelled) setLoaded({ key, value }) }).catch(() => { if (!cancelled) setFailure({ key, message: t("chat.reports.sourceUnavailable") }) })
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [evidence, sessionId, collection, key, t])
  return <section className="pm-citation-file-pane">
    <header className="pm-citation-file-head"><span>{t(evidence.kind === "web" ? "fileMgmt.source" : "chat.reports.filePreview")}</span>
      {preview?.url && preview.text !== undefined && <button type="button" className="pm-citation-view-toggle"
        aria-pressed={mode === "text"} onClick={() => setSelection({ key, mode: mode === "text" ? "original" : "text" })}>
        {t(mode === "text" ? "chat.reports.extractedText" : "chat.reports.originalFile")}
        <ArrowLeftRight size={12} aria-hidden="true"/>
      </button>}
    </header>
    <div className="pm-citation-file-body">
      {error ? <div className="pm-report-empty" role="alert"><span>{error}</span><button type="button" className="pm-citation-retry" onClick={() => setAttempt((value) => value + 1)}>{t("chat.attachments.retry")}</button></div> : !preview ?
        <div className="pm-report-empty" role="status"><Loader2 className="size-5 animate-spin" /></div> : preview.externalUrl ?
        <div className="pm-citation-web-source"><a href={preview.externalUrl} target="_blank" rel="noopener noreferrer">{evidence.title}<span>{preview.externalUrl}</span></a></div> : mode === "text" && preview.text !== undefined ?
        <ParseTextViewer text={preview.text} collectionId={collection} fileId={String(evidence.source.file_id || "")} highlightOffset={range?.offset} highlightLength={range?.length} /> : preview.image ?
        <img className="pm-citation-original-image" src={preview.url} alt={evidence.title} /> :
        <RawFileViewer url={preview.url || null} filename={preview.filename} tools="download-search" initialPage={typeof page === "number" ? page : undefined} />}
    </div>
  </section>
}
