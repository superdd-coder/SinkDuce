import type { QaImportPlan } from "@/api/client"
import { LoaderCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useT } from "@/i18n/use-t"

export function QaImportSubmit({ plan, busy, committing, duplicate, onReview, onAdjust, onImport, onRecheck }: {
  plan: QaImportPlan; busy: boolean; committing?: boolean; duplicate: boolean
  onReview: () => void; onAdjust: () => void; onImport: () => void; onRecheck?: () => void
}) {
  const t = useT()
  const rows = (plan.table_rows || []).filter(row => row.kind === "review").length
  const matches = plan.rows.filter(row => !plan.matches[row.key]).length + plan.mapping.replies.filter(index => !plan.column_matches[String(index)]).length
  const conflicts = plan.review?.conflicts.filter(item => !item.choice && item.kind !== "structure").length || 0
  const reason = plan.stale ? "stalePlan" : rows ? rows === 1 ? "rowBlocked" : "rowsBlocked" : !plan.mapping_valid ? "columnsBlocked"
    : duplicate ? "duplicateBlocked" : matches ? "matchesBlocked" : conflicts ? conflicts === 1 ? "editBlocked" : "editsBlocked" : ""
  const count = rows || matches || conflicts
  const locate = plan.stale ? onRecheck || onReview : !plan.mapping_valid && !rows ? onAdjust : onReview
  return <div className="qa-import-submit">
    {reason && <>
      <p className="qa-meta" role="status">{t(`qa.review.${reason}`, { count })}</p>
      <Button size="sm" variant="outline" disabled={busy} onClick={locate}>
        {t(plan.stale ? "qa.review.stalePlanAction" : "qa.importPreview.reviewPending")}
      </Button>
    </>}
    <Button size="sm" disabled={busy || !!reason} onClick={onImport}>
      {committing && <LoaderCircle className="size-3.5 animate-spin" aria-hidden />}
      {t(committing ? "qa.importPreview.importing" : "qa.confirmImport")}
    </Button>
  </div>
}
