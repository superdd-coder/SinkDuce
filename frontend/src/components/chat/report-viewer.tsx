import {downloadBlob} from '@/lib/download'
import { useCallback, useEffect, useRef, useState, type RefObject } from "react"
import { Download, Loader2, RotateCw, Columns2, Maximize2, X, FileText, ListTree, ChevronDown } from "lucide-react"
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { getChatReport, getChatReportVersions, createChatReportExport, getChatReportExport, downloadChatReport, restoreChatReport,
  type ChatReport, type ReportReference, type ReportHistoryEntry, type ReportExport, type ReportFormat } from "@/api/client"
import { useT } from "@/i18n/use-t"
import { ReportReviewPanel } from "./report-review-panel"
import { useAppStore } from "@/stores/app-store"
import { CitationScope, CitationLink } from "./citation-scope"
import "./chat-reports.css"
import "./chat-report-layout.css"
import { reportSections } from "@/lib/chat-reports"
import { ReportHistoryMenu } from './report-history-menu'
import { refreshReportComments } from '@/hooks/use-report-review'

const FORMATS: ReportFormat[] = ["docx", "pdf", "html"]
type ExportMap = Partial<Record<ReportFormat, ReportExport>>

async function prepareExport(sid: string, rid: string, revision: number, format: ReportFormat,
  signal: AbortSignal, onProgress: (state: ReportExport) => void, timeoutMessage: string): Promise<ReportExport> {
  let state = await createChatReportExport(sid, rid, revision, format)
  for (let checks = 0; state.status === "queued" || state.status === "running"; checks++) {
    if (signal.aborted) throw new DOMException("Preview closed", "AbortError")
    onProgress(state)
    if (checks >= 360) throw new Error(timeoutMessage)
    await new Promise((resolve) => setTimeout(resolve, 500))
    state = await getChatReportExport(sid, rid, revision, format)
  }
  return state
}

