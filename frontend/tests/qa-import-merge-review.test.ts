import assert from "node:assert/strict"
import { before, after, test } from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { createServer } from "vite"
import { fileURLToPath } from "node:url"

let server: Awaited<ReturnType<typeof createServer>>
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage")
before(async () => {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: undefined })
  const root = fileURLToPath(new URL("../", import.meta.url))
  server = await createServer({ root, configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: { alias: { "@": root + "src", "#edition": root + "src/edition" } }, optimizeDeps: { noDiscovery: true },
  })
})
after(async () => {
  await server?.close()
  if (originalStorage) Object.defineProperty(globalThis, "localStorage", originalStorage)
  else Reflect.deleteProperty(globalThis, "localStorage")
})

const question = { id: "question-a", included: true, text: "Describe cabinet access.", replies: [{ column_id: "column-a", body: "Saved entrance details." }] }
const list = { title: "Museum register", questions: [question], columns: [{ id: "column-a", label: "Museum response", included: true }] }
const plan = {
  rows: [{ key: "2", text: question.text, values: [question.text, ""] }],
  mapping: { question: 0, replies: [1] }, matches: { "2": question.id }, column_matches: { "1": "column-a" },
  headers: ["Question", "Museum response"], mapping_valid: true, table_rows: [],
  content_choices: {}, blank_columns: [], retained_questions: [], removed_questions: [],
  review: {
    conflicts: [{ id: "conflict-a", row_key: "2", question_id: question.id, column_key: "1", kind: "reply", label: "Museum response", current: "Saved entrance details.", incoming: "Excel entrance details.", baseline: "Earlier entrance details.", choice: null }],
    unresolved: 1, missing_questions: [
      { id: "question-b", text: "Describe the display cabinet.", kept: true, reason: "local_changes" },
      { id: "question-c", text: "Confirm the spare key.", kept: false, reason: "not_in_file" },
    ], kept_columns: [{ id: "column-b", label: "Organizer follow-up" }],
    counts: { kept_replies: 3, updated_replies: 1, new_replies: 0, conflicts: 1, cleared_replies: 0 },
  },
}
const props = { plan, list, busy: false, onQuestion() {}, onColumn() {}, onRows() {}, onRole() {}, onParent() {}, onAdjust() {}, onMerge() {} }
const structureConflict = {
  ...plan.review.conflicts[0], id: "structure", row_key: "structure", question_id: "", column_key: null, kind: "structure",
  label: "Structure", current: "Display room\nCabinet storage\nKeep the aisle clear.",
  incoming: "Cabinet storage\nDisplay room\nLeave space beside the display.",
}
const structurePlan = { ...plan, review: { ...plan.review, conflicts: [structureConflict] } }

test("update review puts matched, new and current-only questions in paired cards under count filters", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const old = { ...question, id: "question-old", text: "Where is the pottery rack?" }
  const incoming = { ...plan.rows[0], key: "3", text: "Which aprons can be borrowed?", values: ["Which aprons can be borrowed?", ""] }
  const next = { ...plan, rows: [...plan.rows, incoming], matches: { "2": question.id, "3": "new" }, review: undefined }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: next, list: { ...list, questions: [question, old] } }))
  for (const group of ["matched", "added", "old"]) {
    assert.match(html, new RegExp(`data-qa-import-outcome="${group}"`))
    assert.match(html, new RegExp(`data-qa-import-group="${group}"`))
  }
  assert.match(html, /Where is the pottery rack\?/)
  assert.match(html, /Which aprons can be borrowed\?/)
  assert.doesNotMatch(html, /question connections|Reply column connections/)
})

test("first import starts with a three-question sample", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const rows = ["How is clay stored?", "Which shelves are reserved?", "Where are the brushes?", "When are finished pieces collected?"].map((text, index) => ({
    key: String(index + 2), text, values: [text, ""],
  }))
  const next = { ...plan, rows, matches: Object.fromEntries(rows.map(row => [row.key, "new"])), column_matches: { "1": "new" }, review: undefined }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: next, list: { ...list, questions: [], columns: [] } }))
  assert.match(html, /data-qa-import-outcome="questions"/)
  assert.match(html, /data-qa-import-outcome="replies"/)
  assert.match(html, /How is clay stored\?|Which shelves are reserved\?|Where are the brushes\?/)
  assert.doesNotMatch(html, /When are finished pieces collected\?|<details[^>]* open/)
  assert.match(html, /View all 4 questions/)
})

