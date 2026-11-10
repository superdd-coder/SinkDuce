import { useImperativeHandle, useLayoutEffect, useRef, useState, type Ref } from "react"
import { ChevronRight, CircleAlert, Columns2, FileMinus, Link2, MessagesSquare, Plus, Search } from "lucide-react"
import type { QaColumn, QaImportConflict, QaImportContentChoice, QaImportContext, QaImportMergeChanges, QaImportPlan } from "@/api/client"
import { Button } from "@/components/ui/button"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { useT } from "@/i18n/use-t"
import { excelColumn } from "@/lib/qa-import-mapping"
import { qaImportCards, qaImportReplyPairs, qaImportSummary, repeatedImportTargets, type QaImportCard, type QaImportReplyPair } from "@/lib/qa-import-review"
import { currentChoices, QaImportContentReview, QaImportDecidedEdits, QaImportPreservedItems } from "./qa-import-content-review"
import { QaImportComparison } from "./qa-import-comparison"
import { QaActionMenu, QaMenuItem } from "./qa-controls"

export interface QaImportReviewHandle { focusDecision(): void }

type Props = {
  plan: QaImportPlan; list: QaImportContext; busy: boolean
  onQuestion: (key: string, target: string) => void
  onColumn: (key: string, target: string) => void
  onLocate?: (row: string) => void
  onMerge?: (changes: QaImportMergeChanges) => void
  reviewRef?: Ref<QaImportReviewHandle>
  duplicate?: boolean
}

const groups = {
  matched: { icon: Link2, label: "qa.importPreview.matched" },
  added: { icon: Plus, label: "qa.importPreview.added" },
  old: { icon: FileMinus, label: "qa.importPreview.old" },
} as const

function savedColumnLabel(column: QaColumn) {
  return column.source_column == null ? column.label : `${excelColumn(column.source_column)} · ${column.label}`
}

