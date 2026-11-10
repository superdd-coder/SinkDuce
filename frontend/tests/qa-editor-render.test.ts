import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { after, before, test } from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { createServer } from "vite"
import { Editor } from "@tiptap/core"
import { fileURLToPath } from "node:url"

let server: Awaited<ReturnType<typeof createServer>>
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage")
before(async () => {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: undefined })
  const root = fileURLToPath(new URL("../", import.meta.url))
  server = await createServer({
    root, configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: { alias: { "@": root + "src", "#edition": root + "src/edition" } },
    optimizeDeps: { noDiscovery: true },
  })
})
after(async () => {
  await server?.close()
  if (originalStorage) Object.defineProperty(globalThis, "localStorage", originalStorage)
  else Reflect.deleteProperty(globalThis, "localStorage")
})

const reply = { column_id: "c1", label: "Inspection response", body: "", included: true, text_revision_id: "r1", sources: [] }
const question = { id: "q1", included: true, replies: [reply] }
const data = { id: "l1", version: { id: "v1", number: 1 }, can_edit: true, questions: [question] }

test("editable replies expose a textbox directly and column names have a named edit control", async () => {
  const { QaReplyEditor } = await server.ssrLoadModule("/src/components/qa/qa-reply-editor.tsx")
  const html = renderToStaticMarkup(createElement(QaReplyEditor, {
    collectionId: "example", data, question, reply, position: 1, busy: false,
    onSave: async () => data, onRename: async () => true, onSource() {},
  }))
  assert.match(html, /role="textbox"[^>]*aria-label="Inspection response"/)
  assert.match(html, /Write a reply…/)
  assert.equal((html.match(/role="textbox"/g) || []).length, 1)
  assert.match(html, /aria-label="Edit reply column name"/)
  assert.doesNotMatch(html, />Write reply<|>Save reply</)
  const draftButton = html.match(/<button[^>]*aria-label="AI draft"[^>]*>/)?.[0]
  assert.equal(draftButton, undefined, "drafting is hidden before entering editing")
  assert.doesNotMatch(html, />Saved</)
})

test("reply display renders emphasis safely without heading hierarchy", async () => {
  const { QaReplyText } = await server.ssrLoadModule("/src/components/qa/qa-reply-text.tsx")
  const html = renderToStaticMarkup(createElement(QaReplyText, {
    body: "Please **confirm** *access* and ***both***.\n\n# Plain label\n<script>unsafe</script>",
  }))
  assert.match(html, /<strong>confirm<\/strong>/)
  assert.match(html, /<em>access<\/em>/)
  assert.match(html, /<strong><em>both<\/em><\/strong>/)
  assert.match(html, /&lt;script&gt;unsafe&lt;\/script&gt;/)
  assert.doesNotMatch(html, /<h[1-6]|<script|\*\*confirm/)
})

test("reply source buttons aggregate files without dropping their quoted excerpts", async () => {
  const { QaReplyEditor } = await server.ssrLoadModule("/src/components/qa/qa-reply-editor.tsx")
  const base = { kind: "project", file_id: "workbook", version_id: "version_one", filename: "Cabinet register.xlsx", page_number: 1 }
  const sources = [
    { ...base, chunk_id: "a", excerpt: "The cabinet input records a capacity allowance." },
    { ...base, chunk_id: "b", excerpt: "The allowance is listed separately from the schedule." },
    { ...base, page_number: 3, chunk_id: "c", excerpt: "A separate page states the inspection interval." },
    { ...base, file_id: "another_workbook", chunk_id: "d", excerpt: "An unrelated file with the same display name." },
  ]
  const html = renderToStaticMarkup(createElement(QaReplyEditor, {
    collectionId: "example", data: { ...data, can_edit: false }, question,
    reply: { ...reply, body: "Could you clarify the allowance?", sources }, position: 1, busy: false,
    onSave: async () => null, onRename: async () => false, onSource() {},
  }))
  assert.equal((html.match(/<span>Cabinet register\.xlsx<\/span>/g) || []).length, 2)
  assert.match(html, /3 excerpts/)
  assert.match(html, /1 excerpt/)
})