test("long comparisons start collapsed with the conflict marked on its question", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const longQuestion = "Describe the cabinet access arrangements. ".repeat(12)
  const longReply = "The museum courtyard has a separate visitor entrance. ".repeat(12)
  const next = { ...plan, rows: [{ ...plan.rows[0], text: longQuestion, values: [longQuestion, longReply] }], table_rows: [{ key: "2", kind: "question", values: [longQuestion, longReply] }], review: { ...plan.review, conflicts: [...plan.review.conflicts, { ...plan.review.conflicts[0], id: "decided-conflict", choice: "keep" }] } }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: next }))
  assert.match(html, /Needs review/)
  assert.match(html, /aria-label="Change the question connection for Excel row 2"/)
  assert.doesNotMatch(html, /<details[^>]* open|data-conflict-id=|<table|Reply column connections/)
  assert.ok(html.includes(longQuestion))
  assert.ok(!html.includes(longReply))
})

test("duplicate question links are marked on their cards and duplicate columns expose corrective choices", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const next = { ...plan, rows: [plan.rows[0], { key: "3", text: "Describe the courtyard entrance.", values: ["Describe the courtyard entrance.", "", ""] }],
    headers: ["Question", "Museum response", "Curator response"], mapping: { question: 0, replies: [1, 2] },
    matches: { "2": question.id, "3": question.id }, column_matches: { "1": "column-a", "2": "column-a" },
    review: { ...plan.review, conflicts: [], unresolved: 0 },
  }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: next, duplicate: true }))
  const primary = html
  assert.equal((primary.match(/data-qa-import-group="pending"/g) || []).length, 2)
  assert.match(primary, /Needs review/)
  assert.match(primary, /Describe cabinet access\./)
  assert.match(primary, /Describe the courtyard entrance\./)
  assert.equal((primary.match(/aria-label="Change the question connection/g) || []).length, 2)
  assert.doesNotMatch(html, /<details[^>]* open/)
  assert.doesNotMatch(html.slice(html.indexOf("<details")), /question connections|Reply column connections/)
})

test("an expanded reply conflict compares the two actual versions and offers the two choices", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const { QaImportComparison } = await server.ssrLoadModule("/src/components/qa/qa-import-comparison.tsx")
  const html = renderToStaticMarkup(createElement(QaImportReview, props))
  assert.match(html, /Needs review/)
  assert.doesNotMatch(html, /existing reply will become blank|Reply columns not in this version|qa\.review\./)
  const comparison = renderToStaticMarkup(createElement(QaImportComparison, {
    current: { label: "Current response", body: "" }, incoming: { label: "Incoming response", body: "" },
    position: 1, conflict: plan.review.conflicts[0], onChoice() {}, busy: false,
  }))
  assert.match(comparison, /Saved entrance details\./)
  assert.match(comparison, /Excel entrance details\./)
  assert.match(comparison, /Keep current reply/)
  assert.match(comparison, /Use Excel reply/)
  assert.equal((comparison.match(/class="qa-reply-number"[^>]*>01<\/span>/g) || []).length, 2)
  assert.match(comparison, /class="qa-import-side qa-import-current"/)
  assert.equal((comparison.match(/\bqa-import-version-choice\b/g) || []).length, 2)
  assert.match(comparison, /qa-import-card-mark is-pending[^>]*>[\s\S]*Needs review/)
})

