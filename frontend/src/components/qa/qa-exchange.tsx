import { useQaModal } from "./use-qa-modal"
import { QaWorkbookUpload } from "./qa-workbook-upload"
import { QaImportReview, type QaImportReviewHandle } from "./qa-import-review"
import { QaImportSubmit } from "./qa-import-submit"
import { QaImportTable, type QaImportTableHandle } from "./qa-import-table"
import { canConfirmQaFields, changeColumnRole, columnRole, excelColumn, qaParentCandidates } from "@/lib/qa-import-mapping"
import { saveQaDownload } from "@/lib/qa-download"
import { useEffect, useRef, useState } from "react"
import { Download, FileSpreadsheet, LoaderCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { QaActionMenu, QaField, QaMenuItem, QaToggle } from "./qa-controls"
import { Input } from "@/components/ui/input"
import { useAppStore } from "@/stores/app-store"
import { useT } from "@/i18n/use-t"
import { analyzeQaImport, cancelQaImport, createQaImportList, commitQaImport, exportQaFile, getQaTemplates, getQaExports, downloadQaExport, getQaImportContext, patchQaImport, uploadQaImport, type QaExportRecord, type QaImportContext, type QaImportPlan, type QaImportMergeChanges, type QaListDetail } from "@/api/client"
import "./qa-exchange-refinement.css"

export function QaExchange({ collectionId, list: existingList, mode, close: onClosed, onImported, resume, onTemplate }: { collectionId: string; list: QaListDetail | null; mode: "import" | "export"; close: () => void; onImported: (list: QaListDetail) => void; resume?: { list: QaImportContext; plan: QaImportPlan }; onTemplate?: () => void }) {
  const { open, close, onOpenChangeComplete, exit } = useQaModal(onClosed)
  const t = useT()
  const hasDrafts = useAppStore(s => !!existingList && Object.keys(s.qaDrafts).some(key => key.includes(`/${existingList.id}/`)))
  const [list, setList] = useState<QaImportContext | null>(resume?.list || existingList)
  const [exports, setExports] = useState<QaExportRecord[]>([])
  const [busy, setBusy] = useState(false)
  const [committing, setCommitting] = useState(false)
  const busyRef = useRef(false)
  const [error, setError] = useState("")
  const [plan, setPlan] = useState<QaImportPlan | null>(resume?.plan || null)
  const planRef = useRef(plan)
  const acknowledgedPlan = useRef(plan)
  const [columnMapping, setColumnMapping] = useState<QaImportPlan["mapping"] | null>(null)
  const columnMappingRef = useRef(columnMapping)
  const reviewRef = useRef<QaImportReviewHandle>(null)
  const tableRef = useRef<QaImportTableHandle>(null)
  const [cancelConfirm, setCancelConfirm] = useState(false)

  const [headerRow, setHeaderRow] = useState(resume?.plan.header_row || 1)
  const [region, setRegion] = useState(resume?.plan.region || { start_column: 1, end_column: 1, header_end_row: 1, end_row: 1 })
  const [options, setOptions] = useState({ include_number: true, include_status: false, resequence: false })

  useEffect(() => {
    if (mode !== "export" || !existingList) return
    if (!existingList.original_layout) getQaTemplates(collectionId).then(rows => {
      const template = rows.find(r => r.id === existingList.template_id)
      if (template) setOptions({ include_number: template.include_number ?? true, include_status: template.include_status ?? false, resequence: template.resequence ?? false })
    }).catch(() => {})
    getQaExports(collectionId, existingList.id).then(setExports).catch(() => {})
  }, [collectionId, existingList, mode])

  function acceptPlan(next: QaImportPlan, saved = true) {
    if (saved) acknowledgedPlan.current = next
    planRef.current = next
    setPlan(next); setHeaderRow(next.header_row)
    setRegion(next.region || { start_column: 1, end_column: next.headers.length, header_end_row: next.header_row, end_row: Number(next.rows.at(-1)?.key || next.header_row) })
  }
  async function run(work: () => Promise<void>, fallbackError = "qa.failed") {
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true); setError("")
    try { await work() } catch (e) {
      const message = e instanceof Error ? e.message : ""
      const code = ["stale_import", "duplicate_identity_assignment", "unresolved_match", "unresolved_content", "formula_without_cached_value", "parse_limit_exceeded", "invalid_mapping", "invalid_header", "invalid_sheet", "invalid_parent", "question_required", "empty_workbook", "unsupported_layout_change", "source_unavailable"].find(k => message.includes(k))
      if (code === "stale_import" && planRef.current) acceptPlan({ ...planRef.current, stale: true }, false)
      setError(t(code ? `qa.importError.${code}` : fallbackError))
    } finally { busyRef.current = false; setBusy(false) }
  }
  const unresolved = plan ? [...Object.values(plan.matches), ...Object.values(plan.column_matches)].filter(v => !v).length : 0
  const duplicate = plan ? [plan.matches, plan.column_matches].some(values => {
    const ids = Object.values(values).filter(v => v && v !== "new")
    return new Set(ids).size !== ids.length
  }) : false
  function validIdentityState(current: QaImportPlan) {
    return current.mapping_valid && canConfirmQaFields(current) &&
      [current.matches, current.column_matches].every(values => {
        const selected = Object.values(values).filter(target => target && target !== "new")
        return new Set(selected).size === selected.length
      })
  }
  function chooseIdentity(kind: "matches" | "column_matches", key: string, value: string) {
    const current = planRef.current
    if (!current || !list || busyRef.current || current.stale || current[kind][key] === value) return
    const assignments = { ...current[kind], [key]: value }
    const changed = new Set([key])
    if (kind === "column_matches" && value !== "new") {
      for (const [other, target] of Object.entries(current.column_matches)) {
        if (other === key || target !== value) continue
        assignments[other] = "new"
        changed.add(other)
      }
    }
    const contentChoices = Object.fromEntries((current.review?.conflicts || []).flatMap(item => {
      const affected = kind === "matches" ? changed.has(item.row_key)
        : item.kind === "reply" && changed.has(item.column_key || "")
      const choice = current.content_choices?.[item.id] || item.choice
      return choice && !affected && item.kind !== "structure" ? [[item.id, choice]] : []
    }))
    const next = { ...current, [kind]: assignments, content_choices: contentChoices, blank_columns: [] }
    acceptPlan(next, false)
    if (!validIdentityState(next)) return
    void run(async () => {
      const saved = await patchQaImport(collectionId, list.id, next, {
        matches: next.matches, column_matches: next.column_matches, content_choices: next.content_choices, blank_columns: [],
      })
      acceptPlan(saved)
      if (!saved.stale && validIdentityState(saved) &&
        saved.rows.every(row => saved.matches[row.key]) &&
        saved.mapping.replies.some(index => !saved.column_matches[String(index)])) {
        acceptPlan(await analyzeQaImport(collectionId, list.id, saved).catch(() => saved))
      }
    })
  }
  function choose(key: string, value: string) { chooseIdentity("matches", key, value) }
  function chooseColumn(key: string, value: string) { chooseIdentity("column_matches", key, value) }
  function chooseMerge(changes: QaImportMergeChanges) {
    const current = planRef.current
    if (!current || !list || busyRef.current || current.stale) return
    void run(async () => acceptPlan(await patchQaImport(collectionId, list.id, current, {
      matches: current.matches, column_matches: current.column_matches, ...changes,
    })))
  }
  function changeRole(index: number, role: string) {
    const current = planRef.current
    const pending = columnMappingRef.current
    if (!current || !list || current.stale || (busyRef.current && !pending)) return
    const mapping = pending || current.mapping
    if (columnRole(mapping, index) === role) return
    const changed = changeColumnRole({ ...current, mapping }, index, role)
    columnMappingRef.current = changed
    setColumnMapping(changed)
    if (pending) return

    void run(async () => {
      try {
        let saved = current
        if (saved !== acknowledgedPlan.current) {
          saved = await patchQaImport(collectionId, list.id, current, {
            matches: current.matches, column_matches: current.column_matches,
            content_choices: current.content_choices, blank_columns: current.blank_columns,
          })
          acceptPlan(saved)
        }
        // Each request uses the last acknowledged revision. Edits made while it
        // is in flight collapse into the next complete mapping.
        while (columnMappingRef.current && !saved.stale) {
          const target = columnMappingRef.current
          saved = await patchQaImport(collectionId, list.id, saved, { mapping: target })
          acceptPlan(saved)
          if (columnMappingRef.current === target) break
        }
      } finally {
        columnMappingRef.current = null
        setColumnMapping(null)
      }
    }, "qa.importPreview.columnSaveFailed")
  }
  const sourceUnresolved = plan?.table_rows?.filter(row => row.kind === "review").length || 0
  async function adjustStructure(changes: Record<string, unknown>) {
    if (!plan || !list) return
    // Save local identity choices before a structural edit rebuilds the plan.
    const saved = await patchQaImport(collectionId, list.id, plan, { matches: plan.matches, column_matches: plan.column_matches })
    acceptPlan(saved)
    const adjusted = await patchQaImport(collectionId, list.id, saved, changes)
    acceptPlan(adjusted)
    if (adjusted.mapping_valid) acceptPlan(await analyzeQaImport(collectionId, list.id, adjusted).catch(() => adjusted))
  }
  async function adjustRows(changes: Record<string, string>) {
    if (!plan || !list) return
    const parents = Object.fromEntries(Object.entries(changes).map(([key, kind]) => {
      const previous = qaParentCandidates(plan, key).filter(row => Number(row.key) < Number(key)).at(-1)?.key || ""
      return [key, kind === "child" ? previous : ""]
    }))
    await adjustStructure({ row_actions: changes, parents })
  }
  function focusReview() {
    if (!plan?.mapping_valid || sourceUnresolved) {
      tableRef.current?.focusRow(plan?.table_rows?.find(row => row.kind === "review")?.key || "")
    } else {
      reviewRef.current?.focusDecision()
    }
  }

  const exportedColumns = existingList?.columns.filter(column => column.included).map(column => column.label).join(" · ")
  const reviewing = mode === "import" && !!plan
  const firstReview = reviewing && list?.questions.length === 0
  const locked = busy || !!plan?.stale
  const displayedRegion = plan?.region

  return <Dialog open={open} onOpenChangeComplete={onOpenChangeComplete} onOpenChange={open => { if (!open && !busy) close() }}>
    <DialogContent className={`qa-dialog qa-exchange-dialog ${reviewing ? "qa-exchange-review" : "qa-exchange-compact"}`}>
      <DialogHeader>
        <DialogTitle>{t(mode === "export" ? "qa.export" : !plan ? "qa.import" :
          firstReview ? "qa.importPreview.firstTitle" : "qa.importPreview.updateTitle")}</DialogTitle>
        {mode === "import" && plan && <div className="qa-import-file-line">
          <FileSpreadsheet size={14} aria-hidden /><p className="qa-exchange-file" title={plan.filename}>{plan.filename}</p>
        </div>}
        {mode === "import" && plan && <div className="qa-import-header-actions">
          <QaActionMenu label={t("qa.importPreview.more")} disabled={busy}>
            <QaMenuItem destructive onClick={() => setCancelConfirm(true)}>{t("qa.cancelImport")}</QaMenuItem>
          </QaActionMenu>
        </div>}
      </DialogHeader>
      {hasDrafts && <p className="qa-notice">{t(mode === "import" ? "qa.importDraftNotice" : "qa.exportDraftNotice")}</p>}
      {error && !reviewing && <div className="qa-notice" role="alert">{error}</div>}
      {mode === "export" && existingList ? <>
        <div className="qa-exchange-export-summary">
          <span className="qa-exchange-file-icon" aria-hidden><FileSpreadsheet /></span>
          <div>
            <h3>{existingList.title}</h3>
            <p className="qa-meta">V{existingList.version.number} · {t("qa.count", { count: existingList.questions.filter(q => q.included).length })}</p>
            {exportedColumns && <p className="qa-exchange-export-columns" title={exportedColumns}>{exportedColumns}</p>}
          </div>
        </div>
        <p className="qa-meta">{t(existingList.original_layout ? "qa.originalLayoutExport" : "qa.exportHelp")}</p>
        <div className="qa-exchange-export-disclosures">
          {!existingList.original_layout && <details><summary>{t("qa.exportSettings")}</summary><div className="qa-export-options">{(["include_number", "include_status"] as const).map(k => <QaToggle key={k} checked={options[k]} onChange={value => setOptions(s => ({ ...s, [k]: value }))} label={t(`qa.exportOption.${k}`)} />)}</div>{existingList.can_edit && onTemplate && <Button variant="link" size="sm" disabled={busy} onClick={() => exit(onTemplate)}>{t("qa.template")}</Button>}</details>}
          {exports.length > 0 && <details><summary>{t("qa.exportHistory")}</summary>{exports.map(e => <button key={e.id} className="qa-version-row" disabled={busy} onClick={() => run(async () => saveQaDownload(await downloadQaExport(collectionId, existingList.id, e.id), "Q&A.xlsx"))}>{new Date(e.created_at).toLocaleString()} · {t("qa.download")}</button>)}</details>}
        </div>
        {existingList.can_edit && <div className="qa-exchange-export-footer"><Button disabled={busy} onClick={() => run(async () => { saveQaDownload(await exportQaFile(collectionId, existingList, options), `${existingList.title}-V${existingList.version.number}.xlsx`); close() })}><Download />{t(busy ? "qa.processing" : "qa.download")}</Button></div>}
      </> : !plan || !list ? <QaWorkbookUpload busy={busy} onFile={file => run(async () => {
        const target = list || await createQaImportList(collectionId, file.name.replace(/\.xlsx$/i, "").slice(0, 200) || "Q&A")
        setList(target)
        const uploaded = await uploadQaImport(collectionId, target, file)
        acceptPlan(uploaded)
        const analyzed = await analyzeQaImport(collectionId, target.id, uploaded).catch(() => uploaded)
        acceptPlan(analyzed)
      })} /> : <div className="qa-import-body" aria-busy={busy && !columnMapping}>
        <section className="qa-import-sheet" aria-label={t("qa.tablePreview")}>
          <div className="qa-import-sheet-bar">
            <DropdownSelect size="sm" aria-label={t("qa.sheet")} value={plan.sheet} menuClassName="qa-exchange-menu"
              onChange={value => { if (value !== plan.sheet) void run(() => adjustStructure({ sheet: value })) }}
              disabled={locked} options={plan.sheets.map(value => ({ value, label: value }))} />
            {displayedRegion && <span className="qa-sheet-range">{excelColumn(displayedRegion.start_column - 1)}{plan.header_row}:{excelColumn(displayedRegion.end_column - 1)}{displayedRegion.end_row}</span>}
            <span className="qa-meta">{t("qa.headerRow")} {plan.header_row}{displayedRegion && displayedRegion.header_end_row > plan.header_row && `–${displayedRegion.header_end_row}`}</span>
            {plan.mapping_confirmed === false && canConfirmQaFields(plan) && <Button size="sm" variant="outline" disabled={locked}
              onClick={() => run(() => adjustStructure({ mapping: plan.mapping }))}>{t("qa.confirmFields")}</Button>}
            {columnMapping && <span className="qa-import-save-status qa-meta" role="status">
              <LoaderCircle size={13} className="animate-spin" aria-hidden />{t("qa.importPreview.savingColumns")}
            </span>}
          </div>
          <details className="qa-region-settings">
            <summary>{t("qa.adjustRegion")}</summary>
            <div>
              <QaField label={t("qa.headerRow")}><Input type="number" value={headerRow} min={1} max={2020} disabled={locked}
                onChange={event => { const value = Number(event.target.value); setHeaderRow(value); setRegion(current => ({ ...current, header_end_row: Math.max(value, current.header_end_row) })) }} /></QaField>
              {(["start_column", "end_column", "header_end_row", "end_row"] as const).map(key => <QaField key={key} label={t(`qa.region.${key}`)}>
                <Input type="number" min={1} max={key.includes("column") ? 100 : 2020} value={region[key]} disabled={locked}
                  onChange={event => { const value = Number(event.target.value); setRegion(current => ({ ...current, [key]: value })) }} />
              </QaField>)}
              <Button size="sm" variant="secondary" disabled={locked} onClick={() => run(() => adjustStructure({ header_row: headerRow, region }))}>{t("qa.applyRecognition")}</Button>
            </div>
          </details>
          <QaImportTable plan={plan} busy={locked} tableRef={tableRef} onRole={changeRole}
            columnMapping={columnMapping || plan.mapping} columnsBusy={!!plan.stale || (busy && !columnMapping)}
            onRow={(key, kind) => { void run(() => adjustRows({ [key]: kind })) }}
            onParent={(key, parent) => { void run(() => adjustStructure({ parents: { [key]: parent } })) }} />
        </section>
        <QaImportReview plan={plan} list={list} busy={locked} duplicate={duplicate} reviewRef={reviewRef} onQuestion={choose} onColumn={chooseColumn} onMerge={chooseMerge}
          onLocate={key => tableRef.current?.focusRow(key)} />
      </div>}
      {error && reviewing && <div className="qa-notice" role="alert">{error}</div>}
      {mode === "import" && plan && list && <div className="qa-import-footer">{cancelConfirm ? <>
        <span className="qa-meta">{t("qa.importPreview.confirmCancel")}</span>
        <div className="qa-import-primary">
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => setCancelConfirm(false)}>{t("qa.importPreview.continueReview")}</Button>
          <Button variant="destructive" size="sm" disabled={busy} onClick={() => run(async () => { await cancelQaImport(collectionId, list.id, plan); close() })}>{t("qa.cancelImport")}</Button>
        </div>
      </> : <>
        <div className="qa-import-secondary">
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => run(async () => { await patchQaImport(collectionId, list.id, plan, { matches: plan.matches, column_matches: plan.column_matches }); close() })}>{t("qa.continueLater")}</Button>
        </div>
        <div className="qa-import-primary">
            {!plan.stale && (sourceUnresolved > 0 || (plan.mapping_valid && unresolved > 0)) && <Button variant="link" size="sm" disabled={busy} onClick={() => run(async () => {
              const saved = await patchQaImport(collectionId, list.id, plan, { matches: plan.matches, column_matches: plan.column_matches })
              acceptPlan(saved); acceptPlan(await analyzeQaImport(collectionId, list.id, saved))
            })}>{t("qa.retryRecognition")}</Button>}
            <QaImportSubmit plan={plan} busy={busy} committing={committing} duplicate={duplicate} onReview={focusReview} onAdjust={focusReview} onRecheck={() => run(async () => {
              const current = planRef.current
              if (!current) return
              const rebased = await patchQaImport(collectionId, list.id, current, { rebase: true, matches: current.matches, column_matches: current.column_matches })
              if (rebased.base_revision === list.revision) {
                acceptPlan(rebased)
                return
              }
              acceptPlan({ ...rebased, stale: true })
              const refreshed = await getQaImportContext(collectionId, list.id)
              setList(refreshed)
              acceptPlan(refreshed.revision === rebased.base_revision ? rebased : { ...rebased, stale: true })
            })} onImport={() => run(async () => {
              setCommitting(true)
              try {
                const updated = await patchQaImport(collectionId, list.id, plan, { matches: plan.matches, column_matches: plan.column_matches })
                acceptPlan(updated)
                onImported(await commitQaImport(collectionId, list.id, updated))
                close()
              } finally { setCommitting(false) }
            })} />
        </div>
      </>}</div>}
    </DialogContent>
  </Dialog>
}
