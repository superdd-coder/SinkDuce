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

test("optional import checks share one flat details entry while actual conflicts stay directly visible", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const longQuestion = "Describe the cabinet access arrangements. ".repeat(12)
  const longReply = "The museum courtyard has a separate visitor entrance. ".repeat(12)
  const next = { ...plan, rows: [{ ...plan.rows[0], text: longQuestion, values: [longQuestion, longReply] }], table_rows: [{ key: "2", kind: "question", values: [longQuestion, longReply] }], review: { ...plan.review, conflicts: [...plan.review.conflicts, { ...plan.review.conflicts[0], id: "decided-conflict", choice: "keep" }] } }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: next }))
  assert.equal((html.match(/<details\b/g) || []).length, 1)
  assert.match(html, /<summary>View details<\/summary>/)
  assert.ok(html.indexOf('data-conflict-id="conflict-a"') < html.indexOf("<details"))
  assert.ok(html.indexOf('data-conflict-id="decided-conflict"') > html.indexOf("<details"))
  assert.match(html, /Reply columns|Questions and columns absent from this file|Preview imported content/)
  assert.match(html, /aria-label="Change the connection for Excel column “Museum response”"/)
  assert.match(html, /aria-label="Change the question connection for Excel row 2"/)
  assert.doesNotMatch(html, /qa-merge-decisions[^>]*<summary|qa-column-comparison|qa-cell-detail/)
  assert.ok(html.includes(`<span>${longReply}</span>`))
})

test("reply-column choices use names without a redundant sample comparison", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const repeated = "Use the museum courtyard entrance."
  const next = { ...plan, rows: [{ ...plan.rows[0], values: [question.text, repeated] }], table_rows: [{ key: "2", kind: "question", values: [question.text, repeated] }], column_matches: { "1": null }, review: { ...plan.review, conflicts: [], unresolved: 0 } }
  const incomingList = { ...list, questions: [{ ...question, replies: [{ column_id: "column-a", body: repeated }] }] }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: next, list: incomingList }))
  const primary = html.slice(0, html.indexOf("<details"))
  assert.match(primary, /Where should the Excel column “Museum response” go\?|Write into “Museum response”|Create a new reply column/)
  assert.doesNotMatch(primary, /Use the museum courtyard entrance|Compare replies|Existing reply to the same question/)
  assert.equal((html.match(/Use the museum courtyard entrance\./g) || []).length, 1)
})

test("duplicate question and column links expose each corrective choice directly without opening details", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const next = { ...plan, rows: [plan.rows[0], { key: "3", text: "Describe the courtyard entrance.", values: ["Describe the courtyard entrance.", "", ""] }],
    headers: ["Question", "Museum response", "Curator response"], mapping: { question: 0, replies: [1, 2] },
    matches: { "2": question.id, "3": question.id }, column_matches: { "1": "column-a", "2": "column-a" },
    review: { ...plan.review, conflicts: [], unresolved: 0 },
  }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: next, duplicate: true }))
  const primary = html.slice(0, html.indexOf("<details"))
  assert.match(primary, /same existing question cannot be linked twice/)
  assert.match(primary, /same reply column cannot be linked twice/)
  assert.match(primary, /Describe cabinet access\./)
  assert.match(primary, /Describe the courtyard entrance\./)
  assert.equal((primary.match(/>This is a new question</g) || []).length, 2)
  assert.equal((primary.match(/>Create a new reply column</g) || []).length, 2)
  assert.doesNotMatch(html, /<details[^>]* open/)
  assert.doesNotMatch(html.slice(html.indexOf("<details")), /question connections|Reply column connections/)
})

test("the safe import review compares both edits and states preservation instead of obsolete clearing warnings", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const html = renderToStaticMarkup(createElement(QaImportReview, props))
  assert.match(html, /Saved in SinkDuce/)
  assert.match(html, /In Excel/)
  assert.match(html, /Saved entrance details\./)
  assert.match(html, /Excel entrance details\./)
  assert.match(html, /Keep saved content/)
  assert.match(html, /Use Excel content/)
  assert.match(html, /Blank cells keep saved replies/)
  assert.match(html, /Organizer follow-up/)
  assert.doesNotMatch(html, /existing reply will become blank|Reply columns not in this version|qa\.review\./)
})

test("first import summarizes only the replies it will add, and omits the summary when empty", async () => {
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
  assert.match(imported, /Replies to import: 1/)
  assert.doesNotMatch(imported, /Keep 0 replies|Blank cells keep saved replies/)
})

test("import preview names the recognized theme column and the table distinguishes it from question type", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const { QaImportTable } = await server.ssrLoadModule("/src/components/qa/qa-import-table.tsx")
  const row = { key: "2", text: question.text, values: ["Visitor entry", question.text, "", "Confirm"], group: "Visitor entry", group_key: "column:2" }
  const next = { ...plan, headers: ["Topic", "Question", "Response", "Request kind"], mapping: { question: 1, group: 0, replies: [2], type: 3 },
    rows: [row], table_rows: [{ ...row, kind: "question" }], matches: { "2": "new" }, column_matches: { "2": "new" }, review: undefined,
  }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: next, list: { ...list, questions: [], columns: [] } }))
  assert.match(html.slice(0, html.indexOf("<details")), /“Topic” is used to group questions/)
  assert.match(html, /Adjust recognition/)
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
  const clearing = { ...next, review: { ...next.review, counts: { ...next.review.counts, cleared_replies: 1 } } }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: clearing }))
  assert.match(html, /You chose to clear 1 saved replies/)
  assert.doesNotMatch(html, /Keep 0 replies|Blank cells keep saved replies/)
})