test("the quote reader renders every selected excerpt including tables with the shared source policy", async () => {
  const { QaSourceExcerpts } = await server.ssrLoadModule("/src/components/qa/qa-materials.tsx")
  assert.equal(typeof QaSourceExcerpts, "function")
  const base = { kind: "project", file_id: "workbook", version_id: "v1", filename: "Cabinet register.xlsx", page_number: 1 }
  const html = renderToStaticMarkup(createElement(QaSourceExcerpts, {
    collectionId: "example", sources: [
      { ...base, chunk_id: "a", excerpt: "| Cabinet | Allowance |\n|---|---|\n| Display | 7 |" },
      { ...base, chunk_id: "b", excerpt: "The **inspection** assumption is provisional." },
      { ...base, page_number: 3, chunk_id: "c", excerpt: "<script>unsafe()</script>\n\nPlease verify the calculation separately." },
    ],
  }))
  assert.equal((html.match(/class="qa-source-quote"/g) || []).length, 3)
  assert.match(html, /<div class="pm-recall-md-table-wrap"><table/)
  assert.match(html, /<strong>inspection<\/strong>/)
  assert.match(html, /Please verify the calculation separately/)
  assert.match(html, /Page 3/)
  assert.doesNotMatch(html, /<script/)
})

test("tables in Q&A excerpts retain their column widths inside a horizontal scroller", async () => {
  const css = await readFile(new URL("../src/components/qa/qa.css", import.meta.url), "utf8")
  const scroller = css.match(/\.qa-source-quote \.pm-recall-md-table-wrap\s*\{([^}]+)\}/)?.[1] ?? ""
  const table = css.match(/\.qa-source-quote \.pm-recall-md-table-wrap > table\s*\{([^}]+)\}/)?.[1] ?? ""
  assert.match(scroller, /overflow-x:\s*auto/, "the table wrapper must scroll independently of the excerpt pane")
  assert.match(table, /(?:^|;)\s*width:\s*max-content/, "the table must size its columns from its contents")
})

test("missing requested evidence names the material and keeps older proposals compatible", async () => {
  const { QaEvidenceNotice } = await server.ssrLoadModule("/src/components/qa/qa-reply-editor.tsx")
  const named = renderToStaticMarkup(createElement(QaEvidenceNotice, {
    proposal: { insufficient_evidence: true, unverified_sources: ["Cabinet cost register", "  ", "Inspection schedule"] },
  }))
  assert.match(named, /Could not locate evidence from the requested material: Cabinet cost register, Inspection schedule\./)
  assert.match(named, /The draft leaves this point open for confirmation\./)
  assert.doesNotMatch(named, /Available evidence is incomplete/)
  const older = renderToStaticMarkup(createElement(QaEvidenceNotice, { proposal: { insufficient_evidence: true } }))
  assert.match(older, /Available evidence is incomplete/)
  assert.equal(renderToStaticMarkup(createElement(QaEvidenceNotice, {
    proposal: { insufficient_evidence: false, unverified_sources: ["Cabinet cost register"] },
  })), "")
})

test("missing requested evidence notice is translated and material names stay plain text", async () => {
  const { QaEvidenceNotice } = await server.ssrLoadModule("/src/components/qa/qa-reply-editor.tsx")
  const { t } = await server.ssrLoadModule("/src/i18n/index.ts")
  const html = renderToStaticMarkup(createElement(QaEvidenceNotice, {
    proposal: { insufficient_evidence: true, unverified_sources: ["Cabinet register <review>"] },
  }))
  assert.match(html, /Cabinet register &lt;review&gt;/)
  assert.doesNotMatch(html, /<review>/)
  assert.equal(t("zh-CN", "qa.unverifiedSources", { sources: "柜体采购表" }), "未定位到可核对的资料：柜体采购表。草稿保留了待确认事项。")
})

