import assert from "node:assert/strict"
import { test } from "node:test"
import type { QaListDetail, QaQuestion } from "../src/api/qa.ts"
import { reconcileQaSnapshot } from "../src/lib/qa-snapshot.ts"

function listing(): QaListDetail {
  const question = (id: string, text: string): QaQuestion => ({
    id, text, summary: "Check the cabinet lock.", reply_summary: "The lock is pending.",
    included: true, position: 0, status: "open", type: "clarify", type_origin: "ai",
    external_number: "", updated_at: "2030-01-01", absent_since: null, latest_reply: "Pending.",
    replies: [{ column_id: "c1", label: "Response", body: "Pending.", sources: [], included: true,
      modified: false, historical_body: "", cleared_since: null, absent_since: null, text_revision_id: `${id}-r1` }],
  })
  return { id: "list-a", title: "Cabinet register", revision: 4, template_id: null, archived: false,
    current_version_id: "v1", version: { id: "v1", number: 1, state: "working", created_at: "2030-01-01" },
    columns: [{ id: "c1", label: "Response", included: true, position: 0, absent_since: null }],
    questions: [question("q1", "Which lock is fitted to the cabinet?"), question("q2", "When is the cabinet inspected?")],
    outline: [{ id: "q1", kind: "question" }, { id: "q2", kind: "question" }],
    annotation_pending: 0, annotation_state: "ready", can_edit: true }
}

test("reply acknowledgement retains the question label and unchanged list objects", () => {
  const current = listing()
  const next = structuredClone(current)
  next.revision++
  next.questions[0].summary = ""
  next.questions[0].reply_summary = ""
  next.annotation_pending = 1
  next.annotation_state = "idle"
  next.questions[0].replies[0].body = "A new lock is fitted."
  next.questions[0].replies[0].text_revision_id = "q1-r2"
  const merged = reconcileQaSnapshot(current, next)
  assert.equal(merged.questions[0].summary, current.questions[0].summary)
  assert.equal(merged.questions[0].replies[0].body, "A new lock is fitted.")
  assert.equal(merged.questions[0].reply_summary, "", "an old answer summary is never carried over to changed reply text")
  assert.equal(merged.annotation_pending, 0, "reply saves do not flash the question classification prompt")
  assert.equal(merged.annotation_state, "ready")
  assert.equal(merged.questions[1], current.questions[1])
  assert.equal(merged.outline, current.outline)
  assert.equal(merged.columns, current.columns)
  assert.equal(next.questions[0].summary, "", "the network response stays immutable")
})

test("background annotation cannot reword the same question label while a reply is edited", () => {
  const current = listing()
  const next = structuredClone(current)
  next.questions[0].summary = "Confirm the storage lock mechanism."
  next.questions[0].reply_summary = "A replacement lock was fitted."
  const merged = reconcileQaSnapshot(current, next)
  assert.equal(merged.questions[0].summary, current.questions[0].summary)
  assert.equal(merged.questions[0].reply_summary, next.questions[0].reply_summary)
})

test("identical enrichment polls are ignored and older snapshots cannot replace a saved reply", () => {
  const current = listing()
  assert.equal(reconcileQaSnapshot(current, structuredClone(current)), current)
  const stale = structuredClone(current)
  stale.revision--
  stale.questions[0].replies[0].body = "Stale wording."
  assert.equal(reconcileQaSnapshot(current, stale), current)
})

test("changed question text and missing first summaries accept fresh labels without crossing versions", () => {
  const current = listing()
  const changed = structuredClone(current)
  changed.revision++
  changed.questions[0].text = "Which key opens the storage cabinet?"
  changed.questions[0].summary = ""
  assert.equal(reconcileQaSnapshot(current, changed).questions[0].summary, "")
  current.questions[1].summary = ""
  changed.questions[1].summary = "Confirm inspection timing."
  assert.equal(reconcileQaSnapshot(current, changed).questions[1].summary, "Confirm inspection timing.")
  const other = structuredClone(changed)
  other.version.id = "v2"
  assert.equal(reconcileQaSnapshot(current, other), other)
})
