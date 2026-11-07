import { columnRole, qaParentCandidates } from "@/lib/qa-import-mapping"
import { useState } from "react"
import { ChevronLeft, ChevronRight } from "lucide-react"
import type { QaImportPlan } from "@/api/client"
import { Button } from "@/components/ui/button"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { useT } from "@/i18n/use-t"
import { QaToggle } from "./qa-controls"

export function QaImportTable({ plan, busy, onRole, onRow, onParent, initialReviewOnly = false }: { plan: QaImportPlan; busy: boolean; onRole: (index: number, role: string) => void; onRow: (key: string, role: string) => void; onParent?: (key: string, parent: string) => void; initialReviewOnly?: boolean }) {
  const t = useT()
  const [page, setPage] = useState(0)
  const [reviewOnly, setReviewOnly] = useState(initialReviewOnly)
  const rows = plan.table_rows || plan.rows.map(r => ({ ...r, kind: "question" }))
  const filtered = rows.filter(r => !reviewOnly || r.kind === "review")
  const currentPage = Math.min(page, Math.max(0, Math.ceil(filtered.length / 50) - 1))
  const indexes = plan.headers.flatMap((_, i) => i >= (plan.region?.start_column || 1) - 1 ? [i] : [])
  return <div className="qa-table-review">
    <div className="qa-table-summary"><span>{t("qa.recognizedRows", { count: plan.rows.length })}</span><QaToggle checked={reviewOnly} onChange={value => { setReviewOnly(value); setPage(0) }} label={t("qa.onlyUncertainRows")} /></div>
    <div className="qa-import-grid-scroll" tabIndex={0} aria-label={t("qa.tablePreview")}>
      <table className="qa-import-grid"><thead><tr><th scope="col" className="qa-source-row">{t("qa.sourceRow")}</th>{indexes.map(i => <th scope="col" key={i}><span className="qa-source-heading">{plan.headers[i] || t("qa.unnamedColumn", { number: i + 1 })}</span>
        <DropdownSelect size="sm" value={columnRole(plan.mapping, i)} disabled={busy} aria-label={t("qa.columnRole", { name: plan.headers[i] || String(i + 1) })} onChange={role => onRole(i, role)} options={["question", "number", "reply", "status", "group", "type", "extra", "ignore"].filter(role => i !== plan.mapping.question || role === "question").map(value => ({ value, label: t(`qa.field.${value}`) }))} />
      </th>)}</tr></thead><tbody>{filtered.slice(currentPage * 50, currentPage * 50 + 50).map(row => <tr key={row.key} data-row-kind={row.kind}>
        <th scope="row" className="qa-source-row"><span>{row.key}</span><DropdownSelect size="sm" value={row.kind === "review" && plan.row_actions?.[row.key] === "child" ? "child" : row.kind} aria-label={t("qa.rowRole", { number: row.key })} disabled={busy} onChange={role => onRow(row.key, role)} options={["question", "child", "section", "note", "header", "continuation", "exclude", "review"].map(value => ({ value, label: t(`qa.rowKind.${value}`) }))} />
          {(row.kind === "child" || plan.row_actions?.[row.key] === "child") && onParent && <DropdownSelect size="sm" aria-label={t("qa.rowParent")} value={plan.parents?.[row.key] || ("parent_key" in row ? row.parent_key as string : "") || ""} disabled={busy} onChange={value => onParent(row.key, value)} options={[{ value: "", label: t("qa.rowParent") }, ...qaParentCandidates(plan, row.key).map(r => ({ value: r.key, label: `${r.key} · ${r.text}` }))]} />}
        </th>
        {indexes.map(i => <td key={i}>{row.values[i]?.length > 160 ? <details className="qa-cell-detail"><summary>{row.values[i].slice(0, 160)}…</summary><p>{row.values[i]}</p></details> : <span>{row.values[i] || <span className="qa-empty-cell">—</span>}</span>}</td>)}
      </tr>)}</tbody></table>
      {!filtered.length && <p className="qa-empty">{t("qa.noRowsToReview")}</p>}
    </div>
    <div className="qa-table-pagination"><span>{filtered.length ? currentPage * 50 + 1 : 0}–{Math.min(filtered.length, (currentPage + 1) * 50)} / {filtered.length}</span><Button variant="ghost" size="icon-sm" aria-label={t("qa.previousRows")} disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}><ChevronLeft /></Button><Button variant="ghost" size="icon-sm" aria-label={t("qa.nextRows")} disabled={(currentPage + 1) * 50 >= filtered.length} onClick={() => setPage(currentPage + 1)}><ChevronRight /></Button></div>
  </div>
}