test("first import shows detected question and column counts without a textual reply summary", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const next = { ...plan, matches: { "2": "new" }, column_matches: { "1": "new" }, review: { conflicts: [], unresolved: 0,
    missing_questions: [], kept_columns: [], counts: { kept_replies: 0, updated_replies: 0, new_replies: 0, conflicts: 0 },
  } }
  const initialList = { ...list, questions: [], columns: [] }
  const empty = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: next, list: initialList }))
  assert.doesNotMatch(empty, /qa-merge-summary/)
  const filled = { ...next, rows: [{ ...next.rows[0], values: [question.text, "The courtyard is open."] }],
    review: { ...next.review, counts: { ...next.review.counts, new_replies: 1 } },
  }
  const imported = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: filled, list: initialList }))
  assert.match(imported, /data-qa-import-outcome="replies"[\s\S]*?<strong>1<\/strong>/)
  assert.doesNotMatch(imported, /Replies to import|Keep 0 replies|Blank cells keep saved replies/)
})

test("the worksheet shows distinct group and question-type column controls", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const { QaImportTable } = await server.ssrLoadModule("/src/components/qa/qa-import-table.tsx")
  const row = { key: "2", text: question.text, values: ["Visitor entry", question.text, "", "Confirm"], group: "Visitor entry", group_key: "column:2" }
  const next = { ...plan, headers: ["Topic", "Question", "Response", "Request kind"], mapping: { question: 1, group: 0, replies: [2], type: 3 },
    rows: [row], table_rows: [{ ...row, kind: "question" }], matches: { "2": "new" }, column_matches: { "2": "new" }, review: undefined,
  }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: next, list: { ...list, questions: [], columns: [] } }))
  assert.match(html, /Visitor entry/)
  assert.doesNotMatch(html, /“Topic” is used to group questions|<table|<details[^>]* open/)
  const table = renderToStaticMarkup(createElement(QaImportTable, { plan: next, busy: false, onRole() {}, onRow() {} }))
  assert.match(table, /Topic · Column role[^>]*>[^<]*<span[^>]*>Question group/)
  assert.match(table, /Request kind · Column role[^>]*>[^<]*<span[^>]*>Question type/)
})

test("a later import omits an empty reply summary but still calls out explicitly cleared replies", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const next = { ...plan, review: { ...plan.review, conflicts: [], unresolved: 0, kept_columns: [], missing_questions: [],
    counts: { kept_replies: 0, updated_replies: 0, new_replies: 0, conflicts: 0, cleared_replies: 0 },
  } }
  assert.doesNotMatch(renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: next })), /qa-merge-summary/)
  const clearing = { ...next, blank_columns: ["1"], review: { ...next.review, counts: { ...next.review.counts, cleared_replies: 1 } } }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: clearing }))
  assert.match(html, /You chose to clear 1 saved replies/)
  assert.doesNotMatch(html, /Keep 0 replies|Blank cells keep saved replies/)
})

test("content conflicts keep the footer actionable and explain what still needs a decision", async () => {
  const { QaImportSubmit } = await server.ssrLoadModule("/src/components/qa/qa-import-submit.tsx")
  const html = renderToStaticMarkup(createElement(QaImportSubmit, { ...props, duplicate: false, onReview() {}, onImport() {} }))
  assert.match(html, /1 edit needs your choice/)
  assert.match(html, />Review pending items</)
  assert.match(html, /disabled=""[^>]*>Confirm import</)
})

test("resolved content conflicts allow import without replaying already-made decisions", async () => {
  const { QaImportSubmit } = await server.ssrLoadModule("/src/components/qa/qa-import-submit.tsx")
  const ready = { ...plan, review: { ...plan.review, unresolved: 0, conflicts: [{ ...plan.review.conflicts[0], choice: "keep" }] } }
  const html = renderToStaticMarkup(createElement(QaImportSubmit, { ...props, plan: ready, duplicate: false, onReview() {}, onImport() {} }))
  assert.match(html, />Confirm import</)
  assert.doesNotMatch(html, /role="status"|disabled=""/)
})

