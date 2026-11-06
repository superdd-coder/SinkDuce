import { useCallback, useEffect, useRef, useState } from "react"
import { ArrowLeft, ChevronDown, ChevronRight, Download, FileQuestion, FolderOpen, List, Plus, Upload, Pencil, CircleMinus, RotateCcw, Settings2, History, Archive, Trash2, LoaderCircle } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { MenuSeparator } from "@/components/ui/menu"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { QaActionMenu, QaMenuItem, QaField, QaToggle } from "./qa-controls"
import { Textarea } from "@/components/ui/textarea"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { useT } from "@/i18n/use-t"
import { useAppStore } from "@/stores/app-store"
import { sourceScopedKey } from "@/api/source-scope"
import { annotateQaQuestion, createQaList, getQaList, getQaPendingImports, getQaImportContext, getQaLists, getQaProjectQuestions, mutateQaList,
  type QaImportContext, type QaImportPlan, type QaImportRecord, type QaPendingImport, type QaListDetail, type QaListSummary, type QaProjectQuestion, type QaSource } from "@/api/client"
import { filterQaQuestions, qaPanelTransition, type QaPanel } from "@/lib/qa-view"
import { createQaMutationQueue } from "@/lib/qa-autosave"
import { reconcileQaSnapshot } from "@/lib/qa-snapshot"
import { groupQaSources, type QaSourceGroup } from "@/lib/qa-sources"
import { useQaEnrichment } from "./use-qa-enrichment"
import { QaColumnName } from "./qa-column-name"
import { QaReplyEditor } from "./qa-reply-editor"
import { QaFileSpaceDialog, QaMaterialPreview } from "./qa-materials"
import { QaCorrections } from "./qa-corrections"
import { QaHistory } from "./qa-history"
import { QaDeleteList } from "./qa-delete-list"
import { QaTemplateDialog } from "./qa-template-dialog"
import { QaOptionsDialog } from "./qa-options"
import { defaultQaOptions, qaOptionLabel } from "@/lib/qa-options"
import { QaOutlineList } from "./qa-outline"
import { QaQuestionListHeader } from "./qa-question-filter"
import { QaExchange } from "./qa-exchange"
import "./qa.css"

function QaDot({ color, label }: { color?: string; label: string }) {
  return <span className="qa-dot" style={{ backgroundColor: color || "#7c8580" }} role="img" aria-label={label} title={label} />
}

function relativeTime(value: string, locale: string) {
  const days = Math.floor((Date.now() - Date.parse(value)) / 86400000)
  if (!Number.isFinite(days)) return ""
  return new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(-Math.max(0, days), "day")
}

export function QaWorkspace({ collectionId, railCovered = false }: { collectionId: string; railCovered?: boolean }) {
  const actor = useAppStore(s => s.identity?.actor_id || "local")
  return <QaWorkspaceContent key={sourceScopedKey(`${actor}/${collectionId}`)} collectionId={collectionId} railCovered={railCovered} />
}

