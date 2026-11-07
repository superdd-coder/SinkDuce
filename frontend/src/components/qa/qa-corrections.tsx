import { useQaModal } from "./use-qa-modal"
import { useState } from "react"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { QaField } from "./qa-controls"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { useT } from "@/i18n/use-t"
import { applyQaCorrection, previewQaCorrection, type QaCorrection, type QaImportRecord, type QaListDetail } from "@/api/client"
import "./qa-settings-refinement.css"

export function QaCorrections({ collectionId, list, record, close: onClosed, onChanged }: { collectionId: string; list: QaListDetail; record: QaImportRecord; close: () => void; onChanged: (list: QaListDetail) => void }) {
  const { open, close, onOpenChangeComplete } = useQaModal(onClosed)
  const t = useT()
  const iid = record.id
  const [row, setRow] = useState("")
  const [target, setTarget] = useState("")
  const [drafts, setDrafts] = useState<"keep" | "move">("keep")
  const [preview, setPreview] = useState<QaCorrection | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  async function run(work: () => Promise<void>) { setBusy(true); setError(""); try { await work() } catch { setError(t("qa.correctionFailed")) } finally { setBusy(false) } }
  const replies = preview?.replies.filter(r => r.body) || []
  return <Dialog open={open} onOpenChangeComplete={onOpenChangeComplete} onOpenChange={open => { if (!open && !busy) close() }}><DialogContent className="qa-dialog qa-refinement-dialog sm:max-w-2xl"><DialogHeader><DialogTitle>{t("qa.correctMatch")}</DialogTitle><DialogDescription>{t("qa.correctionHelp")}</DialogDescription></DialogHeader>
    <p className="qa-meta qa-refinement-filename" title={record.filename}>{record.filename}</p>{error && <p className="qa-notice" role="alert">{error}</p>}
    {preview ? <><dl className="qa-match-texts qa-refinement-comparison"><div><dt>{t("qa.currentContent")}</dt><dd>{preview.text}</dd></div><div><dt>{t("qa.continueIdentity")}</dt><dd>{preview.target_text || t("qa.asNew")}</dd></div></dl>
      {replies.length > 0 && <dl className="qa-refinement-replies">{replies.map((r, i) => <div key={i}><dt>{r.label}</dt><dd>{r.body}</dd></div>)}</dl>}
      {!!Object.keys(preview.overrides).length && <p className="qa-notice">{t("qa.keepManualChanges")}</p>}{preview.draft_count > 0 && <p className="qa-meta">{t(drafts === "move" ? "qa.moveDrafts" : "qa.keepDraftReferences")}</p>}
      <div className="qa-refinement-footer"><Button variant="ghost" disabled={busy} onClick={() => setPreview(null)}>{t("common.back")}</Button><Button disabled={busy} onClick={() => run(async () => { onChanged(await applyQaCorrection(collectionId, list, iid, preview.id)); close() })}>{t("qa.confirmCorrection")}</Button></div>
    </> : <div className="qa-form">
      <QaField label={t("qa.question")}><DropdownSelect aria-label={t("qa.question")} value={row} onChange={setRow} placeholder={t("qa.chooseQuestion")} options={(record?.rows || []).map(r => ({ value: r.key, label: r.text }))} /></QaField>
      <QaField label={t("qa.continueIdentity")}><DropdownSelect aria-label={t("qa.continueIdentity")} value={target} onChange={setTarget} placeholder={t("qa.chooseHistory")} options={[{ value: "new", label: t("qa.asNew") }, ...list.questions.filter(q => !q.included).map(q => ({ value: q.id, label: q.text }))]} /></QaField>
      <details className="qa-refinement-disclosure"><summary>{t("qa.affectedDrafts")}</summary><DropdownSelect aria-label={t("qa.affectedDrafts")} value={drafts} onChange={value => setDrafts(value as "keep" | "move")} options={[{ value: "keep", label: t("qa.keepDraftReferences") }, { value: "move", label: t("qa.moveDrafts") }]} /></details>
      <div className="qa-refinement-footer"><Button disabled={!row || !target || busy} onClick={() => run(async () => setPreview(await previewQaCorrection(collectionId, list, iid, row, target, drafts)))}>{t("qa.previewCorrection")}</Button></div></div>}
  </DialogContent></Dialog>
}
