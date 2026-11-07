import { useEffect, useRef, useState, type Ref } from "react"
import { ArrowRight, Check, Plus, Search } from "lucide-react"
import type { QaImportContext, QaImportMergeChanges, QaImportPlan } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { qaImportSummary, qaReplyColumnImpact } from "@/lib/qa-import-review"
import { useT } from "@/i18n/use-t"
import { QaImportResult } from "./qa-import-result"
import { QaImportTable } from "./qa-import-table"
import { QaImportContentReview, QaImportDecidedEdits, QaImportPreservedItems } from "./qa-import-content-review"

type Props = {
  plan: QaImportPlan; list: QaImportContext; busy: boolean;
  onQuestion: (key: string, target: string) => void;
  onColumn: (key: string, target: string) => void;
  onAdjust: () => void;
  onMerge?: (changes: QaImportMergeChanges) => void;
  onRows: (changes: Record<string, string>) => void;
  onRole: (index: number, role: string) => void;
  onParent: (key: string, parent: string) => void;
  reviewRef?: Ref<HTMLDivElement>;
  duplicate?: boolean;
}

function Excerpt({ text }: { text: string }) {
  const t = useT()
  return <p className="qa-decision-text">{text || t("qa.review.emptyReply")}</p>
}

function repeatedKeys(keys: string[], matches: Record<string, string | null>) {
  const counts = new Map<string, number>()
  for (const key of keys) {
    const target = matches[key]
    if (target && target !== "new") counts.set(target, (counts.get(target) || 0) + 1)
  }
  return new Set(keys.filter(key => (counts.get(matches[key] || "") || 0) > 1))
}

function ColumnImpact({ plan, list, index, target }: { plan: QaImportPlan; list: QaImportContext; index: number; target: string }) {
  const t = useT()
  const impact = qaReplyColumnImpact(plan, list, index, target)
  return !plan.review && impact.cleared > 0
    ? <p className="qa-column-clear" role="note">{t(impact.cleared === 1 ? "qa.review.clearsOne" : "qa.review.clearsReplies", { count: impact.cleared })}</p>
    : null
}

function ReplyDecision({ columnKey, plan, list, busy, onColumn, onCancel, duplicate = false }: Pick<Props, "plan" | "list" | "busy" | "onColumn"> & { columnKey: string; onCancel?: () => void; duplicate?: boolean }) {
  const t = useT()
  const used = new Set(Object.entries(plan.column_matches).filter(([key]) => key !== columnKey).map(([, target]) => target))
  const available = list.columns.filter(c => !used.has(c.id))
  const index = Number(columnKey)
  const impact = qaReplyColumnImpact(plan, list, index, "new")
  return <article className="qa-import-decision" data-qa-import-decision tabIndex={-1}>
    <header className="qa-decision-heading"><h3>{t("qa.review.replyQuestion", { name: plan.headers[index] })}</h3>{onCancel && <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>{t("qa.cancel")}</Button>}</header>
    {duplicate && <p className="qa-decision-hint" role="alert">{t("qa.review.duplicateColumn")}</p>}
    {impact.empty ? <p className="qa-decision-hint">{t("qa.review.emptyColumn")}</p> : <p className="qa-decision-hint">{t("qa.review.columnFilled", { count: impact.filled })}</p>}
    <div className="qa-column-destinations">
      {available.map(column => <div key={column.id}>
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => onColumn(columnKey, column.id)}><ArrowRight />{t("qa.review.writeColumn", { name: column.label })}</Button>
          <ColumnImpact plan={plan} list={list} index={index} target={column.id} />
        </div>)}
      <div><Button size="sm" variant="secondary" disabled={busy} onClick={() => onColumn(columnKey, "new")}><Plus />{t("qa.review.addRound")}</Button></div>
    </div>
  </article>
}

