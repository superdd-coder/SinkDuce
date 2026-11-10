import assert from "node:assert/strict"
import { test } from "node:test"
import { qaImportReplyPairs, qaImportSummary, qaReplyColumnImpact } from "../src/lib/qa-import-review.ts"

type Plan = Parameters<typeof qaImportSummary>[0]
const plan = (changes: Partial<Plan> = {}): Plan => ({
  rows: [], mapping: { question: 0, replies: [] }, matches: {}, column_matches: {},
  mapping_valid: true, table_rows: [], ...changes,
})
const question = (key: string) => ({ key, text: `Question ${key}`, values: [`Question ${key}`] })

test("reply cards follow column identities, preserve blank cells and keep new or old-only rounds on their own side", () => {
  const source = {
    headers: ["Question", "Reply", "Reply", "Follow-up"],
    mapping: { question: 0, replies: [1, 2, 3] },
    column_matches: { "1": "saved-b", "2": "saved-a", "3": "new" },
  } as Parameters<typeof qaImportReplyPairs>[0]
  const columns = [
    { id: "saved-a", label: "Reply", included: true, position: 0, absent_since: null },
    { id: "saved-b", label: "Reply", included: true, position: 1, absent_since: null },
    { id: "saved-c", label: "Workshop note", included: true, position: 2, absent_since: null },
  ]
  const current = { replies: [
    { column_id: "saved-a", body: "Older response A", included: true },
    { column_id: "saved-b", body: "Older response B", included: true },
    { column_id: "saved-c", body: "Old note", included: true },
  ] } as NonNullable<Parameters<typeof qaImportReplyPairs>[2]["current"]>
  const incoming = { key: "4", text: "Can we borrow brushes?", values: ["Can we borrow brushes?", "", "Incoming", "Bring an apron."], formatted_values: { "2": "**Incoming**" } }
  const pairs = qaImportReplyPairs(source, columns, { key: "4", group: "matched", current, incoming })
  assert.deepEqual(pairs.map(pair => [pair.current?.body, pair.incoming?.body]), [
    ["Older response B", ""], ["Older response A", "**Incoming**"], [undefined, "Bring an apron."], ["Old note", undefined],
  ])
  const old = qaImportReplyPairs(source, columns, { key: "old", group: "old", current })
  assert.equal(old.length, 3)
  assert.ok(old.every(pair => !pair.incoming && pair.column === undefined))
})

test("a first import summarizes new questions and reply columns", () => {
  assert.deepEqual(qaImportSummary(plan({
    rows: [question("2"), question("3")], matches: { "2": "new", "3": "new" },
    mapping: { question: 0, replies: [1, 2] }, column_matches: { "1": "new", "2": "new" },
  }), { questions: [] }), {
    questions: { total: 2, added: 2, continued: 0, pending: 0, omitted: 0 },
    replies: { total: 2, added: 2, continued: 0, pending: 0 },
    rowsNeedingPurpose: 0, groups: 0, notes: 0,
  })
})

test("new and continued questions are separate and only currently included omissions count", () => {
  const summary = qaImportSummary(plan({
    rows: [question("2"), question("3"), question("4")],
    matches: { "2": "q-a", "3": "q-b", "4": "new" },
  }), { questions: [
    { id: "q-a", included: true }, { id: "q-b", included: false },
    { id: "q-c", included: true }, { id: "q-d", included: false },
  ] })
  assert.deepEqual(summary.questions, { total: 3, added: 1, continued: 2, pending: 0, omitted: 1 })
})

test("column-derived groups and explicit sections each count once in the import summary", () => {
  const summary = qaImportSummary(plan({
    mapping: { question: 0, group: 1, replies: [] },
    rows: [
      { ...question("2"), group_key: "column:2" },
      { ...question("3"), group_key: "column:2" },
      { ...question("4"), group_key: "column:4" },
      { ...question("6"), group_key: "5" },
      question("7"),
    ],
    table_rows: [{ key: "5", kind: "section", values: ["Storage records"] }],
  }), { questions: [] })
  assert.equal(summary.groups, 3)
})

test("missing decisions remain pending while irrelevant map entries do not inflate counts", () => {
  const summary = qaImportSummary(plan({
    rows: [question("2"), question("3"), question("4")],
    matches: { "2": "q-a", "3": null, "old": "new", "older": null },
    mapping: { question: 0, replies: [1, 3, 4] },
    column_matches: { "1": "c-a", "3": "new", "old": null, "older": "new" },
  }), { questions: [{ id: "q-a", included: true }, { id: "q-b", included: true }] })
  assert.deepEqual(summary.questions, { total: 3, added: 0, continued: 1, pending: 2, omitted: null })
  assert.deepEqual(summary.replies, { total: 3, added: 1, continued: 1, pending: 1 })
})