test("reply editor serializes nested emphasis and line breaks without adding block features", async () => {
  const { replyExtensions } = await server.ssrLoadModule("/src/lib/qa-reply-document.ts")
  const { replyDocument, replyRuns } = await server.ssrLoadModule("/src/lib/qa-reply-format.ts")
  const body = "Please **confirm** *access* and ***both***.\nNext line\n\nFinal paragraph."
  const editor = new Editor({ element: null, extensions: replyExtensions(), content: replyDocument(body) })
  try {
    const saved = editor.storage.markdown.getMarkdown()
    assert.deepEqual(replyRuns(saved), replyRuns(body))
    assert.deepEqual(Object.keys(editor.schema.marks).sort(), ["bold", "italic"])
    assert.deepEqual(Object.keys(editor.schema.nodes).sort(), ["doc", "hardBreak", "paragraph", "text"])
    assert.ok(editor.extensionManager.extensions.find(e => e.name === "bold")?.config.addKeyboardShortcuts)
  } finally { editor.destroy() }
})

test("historical replies stay read-only and do not offer renaming or AI drafting", async () => {
  const { QaReplyEditor } = await server.ssrLoadModule("/src/components/qa/qa-reply-editor.tsx")
  const html = renderToStaticMarkup(createElement(QaReplyEditor, {
    collectionId: "example", data: { ...data, can_edit: false }, question,
    reply: { ...reply, body: "The inspection is complete." }, position: 1, busy: false,
    onSave: async () => null, onRename: async () => false, onSource() {},
  }))
  assert.match(html, /The inspection is complete\./)
  assert.doesNotMatch(html, /<textarea|aria-label="Edit reply column name"|aria-label="AI draft"/)
})

test("an empty current reply does not automatically display cleared historical text", async () => {
  const { QaReplyEditor } = await server.ssrLoadModule("/src/components/qa/qa-reply-editor.tsx")
  const html = renderToStaticMarkup(createElement(QaReplyEditor, {
    collectionId: "example", data, question,
    reply: { ...reply, historical_body: "An obsolete nursery note.", cleared_since: 1 }, position: 1, busy: false,
    onSave: async () => data, onRename: async () => true, onSource() {},
  }))
  assert.doesNotMatch(html, /An obsolete nursery note|qa-cleared/)
  assert.match(html, /role="textbox"/)
})

test("new reply columns start with a named input and no dialog", async () => {
  const { QaColumnName } = await server.ssrLoadModule("/src/components/qa/qa-column-name.tsx")
  const html = renderToStaticMarkup(createElement(QaColumnName, {
    label: "Reply 3", editable: true, initialEditing: true, onSave: async () => true,
  }))
  assert.match(html, /<input[^>]*aria-label="Reply column name"/)
  assert.match(html, /value="Reply 3"/)
  assert.doesNotMatch(html, /role="dialog"/)
})

test("import review presents source columns horizontally with a role control beneath each header", async () => {
  const { QaImportTable } = await server.ssrLoadModule("/src/components/qa/qa-import-table.tsx")
  const plan = {
    header_row: 2, headers: ["Question", "Response", "Coordinator"], region: { start_column: 1, end_column: 3 },
    mapping: { question: 0, replies: [1], extras: [2] }, rows: [{ key: "3", values: ["Explain museum access.", "", "Desk team"], text: "Explain museum access." }],
    table_rows: [{ key: "3", kind: "question", values: ["Explain museum access.", "", "Desk team"] }],
  }
  const html = renderToStaticMarkup(createElement(QaImportTable, { plan, busy: false, onRole() {}, onRow() {} }))
  assert.match(html, /<table/)
  assert.match(html, /<thead/)
  assert.match(html, /<tbody/)
  assert.match(html, /Coordinator/)
  assert.match(html, /Additional field/)
  assert.match(html, /Desk team/)
  assert.doesNotMatch(html, /<select/)
})

