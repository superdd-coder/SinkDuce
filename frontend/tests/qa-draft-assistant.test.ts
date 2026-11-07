import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"
import * as React from "react"
import ts from "typescript"
import type { QaDraftOptions, QaMaterial, QaProposal } from "../src/api/qa.ts"

type Element = React.ReactElement<Record<string, unknown>>
function elements(node: unknown): Element[] {
  if (Array.isArray(node)) return node.flatMap(elements)
  return React.isValidElement<Record<string, unknown>>(node) ? [node, ...elements(node.props.children)] : []
}

async function harness(component = "qa-reply-editor", uploadResults: QaMaterial[] = [], library: QaMaterial[] = []) {
  const key = `__qa_draft_assistant_${Math.random()}`
  const globals = globalThis as unknown as Record<string, unknown>
  const cells: unknown[] = []
  const calls: { body: string; options?: QaDraftOptions; resolve: (proposal: QaProposal) => void; reject: (error: Error) => void }[] = []
  const adopted: unknown[][] = []
  const submissions: string[][] = []
  let cursor = 0
  const editor = {
    value: "The reading room opens at noon.", ready: true, editing: true, state: "saved",
    flush: async () => true,
    edit: (...args: unknown[]) => adopted.push(args),
    begin() { editor.editing = true },
    async finish() { editor.editing = false; return null },
  }
  const runtime = {
    useState(initial: unknown) {
      const index = cursor++
      if (!(index in cells)) cells[index] = typeof initial === "function" ? initial() : initial
      return [cells[index], (value: unknown) => { cells[index] = typeof value === "function" ? value(cells[index]) : value }]
    },
    useRef(initial: unknown) { return cells[cursor++] ??= { current: initial } },
    useEffect() { cursor++ },
    useId() { return `assistant-${cursor++}` },
    useQaReplyAutosave() { return editor },
    async getQaList() { return { revision: 6 } },
    async getQaReplyHistory() { return [{ id: "old-a", body: "The museum opens before noon.", created_at: "2031-03-01T00:00:00Z" }] },
    async getQaMaterials() { return library },
    async uploadQaMaterial() { return uploadResults.shift() },
    streamQaDraft(_collection: string, _list: unknown, _question: string, _column: string, body: string, _progress: unknown, _signal: AbortSignal, options?: QaDraftOptions) {
      return new Promise<QaProposal>((resolve, reject) => calls.push({ body, options, resolve, reject }))
    },
  }
  globals[key] = runtime
  const uri = (source: string) => `data:text/javascript,${encodeURIComponent(source)}`
  const stub = (...names: string[]) => uri(names.map(name => `export function ${name}(){return null}`).join("\n"))
  const wire = (...names: string[]) => uri(names.map(name => `export const ${name}=(...args)=>globalThis[${JSON.stringify(key)}].${name}(...args);`).join("\n"))
  const imports: Record<string, string> = {
    react: wire("useState", "useRef", "useEffect", "useId"), "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
    "lucide-react": import.meta.resolve("lucide-react"), sonner: uri("export const toast={error(){}}"),
    "@/components/ui/button": uri('export const Button="button"'),
    "./qa-column-name": stub("QaColumnName"), "./qa-controls": stub("QaActionMenu", "QaMenuItem", "QaToggle"),
    "@/components/ui/textarea": uri('export const Textarea="textarea"'),
    "./use-qa-reply-autosave": wire("useQaReplyAutosave"), "./qa-rich-reply": stub("QaRichReply"), "./qa-reply-text": stub("QaReplyText"),
    "./qa-draft-instructions": stub("QaDraftInstructions"),
    "@/i18n/use-t": uri("export const useT=()=>key=>key"), "@/stores/app-store": uri("export const useAppStore=select=>select({})"),
    "@/api/source-scope": uri("export const sourceScopedKey=key=>key"),
    "@/lib/qa-draft-progress": uri("export const createQaProgressSampler=()=>({update(){},stop(){}})"),
    "@/lib/qa-sources": uri("export const groupQaSources=()=>[]"),
    "@/api/client": wire("getQaList", "streamQaDraft", "getQaReplyHistory", "getQaMaterials", "getQaMaterialVersion", "uploadQaMaterial", "archiveQaMaterial", "retryQaMaterial"),
  }
  const source = await readFile(new URL(`../src/components/qa/${component}.tsx`, import.meta.url), "utf8")
  const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
    .replace(/^import ["'][^"']+\.css["'];?$/gm, "")
    .replace(/from ["']([^"']+)["']/g, (statement, path: string) => imports[path] ? `from ${JSON.stringify(imports[path])}` : statement)
  const { QaReplyEditor, QaDraftInstructions } = await import(uri(compiled))
  const props = { collectionId: "collection-a", data: { id: "list-a", version: { id: "version-a" }, can_edit: true },
    question: { id: "question-a", included: true }, reply: { column_id: "column-a", included: true, label: "Museum response" },
    busy: false, position: 1, onSave() {}, onRename() {}, onSource() {},
  }
  return {
    editor, calls, adopted, submissions, dispose() { delete globals[key] }, async tick() { await new Promise(setImmediate) },
    renderInstructions(followUp = false) {
      cursor = 0
      const tree = elements(QaDraftInstructions({ collectionId: "collection-a", listId: "list-a", panelId: "draft-panel", followUp,
        disabled: false, children: null, onGenerate: async (_guidance: string, versions: string[]) => { submissions.push(versions); return true },
      }))
      const fileSpace = tree.find(node => node.type === "section" && node.props["aria-label"] === "qa.materials")
      const spaceItems = elements(fileSpace)
      return {
        drop(files: File[]) {
          const panel = tree.find(node => node.props.id === "draft-panel")
          assert.ok(panel)
          ;(panel.props.onDrop as (event: unknown) => void)({ preventDefault() {}, stopPropagation() {}, dataTransfer: { types: ["Files"], files } })
        },
        submit() {
          const form = tree.find(node => node.type === "form")
          assert.ok(form)
          ;(form.props.onSubmit as (event: unknown) => void)({ preventDefault() {} })
        },
        openFileSpace() {
          const action = tree.find(node => typeof node.props.onClick === "function" && Array.isArray(node.props.children) && node.props.children.includes("qa.aiChooseMaterial"))
          assert.ok(action)
          ;(action.props.onClick as () => void)()
        },
        spaceItems,
        removeAttachment: tree.find(node => node.props["aria-label"] === "qa.aiRemoveAttachment"),
        button: tree.find(node => node.type === "button" && node.props.type === "submit"),
        versionBadges: tree.filter(node => node.type === "small" && Array.isArray(node.props.children)).map(node => (node.props.children as unknown[]).join("")),
      }
    },
    render(modified = false) {
      cursor = 0
      const tree = elements(QaReplyEditor({ ...props, reply: { ...props.reply, modified } }))
      const trigger = tree.find(node => node.props["aria-label"] === "qa.aiDraft")
      const form = tree.find(node => typeof node.type === "function" && node.type.name === "QaDraftInstructions")
      const proposals = tree.filter(node => node.props.className === "qa-proposal")
      const adopt = tree.find(node => node.type === "button" && node.props.children === "qa.useDraft")
      const historyAction = tree.find(node => typeof node.type === "function" && node.type.name === "QaMenuItem" && Array.isArray(node.props.children) && node.props.children.includes("qa.history"))
      const history = tree.find(node => node.props.className === "qa-revision-history")
      return { trigger, form, proposals, adopt, historyAction, history, composer: tree.find(node => typeof node.props.className === "string" && node.props.className.startsWith("qa-composer")) }
    },
  }
}

