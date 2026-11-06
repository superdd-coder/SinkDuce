import { test } from "node:test"
import assert from "node:assert/strict"
import { filterQaQuestions, qaPanelTransition } from "../src/lib/qa-view.ts"

test("omission is separate from closed status and searches complete original replies", () => {
  const questions = [
    { id: "a", included: true, status: "closed", type: "Confirm", text: "Confirm orchard access", latest_reply: "", replies: [{ body: "Gate C is open" }] },
    { id: "b", included: false, status: "waiting", type: "Provide", text: "Provide map", latest_reply: "", replies: [] },
  ]
  assert.deepEqual(filterQaQuestions(questions, "Gate C", "all", false).map(q => q.id), ["a"])
  assert.deepEqual(filterQaQuestions(questions, "", "waiting", false), [])
  assert.deepEqual(filterQaQuestions(questions, "", "waiting", true).map(q => q.id), ["b"])
})

test("status, type and omission filters combine with a preserved search", () => {
  const questions = [
    { id: "a", included: true, status: "waiting", type: "Confirm", text: "Confirm gallery access", replies: [] },
    { id: "b", included: true, status: "waiting", type: "Provide", text: "Provide gallery map", replies: [] },
    { id: "c", included: false, status: "closed", type: "Confirm", text: "Confirm gallery hours", replies: [] },
    { id: "d", included: true, status: "waiting", type: "Confirm", text: "Confirm reception hours", replies: [] },
  ]
  assert.deepEqual(filterQaQuestions(questions, "gallery", "waiting", true, "Confirm").map(q => q.id), ["a"])
  assert.deepEqual(filterQaQuestions(questions, "gallery", "all", true, "all").map(q => q.id), ["a", "b", "c"])
  assert.deepEqual(filterQaQuestions(questions, "gallery", "all", false, "all").map(q => q.id), ["a", "b"])
})

test("preview navigation preserves the selected question", () => {
  let s = qaPanelTransition({ mode: "overview", questionId: null }, { type: "select", questionId: "q1" })
  assert.deepEqual(s, { mode: "reading", questionId: "q1" })
  s = qaPanelTransition(s, { type: "preview" })
  assert.equal(s.mode, "preview")
  s = qaPanelTransition(s, { type: "list" })
  assert.deepEqual(s, { mode: "reading", questionId: "q1" })
  assert.equal(qaPanelTransition({ mode: "overview", questionId: null }, { type: "preview" }).mode, "overview")
  s = qaPanelTransition(s, { type: "overview" })
  assert.deepEqual(s, { mode: "overview", questionId: "q1" })
})


test("manual confirmation accepts unrecognized headings once source rows are resolved", async () => {
  const { canConfirmQaFields } = await import("../src/lib/qa-import-mapping.ts")
  const plan = { rows: [{ text: "Explain the museum entrance." }], table_rows: [{ kind: "question" }], mapping_valid: false }
  assert.equal(canConfirmQaFields(plan as never), true)
  assert.equal(canConfirmQaFields({ ...plan, table_rows: [{ kind: "review" }] } as never), false)
  assert.equal(canConfirmQaFields({ ...plan, rows: [{ text: "  " }] } as never), false)
})

test("changing a column to theme grouping removes its question type role and survives other role edits", async () => {
  const { changeColumnRole, columnRole } = await import("../src/lib/qa-import-mapping.ts")
  const plan = { headers: ["Question", "Type", "Reply", "Progress"], mapping: { question: 0, type: 1, replies: [2], extras: [3] } }
  const grouped = changeColumnRole(plan as never, 1, "group")
  assert.equal(columnRole(grouped, 1), "group")
  assert.equal(grouped.group, 1)
  assert.equal(grouped.type, null)
  const updated = changeColumnRole({ ...plan, mapping: grouped } as never, 3, "status")
  assert.equal(updated.group, 1)
  assert.equal(updated.status, 3)
  assert.deepEqual(updated.extras, [])
  const restored = changeColumnRole({ ...plan, mapping: updated } as never, 1, "type")
  assert.equal(restored.group, null)
  assert.equal(restored.type, 1)
})

test("parent choices include only same-group questions and exclude self and descendants", async () => {
  const { qaParentCandidates } = await import("../src/lib/qa-import-mapping.ts")
  const table_rows = [
    { key: "2", kind: "section", group_key: "" },
    { key: "3", kind: "note", group_key: "2" },
    { key: "4", kind: "question", group_key: "2" },
    { key: "5", kind: "child", group_key: "2", parent_key: "4" },
    { key: "6", kind: "question", group_key: "2" },
    { key: "7", kind: "question", group_key: "other" },
  ]
  const plan = { table_rows, rows: table_rows.map(r => ({ key: r.key, text: "Invented row", values: [] })), parents: { "5": "4" } }
  assert.deepEqual(qaParentCandidates(plan as never, "4").map(row => row.key), ["6"])
  assert.deepEqual(qaParentCandidates(plan as never, "5").map(row => row.key), ["4", "6"])
})