export function ReportViewer({ report: reference, onClose, triggerRef, embedded = false, dialogHosted = false, active = true }: {
  report: ReportReference; onClose: () => void; triggerRef?: RefObject<HTMLButtonElement | null>; embedded?: boolean
  dialogHosted?: boolean; active?: boolean
}) {
  const t = useT()
  const commentFocus = useAppStore((state) => state.reportCommentFocus)
  const handledFocus = useRef<number | null>(null)
  const [open, setOpen] = useState(true)
  const [selection, setSelection] = useState<{id:string;head:number;revision:number|null}>({ id: reference.id, head: reference.revision, revision: null })
  const selectedRevision = selection.id === reference.id && selection.head === reference.revision ? selection.revision : null
  const [loaded, setLoaded] = useState<{ key: string; report: ChatReport } | null>(null)
  const [versionList, setVersionList] = useState<{ id: string; head: number; historyRevision: number; rows: ReportHistoryEntry[] } | null>(null)
  const [restoring, setRestoring] = useState(false)
  const restoreIntent = useRef<{key:string;id:string}|null>(null)
  const restoreBusy = useRef(false)
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [focused, setFocused] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [reviewToolbar, setReviewToolbar] = useState<HTMLDivElement|null>(null)
  const [closing, setClosing] = useState(false)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [exportData, setExportData] = useState<{ key: string; values: ExportMap } | null>(null)
  const [busyData, setBusyData] = useState<{ key: string; format: ReportFormat } | null>(null)
  const lifetime = useRef<AbortController | null>(null)
  const key = [reference.session_id, reference.id, reference.revision, selectedRevision ?? 'current', attempt].join("/")
  const currentKey = useRef(key)
  useEffect(()=>{currentKey.current=key},[key])
  const report = loaded?.key === key ? loaded.report : null
  const versions = versionList?.id === reference.id ? versionList.rows : []
  const currentRevision = Math.max(reference.revision, versionList?.id === reference.id ? versionList.head : 0,
    selectedRevision === null ? report?.revision ?? 0 : 0)
  const revision = selectedRevision ?? report?.revision ?? currentRevision
  const currentHistoryRevision = versionList?.id === reference.id ? versionList.historyRevision : currentRevision
  const selectedHistoryRevision = revision === currentRevision ? currentHistoryRevision : revision
  const error = failure?.key === key ? failure.message : ""
  const exports = exportData?.key === key ? exportData.values : {}
  const busy = busyData?.key === key ? busyData.format : null
  const contents = report ? reportSections(report.markdown, true) : []

  useEffect(() => () => { if (closeTimer.current !== null) clearTimeout(closeTimer.current) }, [])

  useEffect(() => {
    if (commentFocus?.report.id === reference.id && commentFocus.report.session_id === reference.session_id && handledFocus.current !== commentFocus.nonce) {
      handledFocus.current = commentFocus.nonce
      setSelection({id:reference.id,head:reference.revision,revision:commentFocus.report.revision})
    }
  }, [commentFocus,reference.id,reference.session_id,reference.revision])

  useEffect(() => {
    if (!active) { lifetime.current?.abort(); return }
    const controller = new AbortController()
    lifetime.current = controller
    // The history menu omits non-content actions; its last row is not the write head.
    void Promise.all([getChatReport(reference.session_id, reference.id), getChatReportVersions(reference.session_id, reference.id)]).then(async ([current, rows]) => {
      if (controller.signal.aborted) return
      const record = selectedRevision === null || selectedRevision === current.revision ? current
        : await getChatReport(reference.session_id, reference.id, selectedRevision)
      if (controller.signal.aborted) return
      setLoaded({ key, report: record }); setVersionList({ id: reference.id, head: current.revision, historyRevision: current.history_revision ?? current.revision, rows })
      for (const format of FORMATS) void getChatReportExport(reference.session_id, reference.id, record.revision, format).then((state) => {
        if (!controller.signal.aborted) setExportData((current) => ({ key, values: {
          ...(current?.key === key ? current.values : {}), [format]: state,
        } }))
      }).catch(() => {})
    }).catch(() => {
      if (!controller.signal.aborted) setFailure({ key, message: t("chat.reports.loadFailed") })
    })
    return () => controller.abort()
  }, [reference.session_id, reference.id, reference.revision, selectedRevision, attempt, key, t, active])

  const showCurrent = useCallback((updated: ChatReport) => {
    const nextKey = [reference.session_id, reference.id, reference.revision, 'current', attempt].join('/')
    setLoaded({key: nextKey, report: updated})
    setSelection({id: reference.id, head: reference.revision, revision: null})
    setVersionList(current => ({id: reference.id, head: updated.revision, historyRevision: updated.history_revision ?? updated.revision,
      rows: current?.id === reference.id ? current.rows.filter(row => row.revision <= (updated.history_revision ?? updated.revision)) : []}))
    setExportData(null)
  }, [reference.session_id, reference.id, reference.revision, attempt])

  const restore = async () => {
    const controller = lifetime.current
    if (!report || !active || !controller || controller.signal.aborted || restoreBusy.current || revision===currentRevision) return
    const intentKey = `${reference.session_id}/${reference.id}/${revision}/${currentRevision}`
    if (restoreIntent.current?.key!==intentKey) restoreIntent.current={key:intentKey,id:crypto.randomUUID()}
    restoreBusy.current=true;setRestoring(true);setFailure(null)
    try {
      const restored = await restoreChatReport(reference.session_id, reference.id, revision, currentRevision, restoreIntent.current.id)
      const store = useAppStore.getState()
      for (const draftKey of Object.keys(store.reportCommentDrafts ?? {})) {
        if (draftKey.startsWith(`${reference.session_id}/${reference.id}/`) && Number(draftKey.split('/').at(-1)) > (restored.history_revision ?? revision)) {
          store.setReportCommentDraft(draftKey, null)
        }
      }
      store.bumpReportHistory()
      void Promise.resolve(refreshReportComments(reference.session_id)).catch(()=>{})
      if (controller.signal.aborted) return
      restoreIntent.current=null
      showCurrent(restored)
    } catch (error) {
      if (controller.signal.aborted) return
      if (error instanceof Error && 'code' in error && error.code === 'report_version_conflict') {
        restoreIntent.current = null
        try {
          const [current, rows] = await Promise.all([getChatReport(reference.session_id, reference.id), getChatReportVersions(reference.session_id, reference.id)])
          if (controller.signal.aborted) return
          setVersionList({id: reference.id, head: current.revision, historyRevision: current.history_revision ?? current.revision, rows})
          setFailure({key, message: t('chat.reports.restoreConflict')})
        } catch {
          if (!controller.signal.aborted) setFailure({key, message: t('chat.reports.restoreFailed')})
        }
      } else {
        setFailure({key,message:error instanceof Error?error.message:t('chat.reports.restoreFailed')})
      }
    } finally {restoreBusy.current=false;setRestoring(false)}
  }

  const applyReviewChange=useCallback((updated:ChatReport)=>{
    if(currentKey.current!==key||lifetime.current?.signal.aborted)return
    showCurrent(updated)
    void getChatReportVersions(reference.session_id,reference.id).then(rows=>{
      if(currentKey.current===key)setVersionList(current=>({id:reference.id,head:Math.max(current?.head??0,updated.revision),historyRevision:current?.historyRevision??updated.history_revision??updated.revision,rows}))
    }).catch(()=>{})
  },[key,reference.id,reference.session_id,showCurrent])

  const refreshReviewSnapshot=useCallback((updated:ChatReport)=>{
    if(currentKey.current!==key||lifetime.current?.signal.aborted)return
    if(updated.session_id!==reference.session_id||updated.id!==reference.id||updated.revision!==revision)return
    // Reply-only reviews refresh this projection, preserving its history cursor.
    setLoaded(current=>current?.key===key&&current.report.revision===updated.revision?{key,report:updated}:current)
  },[key,reference.id,reference.session_id,revision])

  const close = () => {
    lifetime.current?.abort()
    if (dialogHosted) { onClose(); return }
    if (!embedded) { setOpen(false); return }
    if (closeTimer.current !== null) return
    setClosing(true)
    const reduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    closeTimer.current = setTimeout(onClose, reduced ? 0 : 320)
  }
  const exportReport = async (format: ReportFormat) => {
    const controller = lifetime.current
    if (!active || !report || busy || !controller || controller.signal.aborted) return
    setBusyData({ key, format }); setFailure(null)
    try {
      const state = await prepareExport(reference.session_id, reference.id, revision, format, controller.signal,
        (progress) => { if (!controller.signal.aborted) setExportData((current) => ({ key, values: { ...(current?.key === key ? current.values : {}), [format]: progress } })) },
        t("chat.reports.stillPreparing"))
      if (controller.signal.aborted) return
      setExportData((current) => ({ key, values: { ...(current?.key === key ? current.values : {}), [format]: state } }))
      if (state.status !== "ready") throw new Error(state.error || t("chat.reports.exportFailed"))
      const blob = await downloadChatReport(reference.session_id, reference.id, revision, format)
      if (controller.signal.aborted) return
      const safeTitle = Array.from(report.title).filter((c) => c.charCodeAt(0) >= 32).join("").replace(/[\\/]/g, "_")
      downloadBlob(blob, safeTitle + "." + format)
    } catch (error) {
      if (!controller.signal.aborted) setFailure({ key, message: error instanceof Error ? error.message : t("chat.reports.exportFailed") })
    } finally { if (!controller.signal.aborted) setBusyData(null) }
  }
  const formatName = (format: ReportFormat) => format === "docx" ? "Word" : format.toUpperCase()
  const content = <>
      <header className="pm-report-head">
        <div className="pm-report-heading"><FileText className="pm-report-kicker size-4" aria-hidden />
          <h2 title={report?.title || reference.title}>{report?.title || reference.title}</h2>
        </div>
        <div className="pm-report-toolbar">
        <div className="pm-report-review-toolbar" ref={setReviewToolbar} />
        <ReportHistoryMenu entries={versions} currentRevision={currentHistoryRevision} selectedRevision={selectedHistoryRevision}
          onSelect={value=>setSelection({id:reference.id,head:reference.revision,revision:value===currentHistoryRevision?null:value})}
          onRestore={restore} restoring={restoring}/>
        <div role="group" aria-label={t("chat.reports.export")}>
          <DropdownSelect className="pm-report-export" size="tag" chevron menuZIndex={450} value="" disabled={!report || !!busy}
            placeholder={busy ? `${formatName(busy)} · ${t("chat.reports.preparing")}` : t("chat.reports.export")}
            leading={busy ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
            options={FORMATS.map((format) => ({ value: format, label: formatName(format) + (exports[format]?.status === "ready" ? " ✓" : exports[format]?.status === "failed" ? ` · ${t("chat.attachments.retry")}` : "") }))}
            onChange={(format) => { if (FORMATS.includes(format as ReportFormat)) void exportReport(format as ReportFormat) }} />
        </div>
        <Button variant="ghost" size="icon-sm" className="pm-report-icon-button" aria-pressed={focused} aria-label={t(focused ? "chat.reports.split" : "chat.reports.focus")} title={t(focused ? "chat.reports.split" : "chat.reports.focus")} onClick={() => setFocused((value) => !value)}>
          {focused ? <Columns2 className="size-4" /> : <Maximize2 className="size-4" />}
        </Button>
        <Button variant="ghost" size="icon-sm" className="pm-report-icon-button" aria-label={t("common.close")} onClick={close}><X className="size-4" /></Button>
      </div></header>
      {error && <div className="pm-report-notice pm-report-error" role="alert">{error}</div>}
      {!report ? <div className="pm-report-empty" role="status">{error ? <Button variant="outline" onClick={() => setAttempt((n) => n + 1)}><RotateCw className="size-4" />{t("chat.attachments.retry")}</Button> : <Loader2 className="size-5 animate-spin" />}</div> :
        <CitationScope citations={report.citations} source={report.markdown} sessionId={reference.session_id} onNavigate={close}>
          <div className="pm-report-scroll"><article className="pm-report-paper">
            <header className="pm-report-paper-title"><div className="pm-report-meta">{t("chat.reports.sources", { n: report.citations.length })}</div><h1>{report.title}</h1></header>
            {!!contents.length && <details className="pm-report-contents"><summary><ListTree size={15}/>{t("chat.reports.contents")}<ChevronDown size={14}/></summary><nav>
              {contents.map((section) => <button key={section.line} type="button" onClick={(event) => {
                event.currentTarget.closest(".pm-report-paper")?.querySelector(`#heading-${section.line}`)?.scrollIntoView({ block: "start" })
              }}>{section.title}</button>)}
            </nav></details>}
            <ReportReviewPanel key={`${reference.session_id}/${reference.id}`} report={report} latest={revision === currentRevision} onRailOpenChange={setReviewOpen} toolbarTarget={reviewToolbar}
              onReportChange={applyReviewChange} onReportRefresh={refreshReviewSnapshot} />
            {!!report.citations.length && <section className="pm-report-sources"><h2>{t("chat.reports.sourcesTitle")}</h2>
              {report.citations.map((evidence, index) => <div key={evidence.id}><CitationLink id={evidence.id}>{index + 1}</CitationLink> <span className="pm-report-meta">{evidence.title}</span></div>)}
            </section>}
          </article></div>
        </CitationScope>}
  </>
  if (embedded) return <section className={`pm-report-embedded${reviewOpen ? " is-reviewing" : ""}${focused ? " is-focused" : ""}${closing ? " is-closing" : ""}`}>{content}</section>
  return <Dialog open={open} modal={focused} onOpenChange={(opened, details) => {
    if (!opened && !focused && details.reason === "outside-press") { details.cancel(); return }
    if (!opened) lifetime.current?.abort()
    setOpen(opened)
  }} onOpenChangeComplete={(opened) => { if (!opened) onClose() }}>
    <DialogContent className={`pm-report-dialog ${reviewOpen ? "is-reviewing " : ""}${focused ? "is-focused" : "is-split"}`} finalFocus={triggerRef} showCloseButton={false}
      onMouseDown={(event) => event.stopPropagation()} onClick={(event) => event.stopPropagation()}>
      <DialogTitle className="sr-only">{reference.title}</DialogTitle>
      <DialogDescription className="sr-only">{t("chat.reports.report")}</DialogDescription>
      {content}
    </DialogContent>
  </Dialog>
}
