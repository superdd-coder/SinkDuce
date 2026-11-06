import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"
import * as React from "react"
import ts from "typescript"
import type { QaMaterial, QaSource } from "../src/api/qa.ts"

type Element = React.ReactElement<Record<string, unknown>>
type EffectCell = { deps?: unknown[]; cleanup?: () => void }
function elements(node: unknown): Element[] {
  if (Array.isArray(node)) return node.flatMap(elements)
  return React.isValidElement<Record<string, unknown>>(node) ? [node, ...elements(node.props.children)] : []
}
function component(tree: Element[], name: string) {
  return tree.find(element => typeof element.type === "function" && element.type.name === name)
}
function quotedSources(tree: Element[]) { return component(tree, "QaSourceExcerpts")?.props.sources as QaSource[] | undefined }
function toggle(tree: Element[]) {
  const footer = tree.find(element => element.props.className === "qa-source-preview-toggle")
  const button = elements(footer?.props.children).find(element => typeof element.props.onClick === "function")
  assert.ok(button, "the footer offers a named quoted-text/file toggle")
  assert.equal(typeof button.props.onClick, "function")
  return button
}
function click(element: Element) { (element.props.onClick as () => void)() }
function fileView(tree: Element[]) { return tree.find(element => element.props.className === "qa-source-file-view") }

