import type { QaListDetail } from "../api/qa"

/** Keep unchanged JSON branches so background polls do not restart list layout motion. */
function share<T>(previous: T, next: T): T {
  if (Object.is(previous, next)) return previous
  if (!previous || !next || typeof previous !== "object" || typeof next !== "object") return next
  if (Array.isArray(previous) && Array.isArray(next)) {
    const merged = next.map((value, index) => share(previous[index], value))
    return previous.length === merged.length && merged.every((value, index) => value === previous[index]) ? previous : merged as T
  }
  if (Array.isArray(previous) || Array.isArray(next)) return next
  const before = previous as Record<string, unknown>, after = next as Record<string, unknown>
  const entries = Object.entries(after).map(([key, value]) => [key, share(before[key], value)] as const)
  if (Object.keys(before).length === entries.length && entries.every(([key, value]) => Object.hasOwn(before, key) && before[key] === value)) return previous
  return Object.fromEntries(entries) as T
}

/** A reply edit can invalidate reply annotations, but cannot change the question's wording. */
export function reconcileQaSnapshot(current: QaListDetail | null, next: QaListDetail): QaListDetail {
  if (!current || current.id !== next.id || current.version.id !== next.version.id) return next
  if (current.revision > next.revision) return current
  const questions = new Map(current.questions.map(question => [question.id, question]))
  const stableQuestions = next.questions.map(question => {
    const previous = questions.get(question.id)
    return previous?.summary && previous.text === question.text && previous.summary !== question.summary
      ? { ...question, summary: previous.summary } : question
  })
  const pending = stableQuestions.filter(question => question.included && (!question.summary || question.type_origin === "default")).length
  return share(current, {
    ...next,
    questions: stableQuestions,
    ...(next.annotation_pending === undefined ? {} : {
      annotation_pending: pending,
      annotation_state: pending ? next.annotation_state : "ready" as const,
    }),
  })
}
