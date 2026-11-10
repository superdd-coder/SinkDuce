import type { QaImportContentReview, QaImportContext, QaImportPlan, QaQuestion } from "@/api/client"

type ReviewPlan = Pick<QaImportPlan, "rows" | "mapping" | "matches" | "column_matches" | "mapping_valid" | "table_rows">
type ReviewList = { questions: Pick<QaImportContext["questions"][number], "id" | "included">[] }
type ReplyColumnList = { questions: { id: string; replies: { column_id: string; body: string }[] }[] }
interface OutcomeCounts { total: number; added: number; continued: number; pending: number }
export interface QaImportSummary {
  questions: OutcomeCounts & { omitted: number | null }
  replies: OutcomeCounts
  rowsNeedingPurpose: number
  groups: number
  notes: number
}

export type QaImportGroup = "matched" | "added" | "old" | "pending"
type IncomingQuestion = QaImportPlan["rows"][number]
type MissingQuestion = QaImportContentReview["missing_questions"][number]
export interface QaImportCard {
  key: string
  group: QaImportGroup
  current?: QaQuestion
  incoming?: IncomingQuestion
  missing?: MissingQuestion
}

export function repeatedImportTargets(keys: string[], matches: QaImportPlan["matches"]): Set<string> {
  const counts = new Map<string, number>()
  for (const key of keys) {
    const target = matches[key]
    if (target && target !== "new") counts.set(target, (counts.get(target) || 0) + 1)
  }
  return new Set(keys.filter(key => (counts.get(matches[key] || "") || 0) > 1))
}

export function qaImportCards(plan: QaImportPlan, list: QaImportContext, repeated: Set<string>, omissionsKnown: boolean): QaImportCard[] {
  const questions = new Map(list.questions.map(question => [question.id, question]))
  const matched = new Set<string>()
  const cards: QaImportCard[] = plan.rows.map(incoming => {
    const target = plan.matches[incoming.key]
    const current = target ? questions.get(target) : undefined
    const group = repeated.has(incoming.key) ? "pending" : target === "new" ? "added" : current ? "matched" : "pending"
    if (group === "matched" && current) matched.add(current.id)
    return { key: incoming.key, group, current, incoming }
  })
  if (omissionsKnown) {
    const missing = new Map(plan.review?.missing_questions.map(question => [question.id, question]))
    for (const question of list.questions) {
      if (question.included && !matched.has(question.id)) {
        cards.push({ key: `saved-${question.id}`, group: "old", current: question, missing: missing.get(question.id) })
      }
    }
  }
  return cards
}

export interface QaImportReplyPair {
  key: string
  column?: number
  savedColumn?: string
  missing?: boolean
  carried?: boolean
  current?: { label: string; body: string }
  incoming?: { label: string; body: string }
}

/** Align by saved column identity and Excel column index, even with duplicate labels. */
export function qaImportReplyPairs(plan: QaImportPlan, columns: QaImportContext["columns"], card: QaImportCard): QaImportReplyPair[] {
  const { current, incoming } = card
  const replies = new Map(current?.replies.filter(reply => reply.included !== false).map(reply => [reply.column_id, reply]))
  const savedColumns = new Map(columns.map(column => [column.id, column]))
  const linked = new Set<string>()
  const pairs: QaImportReplyPair[] = []
  if (incoming) {
    for (const column of [...plan.mapping.replies].sort((a, b) => a - b)) {
      const target = plan.column_matches[String(column)]
      const saved = current && target ? savedColumns.get(target) : undefined
      if (saved) linked.add(saved.id)
      const body = saved ? replies.get(saved.id)?.body || "" : ""
      const raw = incoming.formatted_values?.[String(column)] ?? incoming.values[column] ?? ""
      const missing = !!body.trim() && !raw.trim()
      const carried = missing && !!saved && !!current && !!plan.retained_replies?.[saved.id]?.includes(current.id)
      pairs.push({
        key: `excel-${column}`, column, savedColumn: saved?.id, missing, carried,
        current: saved ? { label: saved.label, body } : undefined,
        incoming: { label: plan.headers[column] || "", body: plan.review?.preview?.[incoming.key]?.replies?.[String(column)] ?? (carried ? body : raw) },
      })
    }
  }
  for (const reply of replies.values()) {
    if (linked.has(reply.column_id)) continue
    const column = savedColumns.get(reply.column_id)
    if (!column || !reply.body.trim()) continue
    const carried = !!incoming && !!current && !!plan.retained_replies?.[column.id]?.includes(current.id)
    pairs.push({
      key: `saved-${column.id}`, savedColumn: column.id, missing: !!incoming, carried,
      current: { label: column.label, body: reply.body },
      incoming: carried ? { label: column.label, body: reply.body } : undefined,
    })
  }
  return pairs
}

function countOutcomes(targets: (string | null | undefined)[]): OutcomeCounts {
  return {
    total: targets.length,
    added: targets.filter(target => target === "new").length,
    continued: targets.filter(target => target && target !== "new").length,
    pending: targets.filter(target => !target).length,
  }
}

/** Describe planned outcomes without interpreting reply bodies or changing assignments. */
export function qaImportSummary(plan: ReviewPlan, list: ReviewList): QaImportSummary {
  const questionTargets = plan.rows.map(row => plan.matches[row.key])
  const questions = countOutcomes(questionTargets)
  const continued = questionTargets.filter((target): target is string => !!target && target !== "new")
  const selectedIds = new Set(continued)
  const sourceRows = plan.table_rows || []
  const groupKeys = new Set(sourceRows.filter(row => row.kind === "section").map(row => row.key))
  for (const row of plan.rows) if (row.group_key) groupKeys.add(row.group_key)
  const rowsNeedingPurpose = sourceRows.filter(row => row.kind === "review").length
  const omissionsKnown = plan.mapping_valid && !questions.pending && !rowsNeedingPurpose && selectedIds.size === continued.length
  return {
    questions: {
      ...questions,
      omitted: omissionsKnown ? list.questions.filter(question => question.included && !selectedIds.has(question.id)).length : null,
    },
    replies: countOutcomes(plan.mapping.replies.map(index => plan.column_matches[String(index)])),
    rowsNeedingPurpose,
    groups: groupKeys.size,
    notes: sourceRows.filter(row => row.kind === "note").length,
  }
}

/** An empty incoming cell clears only its own existing question/column intersection. */
export function qaReplyColumnImpact(plan: Pick<QaImportPlan, "rows" | "matches">, list: ReplyColumnList, columnIndex: number, target: string | null | undefined): { filled: number; empty: boolean; cleared: number } {
  let filled = 0, cleared = 0
  const questions = new Map(list.questions.map(question => [question.id, question]))
  for (const row of plan.rows) {
    if (row.values[columnIndex]?.trim()) { filled++; continue }
    const questionId = plan.matches[row.key]
    if (!questionId || questionId === "new" || !target || target === "new") continue
    const previous = questions.get(questionId)?.replies.find(reply => reply.column_id === target)
    if (previous?.body.trim()) cleared++
  }
  return { filled, empty: filled === 0, cleared }
}