test("omissions stay unknown until source row purposes and question assignments are settled", () => {
  const source = plan({
    rows: [question("2")], matches: { "2": "q-a" },
    table_rows: [
      { key: "1", kind: "section", values: ["Nursery"] },
      { key: "2", kind: "question", values: ["Describe storage."] },
      { key: "3", kind: "child", values: ["Confirm dimensions."] },
      { key: "4", kind: "note", values: ["Use the room guide."] },
      { key: "5", kind: "review", values: ["Unclear row"] },
      { key: "6", kind: "header", values: ["Repeated label"] },
      { key: "7", kind: "continuation", values: ["More detail"] },
      { key: "8", kind: "exclude", values: ["Footer"] },
    ],
  })
  const summary = qaImportSummary(source, { questions: [{ id: "q-b", included: true }] })
  assert.equal(summary.questions.omitted, null)
  assert.equal(summary.questions.total, 1)
  assert.equal(summary.rowsNeedingPurpose, 1)
  assert.equal(summary.groups, 1)
  assert.equal(summary.notes, 1)
  assert.equal(qaImportSummary({ ...source, table_rows: [], mapping_valid: false }, { questions: [] }).questions.omitted, null)
})

test("reply choices do not make settled omitted-question counts uncertain", () => {
  const summary = qaImportSummary(plan({
    rows: [question("2")], matches: { "2": "new" },
    mapping: { question: 0, replies: [1] }, column_matches: { "1": null },
  }), { questions: [{ id: "q-a", included: true }] })
  assert.equal(summary.questions.omitted, 1)
  assert.equal(summary.replies.pending, 1)
})

test("duplicate existing question assignments suppress omission claims", () => {
  const summary = qaImportSummary(plan({
    rows: [question("2"), question("3")], matches: { "2": "q-a", "3": "q-a" },
  }), { questions: [{ id: "q-a", included: true }, { id: "q-b", included: true }] })
  assert.equal(summary.questions.continued, 2)
  assert.equal(summary.questions.omitted, null)
})

test("summarizing does not change the plan or existing question membership", () => {
  const source = plan({ rows: [question("2")], matches: { "2": "new" } })
  const list = { questions: [{ id: "q-a", included: true }] }
  const before = JSON.stringify({ source, list })
  qaImportSummary(source, list)
  assert.equal(JSON.stringify({ source, list }), before)
})

test("reply column impact counts incoming filled cells and recognizes a reserved empty column", () => {
  const source = plan({
    rows: [
      { ...question("2"), values: ["Entry access", "Use the east door."] },
      { ...question("3"), values: ["Cabinet count", "0"] },
      { ...question("4"), values: ["Room dimensions", " \n "] },
    ],
  })
  assert.deepEqual(qaReplyColumnImpact(source, { questions: [] }, 1, "new"), { filled: 2, empty: false, cleared: 0 })
  assert.deepEqual(qaReplyColumnImpact(source, { questions: [] }, 2, "new"), { filled: 0, empty: true, cleared: 0 })
})

test("clear counts use only the same imported question and selected existing reply column", () => {
  const source = plan({
    rows: [question("2"), question("3"), question("4"), question("5")],
    matches: { "2": "q-a", "3": "q-b", "4": "new", "5": null },
  })
  const list = { questions: [
    { id: "q-a", replies: [{ column_id: "c-a", body: "Earlier entrance reply." }, { column_id: "c-b", body: "Earlier revision." }] },
    { id: "q-b", replies: [{ column_id: "c-a", body: " \n " }, { column_id: "c-b", body: "Earlier cabinet reply." }] },
    { id: "q-absent", replies: [{ column_id: "c-a", body: "Unrelated room reply." }] },
  ] }
  assert.deepEqual(qaReplyColumnImpact(source, list, 1, "c-a"), { filled: 0, empty: true, cleared: 1 })
  assert.deepEqual(qaReplyColumnImpact(source, list, 1, "c-b"), { filled: 0, empty: true, cleared: 2 })
  assert.equal(qaReplyColumnImpact(source, list, 1, "new").cleared, 0)
  assert.equal(qaReplyColumnImpact(source, list, 1, null).cleared, 0)
  assert.equal(qaReplyColumnImpact(source, list, 1, "missing").cleared, 0)
})

test("a partially filled column clears only blank incoming cells with an earlier nonblank reply", () => {
  const source = plan({
    rows: [
      { ...question("2"), values: ["Entry access", "Updated entrance reply."] },
      { ...question("3"), values: ["Cabinet dimensions", "  "] },
    ],
    matches: { "2": "q-a", "3": "q-b" },
  })
  const list = { questions: [
    { id: "q-a", replies: [{ column_id: "c-a", body: "Earlier entrance reply." }] },
    { id: "q-b", replies: [{ column_id: "c-a", body: "Earlier cabinet reply." }] },
  ] }
  const before = JSON.stringify({ source, list })
  assert.deepEqual(qaReplyColumnImpact(source, list, 1, "c-a"), { filled: 1, empty: false, cleared: 1 })
  assert.equal(JSON.stringify({ source, list }), before)
})
