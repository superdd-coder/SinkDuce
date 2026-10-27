import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"
import { isValidElement } from "react"
import ts from "typescript"

function elements(node: any): any[] {
  if (Array.isArray(node)) return node.flatMap(elements)
  return isValidElement(node) ? [node, ...elements((node.props as any).children)] : []
}

// Exercise the actual Quick Chat hydration and stream handlers without a browser.
// Only hooks, API transport and leaf views are replaced; artifact merging is real.
async function quickChat(kind: "meeting" | "group" | "collection", history: any[] = [], events: any[] = []) {
  const cells: any[] = []
  let cursor = 0
  let effects: Array<() => any> = []
  const key = `__quickNote_${kind}`
  const root = globalThis as any
  const noop = () => {}
  root[key] = {
    useState(initial: any) { const i = cursor++; if (!(i in cells)) cells[i] = typeof initial === "function" ? initial() : initial
      return [cells[i], (next: any) => { cells[i] = typeof next === "function" ? next(cells[i]) : next }] },
    useRef(initial: any) { const i = cursor++; if (!(i in cells)) cells[i] = { current: initial }; return cells[i] },
    useEffect(fn: () => any, deps: any[]) { const i = cursor++; const previous = cells[i]
      if (!previous || !deps || deps.some((value, index) => value !== previous.deps[index])) {
        effects.push(() => { previous?.cleanup?.(); cells[i] = { deps, cleanup: fn() } })
      }
    },
    useCallback: (fn: any) => fn,
    useLayoutEffect(fn: () => any, deps: any[]) { return root[key].useEffect(fn, deps) },
    state: { webSearch: false, setWebSearch: noop, reportComments: {}, consumeChatAttachments: noop, applySessionConfig: noop },
    api: { getSession: async () => ({ messages: history, config: {} }), createSession: async () => ({}),
      postSessionMessage: async () => ({ ok: true, body: {} }), deleteSession: async () => {},
      iterateSessionSse: async function* () { yield* events },
    },
  }
  const data = (text: string) => `data:text/javascript,${encodeURIComponent(text)}`
  const hookModule = data(["useState", "useRef", "useEffect", "useLayoutEffect", "useCallback"].map((name) =>
    `export const ${name}=(...args)=>globalThis[${JSON.stringify(key)}].${name}(...args);`).join("\n"))
  const apiModule = data(Object.keys(root[key].api).map((name) =>
    `export const ${name}=(...args)=>globalThis[${JSON.stringify(key)}].api.${name}(...args);`).join("\n"))
  const real = ["chat-reports", "note-changes", "group-cites", "meeting-ref-chips", "report-review", "ime", "quick-chat-float"]
  const imports: Record<string, string> = {
    react: hookModule, "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
    "@/api/client": apiModule,
    "@/stores/app-store": data(`export const useAppStore=select=>select(globalThis[${JSON.stringify(key)}].state);useAppStore.getState=()=>globalThis[${JSON.stringify(key)}].state;`),
    "@/hooks/use-chat-attachments": data("export const useChatAttachments=()=>({drafts:[],ready:[],dropProps:{}})"),
    "@/hooks/use-report-review": data("export const useReportReview=()=>({hasComments:false,comments:[]});export function useReportReviewNavigation(){};export function consumeReportComments(){};export function consumeSubmittedReviews(){};export async function refreshReportComments(){}"),
    "@/hooks/use-scroll-edge-fade": data("export const useScrollEdgeFade=()=>({top:false,bottom:false})"),
    "@/i18n/use-t": data("export const useT=()=>(key)=>key"),
    "@/i18n/tr": data("export const tr=(key)=>key"),
    "@/lib/utils": data("export const cn=(...values)=>values.filter(Boolean).join(' ')"),
    "@/lib/uuid": data("let n=0;export const newId=()=>String(++n)"),
  }
  for (const name of real) imports[`@/lib/${name}`] = new URL(`../src/lib/${name}.ts`, import.meta.url).href
  const file = kind === "collection" ? "database/quick-chat" : "meeting/meeting-quick-chat"
  const source = ts.transpileModule(await readFile(new URL(`../src/components/${file}.tsx`, import.meta.url), "utf8"), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const parsed = ts.createSourceFile("chat.js", source, ts.ScriptTarget.ES2022, true, ts.ScriptKind.JS)
  for (const statement of parsed.statements) {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier)) continue
    const path = statement.moduleSpecifier.text
    if (imports[path]) continue
    const bindings = statement.importClause?.namedBindings
    if (bindings && ts.isNamedImports(bindings)) imports[path] = data(bindings.elements.map((binding) => {
      const name = binding.propertyName?.text || binding.name.text
      return `export function ${name}(){}`
    }).join("\n"))
  }
  const compiled = source.replace(/from ["']([^"']+)["']/g, (statement, path) => imports[path] ? `from ${JSON.stringify(imports[path])}` : statement)
  const module = await import(data(compiled))
  const component = kind === "collection" ? module.QuickChat : module.MeetingQuickChat
  const props = { open: true, onOpen: noop, onClose: noop, meetingId: "room-a", meetingTitle: "Workshop",
    collectionId: "col-a", collectionName: "Workshop", sessionKind: kind, indexStatus: "ready", layout: "dock", railActive: false }
  const environment = { requestAnimationFrame: () => 1, cancelAnimationFrame: noop,
    document: { querySelector: () => null }, window: { setTimeout, clearTimeout } }
  const originals = new Map(Object.keys(environment).map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]))
  for (const [name, value] of Object.entries(environment)) Object.defineProperty(globalThis, name, { configurable: true, value })
  const render = () => { cursor = 0; return elements(component(props)) }
  return { render,
    async settle() { for (let i = 0; i < 4; i++) { render(); effects.splice(0).forEach((fn) => fn()); await new Promise(setImmediate) } },
    async send() {
      const composer = render().find((element) => element.props.onSend)
      composer.props.setInput("Save a Note")
      await render().find((element) => element.props.onSend).props.onSend()
    },
    dispose() { cells.forEach((cell) => cell?.cleanup?.()); delete root[key]
      for (const [name, value] of originals) {
        if (value) Object.defineProperty(globalThis, name, value); else Reflect.deleteProperty(globalThis, name)
      } },
  }
}

