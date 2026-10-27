import { useEffect, useId, useRef, useState, type CSSProperties } from "react"
import { ArrowUpRight, ChevronDown, Loader2 } from "lucide-react"
import { getChatAttachments, getChatReports, type ChatAttachment, type ReportReference } from "@/api/client"
import { Popover } from "@/components/ui/popover"
import { useAppStore } from "@/stores/app-store"
import { useT } from "@/i18n/use-t"
import { mergeReportChanges } from "@/lib/chat-reports"
import { chatAttachmentLabel, type ChatAttachmentDraft } from "@/lib/chat-attachments"
import { conversationSources, type AssetMessage, type UploadedAsset, type RetrievedAsset } from "@/lib/conversation-assets"
import { ChatAttachmentPreview } from "./chat-attachment-preview"
import { CitationScope } from "./citation-scope"
import { useCitations } from "./citation-context"
import { DocumentMark } from './document-mark'
import { documentLabel, documentDate } from '@/lib/chat-document-display'
import "./conversation-assets.css"

const EMPTY_DRAFTS: ChatAttachmentDraft[] = []

/** One session's outputs and sources, available even without generated output. */
export function ConversationAssets({ sessionId, refreshKey, messages, onOpenReport }: {
  sessionId: string | null; refreshKey: string; messages: readonly AssetMessage[]
  onOpenReport: (report: ReportReference) => void
}) {
  const t = useT()
  const labelId = useId()
  const trigger = useRef<HTMLButtonElement | null>(null)
  const openingPreview = useRef(false)
  const restoreFocus = useRef(false)
  const drafts = useAppStore((state) => sessionId ? state.chatAttachmentDrafts[sessionId] ?? EMPTY_DRAFTS : EMPTY_DRAFTS)
  const historyEpoch = useAppStore(state=>state.reportHistoryEpoch)
  const [listing, setListing] = useState<{ sid: string; reports: ReportReference[]; files: ChatAttachment[]; failed: boolean } | null>(null)
  const [openSession, setOpenSession] = useState<string | null>(null)
  const [surfaceSession, setSurfaceSession] = useState<string | null>(null)
  const [origin, setOrigin] = useState({ width: 178, height: 28 })
  const [retrievedSession, setRetrievedSession] = useState<string | null>(null)
  const [selected, setSelected] = useState<ChatAttachment | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [maxHeight, setMaxHeight] = useState<number | undefined>(undefined)
  const open = !!sessionId && openSession === sessionId
  const current = listing?.sid === sessionId ? listing : null
  const { uploads, retrieved } = conversationSources(sessionId || "", messages, current?.files || [], drafts)
  const outputs = mergeReportChanges(current?.reports, messages.flatMap((message) => message.reportChanges || []))
    .filter((report) => report.session_id === sessionId).sort((a, b) => b.updated_at.localeCompare(a.updated_at))

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    void Promise.allSettled([getChatReports(sessionId), getChatAttachments(sessionId)]).then(([reports, files]) => {
      if (!cancelled) setListing((previous) => ({ sid: sessionId,
        reports: reports.status === "fulfilled" ? reports.value : previous?.sid === sessionId ? previous.reports : [],
        files: files.status === "fulfilled" ? files.value : previous?.sid === sessionId ? previous.files : [],
        failed: reports.status === "rejected" || files.status === "rejected" }))
    })
    return () => { cancelled = true }
  }, [sessionId, refreshKey, attempt, historyEpoch])

  useEffect(() => {
    const stage = trigger.current?.closest(".pm-chat-stage, .pm-qc-panel")
    if (!stage || typeof ResizeObserver === "undefined") return
    const observer = new ResizeObserver(() => setMaxHeight(Math.floor(stage.getBoundingClientRect().height * 2 / 3)))
    observer.observe(stage)
    return () => observer.disconnect()
  }, [sessionId])

  if (!sessionId) return null
  const closeForPreview = () => { openingPreview.current = true; setOpenSession(null); trigger.current?.focus({ preventScroll: true }) }
  const summary = <>
    <span className="pm-assets-counts">
      <span data-output-count={outputs.length}><span>Output</span><b>{outputs.length}</b></span>
      <span data-source-count={uploads.length + retrieved.length}><span>Sources</span><b>{uploads.length + retrieved.length}</b></span>
    </span>
    <ChevronDown className="pm-assets-trigger-chevron size-3" aria-hidden="true" />
  </>
  return <CitationScope key={sessionId} sessionId={sessionId} citations={retrieved.map((row) => row.evidence)}>
    <Popover.Root open={open} onOpenChange={(value, details) => {
      if (value) {
        const bounds = trigger.current?.getBoundingClientRect()
        if (bounds) setOrigin({ width: bounds.width, height: bounds.height })
        setSurfaceSession(sessionId)
        openingPreview.current = false
      } else restoreFocus.current = ["escape-key", "close-press", "trigger-press"].includes(details.reason)
      setOpenSession(value ? sessionId : null)
    }} onOpenChangeComplete={(value) => {
      if (value) return
      setSurfaceSession(null)
      const focus = restoreFocus.current && !openingPreview.current
      openingPreview.current = false
      restoreFocus.current = false
      if (focus) requestAnimationFrame(() => trigger.current?.focus({ preventScroll: true }))
    }}>
      <Popover.Trigger ref={trigger} className="pm-assets-trigger" aria-label={`${t("chat.assets.title")} · Output ${outputs.length} · Sources ${uploads.length + retrieved.length}`} aria-expanded={open} data-surface-active={surfaceSession === sessionId || undefined}>
        {summary}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner side="bottom" align="end" sideOffset={-origin.height} className="pm-assets-positioner">
          <Popover.Popup className="pm-assets-card" aria-labelledby={labelId} style={{ maxHeight,
            "--pm-assets-origin-w": `${origin.width}px`, "--pm-assets-origin-h": `${origin.height}px` } as CSSProperties} finalFocus={false}>
            <Popover.Title id={labelId} className="sr-only">{t("chat.assets.title")}</Popover.Title>
            <Popover.Close className="pm-assets-collapse" aria-label={t("common.close")}>{summary}</Popover.Close>
            <div className="pm-assets-body">
            <ConversationAssetsPanel outputs={outputs} uploads={uploads} retrieved={retrieved}
              retrievedOpen={retrievedSession === sessionId} onToggleRetrieved={() => setRetrievedSession((value) => value === sessionId ? null : sessionId)}
              onOutput={(report) => { closeForPreview(); onOpenReport(report) }}
              onUpload={(file) => { closeForPreview(); setSelected(file) }} onSource={closeForPreview} />
            {!current && <div className="pm-assets-status" role="status"><Loader2 className="size-3.5 animate-spin" /></div>}
            {current?.failed && <button type="button" className="pm-assets-retry" onClick={() => setAttempt((value) => value + 1)}>{t("chat.assets.retry")}</button>}
            </div>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
    {selected?.session_id === sessionId && <ChatAttachmentPreview key={selected.id} attachment={selected} triggerRef={trigger} onClose={() => setSelected(null)} />}
  </CitationScope>
}