export function QaImportReview({ plan, list, busy, onQuestion, onColumn, onLocate, onMerge, reviewRef, duplicate = false }: Props) {
  const t = useT()
  const root = useRef<HTMLDivElement>(null)
  const [filter, setFilter] = useState<"all" | keyof typeof groups>("all")
  const [expanded, setExpanded] = useState(new Set<string>())
  const [showAll, setShowAll] = useState(false)
  const [focusRequest, setFocusRequest] = useState(0)

  const first = list.questions.length === 0
  const summary = qaImportSummary(plan, list)
  const columnKeys = plan.mapping.replies.map(String)
  const repeatedQuestions = repeatedImportTargets(plan.rows.map(row => row.key), plan.matches)
  const repeatedColumns = repeatedImportTargets(columnKeys, plan.column_matches)
  const pendingColumns = new Set(columnKeys.filter(key => !plan.column_matches[key] || repeatedColumns.has(key)))
  const cards = qaImportCards(plan, list, repeatedQuestions, summary.questions.omitted !== null && !plan.stale)
  const canEdit = !busy && plan.mapping_valid && !plan.stale
  const merge = plan.mapping_valid && !duplicate && !pendingColumns.size && !cards.some(card => card.group === "pending") ? onMerge : undefined
  const inline = new Map<string, QaImportConflict[]>()
  if (merge) for (const item of plan.review?.conflicts || []) {
    if (plan.matches[item.row_key] !== item.question_id) continue
    if (item.kind === "question" || (item.kind === "reply" && item.column_key != null && columnKeys.includes(item.column_key))) {
      inline.set(item.row_key, [...(inline.get(item.row_key) || []), item])
    }
  }
  const inlineIds = new Set([...inline.values()].flat().map(item => item.id))
  const needsDecision = (card: QaImportCard) => card.group === "pending" || inline.get(card.key)?.some(item => !item.choice)
  const visible = cards.filter(card => filter === "all" || card.group === filter)
  const incomingCards = visible.filter(card => card.group !== "old")
  const oldCards = visible.filter(card => card.group === "old")
  const shown = first && !showAll ? incomingCards.slice(0, 3) : incomingCards
  const choices = currentChoices(plan)
  const usedQuestions = new Map(Object.entries(plan.matches).map(([row, target]) => [target, row]))
  const usedColumns = new Map(Object.entries(plan.column_matches).map(([column, target]) => [target, column]))
  const columnNames = new Map(list.columns.map(column => [column.id, column.label]))
  const columnOptions = [{ value: "new", label: t("qa.importPreview.newRound") }, ...list.columns.map(column => {
    const used = usedColumns.get(column.id)
    return {
      value: column.id, label: savedColumnLabel(column),
      detail: used != null ? t("qa.importPreview.columnUsed", { column: excelColumn(Number(used)) }) : undefined,
    }
  })]

  useImperativeHandle(reviewRef, () => ({
    focusDecision() {
      setFilter("all")
      const next = cards.find(needsDecision) || (pendingColumns.size > 0 && cards.find(card => card.current && card.incoming)) ||
        (pendingColumns.size > 0 && cards.find(card => card.incoming))
      if (next) {
        setExpanded(open => new Set([...open, next.key]))
        if (first) setShowAll(true)
      }
      setFocusRequest(value => value + 1)
    },
  }))
  useLayoutEffect(() => {
    if (!focusRequest) return
    const target = root.current?.querySelector<HTMLElement>("[data-qa-import-decision]") || root.current
    target?.focus({ preventScroll: true })
    target?.scrollIntoView({ block: "nearest" })
  }, [focusRequest])

  function toggle(key: string) {
    setExpanded(previous => {
      const next = new Set(previous)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function selectContent(conflict: QaImportConflict, choice: QaImportContentChoice) {
    merge?.({ content_choices: { ...choices, [conflict.id]: choice } })
  }

  function carry(column: string, ids: string[], keep: boolean) {
    const retained = { ...plan.retained_replies }
    const current = new Set(retained[column] || [])
    for (const id of ids) {
      if (keep) current.add(id)
      else current.delete(id)
    }
    if (current.size) retained[column] = [...current]
    else delete retained[column]
    merge?.({ retained_replies: retained })
  }

  function carryControl(card: QaImportCard, pair: QaImportReplyPair) {
    const column = pair.savedColumn
    if (!merge || !pair.missing || !column || !card.current?.included) return null
    const id = card.current.id
    return <div className="qa-import-carry">
      <Button size="sm" variant="outline" disabled={!canEdit} onClick={() => carry(column, [id], !pair.carried)}>
        {t(pair.carried ? "qa.importPreview.undoCarry" : "qa.importPreview.carryReply")}
      </Button>
      <QaActionMenu label={t("qa.importPreview.carryOptions")} disabled={!canEdit}>
        <QaMenuItem onClick={() => {
          const ids = cards.flatMap(item => item.incoming && item.current?.included &&
            qaImportReplyPairs(plan, list.columns, item).some(reply => reply.savedColumn === column && reply.missing)
            ? [item.current.id] : [])
          carry(column, ids, true)
        }}>{t("qa.importPreview.carryColumn")}</QaMenuItem>
        <QaMenuItem onClick={() => carry(column, plan.retained_replies?.[column] || [], false)}>{t("qa.importPreview.undoCarryColumn")}</QaMenuItem>
      </QaActionMenu>
    </div>
  }

  function renderCard(card: QaImportCard) {
    const { incoming, current, key, group } = card
    const open = expanded.has(key)
    const Mark = group === "pending" ? Search : groups[group].icon
    const conflicts = inline.get(key) || []
    const questionConflict = conflicts.find(item => item.kind === "question")
    const replyConflicts = new Map(conflicts.filter(item => item.kind === "reply").map(item => [item.column_key, item]))
    const pairs = open ? qaImportReplyPairs(plan, list.columns, card)
      .filter(pair => (!first && pair.column !== undefined) || pair.current?.body.trim() || pair.incoming?.body.trim()) : []
    const result = plan.review?.preview?.[key]
    const questionText = result?.text ?? incoming?.text
    const attention = !!needsDecision(card)
    const badge = attention ? t("qa.importPreview.pending") : result?.included === false ? t("qa.importPreview.removedQuestion")
      : group === "added" && !first ? t("qa.importPreview.addedQuestion") : undefined
    const selectQuestion = !first && !!incoming
    const showMeta = !!(current?.group || incoming?.group || badge || (onLocate && incoming))

    const questionOptions = () => [
      { value: "new", label: t("qa.importPreview.asNewQuestion") },
      ...list.questions.map(question => {
        const used = usedQuestions.get(question.id)
        const occupied = !!used && used !== key
        return {
          value: question.id, label: question.text,
          detail: [question.display_number, question.group,
            occupied ? t("qa.importPreview.questionUsed", { row: used }) : undefined].filter(Boolean).join(" · "),
          disabled: occupied,
        }
      }),
    ]
    const oldSide = <div className="qa-import-card-side qa-import-current">
      {showMeta && <div className="qa-import-card-meta">
        {current?.group && <span className="qa-import-group-label">{current.group}</span>}
      </div>}
      {selectQuestion ? <DropdownSelect size="sm" searchable className="qa-import-question-select"
        menuClassName="qa-exchange-menu qa-question-options" aria-label={t("qa.review.changeQuestion", { row: key })}
        value={plan.matches[key] || ""} options={questionOptions} disabled={!canEdit}
        valueContent={<span className="qa-import-question-text">{current?.text || t("qa.importPreview.matchQuestion")}</span>}
        onChange={value => onQuestion(key, value)} />
        : <span className="qa-import-question-text">{current?.text}</span>}
      {group === "old" && <span className="qa-import-card-mark is-muted">
        {t(card.missing?.kept ? "qa.review.keptExplicit" : "qa.importPreview.removedQuestion")}
      </span>}
    </div>
    const newSide = incoming ? <div className="qa-import-card-side qa-import-incoming">
      {showMeta && <div className="qa-import-card-meta">
        {incoming.group && <span className="qa-import-group-label">{incoming.group}</span>}
        {badge && <span className={`qa-import-card-mark${attention ? " is-pending" : ""}`}>
          {attention && <CircleAlert size={14} aria-hidden />}{badge}
        </span>}
        {onLocate && <button type="button" className="qa-import-coordinate" onClick={() => onLocate(key)}
          aria-label={t("qa.review.excelRow", { row: key })}>{excelColumn(plan.mapping.question)}{key}</button>}
      </div>}
      <button type="button" className="qa-import-question-text" aria-expanded={open} onClick={() => toggle(key)}>{questionText}</button>
    </div> : <div />

    return <article className="qa-import-card" key={key} data-qa-import-group={group} data-open={open || undefined}
      data-qa-import-decision={group === "pending" ? "" : undefined} tabIndex={group === "pending" ? -1 : undefined}>
      <div className="qa-import-card-head">
        <button type="button" className="qa-import-card-toggle" aria-expanded={open}
          aria-label={open ? t("qa.importPreview.collapseQuestion") : t("qa.importPreview.expandQuestion")} onClick={() => toggle(key)}>
          <ChevronRight className="qa-import-fold" aria-hidden />
        </button>
        {first ? newSide : <div className="qa-import-pair" data-meta={showMeta && !!incoming || undefined}>
          {oldSide}<span className="qa-import-pair-mark" data-kind={group} aria-hidden><Mark size={14} /></span>{newSide}
        </div>}
      </div>
      {open && <div className="qa-import-card-thread">
        {questionConflict && <QaImportComparison
          current={{ label: t("qa.importPreview.current"), body: current?.text || "" }}
          incoming={{ label: t("qa.importPreview.excel"), body: questionText || "" }}
          conflict={questionConflict} onChoice={choice => selectContent(questionConflict, choice)} busy={busy} />}
        {pairs.map((pair, position) => {
          const column = pair.column
          const columnKey = String(column)
          const conflict = replyConflicts.get(columnKey)
          const label = column == null ? pair.incoming?.label || "" : plan.headers[column] || t("qa.field.reply")
          const mapping = plan.column_matches[columnKey]
          const currentTitle = mapping ? columnNames.get(mapping) : undefined
          const pending = column != null && pendingColumns.has(columnKey)
          const control = !first && column != null ? <DropdownSelect size="sm" className="qa-import-round-select"
            menuClassName="qa-exchange-menu" aria-label={t("qa.review.changeColumn", { name: `${label} · ${excelColumn(column)}` })}
            value={mapping === "new" ? "" : mapping || ""} placeholder={t("qa.importPreview.matchRound")}
            valueContent={currentTitle ? <span className="pm-select-trigger-label min-w-0 truncate">{currentTitle}</span> : undefined}
            options={columnOptions} disabled={!canEdit} onChange={value => onColumn(columnKey, value)} /> : undefined
          const status = pair.missing && !pendingColumns.size && !pair.carried ? t("qa.importPreview.archivedReply") : undefined
          const incomingStatus = pair.carried ? t("qa.importPreview.carriedReply")
            : !first && mapping === "new" ? t("qa.importPreview.newRound") : undefined
          return <QaImportComparison key={pair.key} current={pair.current}
            incoming={pair.incoming ? { ...pair.incoming, label } : undefined}
            position={position + 1} single={first} status={status} incomingStatus={incomingStatus} pending={pending}
            currentControl={control} currentAction={carryControl(card, pair)}
            busy={busy} conflict={conflict} onChoice={conflict ? choice => selectContent(conflict, choice) : undefined} />
        })}
        {!pairs.length && !questionConflict && <p className="qa-meta">{t("qa.importPreview.noReplies")}</p>}
        {current && card.missing?.kept && merge && <QaImportPreservedItems plan={plan} busy={busy} onMerge={merge} questionId={current.id} />}
      </div>}
    </article>
  }

  function metric(Icon: typeof Link2, label: string, count: number | null) {
    return <><span className="qa-import-outcome-icon" aria-hidden><Icon size={17} /></span>
      <span className="qa-import-outcome-copy"><strong>{count ?? "—"}</strong><span>{label}</span></span></>
  }

  return <div className="qa-import-review" ref={root} tabIndex={-1}>
    {plan.stale && <h2 className="qa-import-review-heading">{t("qa.review.staleHeading")}</h2>}
    <div className={first ? "qa-import-metrics is-first" : "qa-import-metrics"} role="group" aria-label={t("qa.importPreview.outcomes")}>
      {first ? <>
        <div className="qa-import-outcome" data-qa-import-outcome="questions">{metric(MessagesSquare, t("qa.importPreview.questions"), plan.rows.length)}</div>
        <div className="qa-import-outcome" data-qa-import-outcome="replies">{metric(Columns2, t("qa.review.replyColumns"), columnKeys.length)}</div>
      </> : (Object.keys(groups) as (keyof typeof groups)[]).map(group => {
        const count = group === "old" && summary.questions.omitted === null ? null : cards.filter(card => card.group === group).length
        const { icon, label } = groups[group]
        return <button type="button" className="qa-import-outcome" key={group} data-qa-import-outcome={group} aria-pressed={filter === group}
          disabled={count === null} onClick={() => setFilter(previous => previous === group ? "all" : group)}>{metric(icon, t(label), count)}</button>
      })}
    </div>
    {merge && <QaImportContentReview plan={plan} list={list} busy={busy} onMerge={merge} inline={inlineIds} />}
    {!first && <div className="qa-import-column-head qa-meta"><div className="qa-import-pair">
      <span>{t("qa.importPreview.current")}</span><span>{t("qa.importPreview.nextVersion")}</span>
    </div></div>}
    <div className="qa-import-cards">
      {shown.map(renderCard)}
      {oldCards.length > 0 && <h3 className="qa-import-archive-heading qa-meta">{t("qa.importPreview.oldQuestions")}</h3>}
      {oldCards.map(renderCard)}
      {!visible.length && <p className="qa-meta qa-import-empty">{t("qa.importPreview.empty")}</p>}
    </div>
    {first && visible.length > 3 && <Button size="sm" variant="link" aria-expanded={showAll} onClick={() => setShowAll(value => !value)}>
      {t(showAll ? "qa.importPreview.showFewer" : "qa.importPreview.showAll", { count: visible.length })}
    </Button>}
    {merge && plan.review?.conflicts.some(item => item.choice && item.kind !== "structure" && !inlineIds.has(item.id)) && <details className="qa-import-settings">
      <summary>{t("qa.importPreview.savedChoices")}</summary>
      <QaImportDecidedEdits plan={plan} list={list} busy={busy} onMerge={merge} inline={inlineIds} />
    </details>}
  </div>
}