function QaWorkspaceContent({ collectionId, railCovered }: { collectionId: string; railCovered: boolean }) {
  const t = useT()
  const locale = useAppStore(s => s.locale)
  const writable = useAppStore(s => s.collections.find(c => c.id === collectionId)?.permissions?.write === true)
  const [lists, setLists] = useState<QaListSummary[]>([])
  const [data, setData] = useState<QaListDetail | null>(null)
  const [allQuestions, setAllQuestions] = useState<QaProjectQuestion[] | null>(null)
  const [panel, setPanel] = useState<QaPanel>({ mode: "overview", questionId: null })
  const [query, setQuery] = useState("")
  const [status, setStatus] = useState("all")
  const [questionType, setQuestionType] = useState("all")
  const [expandedFieldsFor, setExpandedFieldsFor] = useState<string | null>(null)
  const [newColumnFor, setNewColumnFor] = useState<string | null>(null)
  const [writer] = useState(() => createQaMutationQueue<QaListDetail>((list, path, payload, method) => mutateQaList(collectionId, list, path, payload, method)))
  const pendingWrites = useRef(0)
  const [restoreReplies, setRestoreReplies] = useState(false)
  const [omitted, setOmitted] = useState(false)
  const [archived, setArchived] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  type DialogView = "list" | "question" | "edit" | "restore"
  const [dialogState, setDialogState] = useState<{ shown: DialogView | null; open: boolean }>({ shown: null, open: false })
  const dialog = dialogState.open ? dialogState.shown : null
  const shownDialog = dialogState.shown
  function setDialog(next: DialogView | null) {
    setDialogState(current => next ? { shown: next, open: true } : { ...current, open: false })
  }
  const [title, setTitle] = useState("")
  const [questionGroup, setQuestionGroup] = useState("")
  const [questionParent, setQuestionParent] = useState("")
  const [historyOpen, setHistoryOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [fileSpaceListId, setFileSpaceListId] = useState<string | null>(null)
  const fileSpaceTrigger = useRef<HTMLButtonElement>(null)
  const [exchange, setExchange] = useState<"import" | "export" | null>(null)
  const [source, setSource] = useState<QaSourceGroup | null>(null)
  const [optionsOpen, setOptionsOpen] = useState(false)
  const [templateOpen, setTemplateOpen] = useState(false)
  const [correctionRecord, setCorrectionRecord] = useState<QaImportRecord | null>(null)
  const [pendingImports, setPendingImports] = useState<QaPendingImport[]>([])
  const [resume, setResume] = useState<{ list: QaImportContext; plan: QaImportPlan } | undefined>(undefined)
  const generation = useRef(0)
  const lock = useRef(false)
  const mounted = useRef(true)

  const reportError = useCallback((err: unknown) => {
    const message = err instanceof Error ? err.message : ""
    const code = ["invalid_parent", "invalid_group", "invalid_outline"].find(key => message.includes(key))
    toast.error(t(message.includes("stale_revision") ? "qa.stale" : code ? `qa.importError.${code}` : "qa.failed"))
  }, [t])

  const refreshLists = useCallback(async () => {
    const rows = await getQaLists(collectionId)
    if (mounted.current) setLists(rows)
    if (writable) { const pending = await getQaPendingImports(collectionId); if (mounted.current) setPendingImports(pending) }
  }, [collectionId, writable])

  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    getQaLists(collectionId, controller.signal).then(rows => {
      if (!controller.signal.aborted) { setLists(rows); setError("") }
    }).catch(e => {
      if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "error")
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => { mounted.current = false; controller.abort() }
  }, [collectionId])

  useEffect(() => {
    let active = true
    if (writable) void getQaPendingImports(collectionId).then(rows => {
      if (active) setPendingImports(rows)
    }).catch(() => {})
    return () => { active = false }
  }, [collectionId, writable])

  useEffect(() => { if (data) writer.remember(data) }, [data, writer])

  const enrichment = useQaEnrichment(collectionId, data, next => {
    setData(current => current?.id === next.id && current.version.id === next.version.id ? reconcileQaSnapshot(current, next) : current)
  })

  function refreshQuestionAnnotation(list: QaListDetail, questionId: string) {
    void annotateQaQuestion(collectionId, list, questionId).then(enriched => {
      if (mounted.current) setData(current => current?.id === enriched.id && current.version.id === enriched.version.id && current.revision === enriched.revision ? reconcileQaSnapshot(current, enriched) : current)
    }).catch(() => {})
  }

  async function openList(id: string, version?: string, questionId?: string) {
    const current = ++generation.current
    setLoading(true); setError("")
    try {
      const next = await getQaList(collectionId, id, version)
      if (current !== generation.current || !mounted.current) return
      setData(next); setAllQuestions(null); setDialog(null); setFileSpaceListId(null); setQuery(""); setStatus("all"); setQuestionType("all"); setOmitted(false)
      setPanel({ mode: questionId ? "reading" : "overview", questionId: questionId || null })
    } catch (e) { if (current === generation.current) { setError("load"); reportError(e) } }
    finally { if (current === generation.current) setLoading(false) }
  }

  async function commitChange(path: string, payload: Record<string, unknown>, method = "POST") {
    if (!data) return null
    const replySave = method === "PUT" && /^\/questions\/[^/]+\/replies\/[^/]+$/.test(path)
    if (!replySave) { pendingWrites.current++; setBusy(true) }
    try {
      const next = await writer.write(data, path, payload, method)
      if (!mounted.current) return next
      setData(current => current?.id === data.id && current.version.id === data.version.id ? reconcileQaSnapshot(current, next) : current)
      if (!replySave) void refreshLists().catch(reportError)
      const questionId = path === "/questions" ? next.questions.find(q => !data.questions.some(old => old.id === q.id))?.id : path.startsWith("/questions/") && "text" in payload ? path.split("/")[2] : null
      if (questionId) refreshQuestionAnnotation(next, questionId)
      return next
    } catch (e) { reportError(e); return null }
    finally { if (!replySave) { pendingWrites.current--; if (mounted.current) setBusy(pendingWrites.current > 0) } }
  }

  async function change(path: string, payload: Record<string, unknown>, method = "POST") {
    return !!await commitChange(path, payload, method)
  }

  async function showAll() {
    const current = ++generation.current
    setLoading(true); setError("")
    try {
      const questions = await getQaProjectQuestions(collectionId, true)
      if (current === generation.current && mounted.current) { setAllQuestions(questions); setData(null); setQuery(""); clearFilters(); setOmitted(false) }
    } catch (e) { reportError(e) }
    finally { if (current === generation.current) setLoading(false) }
  }

  function home() {
    generation.current++; setData(null); setAllQuestions(null); setFileSpaceListId(null); setError(""); setQuery(""); clearFilters(); setLoading(false)
    void refreshLists().catch(reportError)
  }

  async function submit() {
    if (!title.trim() && dialog !== "restore") return
    if (dialog === "list") {
      if (lock.current) return
      lock.current = true; setBusy(true)
      try {
        const created = await createQaList(collectionId, title.trim())
        if (mounted.current) { setData(created); setAllQuestions(null); setDialog(null); setPanel({ mode: "overview", questionId: null }); await refreshLists() }
      } catch (e) { reportError(e) }
      finally { lock.current = false; if (mounted.current) setBusy(false) }
    } else {
      const ok = dialog === "restore" && panel.questionId ? await change(`/questions/${panel.questionId}/membership`, { included: true, restore_replies: restoreReplies }) : dialog === "question" ? await change("/questions", { text: title, group_id: questionGroup, parent_id: questionParent })
        : dialog === "edit" && panel.questionId ? await change(`/questions/${panel.questionId}`, { text: title }, "PATCH")
        : false
      if (ok) { setTitle(""); setDialog(null) }
    }
  }

  function action(value: typeof dialog) {
    if (value === "question") { setQuestionGroup(data?.questions.find(q => q.id === panel.questionId)?.group_id || ""); setQuestionParent("") }
    setTitle(""); setDialog(value)
  }

  function preview(next: QaSource, citations?: QaSource[]) {
    const group = groupQaSources(citations?.length ? citations : [next]).find(item => item.source.kind === next.kind && item.source.file_id === next.file_id && item.source.version_id === next.version_id)
    if (!group) return
    setSource(group)
    if (panel.questionId) { setDialog(null); setPanel(s => qaPanelTransition(s, { type: "preview" })) }
  }
  const previewing = panel.mode === "preview" && !!source
  const visible = data ? filterQaQuestions(data.questions, query, status, omitted, questionType) : []
  const projectVisible = (allQuestions || []).filter(q => (omitted || q.included) && (status === "all" || status === `${q.list_id}:${q.status}`) && (questionType === "all" || questionType === `${q.list_id}:${q.type}`) && q.text.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  function clearFilters() { setStatus("all"); setQuestionType("all"); setOmitted(false) }
  const selected = data?.questions.find(q => q.id === panel.questionId)
  const reading = panel.mode !== "overview" && !!selected
  const questionRailCovered = previewing || (railCovered && reading)
  const vocabulary = data?.options || defaultQaOptions
  const statusLabel = (value: string) => qaOptionLabel(vocabulary.statuses.find(o => o.id === value), t)
  const typeLabel = (value: string) => vocabulary.types.find(o => o.id === value)?.label || t("qa.unset")
  const statusColor = (value: string) => vocabulary.statuses.find(o => o.id === value)?.color
  const statusOptions = data ? vocabulary.statuses.map(o => ({ value: o.id, label: qaOptionLabel(o, t) })) : Array.from(new Map((allQuestions || []).map(q => [`${q.list_id}:${q.status}`, { value: `${q.list_id}:${q.status}`, label: `${q.status_label || q.status || t("qa.unset")} · ${q.list_title}` }])).values())
  const typeOptions = data ? vocabulary.types.map(o => ({ value: o.id, label: o.label })) : Array.from(new Map((allQuestions || []).map(q => [`${q.list_id}:${q.type}`, { value: `${q.list_id}:${q.type}`, label: `${q.type_label || q.type || t("qa.unset")} · ${q.list_title}` }])).values())


  return <section className="qa-workspace" aria-label={t("qa.title")}>
    <header className="qa-header">
      {(data || allQuestions) && <div className="qa-heading is-detail">
        <Button variant="ghost" size="icon-sm" disabled={busy} aria-label={t("qa.backLists")} title={t("qa.backLists")} onClick={home}><ArrowLeft /></Button>
        <div className="qa-title-row">
          {data ? <QaColumnName key={data.id} variant="title" label={data.title} editable={data.can_edit} onSave={title => change("", { title }, "PATCH")} /> : <h2 className="qa-title">{t("qa.allQuestions")}</h2>}
          {data && <Button variant="secondary" size="sm" className="qa-version" onClick={() => setHistoryOpen(true)} disabled={busy} title={t("qa.history")}>V{data.version.number}{!data.can_edit && <span> · {t("qa.readOnly")}</span>}</Button>}
        </div>
      </div>}
      {!data && !allQuestions && <div className="qa-tools qa-list-tools"><Button variant="ghost" size="icon" onClick={showAll} disabled={loading} aria-label={t("qa.allQuestions")} title={t("qa.allQuestions")}><List /></Button><QaToggle checked={archived} onChange={setArchived} label={t("qa.showArchived")} /></div>}
      <div className="qa-actions">
        {data?.can_edit && <Button size="icon" onClick={() => action("question")} disabled={busy} aria-label={t("qa.addQuestion")} title={t("qa.addQuestion")}><Plus /></Button>}
        {data && <Button variant="ghost" size="icon" className="qa-toolbar-action" disabled={busy} onClick={() => setExchange("export")} aria-label={t("qa.export")} title={t("qa.export")}><Download /></Button>}
        {data?.can_edit && <Button variant="ghost" size="icon" className="qa-toolbar-action" disabled={busy} onClick={() => { setResume(undefined); setExchange("import") }} aria-label={t("qa.import")} title={t("qa.import")}><Upload /></Button>}
        {data && <Button ref={fileSpaceTrigger} variant="ghost" size="icon" className="qa-toolbar-action" disabled={loading} aria-haspopup="dialog" aria-label={t("qa.materials")} title={t("qa.materials")} onClick={() => setFileSpaceListId(data.id)}><FolderOpen /></Button>}
        {!data && writable && <Button variant="ghost" size="icon" className="qa-toolbar-action" aria-label={t("qa.createFromExcel")} title={t("qa.createFromExcel")} onClick={() => { setResume(undefined); setExchange("import") }}><Upload /></Button>}
        {!data && writable && <Button size="icon" onClick={() => action("list")} disabled={busy} aria-label={t("qa.createList")} title={t("qa.createList")}><Plus /></Button>}
        {data && <QaActionMenu label={t("qa.more")}>
          {data.can_edit && <QaMenuItem disabled={busy} onClick={() => setOptionsOpen(true)}><Settings2 />{t("qa.manageOptions")}</QaMenuItem>}
          <QaMenuItem disabled={busy} onClick={() => setHistoryOpen(true)}><History />{t("qa.history")}</QaMenuItem>
          {writable && data.version.id === data.current_version_id && <><MenuSeparator />
            <QaMenuItem disabled={busy} onClick={() => { void change("", { archived: !data.archived }, "PATCH") }}><Archive />{t(data.archived ? "qa.restoreList" : "qa.archiveList")}</QaMenuItem>
            <QaMenuItem destructive disabled={busy} onClick={() => setDeleteOpen(true)}><Trash2 />{t("qa.deleteList")}</QaMenuItem>
          </>}
        </QaActionMenu>}
      </div>
    </header>

    {error && <div className="qa-notice" role="alert">{t("qa.failed")} <Button variant="link" onClick={() => data ? openList(data.id) : home()}>{t("qa.retry")}</Button></div>}
    {!loading && data?.can_edit && !!data.annotation_pending && <div className="qa-enrichment" role="status">{enrichment.running ? <><LoaderCircle className="size-3.5 animate-spin" />{t("qa.classifying")}</> : <>{t("qa.classificationIncomplete")}<Button size="sm" variant="ghost" onClick={() => { void enrichment.retry() }}>{t("qa.retry")}</Button></>}</div>}
    <div className="qa-content">
    {loading ? <div className="qa-empty" role="status">{t("qa.loading")}</div> : !data && !allQuestions ? <>
      {pendingImports.length > 0 && <details className="qa-pending"><summary>{t("qa.pendingImports", { count: pendingImports.length })}</summary>{pendingImports.map(p => <button key={p.plan.id} className="qa-version-row" onClick={async () => { try { setResume({ list: await getQaImportContext(collectionId, p.list_id), plan: p.plan }); setExchange("import") } catch (e) { reportError(e) } }}>{p.title} · {p.plan.filename}</button>)}</details>}
      <div className="qa-lists">{lists.filter(l => archived || !l.archived).map(l => <button key={l.id} className="qa-list-card pm-rail-card" onClick={() => openList(l.id)}><div className="qa-list-icon"><FileQuestion /></div><div><h3>{l.title}</h3><p>V{l.version_number} · {t("qa.count", { count: l.question_count })}{l.archived ? ` · ${t("qa.archived")}` : ""}</p></div><time>{relativeTime(l.updated_at, locale)}</time><ChevronRight className="size-4" /></button>)}
      {!lists.length && <div className="qa-empty"><FileQuestion className="size-9 opacity-40" /><h3>{t("qa.emptyTitle")}</h3><p>{t("qa.emptyDescription")}</p></div>}</div>
    </> : <>
      {allQuestions ? <div className="qa-overview qa-project-overview">
        <QaQuestionListHeader query={query} onQueryChange={setQuery} count={projectVisible.length} status={status} type={questionType} omitted={omitted} statusOptions={statusOptions} typeOptions={typeOptions} onStatusChange={setStatus} onTypeChange={setQuestionType} onOmittedChange={setOmitted} onClear={clearFilters} />
        <div className="qa-question-list-scroll">{projectVisible.map(q => <button key={`${q.list_id}/${q.id}`} className="qa-question-row" onClick={() => openList(q.list_id, undefined, q.id)}><div className="qa-project-question-copy"><div className="qa-question-summary">{q.display_number && <span className="qa-display-number">{q.display_number}</span>}{q.type_origin !== "default" && q.type_label && <span className="qa-type" title={q.type_label}>{q.type_label}</span>}<span className={!q.included ? "qa-omitted" : ""}>{q.text}</span></div><span className="qa-meta" title={q.list_title}>{q.list_title}</span></div><span className="qa-row-status" title={q.status_label || t("qa.unset")}><QaDot color={q.status_color} label={q.status_label || t("qa.unset")} /><span>{q.status_label || t("qa.unset")}</span></span></button>)}</div>
      </div>
      : data && <div className={`qa-stage ${reading ? "is-reading" : "is-overview"} ${previewing ? "is-preview" : ""}`}>
        <div className="qa-detail-slot" inert={!reading} aria-hidden={!reading}>{selected && <div className="qa-reading pm-rail-card" key={`${data.version.id}/${selected.id}`}>
          <div className="qa-question-head">
            <div className="qa-question-title-row">
              <div className="qa-question-copy">{selected.display_number && <span className="qa-display-number">{selected.display_number}</span>}<h3>{selected.text}</h3>
                {data.can_edit && <div className="qa-question-title-actions">
                  <Button variant="ghost" size="icon-sm" aria-label={t("qa.editQuestion")} title={t("qa.editQuestion")} onClick={() => { setTitle(selected.text); setDialog("edit") }}><Pencil /></Button>
                  <Button variant="ghost" size="icon-sm" disabled={busy} aria-label={t(selected.included ? "qa.removeQuestion" : "qa.restoreQuestion")} title={t(selected.included ? "qa.removeQuestion" : "qa.restoreQuestion")} onClick={() => { if (selected.included) void change(`/questions/${selected.id}/membership`, { included: false }); else { setRestoreReplies(false); setDialog("restore") } }}>{selected.included ? <CircleMinus /> : <RotateCcw />}</Button>
                </div>}
              </div>
            </div>
            <div className="qa-question-controls"><DropdownSelect size="tag" chevron className="qa-type-select qa-filter" aria-label={t("qa.typeLabel")} disabled={!data.can_edit || busy} value={selected.type_origin === "default" ? "" : selected.type} placeholder={t("qa.unclassified")} onChange={type => { void change(`/questions/${selected.id}`, { type }, "PATCH") }} options={typeOptions} /><DropdownSelect size="sm" className="qa-filter" leading={<QaDot color={statusColor(selected.status)} label={statusLabel(selected.status)} />} aria-label={t("qa.statusLabel")} disabled={!data.can_edit || busy} value={selected.status} onChange={status => { void change(`/questions/${selected.id}`, { status }, "PATCH") }} options={[{ value: "", label: t("qa.unset") }, ...statusOptions]} />
              <DropdownSelect size="sm" className="qa-filter" aria-label={t("qa.group")} disabled={!data.can_edit || busy} value={selected.group_id || ""} options={[{ value: "", label: t("qa.ungrouped") }, ...(data.outline || []).filter(i => i.kind === "group").map(i => ({ value: i.id, label: i.text || "" }))]} onChange={group_id => { void change(`/outline/${selected.id}/move`, { group_id, parent_id: "" }) }} />
              {selected.parent_id && <Button size="sm" variant="ghost" onClick={() => setPanel(s => qaPanelTransition(s, { type: "select", questionId: selected.parent_id! }))}>{t("qa.parentQuestion")}</Button>}
              {(selected.fields?.length || selected.group) ? <Button variant="ghost" size="sm" className="qa-fields-toggle" aria-expanded={expandedFieldsFor === selected.id} aria-controls={`qa-fields-${selected.id}`} onClick={() => setExpandedFieldsFor(current => current === selected.id ? null : selected.id)}>{t("qa.moreFields")}<ChevronDown /></Button> : null}
            </div>
            {expandedFieldsFor === selected.id && (selected.fields?.length || selected.group) ? <div className="qa-extra-fields" id={`qa-fields-${selected.id}`}>
              {selected.group && <p className="qa-meta">{selected.group}</p>}
              <dl>{selected.fields?.map(field => <div key={field.id}><dt>{field.label}</dt><dd>{field.value === null || field.value === "" ? "—" : String(field.value)}</dd></div>)}</dl>
            </div> : null}
            {!selected.included && <p className="qa-notice">{t("qa.omittedSince", { version: selected.absent_since || data.version.number })}</p>}
          </div>
          <div className="qa-replies">{selected.replies.map((reply, index) => <QaReplyEditor position={index + 1} key={reply.column_id} collectionId={collectionId} data={data} question={selected} reply={reply} busy={busy} onSource={preview} onToggle={restore_replies => { void change(`/columns/${reply.column_id}`, { included: !reply.included, restore_replies }, "PATCH").then(ok => {
            if (ok && reply.included) toast.success(t("qa.columnRemoved"), { action: { label: t("qa.undo"), onClick: () => { void change(`/columns/${reply.column_id}`, { included: true, restore_replies: true }, "PATCH") } } })
          }) }} onRename={label => change(`/columns/${reply.column_id}`, { label }, "PATCH")} onSave={payload => commitChange(`/questions/${selected.id}/replies/${reply.column_id}`, payload, "PUT")}
            onFinish={saved => { refreshQuestionAnnotation(saved, selected.id); void refreshLists().catch(reportError) }} />)}
            {!data.columns.some(c => c.included) && <p className="qa-meta">{t("qa.noColumns")}</p>}
            {data.can_edit && newColumnFor === selected.id && <article className="qa-reply qa-new-reply">
              <header><div className="qa-reply-heading"><span className="qa-reply-number" aria-hidden>{String(selected.replies.length + 1).padStart(2, "0")}</span>
                <QaColumnName initialEditing editable label={t("qa.defaultColumnName", { number: data.columns.length + 1 })}
                  onCancel={() => setNewColumnFor(null)} onSave={async label => {
                    const saved = await change("/columns", { labels: [label] })
                    if (saved) setNewColumnFor(current => current === selected.id ? null : current)
                    return saved
                  }} />
              </div></header><div className="qa-composer-preview">{t("qa.replyPlaceholder")}</div>
            </article>}
            {data.can_edit && newColumnFor !== selected.id && <Button variant="ghost" size="sm" className="qa-add-reply" onClick={() => setNewColumnFor(selected.id)}><Plus />{t("qa.addColumn")}</Button>}
          </div>
        </div>}</div>
        <div className={`qa-side ${reading ? "is-narrow" : "is-wide"} ${previewing ? "is-preview" : ""}`}>
        <div inert={questionRailCovered} aria-hidden={questionRailCovered} className={`qa-list-layer ${previewing ? "is-hidden" : ""} ${reading ? "qa-question-nav" : "qa-overview"}`} aria-label={t("qa.questionList")}>
          <QaQuestionListHeader query={query} onQueryChange={setQuery} onCollapse={reading ? () => setPanel(s => qaPanelTransition(s, { type: "overview" })) : undefined} count={visible.length} status={status} type={questionType} omitted={omitted} statusOptions={statusOptions} typeOptions={typeOptions} onStatusChange={setStatus} onTypeChange={setQuestionType} onOmittedChange={setOmitted} onClear={clearFilters} />
          <div className="qa-question-list-scroll">
            {!reading && <div className="qa-overview-labels"><span>{t("qa.question")}</span><span>{t("qa.latestReply")}</span><span>{t("qa.statusLabel")}</span><span>{t("qa.updated")}</span></div>}
            <QaOutlineList key={`${data.id}/${data.version.id}`} data={data} visible={visible} busy={busy} filtered={!!query || status !== "all" || questionType !== "all"}
              onMove={async (id, payload) => { const ok = await change(`/outline/${id}/move`, payload); if (ok) toast.success(t("qa.moved"), { action: { label: t("qa.undo"), onClick: () => { void change("/outline/undo", {}) } } }); return ok }}
              onEdit={(id, payload) => change(`/outline/${id}`, payload, "PATCH")}
              onCreate={(kind, text, group_id) => change("/outline", { kind, text, group_id })}
              onAddQuestion={(group, parent = "") => { action("question"); setQuestionGroup(group); setQuestionParent(parent) }}
              renderQuestion={q => <button key={q.id} aria-current={reading && q.id === selected?.id ? "true" : undefined} className={`qa-question-row ${!q.included ? "is-omitted" : ""}`} onClick={() => setPanel(s => qaPanelTransition(s, { type: "select", questionId: q.id }))}>
                <div className="qa-question-summary">{q.display_number && <span className="qa-display-number">{q.display_number}</span>}{q.type_origin !== "default" && q.type && <span className="qa-type" title={typeLabel(q.type)}>{typeLabel(q.type)}</span>}<span>{q.summary || q.text}</span></div>
                {!reading && <span className="qa-reply-summary">{q.reply_summary || q.latest_reply || t("qa.noReply")}</span>}
                <span className="qa-row-status" title={statusLabel(q.status)}><QaDot color={statusColor(q.status)} label={statusLabel(q.status)} />{!reading && <span>{statusLabel(q.status)}</span>}</span>
                {!reading && <time>{relativeTime(q.updated_at, locale)}</time>}
              </button>} />
            {!visible.length && <div className="qa-empty">{t(data.questions.length ? "qa.noResults" : "qa.noQuestions")}</div>}
          </div>
        </div>
        {source && <div className={`qa-preview-layer ${previewing ? "" : "is-hidden"}`} inert={!previewing} aria-hidden={!previewing}><QaMaterialPreview key={source.key} collectionId={collectionId} listId={data.id} source={source.source} sources={source.sources} onCollapse={() => setPanel(s => qaPanelTransition(s, { type: "overview" }))} onBack={() => setPanel(s => qaPanelTransition(s, { type: "list" }))} /></div>}
        </div>
      </div>}
    </>}
    <div className="qa-quick-chat-slot"><div className="qa-quick-chat-rail" data-pm-rail-anchor /></div>
    </div>

    <Dialog open={dialog !== null} onOpenChange={open => { if (!open && !busy) setDialog(null) }}><DialogContent className="qa-dialog sm:max-w-xl"><DialogHeader><DialogTitle>{t(shownDialog === "list" ? "qa.createList" : shownDialog === "question" ? "qa.addQuestion" : shownDialog === "edit" ? "qa.editQuestion" : shownDialog === "restore" ? "qa.restoreQuestion" : "qa.listSettings")}</DialogTitle></DialogHeader>
      <form onSubmit={e => { e.preventDefault(); void submit() }} className="qa-form">
        {shownDialog === "restore" && selected && <><p className="qa-question-summary">{selected.text}</p><QaToggle checked={restoreReplies} onChange={setRestoreReplies} label={t("qa.restoreReplies")} /><div className="qa-dialog-scroll">{selected.replies.filter(r => r.body).map(r => <div className="qa-reply" key={r.column_id}><h4>{r.label}</h4><p>{r.body}</p></div>)}</div></>}
        {shownDialog === "list" && <QaField label={t("qa.name")}><Input autoFocus value={title} maxLength={200} onChange={e => setTitle(e.target.value)} required /></QaField>}
        {(shownDialog === "question" || shownDialog === "edit") && <QaField label={t("qa.question")}><Textarea autoFocus className="min-h-32" value={title} maxLength={32767} onChange={e => setTitle(e.target.value)} required /></QaField>}
        {shownDialog === "question" && data && <><QaField label={t("qa.group")}><DropdownSelect value={questionGroup} options={[{ value: "", label: t("qa.ungrouped") }, ...(data.outline || []).filter(i => i.kind === "group").map(i => ({ value: i.id, label: i.text || "" }))]} onChange={value => { setQuestionGroup(value); setQuestionParent("") }} /></QaField><QaField label={t("qa.parentQuestion")}><DropdownSelect value={questionParent} options={[{ value: "", label: t("qa.independentQuestion") }, ...data.questions.filter(q => q.included && (q.group_id || "") === questionGroup).map(q => ({ value: q.id, label: q.summary || q.text }))]} onChange={setQuestionParent} /></QaField></>}
        <div className="qa-form-actions"><Button type="button" variant="ghost" disabled={busy} onClick={() => setDialog(null)}>{t("qa.cancel")}</Button><Button type="submit" disabled={busy || (shownDialog === "restore" ? false : !title.trim())}>{t(shownDialog === "list" ? (busy ? "common.creating" : "common.create") : (busy ? "qa.saving" : "qa.save"))}</Button></div>
      </form>
    </DialogContent></Dialog>
    {data && data.id === fileSpaceListId && <QaFileSpaceDialog key={data.id} collectionId={collectionId} listId={data.id} writable={data.can_edit} returnFocusRef={fileSpaceTrigger} close={() => setFileSpaceListId(null)} />}
    {correctionRecord && data && <QaCorrections collectionId={collectionId} list={data} record={correctionRecord} close={() => { setCorrectionRecord(null); setHistoryOpen(true) }} onChanged={next => { setData(next); setPanel({ mode: "overview", questionId: null }); void refreshLists().catch(reportError) }} />}
    {historyOpen && data && <QaHistory collectionId={collectionId} list={data} busy={busy} close={() => setHistoryOpen(false)} onOpen={version => { setHistoryOpen(false); void openList(data.id, version.id) }} onCorrect={record => { setHistoryOpen(false); setCorrectionRecord(record) }} onNewVersion={() => change("/versions", {})} />}
    {deleteOpen && data && <QaDeleteList collectionId={collectionId} list={data} close={() => setDeleteOpen(false)} onDeleted={() => {
      const draftStore = useAppStore.getState()
      Object.keys(draftStore.qaDrafts).filter(key => key.includes(`/${data.id}/`)).forEach(key => draftStore.setQaDraft(key, null))
      setDeleteOpen(false); setDialog(null); home()
    }} />}
    {optionsOpen && data && <QaOptionsDialog data={data} close={() => setOptionsOpen(false)} onSave={payload => change("/options", payload, "PATCH")} />}
    {templateOpen && data && <QaTemplateDialog collectionId={collectionId} list={data} close={() => { setTemplateOpen(false); setExchange("export") }} onSelect={template_id => change("", { template_id }, "PATCH")} />}
    {exchange && <QaExchange onTemplate={() => { setExchange(null); setTemplateOpen(true) }} collectionId={collectionId} list={data} mode={exchange} resume={resume} close={() => { setExchange(null); setResume(undefined); void refreshLists().catch(reportError) }} onImported={next => { setData(next); setAllQuestions(null); setQuery(""); setStatus("all"); setQuestionType("all"); setOmitted(false); setPanel({ mode: "overview", questionId: null }); void refreshLists().catch(reportError) }} />}
  </section>
}
