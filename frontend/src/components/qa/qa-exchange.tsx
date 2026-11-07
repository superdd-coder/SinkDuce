import { useQaModal } from "./use-qa-modal"
import { QaWorkbookUpload } from "./qa-workbook-upload"
import { QaImportReview } from "./qa-import-review"
import { QaImportSubmit } from "./qa-import-submit"
import { QaImportTable } from "./qa-import-table"
import { canConfirmQaFields, changeColumnRole, qaParentCandidates } from "@/lib/qa-import-mapping"
import { saveQaDownload } from "@/lib/qa-download"
import { useEffect, useRef, useState } from "react"
import { Download, FileSpreadsheet } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { QaField, QaToggle } from "./qa-controls"
import { Input } from "@/components/ui/input"
import { useAppStore } from "@/stores/app-store"
import { useT } from "@/i18n/use-t"
import { analyzeQaImport, cancelQaImport, createQaImportList, commitQaImport, exportQaFile, getQaTemplates, getQaExports, downloadQaExport, patchQaImport, uploadQaImport, type QaExportRecord, type QaImportContext, type QaImportPlan, type QaImportMergeChanges, type QaListDetail } from "@/api/client"
import "./qa-exchange-refinement.css"

export function QaExchange({ collectionId, list: existingList, mode, close: onClosed, onImported, resume, onTemplate }: { collectionId: string; list: QaListDetail | null; mode: "import" | "export"; close: () => void; onImported: (list: QaListDetail) => void; resume?: { list: QaImportContext; plan: QaImportPlan }; onTemplate?: () => void }) {
  const { open, close, onOpenChangeComplete, exit } = useQaModal(onClosed)
  const t = useT()
  const hasDrafts = useAppStore(s => !!existingList && Object.keys(s.qaDrafts).some(key => key.includes(`/${existingList.id}/`)))
  const [list, setList] = useState<QaImportContext | null>(resume?.list || existingList)
  const [exports, setExports] = useState<QaExportRecord[]>([])
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const [error, setError] = useState("")
  const [plan, setPlan] = useState<QaImportPlan | null>(resume?.plan || null)
  const planRef = useRef(plan)
  const [step, setStep] = useState<"mapping" | "review">("review")
  const reviewRef = useRef<HTMLDivElement>(null)

  const [headerRow, setHeaderRow] = useState(resume?.plan.header_row || 1)
  const [region, setRegion] = useState(resume?.plan.region || { start_column: 1, end_column: 1, header_end_row: 1, end_row: 1 })
  const [sheet, setSheet] = useState(resume?.plan.sheet || "")
  const [options, setOptions] = useState({ include_number: true, include_status: false, include_type: false, resequence: false })

  useEffect(() => {
    if (mode !== "export" || !existingList) return
    if (!existingList.original_layout) getQaTemplates(collectionId).then(rows => {
      const template = rows.find(r => r.id === existingList.template_id)
      if (template) setOptions({ include_number: template.include_number ?? true, include_status: template.include_status ?? false, include_type: template.include_type ?? false, resequence: template.resequence ?? false })
    }).catch(() => {})
    getQaExports(collectionId, existingList.id).then(setExports).catch(() => {})
  }, [collectionId, existingList, mode])

  function acceptPlan(next: QaImportPlan) {
    planRef.current = next
    setPlan(next); setHeaderRow(next.header_row); setSheet(next.sheet)
    setRegion(next.region || { start_column: 1, end_column: next.headers.length, header_end_row: next.header_row, end_row: Number(next.rows.at(-1)?.key || next.header_row) })
  }
  async function run(work: () => Promise<void>) {
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true); setError("")
    try { await work() } catch (e) {
      const message = e instanceof Error ? e.message : ""
      const code = ["stale_import", "duplicate_identity_assignment", "unresolved_match", "unresolved_content", "formula_without_cached_value", "parse_limit_exceeded", "invalid_mapping", "invalid_parent", "question_required", "empty_workbook", "unsupported_layout_change", "source_unavailable"].find(k => message.includes(k))
      if (code === "stale_import" && planRef.current) acceptPlan({ ...planRef.current, stale: true })
      setError(t(code ? `qa.importError.${code}` : "qa.failed"))
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
    const next = { ...current, [kind]: { ...current[kind], [key]: value }, content_choices: {}, blank_columns: [] }
    acceptPlan(next)
    if (!validIdentityState(next)) return
    void run(async () => {
      const saved = await patchQaImport(collectionId, list.id, next, {
        matches: next.matches, column_matches: next.column_matches, content_choices: {}, blank_columns: [],
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
  const sourceUnresolved = plan?.table_rows?.filter(row => row.kind === "review").length || 0
  function focusReview() {
    const review = reviewRef.current
    const target = review?.querySelector<HTMLElement>("[data-qa-import-decision]")
    if (target) {
      target.focus({ preventScroll: true })
      target.scrollIntoView({ block: "nearest" })
    } else {
      review?.scrollTo({ top: 0 })
      review?.focus({ preventScroll: true })
    }
  }
  async function adjustStructure(changes: Record<string, unknown>, analyze = true) {
    if (!plan || !list) return
    // Save local identity choices before a structural edit rebuilds the plan.
    const saved = await patchQaImport(collectionId, list.id, plan, { matches: plan.matches, column_matches: plan.column_matches })
    acceptPlan(saved)
    const adjusted = await patchQaImport(collectionId, list.id, saved, changes)
    acceptPlan(adjusted)
    if (analyze && adjusted.mapping_valid) acceptPlan(await analyzeQaImport(collectionId, list.id, adjusted).catch(() => adjusted))
  }
  async function adjustRows(changes: Record<string, string>) {
    if (!plan || !list) return
    const parents = Object.fromEntries(Object.entries(changes).map(([key, kind]) => {
      const previous = qaParentCandidates(plan, key).filter(row => Number(row.key) < Number(key)).at(-1)?.key || ""
      return [key, kind === "child" ? previous : ""]
    }))
    await adjustStructure({ row_actions: changes, parents }, step === "review")
  }

  const exportedColumns = existingList?.columns.filter(column => column.included).map(column => column.label).join(" · ")

  return <Dialog open={open} onOpenChangeComplete={onOpenChangeComplete} onOpenChange={open => { if (!open && !busy) close() }}>
    <DialogContent className={`qa-dialog qa-exchange-dialog ${mode === "import" && plan ? step === "review" ? "qa-exchange-review" : "qa-exchange-wide" : "qa-exchange-compact"}`}>
      <DialogHeader>
        <DialogTitle>{t(mode === "export" ? "qa.export" : plan ? step === "mapping" ? "qa.review.sourceSettings" : "qa.confirmImport" : "qa.import")}</DialogTitle>
        {mode === "import" && plan && <p className="qa-exchange-file" title={plan.filename}>{plan.filename}</p>}
      </DialogHeader>
      {hasDrafts && <p className="qa-notice">{t(mode === "import" ? "qa.importDraftNotice" : "qa.exportDraftNotice")}</p>}
      {error && <div className="qa-notice" role="alert">{error}</div>}
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
          {!existingList.original_layout && <details><summary>{t("qa.exportSettings")}</summary><div className="qa-export-options">{(["include_number", "include_status", "include_type"] as const).map(k => <QaToggle key={k} checked={options[k]} onChange={value => setOptions(s => ({ ...s, [k]: value }))} label={t(`qa.exportOption.${k}`)} />)}</div>{existingList.can_edit && onTemplate && <Button variant="link" size="sm" disabled={busy} onClick={() => exit(onTemplate)}>{t("qa.template")}</Button>}</details>}
          {exports.length > 0 && <details><summary>{t("qa.exportHistory")}</summary>{exports.map(e => <button key={e.id} className="qa-version-row" disabled={busy} onClick={() => run(async () => saveQaDownload(await downloadQaExport(collectionId, existingList.id, e.id), "Q&A.xlsx"))}>{new Date(e.created_at).toLocaleString()} · {t("qa.download")}</button>)}</details>}
        </div>
        {existingList.can_edit && <div className="qa-exchange-export-footer"><Button disabled={busy} onClick={() => run(async () => { saveQaDownload(await exportQaFile(collectionId, existingList, options), `${existingList.title}-V${existingList.version.number}.xlsx`); close() })}><Download />{t(busy ? "qa.processing" : "qa.download")}</Button></div>}
      </> : !plan || !list ? <QaWorkbookUpload busy={busy} onFile={file => run(async () => {
        const target = list || await createQaImportList(collectionId, file.name.replace(/\.xlsx$/i, "").slice(0, 200) || "Q&A")
        setList(target)
        const uploaded = await uploadQaImport(collectionId, target, file)
        acceptPlan(uploaded)
        const analyzed = await analyzeQaImport(collectionId, target.id, uploaded).catch(() => uploaded)
        acceptPlan(analyzed); setStep("review")
      })} /> : step === "mapping" ? <>
        <div className="qa-import-source">
          <QaField label={t("qa.sheet")}><DropdownSelect aria-label={t("qa.sheet")} value={sheet} onChange={value => { setSheet(value); void run(async () => acceptPlan(await patchQaImport(collectionId, list.id, plan, { sheet: value }))) }} disabled={busy} options={plan.sheets.map(value => ({ value, label: value }))} /></QaField>
          <QaField label={t("qa.headerRow")}><Input type="number" value={headerRow} min={1} max={2020} disabled={busy} onChange={e => { const value = Number(e.target.value); setHeaderRow(value); setRegion(r => ({ ...r, header_end_row: Math.max(value, r.header_end_row) })) }} /></QaField>
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(async () => acceptPlan(await patchQaImport(collectionId, list.id, plan, { sheet, header_row: headerRow, region })))}>{t("qa.refreshPreview")}</Button>
        </div>
        <details className="qa-region-settings"><summary>{t("qa.adjustRegion")}</summary><div>
          {(["start_column", "end_column", "header_end_row", "end_row"] as const).map(key => <QaField key={key} label={t(`qa.region.${key}`)}><Input type="number" min={1} max={key.includes("column") ? 100 : 2020} value={region[key]} disabled={busy} onChange={e => setRegion(r => ({ ...r, [key]: Number(e.target.value) }))} /></QaField>)}
        </div></details>
        {plan.ai_status !== "ready" && <p className="qa-meta">{t("qa.structureFallback")}</p>}
        <QaImportTable plan={plan} busy={busy} onRole={(index, role) => { void run(() => adjustStructure({ mapping: changeColumnRole(plan, index, role) }, false)) }} onRow={(key, kind) => { void run(() => adjustRows({ [key]: kind })) }} onParent={(key, parent) => { void run(() => adjustStructure({ parents: { [key]: parent } }, false)) }} />
        {!plan.mapping_valid && <p className="qa-meta" role="status">{t("qa.resolveRows")}</p>}
      </> : <>
        <QaImportReview plan={plan} list={list} busy={busy || !!plan.stale} duplicate={duplicate} reviewRef={reviewRef} onQuestion={choose} onColumn={chooseColumn} onMerge={chooseMerge} onAdjust={() => setStep("mapping")}
          onRows={changes => { void run(() => adjustRows(changes)) }}
          onRole={(index, role) => { void run(() => adjustStructure({ mapping: changeColumnRole(plan, index, role) })) }}
          onParent={(key, parent) => { void run(() => adjustStructure({ parents: { [key]: parent } })) }} />
      </>}
      {mode === "import" && plan && list && <div className="qa-import-footer">
        <div className="qa-import-secondary">
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => run(async () => { await patchQaImport(collectionId, list.id, plan, { matches: plan.matches, column_matches: plan.column_matches }); close() })}>{t("qa.continueLater")}</Button>
          <Button variant="link" size="sm" disabled={busy} onClick={() => run(async () => { await cancelQaImport(collectionId, list.id, plan); close() })}>{t("qa.cancelImport")}</Button>
        </div>
        <div className="qa-import-primary">
          {step === "mapping" ? <Button size="sm" disabled={busy} onClick={() => run(async () => {
            if (!canConfirmQaFields(plan)) { setStep("review"); return }
            const mapped = await patchQaImport(collectionId, list.id, plan, { mapping: plan.mapping })
            acceptPlan(mapped); if (!mapped.mapping_valid) return; const analyzed = await analyzeQaImport(collectionId, list.id, mapped).catch(() => mapped); acceptPlan(analyzed); setStep("review")
          })}>{t(busy ? "qa.processing" : "qa.review.backToReview")}</Button> : <>
            {!plan.stale && (unresolved > 0 || sourceUnresolved > 0) && <Button variant="link" size="sm" disabled={busy} onClick={() => run(async () => {
              const saved = await patchQaImport(collectionId, list.id, plan, { matches: plan.matches, column_matches: plan.column_matches })
              acceptPlan(saved); acceptPlan(await analyzeQaImport(collectionId, list.id, saved))
            })}>{t("qa.retryRecognition")}</Button>}
            <QaImportSubmit plan={plan} busy={busy} duplicate={duplicate} onReview={focusReview} onAdjust={() => setStep("mapping")} onRecheck={() => run(async () => {
              const current = planRef.current
              if (!current) return
              acceptPlan(await patchQaImport(collectionId, list.id, current, { rebase: true, matches: current.matches, column_matches: current.column_matches }))
            })} onImport={() => run(async () => {
              const updated = await patchQaImport(collectionId, list.id, plan, { matches: plan.matches, column_matches: plan.column_matches })
              acceptPlan(updated); onImported(await commitQaImport(collectionId, list.id, updated)); close()
            })} />
          </>}
        </div>
      </div>}
    </DialogContent>
  </Dialog>
}