test("outline shows groups, independent child questions and notes without detail buttons", async () => {
  const { QaOutlineList } = await server.ssrLoadModule("/src/components/qa/qa-outline.tsx")
  const questions = [{ id: "q1", text: "Describe the seed cabinet.", included: true }, { id: "q2", text: "Confirm its lock.", included: true }]
  const outline = [{ id: "g1", kind: "group", text: "Seed records" }, { id: "n1", kind: "note", text: "Use the nursery register.", group_id: "g1" }, { id: "q1", kind: "question", group_id: "g1" }, { id: "q2", kind: "question", group_id: "g1", parent_id: "q1" }]
  const html = renderToStaticMarkup(createElement(QaOutlineList, {
    data: { ...data, questions, outline }, visible: questions, busy: false, filtered: false,
    renderQuestion: (q: { text: string }) => createElement("button", null, q.text),
    onMove: async () => false, onEdit: async () => false, onCreate: async () => false, onAddQuestion() {},
  }))
  assert.match(html, /Seed records/)
  assert.match(html, /data-depth="1"/)
  assert.match(html, /<p[^>]*>Use the nursery register\.<\/p>/)
  assert.doesNotMatch(html, /<button[^>]*>Use the nursery register/)
  assert.match(html, /aria-label="Move item"/)
})

test("recognized preview shows joined question text and independent notes, without repeated headers", async () => {
  const { QaImportResult } = await server.ssrLoadModule("/src/components/qa/qa-import-result.tsx")
  const plan = {
    mapping: { question: 0, replies: [1] },
    rows: [{ key: "2", text: "Describe the seed cabinet. Include storage dimensions." }],
    table_rows: [
      { key: "1", kind: "header", values: ["Repeated heading", "Reply"] },
      { key: "2", kind: "question", values: ["Describe the seed cabinet.", ""] },
      { key: "3", kind: "continuation", values: ["Include storage dimensions.", ""] },
      { key: "4", kind: "note", values: ["Use the room guide.", ""] },
    ],
  }
  const html = renderToStaticMarkup(createElement(QaImportResult, { plan }))
  assert.match(html, /Describe the seed cabinet\. Include storage dimensions\./)
  assert.match(html, /Use the room guide\./)
  assert.doesNotMatch(html, /Repeated heading/)
})

test("import decisions name the destination reply column without comparing arbitrary example replies", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const plan = {
    mapping: { question: 0, replies: [1] }, headers: ["Question", "Inspection response"],
    rows: [{ key: "2", text: "Describe the nursery entrance.", values: ["Describe the nursery entrance.", "Use the east gate."] }],
    matches: { "2": "q1" }, column_matches: { "1": null }, table_rows: [],
    mapping_valid: true, ai_status: "ready", structure_status: "ready",
  }
  const list = { ...data, title: "Garden register", columns: [{ id: "c1", label: "Earlier response", included: true }], questions: [{ ...question, text: "Describe the nursery entrance.", replies: [{ ...reply, body: "Use the north gate." }] }] }
  const html = renderToStaticMarkup(createElement(QaImportReview, { plan, list, busy: false, onQuestion() {}, onColumn() {}, onAdjust() {} }))
  assert.match(html, /Create a new reply column/)
  assert.match(html, /Write into “Earlier response”/)
  assert.doesNotMatch(html, /Use the north gate\.|Compare replies to the same question/)
  assert.match(html, /Earlier response/)
  assert.ok(html.indexOf("Create a new reply column") < html.indexOf("Preview imported content"))
  assert.equal((html.match(/<details\b/g) || []).length, 1)
  assert.doesNotMatch(html, /<details[^>]* open/)
  assert.doesNotMatch(html, />All matches</)
})

