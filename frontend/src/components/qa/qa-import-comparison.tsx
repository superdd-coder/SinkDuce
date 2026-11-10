import { useState, type ReactNode } from "react"
import { CircleAlert } from "lucide-react"
import type { QaImportConflict, QaImportContentChoice } from "@/api/client"
import { Button } from "@/components/ui/button"
import { useT } from "@/i18n/use-t"
import { QaImportExcerpt } from "./qa-import-excerpt"

type Side = { label: string; body: string }

export function QaImportComparison({
  current, incoming, position, single, status, currentControl, currentAction, incomingStatus, pending, conflict, onChoice, busy,
}: {
  current?: Side
  incoming?: Side
  position?: number
  single?: boolean
  status?: string
  currentControl?: ReactNode
  currentAction?: ReactNode
  incomingStatus?: string
  pending?: boolean
  conflict?: QaImportConflict
  onChoice?: (value: QaImportContentChoice) => void
  busy: boolean
}) {
  const t = useT()
  const [changing, setChanging] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const choice = conflict?.choice
  const deciding = !!conflict && !!onChoice && (!choice || changing)
  const isReply = position !== undefined
  const needsReview = pending || !!conflict && !choice
  const number = isReply && <span className="qa-reply-number" aria-hidden="true">{String(position).padStart(2, "0")}</span>
  function toggleExcerpt() { setExpanded(value => !value) }
  function action(value: QaImportContentChoice) {
    if (!deciding) return null
    const label = value === "keep" ? (isReply ? "qa.importPreview.keepReply" : "qa.review.keepSaved")
      : (isReply ? "qa.importPreview.useExcelReply" : "qa.review.useExcel")
    return <Button size="sm" variant="outline" className="qa-import-version-choice" disabled={busy} aria-pressed={choice === value} onClick={() => {
      setChanging(false)
      onChoice?.(value)
    }}>{t(label)}</Button>
  }
  function side(value: Side | undefined, name: QaImportContentChoice) {
    const old = name === "keep"
    const body = deciding ? (old ? conflict?.current : conflict?.incoming) : value?.body
    const attention = !old && needsReview
    const badge = old ? status : attention ? t("qa.importPreview.pending") : incomingStatus
    const title = old && currentControl ? currentControl : value?.label
    const present = !!(value || title || deciding)
    return <div className={`qa-import-side${present ? old ? " qa-import-current" : " qa-import-incoming" : ""}`}>
      {(title || badge || (present && number)) && <div className="qa-import-side-label">
        {number}
        <div className="qa-import-side-heading">
          {title && <div className="qa-import-side-title">{title}</div>}
          {badge && <span className={`qa-import-card-mark${attention ? " is-pending" : old ? " is-muted" : ""}`}>
            {attention && <CircleAlert size={14} aria-hidden />}{badge}
          </span>}
        </div>
      </div>}
      {body ? <QaImportExcerpt text={body} formatted={isReply} expanded={expanded} onToggle={toggleExcerpt} />
        : deciding && <span className="qa-meta">{t("qa.review.emptyValue")}</span>}
      {old && currentAction}
      {action(name)}
    </div>
  }
  return <section className="qa-import-round" data-conflict-id={conflict?.id}
    data-qa-import-decision={needsReview ? "" : undefined} tabIndex={needsReview ? -1 : undefined}>
    {deciding && <p className="qa-meta qa-import-conflict-note">{t(isReply ? "qa.importPreview.replyConflict" : "qa.importPreview.questionConflict")}</p>}
    {single ? side(incoming, "incoming")
      : <div className="qa-import-pair">{side(current, "keep")}{side(incoming, "incoming")}</div>}
    {conflict && choice && !deciding && <div className="qa-import-choice-result qa-meta">
      {t(choice === "keep" ? "qa.importPreview.keeping" : "qa.importPreview.usingExcel")}
      {onChoice && <Button size="sm" variant="link" disabled={busy} onClick={() => setChanging(true)}>{t("qa.review.change")}</Button>}
    </div>}
  </section>
}
