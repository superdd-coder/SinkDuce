import type { QaImportPlan } from "@/api/client"
import { Button } from "@/components/ui/button"
import { useT } from "@/i18n/use-t"

export function QaImportSubmit({ plan, busy, duplicate, onReview, onAdjust, onImport, onRecheck }: {
  plan: QaImportPlan; busy: boolean; duplicate: boolean;
  onReview: () => void; onAdjust: () => void; onImport: () => void; onRecheck?: () => void;
}) {
  const t = useT()
  const rows = (plan.table_rows || []).filter(row => row.kind === "review").length
  const matches = plan.rows.filter(row => !plan.matches[row.key]).length + plan.mapping.replies.filter(index => !plan.column_matches[String(index)]).length
  const conflicts = plan.review?.unresolved || 0
  const reason = plan.stale ? "stalePlan" : rows ? rows === 1 ? "rowBlocked" : "rowsBlocked" : !plan.mapping_valid ? "columnsBlocked" : duplicate ? "duplicateBlocked" : matches ? "matchesBlocked" : conflicts ? conflicts === 1 ? "editBlocked" : "editsBlocked" : ""
  const count = rows || matches || conflicts
  const action = plan.stale ? onRecheck || onReview : rows ? onReview : !plan.mapping_valid ? onAdjust : duplicate || matches || conflicts ? onReview : onImport
  const label = plan.stale ? "stalePlanAction" : rows ? rows === 1 ? "reviewRow" : "reviewRows" : !plan.mapping_valid ? "adjustTable" : duplicate || matches ? "reviewMatches" : conflicts ? conflicts === 1 ? "reviewEdit" : "reviewEdits" : "importCount"
  return <div className="qa-import-submit">
    {reason && <p className="qa-meta" role="status">{t(`qa.review.${reason}`, { count })}</p>}
    <Button size="sm" disabled={busy} onClick={action}>{t(busy ? "qa.processing" : `qa.review.${label}`, { count: reason ? count : plan.rows.length })}</Button>
  </div>
}