test("recognized question links are outcomes with an optional edit action rather than a form of dropdowns", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const plan = {
    mapping: { question: 0, replies: [] }, headers: ["Question"],
    rows: [
      { key: "2", text: "Describe cabinet access.", values: ["Describe cabinet access."] },
      { key: "3", text: "Confirm the spare key.", values: ["Confirm the spare key."] },
    ], matches: { "2": "q1", "3": "new" }, column_matches: {}, table_rows: [], mapping_valid: true,
  }
  const list = { ...data, title: "Museum register", columns: [], questions: [
    { ...question, text: "Describe the cabinet entrance.", summary: "Cabinet access" },
    { id: "q2", included: true, text: "Confirm the inspection interval.", replies: [] },
  ] }
  const html = renderToStaticMarkup(createElement(QaImportReview, { plan, list, busy: false, onQuestion() {}, onColumn() {}, onAdjust() {} }))
  assert.match(html, /Continue existing/)
  assert.match(html, /Add new/)
  assert.match(html, /Not in this file/)
  assert.match(html, /Cabinet access/)
  assert.match(html, /Describe cabinet access\./)
  assert.match(html, />Change</)
  assert.doesNotMatch(html, /aria-haspopup="listbox"|Update:|qa-confirmed-row/)
  assert.doesNotMatch(html, /<details[^>]* open/)
})

test("an empty incoming reply column explains clearing without borrowing another question's reply", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const plan = {
    mapping: { question: 0, replies: [1] }, headers: ["Question", "Response"],
    rows: [{ key: "2", text: "Describe cabinet access.", values: ["Describe cabinet access.", ""] }],
    matches: { "2": "q1" }, column_matches: { "1": null }, table_rows: [], mapping_valid: true,
  }
  const list = { ...data, title: "Museum register", columns: [{ id: "c1", label: "Earlier response", included: true }], questions: [
    { ...question, text: "Describe cabinet access.", replies: [{ ...reply, body: "Use the staff entrance." }] },
    { id: "q2", text: "Confirm the inspection interval.", included: true, replies: [{ ...reply, body: "Unrelated inspection note." }] },
  ] }
  const html = renderToStaticMarkup(createElement(QaImportReview, { plan, list, busy: false, onQuestion() {}, onColumn() {}, onAdjust() {} }))
  assert.match(html, /This column is empty/)
  assert.match(html, /1 existing reply will become blank/)
  assert.doesNotMatch(html, /Unrelated inspection note|Use the staff entrance|Example from/)
})

test("even an automatically linked reply column discloses cleared replies and absent old columns", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const plan = {
    mapping: { question: 0, replies: [1] }, headers: ["Question", "Response"],
    rows: [{ key: "2", text: "Describe cabinet access.", values: ["Describe cabinet access.", ""] }],
    matches: { "2": "q1" }, column_matches: { "1": "c1" }, table_rows: [], mapping_valid: true,
  }
  const list = { ...data, title: "Museum register", columns: [
    { id: "c1", label: "Response", included: true }, { id: "c2", label: "Inspection follow-up", included: true },
  ], questions: [{ ...question, text: "Describe cabinet access.", replies: [{ ...reply, body: "Use the staff entrance." }] }] }
  const html = renderToStaticMarkup(createElement(QaImportReview, { plan, list, busy: false, onQuestion() {}, onColumn() {}, onAdjust() {} }))
  assert.match(html, /1 existing reply will become blank/)
  assert.match(html, /Reply columns not in this version/)
  assert.match(html, /Inspection follow-up/)
  assert.doesNotMatch(html, /aria-haspopup="listbox"/)
})