test("current-only cards mark retained questions and leave membership changes in the expanded card", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const old = plan.review.missing_questions.map(({ id, text }) => ({ ...question, id, text }))
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, list: { ...list, questions: [question, ...old] } }))
  assert.match(html, /Describe the display cabinet\./)
  assert.match(html, /Confirm the spare key\./)
  assert.match(html, /Kept in this version/)
  assert.equal((html.match(/data-qa-import-group="old"/g) || []).length, 2)
  assert.doesNotMatch(html, />Keep in this version<|>Omit from this version</)
})

test("the expanded current-only question discloses preserved historical work before opting in", async () => {
  const { QaImportPreservedItems } = await server.ssrLoadModule("/src/components/qa/qa-import-content-review.tsx")
  const missing = { ...plan, review: { ...plan.review, missing_questions: [
    { id: "question-b", text: "Describe the display cabinet.", kept: false, reason: "local_changes" },
    { id: "question-c", text: "Confirm the spare key.", kept: false, reason: "draft" },
  ] } }
  const html = missing.review.missing_questions.map(({ id }) => renderToStaticMarkup(createElement(QaImportPreservedItems, {
    plan: missing, questionId: id, busy: false, onMerge() {},
  }))).join("")
  assert.match(html, /SinkDuce edits remain in history/)
  assert.match(html, /Saved draft remains in history/)
  assert.equal((html.match(/>Keep in this version</g) || []).length, 2)
  assert.doesNotMatch(html, /Also keep in this version|Kept: edited|Kept: has a saved draft/)
})

/** Invoke real decision handlers while keeping the test independent of a browser. */
async function decisionHarness(component = "qa-import-content-review") {
  const { readFile } = await import("node:fs/promises")
  const React = await import("react")
  const ts = await import("typescript")
  const data = (source: string) => `data:text/javascript,${encodeURIComponent(source)}`
  const imports: Record<string, string> = {
    "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
    "lucide-react": import.meta.resolve("lucide-react"),
    "@/components/ui/button": data('export const Button="button"'),
    "@/i18n/use-t": data('export const useT=()=>key=>key'),
  }
  const source = await readFile(new URL(`../src/components/qa/${component}.tsx`, import.meta.url), "utf8")
  const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
    .replace(/from ["']([^"']+)["']/g, (statement, path: string) => imports[path] ? `from ${JSON.stringify(imports[path])}` : statement)
  const module = await import(data(compiled))
  type Element = React.ReactElement<Record<string, unknown>>
  function elements(node: unknown): Element[] {
    if (Array.isArray(node)) return node.flatMap(elements)
    if (!React.isValidElement<Record<string, unknown>>(node)) return []
    if (typeof node.type === "function") return elements((node.type as (props: unknown) => unknown)(node.props))
    return [node, ...elements(node.props.children)]
  }
  return { module, elements }
}

test("choosing Excel preserves unrelated decisions and a bulk choice affects only currently pending conflicts", async () => {
  const { module, elements } = await decisionHarness()
  const changes: unknown[] = []
  const next = { ...plan, content_choices: { "earlier-conflict": "keep", "obsolete-conflict": "incoming" }, review: {
    ...plan.review, conflicts: [plan.review.conflicts[0], { ...plan.review.conflicts[0], id: "conflict-b", choice: null }, { ...plan.review.conflicts[0], id: "earlier-conflict", choice: "keep" }],
  } }
  const tree = elements(module.QaImportContentReview({ ...props, plan: next, onMerge: (change: unknown) => changes.push(change) }))
  const find = (label: string) => {
    const button = tree.find(element => element.type === "button" && elements(element.props.children).length === 0 && element.props.children === label)
    assert.ok(button, label)
    return button.props.onClick as () => void
  }
  find("qa.review.excelAllPending")()
  assert.deepEqual(changes[0], { content_choices: { "earlier-conflict": "keep", "conflict-a": "incoming", "conflict-b": "incoming" } })
  const excel = tree.find(element => element.type === "button" && Array.isArray(element.props.children) && element.props.children.includes("qa.review.useExcel"))
  assert.ok(excel)
  ;(excel.props.onClick as () => void)()
  assert.deepEqual(changes[1], { content_choices: { "earlier-conflict": "keep", "conflict-a": "incoming" } })
})