function draft(id: string, body: string): QaProposal {
  return { id, body, sources: [], base_cell_revision_id: "reply-revision-a", insufficient_evidence: false }
}

test("drafting is an icon shown only during editing, independent of autosave state", async () => {
  const h = await harness()
  try {
    h.editor.editing = false
    assert.equal(h.render().trigger, undefined)
    h.editor.editing = true
    for (const state of ["dirty", "saving", "saved", "error"]) {
      h.editor.state = state
      const button = h.render().trigger
      assert.ok(button, `the icon stays visible while editing and ${state}`)
      assert.equal(button.props.size, "icon-sm")
      assert.equal(button.props.title, "qa.aiDraft")
      assert.ok(React.isValidElement(button.props.children), "the control contains only its icon")
    }
    h.editor.editing = false
    assert.equal(h.render().trigger, undefined)
  } finally { h.dispose() }
})

test("leaving a composer control ends editing but moving within it does not", async () => {
  const h = await harness()
  try {
    const onBlur = h.render().composer?.props.onBlur as (event: unknown) => void
    assert.equal(typeof onBlur, "function")
    onBlur({ currentTarget: { contains: () => true }, relatedTarget: {} })
    await h.tick()
    assert.ok(h.render().trigger, "tabbing from text to its actions keeps editing active")
    onBlur({ currentTarget: { contains: () => false }, relatedTarget: {} })
    await h.tick()
    assert.equal(h.render().trigger, undefined, "tabbing away from the action hides the icon")
  } finally { h.dispose() }
})

