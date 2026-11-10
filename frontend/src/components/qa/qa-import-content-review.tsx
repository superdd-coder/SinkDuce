import type { QaImportContentChoice, QaImportConflict, QaImportContext, QaImportMergeChanges, QaImportPlan } from "@/api/client"
import { Check } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useT } from "@/i18n/use-t"

type Props = { plan: QaImportPlan; list: QaImportContext; busy: boolean; onMerge: (changes: QaImportMergeChanges) => void; inline?: ReadonlySet<string> }

export function currentChoices(plan: QaImportPlan): Record<string, QaImportContentChoice> {
  const choices: Record<string, QaImportContentChoice> = {}
  for (const item of plan.review?.conflicts || []) {
    const choice = plan.content_choices?.[item.id] || item.choice
    if (choice && item.kind !== "structure") choices[item.id] = choice
  }
  return choices
}

function Content({ text }: { text: string }) {
  const t = useT()
  return <p className="qa-merge-text">{text || t("qa.review.emptyValue")}</p>
}

function Conflict({ item, plan, list, busy, onMerge }: Props & { item: QaImportConflict }) {
  const t = useT()
  function choose(choice: QaImportContentChoice) {
    onMerge({ content_choices: { ...currentChoices(plan), [item.id]: choice } })
  }
  const question = list.questions.find(question => question.id === item.question_id)
  const incoming = plan.rows.find(row => row.key === item.row_key)
  return <article className="qa-import-conflict" data-conflict-id={item.id}>
    <header><div><p className="qa-meta">{item.row_key && <>{t("qa.review.excelRow", { row: item.row_key })} · </>}{item.kind === "membership" ? t("qa.review.membershipChange") : item.label}</p><h4>{question?.summary || question?.text || incoming?.text}</h4></div>{item.choice && <span className="qa-merge-decided"><Check aria-hidden />{t("qa.review.choiceSaved")}</span>}</header>
    <div className="qa-merge-comparison">
      <section data-selected={item.choice === "keep" || undefined}><h5>{t("qa.review.savedContent")}</h5><Content text={item.kind === "membership" ? t("qa.review.membershipRemoved") : item.current} /><Button size="sm" variant={item.choice === "keep" ? "secondary" : "outline"} aria-pressed={item.choice === "keep"} disabled={busy} onClick={() => choose("keep")}>{item.choice === "keep" && <Check aria-hidden />}{t("qa.review.keepSaved")}</Button></section>
      <section data-selected={item.choice === "incoming" || undefined}><h5>{t("qa.review.excelContent")}</h5><Content text={item.kind === "membership" ? t("qa.review.membershipIncluded") : item.incoming} /><Button size="sm" variant={item.choice === "incoming" ? "secondary" : "outline"} aria-pressed={item.choice === "incoming"} disabled={busy} onClick={() => choose("incoming")}>{item.choice === "incoming" && <Check aria-hidden />}{t("qa.review.useExcel")}</Button></section>
    </div>
  </article>
}

/** Only edits that changed independently in both places ask for a choice. */
export function QaImportContentReview({ plan, list, busy, onMerge, inline }: Props) {
  const t = useT()
  const review = plan.review
  if (!review) return null
  const pending = review.conflicts.filter(item => !item.choice && item.kind !== "structure")
  const other = pending.filter(item => !inline?.has(item.id))
  const cleared = review.counts.cleared_replies || 0
  function chooseAll(choice: QaImportContentChoice) {
    onMerge({ content_choices: { ...currentChoices(plan), ...Object.fromEntries(pending.map(item => [item.id, choice])) } })
  }
  return <>
    {!!plan.blank_columns?.length && cleared > 0 && <p className="qa-column-clear">{t("qa.review.explicitClears", { count: cleared })}</p>}
    {pending.length > 1 && <details className="qa-import-bulk">
      <summary>{t("qa.importPreview.bulkChoices")}</summary>
      <div className="qa-merge-bulk"><Button size="sm" variant="ghost" disabled={busy} onClick={() => chooseAll("keep")}>{t("qa.review.keepAllPending")}</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => chooseAll("incoming")}>{t("qa.review.excelAllPending")}</Button></div>
    </details>}
    {other.length > 0 && <section className="qa-import-conflicts" data-qa-import-decision tabIndex={-1} aria-label={t("qa.review.contentConflicts")}>
      <header className="qa-merge-section-heading"><h3>{t("qa.review.contentConflicts")}</h3></header>
      {other.map(item => <Conflict key={item.id} item={item} plan={plan} list={list} busy={busy} onMerge={onMerge} />)}
    </section>}
  </>
}

export function QaImportDecidedEdits({ plan, list, busy, onMerge, inline }: Props) {
  const t = useT()
  const decided = plan.review?.conflicts.filter(item => item.choice && item.kind !== "structure" && !inline?.has(item.id)) || []
  if (!decided.length) return null
  return <section className="qa-review-detail-section"><h3>{t("qa.review.decidedEdits", { count: decided.length })}</h3>{decided.map(item => <Conflict key={item.id} item={item} plan={plan} list={list} busy={busy} onMerge={onMerge} />)}</section>
}

export function QaImportPreservedItems({ plan, busy, onMerge, questionId }: Omit<Props, "list"> & { questionId?: string }) {
  const t = useT()
  const review = plan.review
  if (!review) return null
  if (!questionId) return review.kept_columns.length > 0
    ? <p className="qa-merge-kept-columns">{t("qa.review.keptColumns", { names: review.kept_columns.map(column => column.label).join(", ") })}</p>
    : null
  const question = review.missing_questions.find(item => item.id === questionId)
  if (!question) return null
  function membership(id: string, keep: boolean) {
    const retained = (plan.retained_questions || []).filter(value => value !== id)
    const removed = (plan.removed_questions || []).filter(value => value !== id)
    onMerge({ retained_questions: keep ? [...retained, id] : retained, removed_questions: keep ? removed : [...removed, id] })
  }
  return <div className="qa-import-retain">
    <Button size="sm" variant="secondary" disabled={busy} onClick={() => membership(question.id, !question.kept)}>{t(question.kept ? "qa.review.omitQuestion" : "qa.review.retainQuestion")}</Button>
    {!question.kept && question.reason !== "not_in_file" && <span className="qa-meta">{t(question.reason === "draft" ? "qa.review.historicalDraft" : "qa.review.historicalEdits")}</span>}
  </div>
}