/** Lists scroll independently so the bottom retrieval control stays reachable. */
export function ConversationAssetsPanel({ outputs, uploads, retrieved, retrievedOpen, onToggleRetrieved, onOutput, onUpload, onSource }: {
  outputs: ReportReference[]; uploads: UploadedAsset[]; retrieved: RetrievedAsset[]; retrievedOpen: boolean
  onToggleRetrieved: () => void; onOutput: (report: ReportReference) => void; onUpload: (file: ChatAttachment) => void; onSource: () => void
}) {
  const t = useT()
  const locale = useAppStore(state=>state.locale)
  const citations = useCitations()
  const listId = useId()
  return <>
    <section className="pm-assets-section pm-assets-output">
      <h3 className="pm-assets-heading">Output</h3>
      <div className="pm-assets-list">
        {outputs.map((report) => <button type="button" className="pm-assets-row" data-output="true" key={report.id} title={report.title} onClick={() => onOutput(report)}>
          <DocumentMark kind="report"/><span className="pm-assets-row-body"><span className="pm-assets-row-title">{report.title}</span>
            <span className="pm-assets-row-meta"><span>{t('chat.reports.report')}</span>{documentDate(report.updated_at,locale)&&<><span className="pm-artifact-meta-dot" aria-hidden="true"/><time dateTime={report.updated_at}>{documentDate(report.updated_at,locale)}</time></>}</span></span>
          <ArrowUpRight className="pm-assets-row-arrow" size={14} aria-hidden="true"/>
        </button>)}
        {!outputs.length && <span className="pm-assets-empty">{t("chat.assets.noOutput")}</span>}
      </div>
    </section>
    <section className="pm-assets-section pm-assets-uploads">
      <h3 className="pm-assets-heading">Sources</h3>
      <div className="pm-assets-group-label">{t("chat.assets.uploaded")}<span>{uploads.length}</span></div>
      <div className="pm-assets-list">
        {uploads.map((file) => <button type="button" className="pm-assets-row" key={file.id} title={file.filename} data-status={file.status} disabled={!file.attachment} onClick={() => file.attachment && onUpload(file.attachment)}>
          <DocumentMark filename={file.filename}/>
          <span className="pm-assets-row-body"><span className="pm-assets-row-title">{file.filename}</span>
            <span className="pm-assets-row-meta">{file.status === 'processing' || file.status === 'uploading' ? <><Loader2 className="size-3 animate-spin"/>{t(`chat.attachments.${file.status}`)}</>
              : file.status === 'failed' ? t('chat.attachments.failed') : file.attachment ? chatAttachmentLabel(file.attachment) : documentLabel(file.filename)}</span></span>
          <ArrowUpRight className="pm-assets-row-arrow" size={14} aria-hidden="true"/>
        </button>)}
        {!uploads.length && <span className="pm-assets-empty">{t("chat.assets.noUploads")}</span>}
      </div>
    </section>
    <section className="pm-assets-section pm-assets-retrieved" data-expanded={retrievedOpen || undefined}>
      <button type="button" className="pm-assets-retrieved-toggle" aria-expanded={retrievedOpen} aria-controls={listId} onClick={onToggleRetrieved}>
        <ChevronDown className="size-3.5" /><span>{t("chat.assets.retrieved")}</span><span>{retrieved.length}</span>
      </button>
      <div id={listId} className="pm-assets-retrieved-fold" inert={!retrievedOpen} aria-hidden={!retrievedOpen}>
        <div className="pm-assets-list">
          {retrieved.map((row) => <button type="button" className="pm-assets-row" key={row.id} title={row.title} onClick={() => { onSource(); citations.open(row.evidence) }}>
            <DocumentMark filename={row.title} kind={row.evidence.kind}/><span className="pm-assets-row-body"><span className="pm-assets-row-title">{row.title || t("common.file")}</span>
              <span className="pm-assets-row-meta"><span>{documentLabel(row.title,row.evidence.kind)}</span>{!!row.evidence.source.collection_name&&<><span className="pm-artifact-meta-dot" aria-hidden="true"/><span>{String(row.evidence.source.collection_name)}</span></>}</span></span>
            <ArrowUpRight className="pm-assets-row-arrow" size={14} aria-hidden="true"/>
          </button>)}
          {!retrieved.length && <span className="pm-assets-empty">{t("chat.assets.noRetrieved")}</span>}
        </div>
      </div>
    </section>
  </>
}