const unresolvedSourcePlan = {
  mapping: { question: 1, replies: [2], extras: [0], status: 3 },
  headers: ["Topic", "Request", "Response", "Readiness"],
  rows: [{ key: "2", text: "Describe the seed room.", values: ["Seeds", "Describe the seed room.", "Cool storage.", "Open"] }],
  table_rows: [
    { key: "2", kind: "question", values: ["Seeds", "Describe the seed room.", "Cool storage.", "Open"] },
    { key: "3", kind: "review", values: ["Pond", "When is the pond inspected?", "Every morning.", "Done"] },
  ],
  matches: { "2": "new" }, column_matches: { "2": "new" }, mapping_valid: false,
}

test("unresolved source rows are actionable without opening advanced settings", async () => {
  const { QaImportReview } = await server.ssrLoadModule("/src/components/qa/qa-import-review.tsx")
  const html = renderToStaticMarkup(createElement(QaImportReview, {
    plan: unresolvedSourcePlan, list: { ...data, title: "Garden register", columns: [] }, busy: false,
    onQuestion() {}, onColumn() {}, onAdjust() {}, onRows() {}, onRole() {}, onParent() {},
  }))
  assert.match(html, /1 row needs a purpose/)
  assert.match(html, /When is the pond inspected\?/)
  assert.match(html, /Every morning\./)
  assert.match(html, /<table/)
  assert.match(html, /<th[^>]*>[\s\S]*Response/)
  assert.match(html, /aria-label="Row 3 purpose"/)
  assert.match(html, />Treat all as questions</)
  assert.doesNotMatch(html, /qa-confirmed-decisions|will be omitted/)
})

test("blocked import gives an enabled review action and a concrete reason", async () => {
  const { QaImportSubmit } = await server.ssrLoadModule("/src/components/qa/qa-import-submit.tsx")
  const html = renderToStaticMarkup(createElement(QaImportSubmit, {
    plan: unresolvedSourcePlan, busy: false, duplicate: false, onReview() {}, onAdjust() {}, onImport() {},
  }))
  assert.match(html, /1 row still needs a purpose before import/)
  assert.match(html, />Review 1 row</)
  assert.doesNotMatch(html, /disabled=""/)
})

test("result preview retains column labels and separates replies from question text", async () => {
  const { QaImportResult } = await server.ssrLoadModule("/src/components/qa/qa-import-result.tsx")
  const html = renderToStaticMarkup(createElement(QaImportResult, { plan: unresolvedSourcePlan }))
  assert.match(html, /<table/)
  assert.match(html, /<th[^>]*>Response<\/th>/)
  assert.match(html, /<td[^>]*>[\s\S]*When is the pond inspected\?[\s\S]*<\/td>/)
  assert.doesNotMatch(html, /When is the pond inspected\?\nEvery morning/)
})

test("a subquestion waiting for a parent keeps the parent chooser visible", async () => {
  const { QaImportTable } = await server.ssrLoadModule("/src/components/qa/qa-import-table.tsx")
  const plan = { ...unresolvedSourcePlan, row_actions: { "3": "child" }, parents: { "3": "" } }
  const html = renderToStaticMarkup(createElement(QaImportTable, { plan, busy: false, initialReviewOnly: true, onRow() {}, onRole() {}, onParent() {} }))
  assert.match(html, /aria-label="Parent question"/)
  assert.match(html, /Subquestion/)
})

test("ready plans import directly, while a busy plan cannot be submitted again", async () => {
  const { QaImportSubmit } = await server.ssrLoadModule("/src/components/qa/qa-import-submit.tsx")
  const plan = { ...unresolvedSourcePlan, mapping_valid: true, table_rows: unresolvedSourcePlan.table_rows.slice(0, 1) }
  const props = { plan, duplicate: false, onReview() {}, onAdjust() {}, onImport() {} }
  const ready = renderToStaticMarkup(createElement(QaImportSubmit, { ...props, busy: false }))
  assert.match(ready, />Import 1 questions</)
  assert.doesNotMatch(ready, /disabled=""|role="status"/)
  const busy = renderToStaticMarkup(createElement(QaImportSubmit, { ...props, busy: true }))
  assert.match(busy, /disabled=""/)
})