test("opening drafting only opens the instruction form; submitting passes instructions and selected versions separately", async () => {
  const h = await harness()
  try {
    ;(h.render().trigger?.props.onClick as () => void)()
    await h.tick()
    assert.equal(h.calls.length, 0)
    assert.equal(h.render().trigger, undefined, "the open panel replaces its launch action")
    const submit = h.render().form?.props.onGenerate as (guidance: string, versions: string[]) => Promise<boolean>
    assert.ok(submit)
    const request = submit("Use a concise tone.", ["material-version-a"])
    await h.tick()
    assert.equal(h.calls[0]?.body, "The reading room opens at noon.")
    assert.deepEqual(h.calls[0]?.options, { guidance: "Use a concise tone.", material_version_ids: ["material-version-a"] })
    h.calls[0].resolve(draft("proposal-a", "The reading room opens at noon."))
    assert.equal(await request, true)
    assert.equal(h.render().proposals.length, 1)
    assert.ok(h.render().form, "the instruction field stays after the first result")
  } finally { h.dispose() }
})

test("reply history remains accessible through the reply menu", async () => {
  const h = await harness()
  try {
    const action = h.render(true).historyAction
    assert.ok(action)
    ;(action.props.onClick as () => void)()
    await h.tick()
    assert.ok(h.render(true).history)
  } finally { h.dispose() }
})

test("subsequent instructions revise one draft, preserve it on failure, and adopt only the latest result", async () => {
  const h = await harness()
  try {
    ;(h.render().trigger?.props.onClick as () => void)()
    const submit = () => h.render().form?.props.onGenerate as (guidance: string, versions: string[]) => Promise<boolean>
    const first = submit()("", [])
    await h.tick()
    h.calls[0].resolve(draft("proposal-a", "The reading room opens at noon."))
    assert.equal(await first, true)
    const adoptPrevious = h.render().adopt?.props.onClick as () => void
    const retry = submit()("Make this shorter.", [])
    await h.tick()
    assert.equal(h.calls[1].options?.previous_proposal_id, "proposal-a")
    assert.equal(h.render().proposals.length, 1)
    assert.equal(h.render().adopt?.props.disabled, true)
    h.calls[1].reject(new Error("stale_draft"))
    assert.equal(await retry, false)
    assert.equal(h.render().proposals.length, 1)
    const final = submit()("Make this shorter.", [])
    await h.tick()
    assert.equal(h.calls[2].options?.previous_proposal_id, "proposal-a")
    h.calls[2].resolve(draft("proposal-b", "Opens at noon."))
    assert.equal(await final, true)
    assert.equal(h.render().proposals.length, 1)
    const adoptLatest = h.render().adopt?.props.onClick as () => void
    adoptPrevious()
    adoptLatest()
    adoptLatest()
    assert.deepEqual(h.adopted, [["Opens at noon.", "proposal-b"]])
  } finally { h.dispose() }
})