test("membership overrides remove their opposite choice without losing other retained questions", async () => {
  const { module, elements } = await decisionHarness()
  const changes: unknown[] = []
  const next = { ...plan, retained_questions: ["another-retained"], removed_questions: ["question-c", "another-removed"] }
  const tree = elements(module.QaImportPreservedItems({ plan: next, questionId: "question-c", busy: false, onMerge: (change: unknown) => changes.push(change) }))
  const keep = tree.find(element => element.type === "button" && element.props.children === "qa.review.retainQuestion")
  assert.ok(keep)
  ;(keep.props.onClick as () => void)()
  assert.deepEqual(changes[0], { retained_questions: ["another-retained", "question-c"], removed_questions: ["another-removed"] })
})

test("a pending question identity does not prematurely describe unmatched questions as removed", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const next = { ...plan, matches: { "2": null } }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: next }))
  assert.match(html, /data-qa-import-outcome="old"[^>]*disabled=""[\s\S]*?<strong>—<\/strong>/)
  assert.doesNotMatch(html, /Describe the display cabinet\.|Confirm the spare key\./)
})

test("a stale plan cannot import even when its earlier choices are complete", async () => {
  const { QaImportSubmit } = await server.ssrLoadModule("/src/components/qa/qa-import-submit.tsx")
  const ready = { ...plan, stale: true, review: { ...plan.review, unresolved: 0 } }
  const html = renderToStaticMarkup(createElement(QaImportSubmit, { ...props, plan: ready, duplicate: false, onReview() {}, onImport() {} }))
  assert.match(html, /list changed after this file was uploaded/)
  assert.match(html, /Check the latest changes before importing/)
  assert.match(html, />Check latest changes</)
  assert.match(html, /disabled=""[^>]*>Confirm import</)
})

test("group labels appear on their own sides without a legacy arrangement comparison", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const next = { ...structurePlan, rows: [{ ...structurePlan.rows[0], group: "Front room" }] }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: next, list: { ...list, questions: [{ ...question, group: "Storage room" }] } }))
  assert.match(html, /Storage room/)
  assert.match(html, /Front room/)
  assert.doesNotMatch(html, /Compare arrangements|Question order and groups|Keep the aisle clear/)
})

test("a legacy arrangement choice never blocks the confirm button", async () => {
  const { QaImportSubmit } = await server.ssrLoadModule("/src/components/qa/qa-import-submit.tsx")
  const html = renderToStaticMarkup(createElement(QaImportSubmit, { ...props, plan: structurePlan, duplicate: false, onReview() {}, onImport() {} }))
  assert.match(html, />Confirm import</)
  assert.doesNotMatch(html, /disabled=""|Review pending items/)
})

test("stale submission rechecks the plan and never calls import", async () => {
  const { module, elements } = await decisionHarness("qa-import-submit")
  const actions: string[] = []
  const tree = elements(module.QaImportSubmit({ plan: { ...plan, stale: true }, busy: false, duplicate: false,
    onImport: () => actions.push("import"), onReview: () => actions.push("review"),
    onAdjust: () => actions.push("adjust"), onRecheck: () => actions.push("recheck"),
  }))
  const button = tree.find(element => element.type === "button")
  assert.ok(button)
  assert.equal(button.props.disabled, false)
  ;(button.props.onClick as () => void)()
  assert.deepEqual(actions, ["recheck"])
})

test("a stale review cannot present its old plan as ready to import", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: { ...plan, stale: true, review: { ...plan.review, unresolved: 0 } } }))
  assert.match(html, /<h2[^>]*>The list has changed<\/h2>/)
  assert.doesNotMatch(html, /Ready to import|qa-review-ready-icon/)
})


test("content decisions wait until question and column connections are settled", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const pendingIdentity = { ...plan, matches: { "2": null }, column_matches: { "1": null } }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: pendingIdentity }))
  assert.match(html, /data-qa-import-group="pending"/)
  assert.match(html, /Match an old question/)
  assert.doesNotMatch(html, /data-conflict-id="conflict-a"|Keep saved content/)
})
