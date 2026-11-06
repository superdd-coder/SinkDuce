import type { QaImportPlan } from "@/api/client"
import { useT } from "@/i18n/use-t"
import { CornerDownRight } from "lucide-react"

export function QaImportResult({ plan, foldLongCells = true }: { plan: QaImportPlan; foldLongCells?: boolean }) {
  const t = useT()
  const rows = (plan.table_rows || []).filter(row => !["header", "exclude", "continuation"].includes(row.kind))
  const headers = plan.headers || Array.from({ length: Math.max(0, ...rows.map(row => row.values.length)) }, () => "")
  const indexes = headers.flatMap((_, i) => i >= (plan.region?.start_column || 1) - 1 && !plan.mapping.ignored?.includes(i) ? [i] : [])
  return <div className="qa-import-result" aria-label={t("qa.recognizedPreview")} tabIndex={0}>
    <table className="qa-import-grid qa-result-grid">
      <thead><tr><th scope="col" className="qa-source-row">{t("qa.sourceRow")}</th>{indexes.map(i => <th scope="col" key={i}>{headers[i] || t("qa.unnamedColumn", { number: i + 1 })}</th>)}</tr></thead>
      <tbody>{rows.map(row => {
        const logical = plan.rows.find(r => r.key === row.key)
        const structural = row.kind === "section" || row.kind === "note"
        return <tr key={row.key} data-row-kind={row.kind}>
          <th scope="row" className="qa-source-row"><span>{row.key}</span><span className="qa-meta">{t(`qa.rowKind.${row.kind}`)}</span></th>
          {structural ? <td colSpan={indexes.length} className={`qa-result-${row.kind}`}><p>{row.values.filter((v, i) => v && i !== plan.mapping.number).join("\n")}</p></td> : indexes.map(i => {
            const value = i === plan.mapping.question ? logical?.text || row.values[i] : logical?.values?.[i] || row.values[i]
            return <td key={i}>{i === plan.mapping.question && row.kind === "child" && <CornerDownRight className="size-3.5" />}{foldLongCells && value?.length > 240 ? <details className="qa-cell-detail"><summary>{value.slice(0, 240)}…</summary><p>{value}</p></details> : <span>{value || <span className="qa-empty-cell">—</span>}</span>}</td>
          })}
        </tr>
      })}</tbody>
    </table>
  </div>
}