/** Run the real preview hooks and handlers, replacing only platform/service boundaries. */
async function harness(getSource: (...args: unknown[]) => Promise<Blob>, api: Record<string, unknown> = {}) {
  const key = `__qa_preview_${Math.random()}`
  const globals = globalThis as unknown as Record<string, unknown>
  const cells: unknown[] = [], effects: (() => void)[] = []
  const frames = new Map<number, () => void>()
  let frameId = 0
  let cursor = 0
  const runtime = {
    useState(initial: unknown) {
      const index = cursor++
      if (!(index in cells)) cells[index] = typeof initial === "function" ? initial() : initial
      return [cells[index], (value: unknown) => { cells[index] = typeof value === "function" ? value(cells[index]) : value }]
    },
    useRef(initial: unknown) { const index = cursor++; return cells[index] ??= { current: initial } },
    useEffect(effect: () => void | (() => void), deps: unknown[]) {
      const index = cursor++, previous = cells[index] as EffectCell | undefined
      if (previous && deps?.every((value, i) => Object.is(value, previous.deps?.[i]))) return
      effects.push(() => { previous?.cleanup?.(); cells[index] = { deps, cleanup: effect() } })
    },
    useCallback(callback: unknown, deps: unknown[]) {
      const index = cursor++
      const previous = cells[index] as { callback: unknown; deps: unknown[] } | undefined
      if (previous && deps.every((value, i) => Object.is(value, previous.deps[i]))) return previous.callback
      cells[index] = { callback, deps }
      return callback
    },
    useId() { return key },
    requestAnimationFrame(callback: () => void) { frames.set(++frameId, callback); return frameId },
    cancelAnimationFrame(id: number) { frames.delete(id) },
    getQaSourceFile: getSource,
    ...api,
  }
  globals[key] = runtime
  const data = (source: string) => `data:text/javascript,${encodeURIComponent(source)}`
  const stub = (name: string) => data(`export function ${name}(){ return null }`)
  const imports: Record<string, string> = {
    react: data(["useState", "useRef", "useEffect", "useCallback", "useId"].map(name => `export const ${name}=(...args)=>globalThis[${JSON.stringify(key)}].${name}(...args);`).join("\n")),
    "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
    "lucide-react": import.meta.resolve("lucide-react"),
    "./qa-controls": data(["QaToggle", "QaActionMenu", "QaMenuItem"].map(name => `export function ${name}(){ return null }`).join("\n")),
    "@/components/ui/button": stub("Button"),
    "@/components/ui/dialog": data(["Dialog", "DialogContent", "DialogHeader", "DialogTitle", "DialogDescription"].map(name => `export function ${name}(){ return null }`).join("\n")),
    "@/components/file-mgmt/raw-file-viewer": stub("RawFileViewer"),
    "@/components/shared/chunk-md": stub("ChunkMd"),
    "@/lib/qa-sources": new URL("../src/lib/qa-sources.ts", import.meta.url).href,
    "@/i18n/use-t": data("export const useT=()=>key=>key"),
    "@/api/client": data(["archiveQaMaterial", "getQaMaterials", "getQaSourceFile", "retryQaMaterial", "uploadQaMaterial"].map(name => `export const ${name}=(...args)=>globalThis[${JSON.stringify(key)}].${name}?.(...args);`).join("\n")),
  }
  const compile = (source: string) => ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
    .replace(/^import ["']\.\/qa-file-space\.css["'];?$/m, "")
    .replace(/from ["']([^"']+)["']/g, (statement, path: string) => imports[path] ? `from ${JSON.stringify(imports[path])}` : statement)
  const modal = await readFile(new URL("../src/components/qa/use-qa-modal.ts", import.meta.url), "utf8")
  imports["./use-qa-modal"] = data(`const {requestAnimationFrame,cancelAnimationFrame}=globalThis[${JSON.stringify(key)}];\n${compile(modal)}`)
  const source = await readFile(new URL("../src/components/qa/qa-materials.tsx", import.meta.url), "utf8")
  const module = await import(data(compile(source)))
  return {
    render(props: Record<string, unknown>) { cursor = 0; return elements(module.QaMaterialPreview(props)) },
    renderMaterials(props: Record<string, unknown>) { cursor = 0; return elements(module.QaMaterials(props)) },
    renderFileSpace(props: Record<string, unknown>) { cursor = 0; return elements(module.QaFileSpaceDialog(props)) },
    excerpts(sources: QaSource[]) { return elements(module.QaSourceExcerpts({ collectionId: "collection-a", sources })) },
    async flush() { effects.splice(0).forEach(effect => effect()); await new Promise(setImmediate) },
    paint() { const callbacks = Array.from(frames.values()); frames.clear(); callbacks.forEach(callback => callback()) },
    dispose() { cells.forEach(cell => (cell as EffectCell)?.cleanup?.()); delete globals[key] },
  }
}

const source: QaSource = { kind: "project", file_id: "file-a", version_id: "version-a", filename: "Cabinet register.pdf", page_number: 2, chunk_id: "passage-a", excerpt: "The cabinet uses a keyed lock." }
const second: QaSource = { ...source, page_number: 4, chunk_id: "passage-b", excerpt: "The spare key is recorded separately." }
const props = { collectionId: "collection-a", listId: "list-a", source, onBack() {} }

test("preview toggles the whole quoted-text rail while retaining the loaded file viewer", async () => {
  let fetches = 0
  const h = await harness(async () => { fetches++; return new Blob(["Example file"]) })
  const sources = [source, second, { ...source, file_id: "other-file", chunk_id: "other", excerpt: "A different file." }, { ...source, version_id: "old-version", excerpt: "An older file version." }]
  try {
    h.render({ ...props, sources }); await h.flush()
    let tree = h.render({ ...props, sources })
    const url = component(tree, "RawFileViewer")?.props.url
    assert.equal(typeof url, "string")
    assert.equal(fileView(tree)?.props.hidden, false)
    assert.equal(quotedSources(tree), undefined)
    assert.equal(toggle(tree).props["aria-pressed"], false)
    click(toggle(tree))
    tree = h.render({ ...props, sources }); await h.flush()
    assert.equal(fileView(tree)?.props.hidden, true)
    assert.equal(fileView(tree)?.props.inert, true)
    assert.equal(fileView(tree)?.props["aria-hidden"], true)
    assert.equal(component(tree, "RawFileViewer")?.props.url, url, "the original viewer remains in the tree while quoted text is shown")
    assert.deepEqual(quotedSources(tree), [source, second])
    assert.deepEqual(h.excerpts(quotedSources(tree)!).filter(element => typeof element.type === "function" && element.type.name === "ChunkMd").map(element => element.props.text), [source.excerpt, second.excerpt])
    assert.equal(toggle(tree).props["aria-pressed"], true)
    click(toggle(tree))
    tree = h.render({ ...props, sources }); await h.flush()
    assert.equal(fileView(tree)?.props.hidden, false)
    assert.equal(quotedSources(tree), undefined)
    assert.equal(component(tree, "RawFileViewer")?.props.url, url)
    assert.equal(fetches, 1, "view changes do not download the original again")
  } finally { h.dispose() }
})

test("a different reply source selection returns to the file without showing old quoted text", async () => {
  let fetches = 0
  const h = await harness(async () => { fetches++; return new Blob(["Example file"]) })
  const firstSelection = [source, second]
  const replacement = { ...source, chunk_id: "passage-c", excerpt: "The maintenance interval is annual." }
  const nextSelection = [replacement]
  try {
    h.render({ ...props, sources: firstSelection }); await h.flush()
    click(toggle(h.render({ ...props, sources: firstSelection })))
    assert.deepEqual(quotedSources(h.render({ ...props, sources: firstSelection })), firstSelection)
    let tree = h.render({ ...props, sources: nextSelection }); await h.flush()
    assert.equal(fileView(tree)?.props.hidden, false)
    assert.equal(quotedSources(tree), undefined)
    click(toggle(tree))
    tree = h.render({ ...props, sources: nextSelection }); await h.flush()
    assert.deepEqual(quotedSources(tree), nextSelection)
    assert.equal(fetches, 1, "changing the quoted passages for the same file keeps its original loaded")
  } finally { h.dispose() }
})

test("saved quoted passages stay available when the original file cannot be loaded", async () => {
  const h = await harness(async () => { throw new Error("unavailable") })
  const sources = [source, second]
  try {
    h.render({ ...props, sources }); await h.flush()
    let tree = h.render({ ...props, sources })
    assert.ok(tree.some(element => element.props.role === "alert"))
    click(toggle(tree))
    tree = h.render({ ...props, sources }); await h.flush()
    assert.equal(fileView(tree)?.props.hidden, true)
    assert.deepEqual(quotedSources(tree), sources)
    click(toggle(tree))
    tree = h.render({ ...props, sources })
    assert.equal(fileView(tree)?.props.hidden, false)
    assert.ok(tree.some(element => element.props.role === "alert"))
  } finally { h.dispose() }
})

test("empty saved excerpts do not offer an empty quoted-text view", async () => {
  const h = await harness(async () => new Blob(["Example file"]))
  const empty = { ...source, excerpt: " \n\t" }
  try {
    h.render({ ...props, source: empty, sources: [empty] }); await h.flush()
    const tree = h.render({ ...props, source: empty, sources: [empty] })
    assert.equal(tree.some(element => element.props.className === "qa-source-preview-toggle"), false)
    assert.equal(quotedSources(tree), undefined)
    assert.ok(component(tree, "RawFileViewer"))
  } finally { h.dispose() }
})

const material: QaMaterial = { id: "material-a", current_version_id: "material-v2", filename: "Cabinet drawing.pdf", revision: 4, number: 2, archived: false, parse_state: "ready", index_state: "failed", error_code: "" }
const space = { collectionId: "collection-a", listId: "list-a", writable: false }
const texts = (node: unknown): string[] => Array.isArray(node) ? node.flatMap(texts)
  : typeof node === "string" ? [node]
  : React.isValidElement<Record<string, unknown>>(node) ? texts(node.props.children) : []
const copy = (tree: Element[]) => texts(tree[0])

test("file space distinguishes loading from empty and offers a scoped retry after a load failure", async () => {
  let attempts = 0
  const h = await harness(async () => new Blob(), { getQaMaterials(collection: string, list: string) {
    assert.deepEqual([collection, list], [space.collectionId, space.listId])
    if (++attempts === 1) throw new Error("unavailable")
    return Promise.resolve([])
  } })
  const props = { ...space, onSource() {} }
  try {
    const first = h.renderMaterials(props)
    assert.ok(first.some(node => node.props.role === "status"))
    assert.ok(!copy(first).includes("qa.noMaterials"))
    await h.flush()
    const failed = h.renderMaterials(props)
    const error = failed.find(node => node.props.role === "alert")
    assert.ok(error)
    const retry = component(elements(error), "Button")
    assert.ok(retry)
    click(retry)
    await h.flush()
    const loaded = h.renderMaterials(props)
    assert.ok(copy(loaded).includes("qa.noMaterials"))
    assert.ok(!loaded.some(node => node.props.role === "alert"))
    assert.equal(attempts, 2)
  } finally { h.dispose() }
})

test("read-only files use parse readiness and can open the exact current version without write controls", async () => {
  const h = await harness(async () => new Blob(), { getQaMaterials: async () => [material] })
  let selected: QaSource | undefined
  const props = { ...space, onSource(value: QaSource) { selected = value } }
  try {
    h.renderMaterials(props); await h.flush()
    const tree = h.renderMaterials(props)
    assert.ok(copy(tree).includes("qa.materialReady"))
    assert.ok(!copy(tree).includes("qa.indexFailed"))
    assert.ok(!copy(tree).includes("qa.uploadMaterial"))
    assert.equal(component(tree, "QaActionMenu"), undefined)
    const file = tree.find(node => node.type === "button" && texts(node).includes(material.filename))
    assert.ok(file)
    ;(file.props.onClick as (event: { currentTarget: unknown }) => void)({ currentTarget: null })
    assert.deepEqual(selected, { kind: "material", file_id: material.id, version_id: material.current_version_id, filename: material.filename, excerpt: "", chunk_id: "" })
  } finally { h.dispose() }
})

test("a stalled parse can be retried against the displayed version", async () => {
  const calls: unknown[][] = []
  const h = await harness(async () => new Blob(), {
    getQaMaterials: async () => [{ ...material, parse_state: "processing" }],
    retryQaMaterial: async (...args: unknown[]) => { calls.push(args) },
  })
  const props = { ...space, writable: true, onSource() {} }
  try {
    h.renderMaterials(props); await h.flush()
    const tree = h.renderMaterials(props)
    assert.ok(copy(tree).includes("qa.processing"))
    const retry = tree.find(node => typeof node.type === "function" && node.type.name === "QaMenuItem" && texts(node).includes("qa.retry"))
    assert.ok(retry)
    click(retry)
    await h.flush()
    assert.deepEqual(calls, [[space.collectionId, space.listId, material.id, material.current_version_id]])
  } finally { h.dispose() }
})

test("file-space preview uses the same dialog and returns to its list", async () => {
  const h = await harness(async () => new Blob())
  const returnFocusRef = { current: null }
  const props = { ...space, close() {}, returnFocusRef }
  try {
    let tree = h.renderFileSpace(props)
    assert.equal(component(tree, "DialogContent")?.props.finalFocus, returnFocusRef)
    const files = component(tree, "QaMaterials")
    assert.ok(files)
    const attachment = { ...source, kind: "material" as const, excerpt: "" }
    ;(files.props.onSource as (value: QaSource) => void)(attachment)
    tree = h.renderFileSpace(props)
    assert.equal(tree.filter(node => typeof node.type === "function" && node.type.name === "Dialog").length, 1)
    const preview = component(tree, "QaMaterialPreview")
    assert.equal(preview?.props.source, attachment)
    assert.equal(preview?.props.listButton, false)
    assert.ok(tree.some(node => node.props.hidden === true && component(elements(node), "QaMaterials")), "the files stay mounted so their state is retained")
    ;(preview?.props.onBack as () => void)()
    tree = h.renderFileSpace(props)
    assert.ok(component(tree, "QaMaterials"))
    assert.equal(component(tree, "QaMaterialPreview"), undefined)
  } finally { h.dispose() }
})

test("file space opens on the next frame and dismisses only after the exit completes", async () => {
  const h = await harness(async () => new Blob())
  let dismissals = 0
  const props = { ...space, close() { dismissals++ } }
  const dialog = () => component(h.renderFileSpace(props), "Dialog")?.props
  try {
    assert.equal(dialog()?.open, false)
    await h.flush()
    h.paint()
    assert.equal(dialog()?.open, true)
    ;(dialog()?.onOpenChange as (open: boolean) => void)(false)
    const exiting = dialog()
    assert.equal(exiting?.open, false)
    assert.equal(dismissals, 0)
    const complete = exiting?.onOpenChangeComplete as (open: boolean) => void
    complete(false)
    complete(false)
    assert.equal(dismissals, 1)
  } finally { h.dispose() }
})