function QuestionDecision({ row, plan, list, busy, onQuestion, onCancel, duplicate = false }: Pick<Props, "plan" | "list" | "busy" | "onQuestion"> & { row: QaImportPlan["rows"][number]; onCancel?: () => void; duplicate?: boolean }) {
  const t = useT()
  const suggested = plan.matches[row.key] || plan.suggestions?.[row.key]?.target
  const [choice, setChoice] = useState(suggested && suggested !== "new" ? suggested : "")
  const [searching, setSearching] = useState(false)
  const [query, setQuery] = useState("")
  const confirmRef = useRef<HTMLButtonElement>(null)
  const focusConfirmation = useRef(false)
  const used = new Set(Object.entries(plan.matches).filter(([key]) => key !== row.key).map(([, target]) => target))
  const available = list.questions.filter(q => !used.has(q.id))
  const target = available.find(q => q.id === choice)
  const candidates = available.filter(q => `${q.display_number || ""} ${q.text} ${q.summary || ""}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  useEffect(() => {
    if (focusConfirmation.current && !searching && target) {
      confirmRef.current?.focus()
      focusConfirmation.current = false
    }
  }, [searching, target])
  return <article className="qa-import-decision" data-qa-import-decision tabIndex={-1}>
    <header className="qa-decision-heading"><h3>{t("qa.review.questionQuestion")}</h3>{onCancel && <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>{t("qa.cancel")}</Button>}</header>
    {duplicate && <p className="qa-decision-hint" role="alert">{t("qa.review.duplicateQuestion")}</p>}
    <div className="qa-reply-comparison">
      {target && <div><span className="pm-label">{t("qa.historical")}</span><Excerpt text={target.text} /></div>}
      <div><span className="pm-label">{t("qa.incoming")}</span><Excerpt text={row.text} /></div>
    </div>
    <div className="qa-decision-actions">
      {target && <Button ref={confirmRef} size="sm" disabled={busy} onClick={() => onQuestion(row.key, target.id)}><Check />{t("qa.review.sameQuestion")}</Button>}
      <Button size="sm" variant="secondary" disabled={busy} onClick={() => onQuestion(row.key, "new")}><Plus />{t("qa.review.newQuestion")}</Button>
      {available.length > 0 && <Button size="sm" variant="ghost" disabled={busy} aria-expanded={searching} onClick={() => setSearching(value => !value)}><Search />{t("qa.review.findQuestion")}</Button>}
    </div>
    {searching && <div className="qa-question-picker">
      <Input autoFocus aria-label={t("qa.review.searchExisting")} placeholder={t("qa.review.searchExisting")} value={query} disabled={busy} onChange={event => setQuery(event.target.value)} />
      <div role="group" aria-label={t("qa.historical")}>{candidates.map(candidate => <button type="button" key={candidate.id} disabled={busy} onClick={() => { focusConfirmation.current = true; setChoice(candidate.id); setSearching(false) }}>
        {candidate.display_number && <span>{candidate.display_number}</span>}<p>{candidate.text}</p>
      </button>)}{!candidates.length && <p className="qa-meta">{t("qa.noResults")}</p>}</div>
    </div>}
  </article>
}

export function QaImportReview({ plan, list, busy, onQuestion, onColumn, onAdjust, onRows, onRole, onParent, onMerge, reviewRef, duplicate = false }: Props) {
  const t = useT()
  const summary = qaImportSummary(plan, list)
  const [editing, setEditing] = useState<string | null>(null)
  const columnKeys = plan.mapping.replies.map(String)
  const repeatedQuestions = repeatedKeys(plan.rows.map(row => row.key), plan.matches)
  const repeatedColumns = repeatedKeys(columnKeys, plan.column_matches)
  const duplicateCount = repeatedQuestions.size + repeatedColumns.size
  const hasDuplicates = duplicate || duplicateCount > 0
  const questions = plan.rows.filter(row => !plan.matches[row.key] || repeatedQuestions.has(row.key))
  const confirmedColumns = columnKeys.filter(key => plan.column_matches[key] && !repeatedColumns.has(key))
  const identityPending = summary.questions.pending + summary.replies.pending + (duplicateCount || (duplicate ? 1 : 0))
  const pending = identityPending || plan.review?.unresolved || 0
  const sourceRows = (plan.table_rows || []).filter(row => row.kind === "review")
  const allHaveQuestion = sourceRows.length > 0 && sourceRows.every(row => row.values[plan.mapping.question]?.trim())
  const confirmed = plan.rows.filter(row => plan.matches[row.key] && !repeatedQuestions.has(row.key))
  const questionTargets = new Set(plan.rows.map(row => plan.matches[row.key]))
  const identitiesSettled = !summary.questions.pending && !hasDuplicates
  const omittedQuestions = summary.questions.omitted === null ? [] : list.questions.filter(q => q.included && !questionTargets.has(q.id))
  const columnTargets = columnKeys.map(key => plan.column_matches[key])
  const omittedColumns = !plan.review && plan.mapping_valid && !summary.replies.pending && !hasDuplicates ? list.columns.filter(column => column.included && !columnTargets.includes(column.id)) : []
  const groupColumn = plan.mapping.group
  const typeHeading = plan.mapping.type == null ? "" : plan.headers[plan.mapping.type] || ""
  const ambiguousType = /^(?:type|category|类型|類型|类别|類別)$/i.test(typeHeading.split(" / ").at(-1)?.trim() || "")
  const columnHint = groupColumn != null
    ? { key: "qa.review.groupColumnHint", name: plan.headers[groupColumn] || t("qa.unnamedColumn", { number: groupColumn + 1 }) }
    : ambiguousType ? { key: "qa.review.ambiguousTypeColumnHint", name: typeHeading } : null
  function chooseQuestion(key: string, target: string) { setEditing(null); onQuestion(key, target) }
  function chooseColumn(key: string, target: string) { setEditing(null); onColumn(key, target) }
  return <div className="qa-import-review" ref={reviewRef} tabIndex={-1}>
    <header className="qa-review-heading">
      <div><p className="qa-meta">{t("qa.review.destination", { name: list.title })}</p><h2>{t(plan.stale ? "qa.review.staleHeading" : sourceRows.length ? sourceRows.length === 1 ? "qa.review.rowNeeded" : "qa.review.rowsNeeded" : !plan.mapping_valid ? "qa.review.checkTable" : pending ? "qa.review.needsDecisions" : "qa.review.ready", { count: sourceRows.length || pending })}</h2></div>
      {!plan.stale && plan.mapping_valid && !sourceRows.length && <Button size="sm" variant="link" disabled={busy} onClick={onAdjust}>{t("qa.review.adjustTable")}</Button>}
    </header>
    <div className="qa-review-totals">
      <p>{t(sourceRows.length ? "qa.review.recognizedTotals" : "qa.review.totals", { questions: plan.rows.length, replies: plan.mapping.replies.length, groups: summary.groups })}</p>
      {plan.mapping_valid && !sourceRows.length && columnHint && <p className="qa-recognized-column">{t(columnHint.key, { name: columnHint.name, details: t("qa.review.details"), adjust: t("qa.review.adjustTable") })}</p>}
    </div>
    {plan.mapping_valid && !sourceRows.length && <>
      {list.questions.length > 0 && <dl className="qa-import-outcomes">
        <div><dt>{t("qa.review.continuedCount")}</dt><dd>{summary.questions.continued}</dd></div>
        <div><dt>{t("qa.review.newCount")}</dt><dd>{summary.questions.added}</dd></div>
        <div><dt>{t(plan.review ? "qa.review.omittedResultCount" : "qa.review.omittedCount")}</dt><dd>{!identitiesSettled ? "—" : plan.review ? plan.review.missing_questions.filter(question => !question.kept).length : summary.questions.omitted ?? "—"}</dd></div>
        {plan.review && identitiesSettled && plan.review.missing_questions.some(question => question.kept) && <div><dt>{t("qa.review.protectedCount")}</dt><dd>{plan.review.missing_questions.filter(question => question.kept).length}</dd></div>}
        {summary.questions.pending > 0 && <div><dt>{t("qa.review.pendingCount")}</dt><dd>{summary.questions.pending}</dd></div>}
      </dl>}
      {list.questions.length > 0 && <p className="qa-decision-hint">{t("qa.review.versionHint")}</p>}
    </>}
    {sourceRows.length > 0 ? <section className="qa-source-decisions" data-qa-import-decision tabIndex={-1} aria-label={t("qa.review.rowsNeeded", { count: sourceRows.length })}>
      <div className="qa-source-help"><p>{t("qa.review.rowHelp")}</p><div>
        {allHaveQuestion && <Button size="sm" variant="secondary" disabled={busy} onClick={() => onRows(Object.fromEntries(sourceRows.map(row => [row.key, "question"])))}>{t("qa.review.allQuestions")}</Button>}
        <Button size="sm" variant="ghost" disabled={busy} onClick={onAdjust}>{t("qa.review.sourceSettings")}</Button>
      </div></div>
      <QaImportTable plan={plan} busy={busy} initialReviewOnly onRole={onRole} onRow={(key, kind) => onRows({ [key]: kind })} onParent={onParent} />
    </section> : !plan.mapping_valid && <div className="qa-notice"><p>{t("qa.review.columnsBlocked")}</p><Button size="sm" variant="secondary" disabled={busy} onClick={onAdjust}>{t("qa.review.adjustTable")}</Button></div>}
    {plan.mapping_valid && !sourceRows.length && <>
      {questions.map(row => <QuestionDecision key={row.key} row={row} plan={plan} list={list} busy={busy} onQuestion={chooseQuestion} duplicate={repeatedQuestions.has(row.key)} />)}
      {columnKeys.filter(key => !plan.column_matches[key] || repeatedColumns.has(key)).map(key => <ReplyDecision key={key} columnKey={key} plan={plan} list={list} busy={busy} onColumn={chooseColumn} duplicate={repeatedColumns.has(key)} />)}
      {!identityPending && plan.review && onMerge && <QaImportContentReview plan={plan} list={list} busy={busy} onMerge={onMerge} />}
    </>}
    {!sourceRows.length && <details className="qa-review-details">
      <summary>{t("qa.review.details")}</summary>
      <div className="qa-review-details-body">
      {plan.mapping_valid && <>
        {confirmedColumns.length > 0 && <section className="qa-review-detail-section">
          <h3>{t("qa.review.columnLinks")}</h3>
          {confirmedColumns.map(key => {
          const value = plan.column_matches[key]
          if (!value) return null
          if (editing === `column-${key}`) return <ReplyDecision key={key} columnKey={key} plan={plan} list={list} busy={busy} onColumn={chooseColumn} onCancel={() => setEditing(null)} />
          const target = list.columns.find(column => column.id === value)
          const impact = qaReplyColumnImpact(plan, list, Number(key), value)
          return <div className="qa-import-column-result" key={key}>
            <div className="qa-import-link"><span>{plan.headers[Number(key)]}</span><ArrowRight aria-hidden className="size-3.5" /><strong>{target?.label || t("qa.review.newColumnResult")}</strong><Button size="sm" variant="ghost" disabled={busy} aria-label={t("qa.review.changeColumn", { name: plan.headers[Number(key)] })} onClick={() => setEditing(`column-${key}`)}>{t("qa.review.change")}</Button></div>
            <p className="qa-meta">{t(impact.empty ? "qa.review.emptyColumn" : "qa.review.columnFilled", { count: impact.filled })}</p>
            <ColumnImpact plan={plan} list={list} index={Number(key)} target={value} />
            {plan.review && onMerge && value !== "new" && impact.cleared > 0 && <label className="qa-merge-clear-option"><input type="checkbox" disabled={busy} checked={(plan.blank_columns || []).includes(key)} onChange={event => onMerge({ blank_columns: event.target.checked ? [...new Set([...(plan.blank_columns || []), key])] : (plan.blank_columns || []).filter(column => column !== key) })} /><span>{t("qa.review.clearBlankOption", { count: impact.cleared })}</span></label>}
          </div>
          })}
        </section>}
      {list.questions.length > 0 && confirmed.length > 0 && <section className="qa-review-detail-section">
        <h3>{t("qa.review.questionLinks", { count: confirmed.length })}</h3>
        {confirmed.map(row => {
          if (editing === `question-${row.key}`) return <QuestionDecision key={row.key} row={row} plan={plan} list={list} busy={busy} onQuestion={chooseQuestion} onCancel={() => setEditing(null)} />
          const previous = list.questions.find(question => question.id === plan.matches[row.key])
          return <div className="qa-import-question-result" key={row.key}>
            <header><span className="qa-meta">{t(previous ? "qa.review.linkedQuestion" : "qa.review.newQuestionResult", { row: row.key })}</span><Button size="sm" variant="ghost" disabled={busy} aria-label={t("qa.review.changeQuestion", { row: row.key })} onClick={() => setEditing(`question-${row.key}`)}>{t("qa.review.change")}</Button></header>
            {previous && <p className="qa-import-previous">{previous.display_number && <span>{previous.display_number} · </span>}{previous.summary || previous.text}</p>}
            <Excerpt text={row.text} />
          </div>
        })}
      </section>}
      {plan.review && identitiesSettled && onMerge && <QaImportPreservedItems plan={plan} busy={busy} onMerge={onMerge} />}
      {!plan.review && (omittedQuestions.length > 0 || omittedColumns.length > 0) && <section className="qa-review-detail-section qa-review-omissions">
        {omittedQuestions.length > 0 && <><h3>{t("qa.review.omittedQuestions", { count: omittedQuestions.length })}</h3>{omittedQuestions.map(question => <p key={question.id}>{question.summary || question.text}</p>)}</>}
        {omittedColumns.length > 0 && <p>{t("qa.review.omittedColumns", { names: omittedColumns.map(column => column.label).join(", ") })}</p>}
        <p className="qa-meta">{t("qa.review.omittedHistory")}</p>
      </section>}
      {!identityPending && plan.review && onMerge && <QaImportDecidedEdits plan={plan} list={list} busy={busy} onMerge={onMerge} />}
      </>}
      <section className="qa-review-detail-section"><header className="qa-review-detail-heading"><h3>{t("qa.review.preview")}</h3></header><QaImportResult plan={plan} foldLongCells={false} /></section>
      </div>
    </details>}
  </div>
}
