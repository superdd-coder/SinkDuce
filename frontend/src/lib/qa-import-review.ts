import type { QaImportContext, QaImportPlan } from "@/api/client"

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