test("dropping a replacement attaches its exact parsed version without duplicating the material", async () => {
  const material = { id: "guide", filename: "Visitor guide.txt", version_id: "guide-v1", current_version_id: "guide-v2",
    number: 1, revision: 1, archived: false, parse_state: "ready", index_state: "queued", error_code: "",
  }
  const note = { ...material, id: "notes", filename: "Room notes.txt", version_id: "notes-v1", current_version_id: "notes-v1" }
  const revised = { ...material, version_id: "guide-v2", number: 2, revision: 2 }
  const h = await harness("qa-draft-instructions", [material, note, revised])
  try {
    const file = new File(["Museum guide"], material.filename)
    h.renderInstructions().drop([file, new File(["Reading room"], note.filename)])
    await h.tick()
    const first = h.renderInstructions()
    assert.equal(first.button?.props.disabled, false, "parsed files do not wait for an index")
    assert.ok(first.versionBadges.includes("V1"))
    first.submit()
    await h.tick()
    assert.deepEqual(h.submissions[0], ["guide-v1", "notes-v1"])
    assert.equal(h.renderInstructions(true).button?.props.disabled, true, "an unchanged blank follow-up cannot send")
    h.renderInstructions(true).drop([file])
    await h.tick()
    const changed = h.renderInstructions(true)
    assert.equal(changed.button?.props.disabled, false, "a replacement reference is enough to adjust the draft")
    changed.submit()
    await h.tick()
    assert.deepEqual(h.submissions[1], ["guide-v2", "notes-v1"])
  } finally { h.dispose() }
})

test("the compact send action has an accessible name for drafting and revising", async () => {
  const h = await harness("qa-draft-instructions")
  try {
    assert.equal(h.renderInstructions().button?.props["aria-label"], "qa.aiGenerateDraft")
    assert.equal(h.renderInstructions(true).button?.props["aria-label"], "qa.aiReviseDraft")
  } finally { h.dispose() }
})

test("the file space picker only attaches active files and flags archived pins", async () => {
  const pinned = { id: "guide", filename: "Visitor guide.txt", version_id: "guide-v1", current_version_id: "guide-v1",
    number: 1, revision: 1, archived: false, parse_state: "ready", index_state: "ready", error_code: "",
  }
  const available = { ...pinned, id: "notes", filename: "Room notes.txt", version_id: "notes-v1", current_version_id: "notes-v1" }
  const h = await harness("qa-draft-instructions", [pinned], [{ ...pinned, archived: true }, available])
  try {
    h.renderInstructions().drop([new File(["Museum guide"], pinned.filename)])
    await h.tick()
    h.renderInstructions().openFileSpace()
    await h.tick()
    const picker = h.renderInstructions()
    assert.equal(picker.button?.props.disabled, true, "a previously pinned archived version cannot send")
    assert.equal(picker.spaceItems.some(node => node.props.label === "qa.showRemovedMaterials"), false)
    assert.equal(picker.spaceItems.some(node => node.props.children === "qa.aiArchiveMaterial"), false)
    assert.deepEqual(picker.spaceItems.filter(node => node.props.title).map(node => node.props.title), [available.filename])
    assert.ok(picker.removeAttachment)
    ;(picker.removeAttachment.props.onClick as () => void)()
    const select = h.renderInstructions().spaceItems.find(node => node.props.title === available.filename)
    assert.ok(select)
    ;(select.props.onClick as () => void)()
    h.renderInstructions().submit()
    await h.tick()
    assert.deepEqual(h.submissions, [[available.version_id]])
  } finally { h.dispose() }
})
