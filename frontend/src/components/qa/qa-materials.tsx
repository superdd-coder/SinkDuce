import { useCallback, useEffect, useId, useRef, useState, type RefObject } from "react"
import { Archive, ArrowLeft, FileText, List, LoaderCircle, PanelLeftClose, RotateCcw, Upload } from "lucide-react"
import { QaActionMenu, QaMenuItem, QaToggle } from "./qa-controls"
import { useQaModal } from "./use-qa-modal"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { RawFileViewer } from "@/components/file-mgmt/raw-file-viewer"
import { ChunkMd } from "@/components/shared/chunk-md"
import { groupQaSources } from "@/lib/qa-sources"
import { useT } from "@/i18n/use-t"
import { archiveQaMaterial, getQaMaterials, getQaSourceFile, retryQaMaterial, uploadQaMaterial, type QaMaterial, type QaSource } from "@/api/client"
import "./qa-file-space.css"

const supportedFiles = ".pdf,.docx,.xlsx,.pptx,.md,.txt,.csv,.tsv"

export function QaMaterials({ collectionId, listId, writable, onSource }: {
  collectionId: string; listId: string; writable: boolean; onSource: (source: QaSource, trigger: HTMLButtonElement) => void
}) {
  const t = useT()
  const [items, setItems] = useState<QaMaterial[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [loadFailed, setLoadFailed] = useState(false)
  const [actionFailed, setActionFailed] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const replacing = useRef<QaMaterial>(undefined)
  const request = useRef<AbortController | null>(null)
  const operation = useRef<AbortController | null>(null)

  const refresh = useCallback(() => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    return Promise.resolve().then(async () => {
      if (controller.signal.aborted) return
      const next = await getQaMaterials(collectionId, listId, controller.signal)
      if (!controller.signal.aborted) { setItems(next); setLoadFailed(false) }
    }).catch(() => {
      if (!controller.signal.aborted) setLoadFailed(true)
    }).finally(() => {
      if (request.current === controller) request.current = null
    })
  }, [collectionId, listId])

  useEffect(() => {
    void refresh()
    return () => { request.current?.abort(); operation.current?.abort() }
  }, [refresh])

  const parsing = items?.some(file => !file.archived && !["ready", "failed"].includes(file.parse_state))
  useEffect(() => {
    if (!parsing || busy || loadFailed) return
    const timer = setInterval(() => { if (!request.current && !operation.current) void refresh() }, 3000)
    return () => clearInterval(timer)
  }, [parsing, busy, loadFailed, refresh])

  async function run(action: (signal: AbortSignal) => Promise<unknown>) {
    if (!writable || operation.current) return
    const controller = new AbortController()
    operation.current = controller
    request.current?.abort()
    setBusy(true)
    setActionFailed(false)
    try {
      await action(controller.signal)
    } catch {
      if (!controller.signal.aborted) setActionFailed(true)
    } finally {
      if (!controller.signal.aborted) await refresh()
      if (operation.current === controller) operation.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  function chooseFile(file?: QaMaterial) {
    if (!writable || operation.current) return
    replacing.current = file
    input.current?.click()
  }

  const visible = items?.filter(file => showArchived || !file.archived) || []
  const hasArchived = items?.some(file => file.archived)
  return <div className="qa-file-space-files">
    <div className="qa-file-space-tools">
      {!writable && <span className="qa-meta">{t("qa.readOnly")}</span>}
      {hasArchived && <QaToggle checked={showArchived} onChange={setShowArchived} label={t("qa.showRemovedMaterials")} />}
      {busy && <span className="qa-meta" role="status">{t("qa.processing")}</span>}
      {writable && <Button className="qa-file-space-upload" type="button" size="sm" disabled={busy} onClick={() => chooseFile()}><Upload />{t("qa.uploadMaterial")}</Button>}
    </div>
    {writable && <input ref={input} type="file" hidden accept={supportedFiles} disabled={busy} onChange={event => {
      const file = event.target.files?.[0]
      const versionOf = replacing.current
      event.target.value = ""
      replacing.current = undefined
      if (file) void run(signal => uploadQaMaterial(collectionId, listId, file, versionOf, signal))
    }} />}
    {(actionFailed || loadFailed) && <div className="qa-notice" role="alert">{t("qa.failed")} {loadFailed && <Button type="button" variant="link" disabled={busy} onClick={() => { setLoadFailed(false); void refresh() }}>{t("qa.retry")}</Button>}</div>}
    <div className="qa-file-space-scroll">
      {!items && !loadFailed && <div className="qa-empty" role="status"><LoaderCircle className="size-5 animate-spin" />{t("fileMgmt.loadingFiles")}</div>}
      {items && !visible.length && !loadFailed && <div className="qa-empty"><FileText className="size-8 opacity-40" /><p>{t("qa.noMaterials")}</p></div>}
      {!!visible.length && <ul className="qa-file-space-rows">{visible.map(file => {
        const status = file.archived ? "qa.archived" : file.parse_state === "failed" ? "qa.parseFailed" : file.parse_state === "ready" ? "qa.materialReady" : "qa.processing"
        const canRetry = !file.archived && ["failed", "processing"].includes(file.parse_state)
        return <li className="qa-file-space-row" data-archived={file.archived || undefined} key={file.id}>
          <button type="button" className="qa-file-space-name" onClick={event => onSource({ kind: "material", file_id: file.id, version_id: file.current_version_id, filename: file.filename, excerpt: "", chunk_id: "" }, event.currentTarget)}>
            <FileText className="size-4" aria-hidden="true" />
            <span className="qa-file-space-filename" title={file.filename}>{file.filename}</span>
            <small className="qa-file-space-version">V{file.number}</small>
            <small className={status === "qa.materialReady" ? "sr-only" : "qa-file-space-status"} data-failed={status === "qa.parseFailed" || undefined} title={file.archived ? t("qa.materialRemoved") : undefined}>{t(status)}</small>
          </button>
          {writable && <QaActionMenu label={`${t("qa.more")} · ${file.filename}`} disabled={busy}>
            {canRetry && <QaMenuItem onClick={() => { void run(() => retryQaMaterial(collectionId, listId, file.id, file.current_version_id)) }}><RotateCcw />{t("qa.retry")}</QaMenuItem>}
            {!file.archived && <QaMenuItem onClick={() => chooseFile(file)}><Upload />{t("qa.fileSpaceNewVersion")}</QaMenuItem>}
            <QaMenuItem onClick={() => { void run(() => archiveQaMaterial(collectionId, listId, file)) }}>{file.archived ? <RotateCcw /> : <Archive />}{t(file.archived ? "qa.restore" : "qa.aiArchiveMaterial")}</QaMenuItem>
          </QaActionMenu>}
        </li>
      })}</ul>}
    </div>
  </div>
}

export function QaFileSpaceDialog({ collectionId, listId, writable, close: onClosed, returnFocusRef }: {
  collectionId: string; listId: string; writable: boolean; close: () => void; returnFocusRef?: RefObject<HTMLButtonElement | null>
}) {
  const t = useT()
  const { open, close, onOpenChangeComplete } = useQaModal(onClosed)
  const [source, setSource] = useState<QaSource | null>(null)
  const fileTrigger = useRef<HTMLButtonElement | null>(null)
  useEffect(() => {
    if (!source) fileTrigger.current?.focus({ preventScroll: true })
  }, [source])

  return <Dialog open={open} onOpenChange={open => { if (!open) close() }} onOpenChangeComplete={onOpenChangeComplete}>
    <DialogContent className="qa-file-space-dialog" finalFocus={returnFocusRef}>
      <DialogHeader className={source ? "sr-only" : "qa-file-space-header"}>
        <DialogTitle>{t("qa.materials")}</DialogTitle>
        <DialogDescription>{t("qa.sharedMaterialsHint")}</DialogDescription>
      </DialogHeader>
      <div className="qa-file-space-list" hidden={!!source} inert={!!source}>
        <QaMaterials collectionId={collectionId} listId={listId} writable={writable} onSource={(next, trigger) => {
          fileTrigger.current = trigger
          setSource(next)
        }} />
      </div>
      {source && <QaMaterialPreview key={`${source.file_id}/${source.version_id}`} collectionId={collectionId} listId={listId} source={source} listButton={false} onBack={() => setSource(null)} />}
    </DialogContent>
  </Dialog>
}

export function QaMaterialPreview({ collectionId, listId, source, sources, onBack, onCollapse, listButton = true }: { collectionId: string; listId: string; source: QaSource; sources?: QaSource[]; onBack: () => void; onCollapse?: () => void; listButton?: boolean }) {
  const t = useT()
  const contentId = useId()
  const [url, setUrl] = useState<string | null>(null)
  const [error, setError] = useState(false)
  const selection = sources || source
  const [quotedSelection, setQuotedSelection] = useState<typeof selection | null>(null)
  const excerpts = groupQaSources(sources || [source]).find(group => group.source.kind === source.kind && group.source.file_id === source.file_id && group.source.version_id === source.version_id)?.sources.filter(s => s.excerpt.trim()) || []
  const showExcerpts = excerpts.length > 0 && quotedSelection === selection
  useEffect(() => {
    const controller = new AbortController()
    let objectUrl: string | null = null
    getQaSourceFile(collectionId, listId, source, controller.signal).then(blob => {
      if (!controller.signal.aborted) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl) }
    }).catch(() => { if (!controller.signal.aborted) setError(true) })
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [collectionId, listId, source])
  return <section className="qa-material-preview pm-rail-card"><header>{onCollapse && <Button variant="ghost" size="icon-sm" aria-label={t("qa.collapseDetail")} title={t("qa.collapseDetail")} onClick={onCollapse}><PanelLeftClose /></Button>}{!listButton && <Button autoFocus size="sm" variant="ghost" onClick={onBack}><ArrowLeft />{t("qa.backMaterials")}</Button>}<span title={source.filename}>{source.filename || t("qa.sourceFile")}</span>{listButton && <Button size="icon-sm" variant="ghost" aria-label={t("qa.questionList")} onClick={onBack}><List /></Button>}</header>
    <div className="qa-source-preview-body" id={contentId}>
      <div className="qa-source-file-view" hidden={showExcerpts} inert={showExcerpts} aria-hidden={showExcerpts}>
        {error ? <div className="qa-empty" role="alert">{t("qa.sourceUnavailable")}</div> : url ? <RawFileViewer url={url} filename={source.filename} initialPage={source.page_number} className="flex-1 min-h-0" tools="download-search" /> : <div className="qa-empty" role="status">{t("fileMgmt.loadingPreview")}</div>}
      </div>
      {showExcerpts && <QaSourceExcerpts collectionId={collectionId} sources={excerpts} />}
    </div>
    {excerpts.length > 0 && <footer className="qa-source-preview-toggle"><Button variant="ghost" size="sm" aria-pressed={showExcerpts} aria-controls={contentId} onClick={() => setQuotedSelection(showExcerpts ? null : selection)}><FileText className="size-3.5" />{t(showExcerpts ? "qa.sourcePreview" : "qa.sourceExcerpt")}</Button></footer>}
  </section>
}

export function QaSourceExcerpts({ collectionId, sources }: { collectionId: string; sources: QaSource[] }) {
  const t = useT()
  return <div className="qa-source-excerpts" role="region" aria-label={t("qa.sourceExcerptsTitle")}>
    {sources.map((source, index) => <section className="qa-source-quote" key={`${source.chunk_id}/${index}`}>
      <header><span>{t("qa.sourceExcerptNumber", { number: index + 1 })}</span>{source.page_number && <small>{t("qa.sourcePage", { page: source.page_number })}</small>}</header>
      <ChunkMd text={source.excerpt} collection={collectionId} fileId={source.file_id} compactFigures />
    </section>)}
  </div>
}
