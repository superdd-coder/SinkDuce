import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"
import * as React from "react"
import ts from "typescript"
import type { QaImportContext, QaImportPlan } from "../src/api/qa.ts"

type Element = React.ReactElement<Record<string, unknown>>
function elements(node: unknown): Element[] {
  if (Array.isArray(node)) return node.flatMap(elements)
  return React.isValidElement<Record<string, unknown>>(node) ? [node, ...elements(node.props.children)] : []
}

function pendingPlan(): QaImportPlan {
  return {
    id: "import-a", filename: "Cabinet questions.xlsx", plan_revision: 4, base_revision: 2,
    sheet: "Questions", sheets: ["Questions"], header_row: 1,
    headers: ["Question", "Museum response", "Organizer follow-up"],
    mapping: { question: 0, replies: [1, 2] }, mapping_valid: true, ai_status: "ready",
    rows: [
      { key: "2", text: "Explain cabinet access.", values: ["Explain cabinet access.", "Access uses a key.", ""] },
      { key: "3", text: "Describe the inspection schedule.", values: ["Describe the inspection schedule.", "Inspected each morning.", ""] },
    ],
    table_rows: [], matches: { "2": null, "3": null }, column_matches: { "1": null, "2": "column-follow-up" },
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

/** Exercise the real exchange handlers with only UI and API boundaries replaced. */
async function harness(initial: QaImportPlan, options: {
  save?: (plan: QaImportPlan) => Promise<QaImportPlan>;
  analyze?: (plan: QaImportPlan) => Promise<QaImportPlan>;
  loadList?: () => Promise<QaImportContext>;
} = {}) {
  const key = `__qa_import_followup_${Math.random()}`
  const globals = globalThis as unknown as Record<string, unknown>
  const cells: unknown[] = []
  let cursor = 0
  const saved: QaImportPlan[] = [], analyzed: QaImportPlan[] = []
  const payloads: Record<string, unknown>[] = []
  const runtime = {
    useState(initial: unknown) {
      const index = cursor++
      if (!(index in cells)) cells[index] = typeof initial === "function" ? initial() : initial
      return [cells[index], (value: unknown) => { cells[index] = typeof value === "function" ? value(cells[index]) : value }]
    },
    useRef(initial: unknown) { const index = cursor++; return cells[index] ??= { current: initial } },
    useEffect() { cursor++ },
    async patchQaImport(_collection: string, _list: string, plan: QaImportPlan, changes: Partial<QaImportPlan>) {
      const next = { ...plan, ...changes }
      payloads.push(structuredClone(changes))
      saved.push(structuredClone(next))
      return options.save ? options.save(next) : { ...next, plan_revision: next.plan_revision + 1 }
    },
    async analyzeQaImport(_collection: string, _list: string, plan: QaImportPlan) {
      analyzed.push(structuredClone(plan))
      return options.analyze ? options.analyze(plan) : {
        ...plan, plan_revision: plan.plan_revision + 1,
        column_matches: { ...plan.column_matches, "1": "column-response" },
      }
    },
    getQaImportContext: options.loadList,
  }
  globals[key] = runtime
  const data = (source: string) => `data:text/javascript,${encodeURIComponent(source)}`
  const stubs = (...names: string[]) => data(names.map(name => `export function ${name}(){ return null }`).join("\n"))
  const imports: Record<string, string> = {
    react: data(["useState", "useRef", "useEffect"].map(name => `export const ${name}=(...args)=>globalThis[${JSON.stringify(key)}].${name}(...args);`).join("\n")),
    "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
    "lucide-react": import.meta.resolve("lucide-react"),
    "./use-qa-modal": data("export const useQaModal=()=>({open:true,close(){},exit(){},onOpenChangeComplete(){}})"),
    "./qa-workbook-upload": stubs("QaWorkbookUpload"),
    "./qa-import-review": stubs("QaImportReview"),
    "./qa-import-submit": stubs("QaImportSubmit"),
    "./qa-import-table": stubs("QaImportTable"),
    "./qa-controls": stubs("QaActionMenu", "QaMenuItem", "QaField", "QaToggle"),
    "@/components/ui/button": stubs("Button"),
    "@/components/ui/dialog": stubs("Dialog", "DialogContent", "DialogHeader", "DialogTitle"),
    "@/components/ui/dropdown-select": stubs("DropdownSelect"),
    "@/components/ui/input": stubs("Input"),
    "@/lib/qa-import-mapping": new URL("../src/lib/qa-import-mapping.ts", import.meta.url).href,
    "@/lib/qa-download": stubs("saveQaDownload"),
    "@/stores/app-store": data("export const useAppStore=select=>select({qaDrafts:{}})"),
    "@/i18n/use-t": data("export const useT=()=>key=>key"),
    "@/api/client": data(["analyzeQaImport", "cancelQaImport", "createQaImportList", "commitQaImport", "exportQaFile", "getQaTemplates", "getQaExports", "downloadQaExport", "getQaImportContext", "patchQaImport", "uploadQaImport"].map(name => `export const ${name}=(...args)=>globalThis[${JSON.stringify(key)}].${name}?.(...args);`).join("\n")),
  }
  const source = await readFile(new URL("../src/components/qa/qa-exchange.tsx", import.meta.url), "utf8")
  const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
    .replace(/import ["']\.\/qa-exchange-refinement\.css["']/, "")
    .replace(/from ["']([^"']+)["']/g, (statement, path: string) => imports[path] ? `from ${JSON.stringify(imports[path])}` : statement)
  const module = await import(data(compiled))
  const props = { collectionId: "collection-a", list: null, mode: "import", close() {}, onImported() {},
    resume: { list: { id: "list-a", title: "Cabinet register", revision: 2, columns: [], questions: [] }, plan: initial } }
  return {
    saved, analyzed, payloads,
    render() {
      cursor = 0
      const tree = elements(module.QaExchange(props))
      const review = tree.find(element => typeof element.type === "function" && element.type.name === "QaImportReview")
      assert.ok(review)
      const submit = tree.find(element => typeof element.type === "function" && element.type.name === "QaImportSubmit")
      assert.ok(submit)
      const table = tree.find(element => typeof element.type === "function" && element.type.name === "QaImportTable")
      assert.ok(table)
      return {
        plan: review.props.plan as QaImportPlan,
        list: review.props.list as QaImportContext,
        busy: review.props.busy, submitBusy: submit.props.busy,
        columnMapping: (table.props.columnMapping || (table.props.plan as QaImportPlan).mapping) as QaImportPlan["mapping"],
        columnsBusy: table.props.columnsBusy ?? table.props.busy,
        onRole: table.props.onRole as (index: number, role: string) => void,
        onRecheck: submit.props.onRecheck as () => void,
        onReview: submit.props.onReview as () => void,
        reviewRef: review.props.reviewRef as { current: { focusDecision: () => void } | null },
        onQuestion: review.props.onQuestion as (key: string, target: string) => void,
        onColumn: review.props.onColumn as (key: string, target: string) => void,
        onMerge: review.props.onMerge as (changes: Partial<QaImportPlan>) => void,
        alerts: tree.filter(element => element.props.role === "alert"),
      }
    },
    async flush() { await new Promise(setImmediate) },
    dispose() { delete globals[key] },
  }
}

test("column roles stay editable while the latest changes save in revision order", async () => {
  const first = deferred<QaImportPlan>(), second = deferred<QaImportPlan>()
  const h = await harness(pendingPlan(), {
    save: next => next.plan_revision === 4 ? first.promise : second.promise,
  })
  try {
    h.render().onRole(1, "extra")
    await h.flush()
    assert.deepEqual(Object.keys(h.payloads[0]), ["mapping"], "one save is enough for an acknowledged plan")
    const pending = h.render()
    assert.equal(pending.columnsBusy, false)
    assert.equal(pending.submitBusy, true)
    assert.deepEqual(pending.columnMapping.extras, [1])
    pending.onRole(2, "status")
    pending.onRole(1, "number")
    assert.equal(h.saved.length, 1)
    assert.equal(h.render().columnMapping.number, 1)
    assert.equal(h.render().columnMapping.status, 2)

    first.resolve({ ...h.saved[0], plan_revision: 5 })
    await h.flush()
    assert.equal(h.saved.length, 2)
    assert.equal(h.saved[1].plan_revision, 5)
    assert.equal(h.saved[1].mapping.number, 1)
    assert.equal(h.saved[1].mapping.status, 2)
    assert.equal(h.render().columnsBusy, false)
    assert.equal(h.render().submitBusy, true)

    second.resolve({ ...h.saved[1], plan_revision: 6 })
    await h.flush()
    assert.equal(h.render().plan.plan_revision, 6)
    assert.equal(h.render().submitBusy, false)
    assert.equal(h.analyzed.length, 0, "manual column edits do not wait for an AI run")
  } finally { h.dispose() }
})

test("a failed column save restores the acknowledged roles and lets the user retry", async () => {
  const plan = pendingPlan()
  let failed = false
  const h = await harness(plan, {
    save: async next => {
      if (!failed) { failed = true; throw new Error("save unavailable") }
      return { ...next, plan_revision: next.plan_revision + 1 }
    },
  })
  try {
    h.render().onRole(1, "extra")
    await h.flush()
    const settled = h.render()
    assert.deepEqual(settled.columnMapping, plan.mapping)
    assert.equal(settled.columnsBusy, false)
    assert.equal(settled.submitBusy, false)
    assert.equal(settled.alerts[0]?.props.children, "qa.importPreview.columnSaveFailed")

    settled.onRole(1, "extra")
    await h.flush()
    assert.deepEqual(h.render().columnMapping.extras, [1])
    assert.equal(h.render().alerts.length, 0)
    assert.equal(h.analyzed.length, 0)
  } finally { h.dispose() }
})

test("changing a column saves unsent identity choices before rebuilding the mapping", async () => {
  const h = await harness({ ...pendingPlan(), mapping_valid: false })
  try {
    h.render().onQuestion("2", "question-access")
    assert.equal(h.saved.length, 0)
    h.render().onRole(1, "extra")
    await h.flush()
    assert.equal(h.saved.length, 2)
    assert.equal(h.payloads[0].matches && h.saved[0].matches["2"], "question-access")
    assert.equal(h.saved[1].plan_revision, 5)
    assert.deepEqual(h.saved[1].mapping.extras, [1])
    assert.equal(h.analyzed.length, 0)
  } finally { h.dispose() }
})

test("each identity choice saves immediately and only the final question triggers pending column analysis", async () => {
  const h = await harness(pendingPlan())
  try {
    h.render().onQuestion("2", "question-access")
    await h.flush()
    assert.equal(h.saved.length, 1)
    assert.equal(h.analyzed.length, 0, "remaining question ambiguity prevents column analysis")
    assert.equal(h.render().plan.plan_revision, 5)
    h.render().onColumn("2", "new")
    await h.flush()
    assert.equal(h.saved.length, 2)
    assert.equal(h.render().plan.plan_revision, 6)
    h.render().onQuestion("3", "question-inspection")
    await h.flush()
    assert.equal(h.saved.length, 3)
    assert.deepEqual(h.saved[2].matches, { "2": "question-access", "3": "question-inspection" })
    assert.deepEqual(h.saved[2].column_matches, { "1": null, "2": "new" })
    assert.equal(h.analyzed.length, 1)
    assert.equal(h.analyzed[0].plan_revision, 7, "analysis uses the revision acknowledged by the latest save")
    assert.equal(h.render().plan.plan_revision, 8)
    assert.deepEqual(h.render().plan.column_matches, { "1": "column-response", "2": "new" })
  } finally { h.dispose() }
})

test("a content decision saves immediately with current identities without another AI call", async () => {
  const plan = pendingPlan()
  plan.matches = { "2": "question-access", "3": "question-inspection" }
  plan.column_matches = { "1": null, "2": "new" }
  const h = await harness(plan, { save: async next => ({ ...next, plan_revision: 5 }) })
  try {
    h.render().onMerge({ content_choices: { "conflict-a": "keep" } })
    await h.flush()
    assert.equal(h.saved.length, 1)
    assert.deepEqual(h.saved[0].content_choices, { "conflict-a": "keep" })
    assert.equal(h.saved[0].column_matches["2"], "new")
    assert.equal(h.render().plan.plan_revision, 5)
    assert.equal(h.analyzed.length, 0)
  } finally { h.dispose() }
})

test("same-render choices use the latest local plan and in-flight requests lock stale callbacks", async () => {
  const save = deferred<QaImportPlan>()
  const analysis = deferred<QaImportPlan>()
  const plan = pendingPlan()
  plan.matches = { "2": "question-access", "3": "question-access" }
  plan.content_choices = { "obsolete-conflict": "incoming" }
  plan.blank_columns = ["1"]
  const h = await harness(plan, { save: () => save.promise, analyze: () => analysis.promise })
  try {
    const decisions = h.render()
    decisions.onColumn("2", "new")
    assert.equal(h.saved.length, 0, "a duplicate question assignment remains local until corrected")
    decisions.onQuestion("3", "question-inspection")
    assert.equal(h.saved.length, 1)
    assert.equal(h.render().busy, true)
    assert.deepEqual(h.saved[0].content_choices, {})
    assert.deepEqual(h.saved[0].blank_columns, [])
    decisions.onColumn("2", "column-unwanted")
    decisions.onQuestion("3", "new")
    assert.deepEqual(h.render().plan.matches, { "2": "question-access", "3": "question-inspection" })
    assert.equal(h.render().plan.column_matches["2"], "new")
    save.resolve({ ...h.saved[0], plan_revision: 5 })
    await h.flush()
    assert.equal(h.render().busy, true)
    assert.equal(h.analyzed.length, 1)
    analysis.resolve({ ...h.analyzed[0], plan_revision: 6, column_matches: { "1": "column-response", "2": "new" } })
    await h.flush()
    assert.equal(h.render().busy, false)
    assert.equal(h.saved.length, 1)
    assert.equal(h.render().plan.column_matches["2"], "new")
  } finally { h.dispose() }
})

for (const state of ["invalid-mapping", "unresolved-source", "duplicate-question", "duplicate-column"] as const) {
  test(`identity choices remain local without API calls when ${state}`, async () => {
    const plan = pendingPlan()
    plan.matches = { "2": "question-access", "3": null }
    if (state === "invalid-mapping") plan.mapping_valid = false
    if (state === "unresolved-source") plan.table_rows = [{ key: "4", values: ["A source note", "", ""], kind: "review" }]
    if (state === "duplicate-column") plan.column_matches = { "1": "column-response", "2": "column-response" }
    const h = await harness(plan)
    try {
      h.render().onQuestion("3", state === "duplicate-question" ? "question-access" : "new")
      await h.flush()
      assert.equal(h.saved.length, 0)
      assert.equal(h.analyzed.length, 0)
      assert.equal(h.render().plan.matches["3"], state === "duplicate-question" ? "question-access" : "new")
    } finally { h.dispose() }
  })
}

test("an unchanged identity choice makes no request and preserves completed content decisions", async () => {
  const plan = pendingPlan()
  plan.matches = { "2": "question-access", "3": "question-inspection" }
  plan.content_choices = { "content-a": "keep" }
  plan.blank_columns = ["1"]
  const h = await harness(plan)
  try {
    h.render().onQuestion("3", "question-inspection")
    h.render().onColumn("2", "column-follow-up")
    await h.flush()
    assert.equal(h.saved.length, 0)
    assert.equal(h.analyzed.length, 0)
    assert.deepEqual(h.render().plan.content_choices, { "content-a": "keep" })
    assert.deepEqual(h.render().plan.blank_columns, ["1"])
  } finally { h.dispose() }
})

test("resolved reply columns are saved without analysis and the server's refreshed review is accepted", async () => {
  const plan = pendingPlan()
  plan.matches = { "2": "question-access", "3": "question-inspection" }
  plan.column_matches = { "1": "column-response", "2": "new" }
  const review = { conflicts: [], unresolved: 0, missing_questions: [], kept_columns: [], counts: { kept_replies: 2, updated_replies: 1, new_replies: 0, conflicts: 0 } }
  const h = await harness(plan, { save: async saved => ({ ...saved, plan_revision: 5, review }) })
  try {
    h.render().onColumn("2", "column-follow-up")
    await h.flush()
    assert.equal(h.saved.length, 1)
    assert.equal(h.saved[0].column_matches["2"], "column-follow-up")
    assert.equal(h.analyzed.length, 0)
    assert.deepEqual(h.render().plan.review, review)
  } finally { h.dispose() }
})

test("choosing an occupied old round transfers it across the whole import and frees its previous match", async () => {
  const plan = pendingPlan()
  plan.matches = { "2": "question-access", "3": "question-inspection" }
  plan.headers.push("Final response")
  plan.mapping.replies.push(3)
  for (const row of plan.rows) row.values.push("A new note.")
  plan.column_matches = { "1": "old-k", "2": "old-m", "3": "new" }
  const h = await harness(plan)
  try {
    h.render().onColumn("1", "old-m")
    await h.flush()
    assert.equal(h.saved.length, 1)
    assert.deepEqual(h.saved[0].column_matches, { "1": "old-m", "2": "new", "3": "new" })
    h.render().onColumn("3", "old-k")
    await h.flush()
    assert.equal(h.saved.length, 2)
    assert.deepEqual(h.saved[1].column_matches, { "1": "old-m", "2": "new", "3": "old-k" })
    assert.equal(h.analyzed.length, 0)
  } finally { h.dispose() }
})

test("unavailable automatic analysis retains the saved choices and revision for manual review", async () => {
  const plan = pendingPlan()
  plan.matches["2"] = "question-access"
  const h = await harness(plan, { analyze: async () => { throw new Error("unavailable") } })
  try {
    h.render().onQuestion("3", "new")
    await h.flush()
    const current = h.render()
    assert.equal(current.busy, false)
    assert.equal(current.plan.plan_revision, 5)
    assert.deepEqual(current.plan.matches, { "2": "question-access", "3": "new" })
    assert.equal(current.plan.column_matches["1"], null)
  } finally { h.dispose() }
})

test("saving can resolve empty reply columns without an unnecessary analysis call", async () => {
  const plan = pendingPlan()
  plan.matches["2"] = "new"
  const h = await harness(plan, { save: async saved => ({
    ...saved, plan_revision: 5, column_matches: { "1": "new", "2": "new" },
  }) })
  try {
    h.render().onQuestion("3", "new")
    await h.flush()
    assert.equal(h.saved.length, 1)
    assert.equal(h.analyzed.length, 0)
    assert.deepEqual(h.render().plan.column_matches, { "1": "new", "2": "new" })
    assert.equal(h.render().plan.plan_revision, 5)
  } finally { h.dispose() }
})

test("a failed save keeps the local selection and never analyzes an unsaved revision", async () => {
  const plan = pendingPlan()
  plan.matches["2"] = "question-access"
  const h = await harness(plan, { save: async () => { throw new Error("stale_import") } })
  try {
    h.render().onQuestion("3", "new")
    await h.flush()
    const current = h.render()
    assert.equal(current.busy, true, "a stale review remains locked for editing")
    assert.equal(current.submitBusy, false, "recheck remains available after the request settles")
    assert.equal(current.plan.stale, true)
    assert.equal(current.plan.matches["3"], "new")
    assert.equal(current.plan.plan_revision, 4)
    assert.equal(h.analyzed.length, 0)
    assert.equal(current.alerts.length, 1)
  } finally { h.dispose() }
})


test("rechecking a stale plan rebases the current identities without reuploading or invoking AI", async () => {
  const plan = pendingPlan()
  plan.stale = true
  plan.matches = { "2": "question-access", "3": "question-inspection" }
  plan.column_matches = { "1": "column-response", "2": "new" }
  const h = await harness(plan, { save: async saved => ({ ...saved, stale: false, plan_revision: 5 }) })
  try {
    h.render().onRecheck()
    await h.flush()
    assert.equal(h.saved.length, 1)
    assert.equal(h.payloads[0].rebase, true)
    assert.deepEqual(h.payloads[0].matches, plan.matches)
    assert.deepEqual(h.payloads[0].column_matches, plan.column_matches)
    assert.equal(h.render().plan.stale, false)
    assert.equal(h.render().plan.plan_revision, 5)
    assert.equal(h.analyzed.length, 0)
  } finally { h.dispose() }
})


test("review action opens the review's next decision", async () => {
  const h = await harness(pendingPlan())
  try {
    const events: string[] = []
    const view = h.render()
    view.reviewRef.current = { focusDecision: () => events.push("focus decision") }
    view.onReview()
    assert.deepEqual(events, ["focus decision"])
  } finally { h.dispose() }
})

test("rechecking loads the rebased current list before the paired preview becomes editable", async () => {
  const plan = { ...pendingPlan(), stale: true }
  const refreshed: QaImportContext = { id: "list-a", title: "Updated workshop register", revision: 3, columns: [], questions: [] }
  const result = deferred<QaImportContext>()
  const h = await harness(plan, {
    save: async saved => ({ ...saved, stale: false, base_revision: 3, plan_revision: 5 }),
    loadList: () => result.promise,
  })
  try {
    h.render().onRecheck()
    await h.flush()
    assert.equal(h.render().plan.plan_revision, 5)
    assert.equal(h.render().plan.stale, true)
    result.resolve(refreshed)
    await h.flush()
    assert.equal(h.render().plan.stale, false)
    assert.deepEqual(h.render().list, refreshed)
    assert.equal(h.analyzed.length, 0)
  } finally { h.dispose() }
})