const note = { id: "note-a", collection: "col-a", title: "Workshop follow-through" }
for (const kind of ["meeting", "group", "collection"] as const) {
  test(`${kind} Quick Chat keeps a Note-only answer when reopening history`, async () => {
    const h = await quickChat(kind, [
      { id: "tool-call", role: "assistant", content: "", metadata: { tool_calls: [{}] } },
      { id: "answer", role: "assistant", content: "", metadata: { tool_calls: 1, note_changes: [note] } },
    ])
    try {
      await h.settle()
      const cards = h.render().filter((element) => element.props.notes?.length)
      assert.equal(cards.length, 1, "The saved Note is the answer even when no prose was generated")
      assert.deepEqual(cards[0].props.notes, [note])
    } finally { h.dispose() }
  })

  for (const event of ["tool_result", "done"]) test(`${kind} Quick Chat renders Notes received in ${event}`, async () => {
    const h = await quickChat(kind, [], [
      { event: "token", data: { content: "Saved the requested Note." } },
      { event, data: { tool: "create_note", status: "done", note_changes: [note] } },
      ...(event === "done" ? [] : [{ event: "done", data: {} }]),
    ])
    try {
      await h.settle(); await h.send()
      const tree = h.render()
      const cards = tree.filter((element) => element.props.notes?.length)
      assert.equal(cards.length, 1)
      assert.deepEqual(cards[0].props.notes, [note])
      const answer = tree.findIndex((element) => element.props.content === "Saved the requested Note." || element.props.children === "Saved the requested Note.")
      assert.ok(answer >= 0 && answer < tree.indexOf(cards[0]), "The Note card follows the answer")
    } finally { h.dispose() }
  })
}