test("content conflicts keep the footer actionable and explain what still needs a decision", async () => {
  const { QaImportSubmit } = await server.ssrLoadModule("/src/components/qa/qa-import-submit.tsx")
  const html = renderToStaticMarkup(createElement(QaImportSubmit, { ...props, duplicate: false, onReview() {}, onImport() {} }))
  assert.match(html, /1 edit needs your choice/)
  assert.match(html, />Review 1 edit</)
  assert.doesNotMatch(html, /disabled=""|>Import /)
})

test("resolved content conflicts allow import without replaying already-made decisions", async () => {
  const { QaImportSubmit } = await server.ssrLoadModule("/src/components/qa/qa-import-submit.tsx")
  const ready = { ...plan, review: { ...plan.review, unresolved: 0, conflicts: [{ ...plan.review.conflicts[0], choice: "keep" }] } }
  const html = renderToStaticMarkup(createElement(QaImportSubmit, { ...props, plan: ready, duplicate: false, onReview() {}, onImport() {} }))
  assert.match(html, />Import 1 questions</)
  assert.doesNotMatch(html, /role="status"|disabled=""/)
})

test("missing entries expose explicit reversible membership choices in the shared details", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const html = renderToStaticMarkup(createElement(QaImportReview, props))
  assert.match(html, /Describe the display cabinet\./)
  assert.match(html, /Confirm the spare key\./)
  assert.match(html, /Kept in this version/)
  assert.match(html, /Omitted from this version/)
  assert.match(html, />Keep in this version</)
  assert.match(html, />Omit from this version</)
})

test("questions missing from Excel disclose removal and historical work before any opt-in to retain", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const missing = { ...plan, review: { ...plan.review, missing_questions: [
    { id: "question-b", text: "Describe the display cabinet.", kept: false, reason: "local_changes" },
    { id: "question-c", text: "Confirm the spare key.", kept: false, reason: "draft" },
  ] } }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: missing }))
  assert.match(html.slice(0, html.indexOf("<details")), /Omit from this version<\/dt><dd>2/)
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
  const tree = elements(module.QaImportPreservedItems({ plan: next, busy: false, onMerge: (change: unknown) => changes.push(change) }))
  const keep = tree.find(element => element.type === "button" && element.props.children === "qa.review.retainQuestion")
  assert.ok(keep)
  ;(keep.props.onClick as () => void)()
  assert.deepEqual(changes[0], { retained_questions: ["another-retained", "question-c"], removed_questions: ["another-removed"] })
})

test("a pending question identity does not prematurely describe unmatched questions as removed", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const next = { ...plan, matches: { "2": null } }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: next }))
  assert.match(html, /<dd>—<\/dd>/)
  assert.doesNotMatch(html, /Describe the display cabinet\.|Confirm the spare key\./)
})

test("a stale plan cannot import even when its earlier choices are complete", async () => {
  const { QaImportSubmit } = await server.ssrLoadModule("/src/components/qa/qa-import-submit.tsx")
  const ready = { ...plan, stale: true, review: { ...plan.review, unresolved: 0 } }
  const html = renderToStaticMarkup(createElement(QaImportSubmit, { ...props, plan: ready, duplicate: false, onReview() {}, onImport() {} }))
  assert.match(html, /list changed after this file was uploaded/)
  assert.match(html, /Check the latest changes before importing/)
  assert.match(html, />Check latest changes</)
  assert.doesNotMatch(html, /disabled=""|>Import 1 questions</)
})

test("a list-wide order conflict uses a list label rather than a nonexistent Excel row", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const structural = { ...plan.review.conflicts[0], id: "structure", row_key: "", question_id: "", column_key: null, kind: "structure", label: "Structure", current: "Display room → Cabinet storage", incoming: "Cabinet storage → Display room" }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: { ...plan, review: { ...plan.review, conflicts: [structural] } } }))
  assert.match(html, /Question order and groups/)
  assert.doesNotMatch(html, /Excel row {2}·/)
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
  assert.match(html, /<h2>The list has changed<\/h2>/)
  assert.doesNotMatch(html, /Ready to import|qa-review-ready-icon/)
})


test("content decisions wait until question and column connections are settled", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const pendingIdentity = { ...plan, matches: { "2": null }, column_matches: { "1": null } }
  const html = renderToStaticMarkup(createElement(QaImportReview, { ...props, plan: pendingIdentity }))
  assert.match(html, /This is a new question/)
  assert.match(html, /Create a new reply column/)
  assert.doesNotMatch(html, /data-conflict-id="conflict-a"|Keep saved content/)
})
