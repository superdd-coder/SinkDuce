import assert from "node:assert/strict"
import { after, before, test } from "node:test"
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { createServer } from "vite"
import { fileURLToPath } from "node:url"

let server: Awaited<ReturnType<typeof createServer>>
const globals = globalThis as typeof globalThis & {
  __composerText?: string
  __composerState?: Record<string, unknown>
  __composerReact?: typeof React
}
const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document")
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage")

before(async () => {
  const root = fileURLToPath(new URL("../", import.meta.url))
  globals.__composerReact = React
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: undefined })
  server = await createServer({
    root, configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: { alias: [
      { find: /^react$/, replacement: "virtual:composer-react" },
      { find: /^react-dom$/, replacement: "virtual:composer-portals" },
      { find: "@/stores/app-store", replacement: "virtual:composer-store" },
      { find: "@/hooks/use-stream", replacement: "virtual:composer-stream" },
      { find: "@/i18n/use-t", replacement: "virtual:composer-locale" },
      { find: "@/components/ui/button", replacement: "virtual:composer-button" },
      { find: "@", replacement: root + "src" },
      { find: "#edition", replacement: root + "src/edition" },
    ] },
    plugins: [{
      name: "composer-fixture",
      resolveId(id) { if (id.startsWith("virtual:composer-")) return "\0" + id },
      load(id) {
        if (id === "\0virtual:composer-react") return `
          ${Object.keys(React).filter((name) => name !== "default" && name !== "useState" && /^[A-Za-z_$][\w$]*$/.test(name)).map((name) => `export const ${name} = globalThis.__composerReact.${name};`).join("\n")}
          export default globalThis.__composerReact;
          export const useState = (initial) => globalThis.__composerReact.useState(initial === "" ? globalThis.__composerText : initial);
        `
        if (id === "\0virtual:composer-portals") return "export const createPortal = () => null"
        if (id === "\0virtual:composer-store") return "export const useAppStore = (select) => select(globalThis.__composerState); useAppStore.getState = () => globalThis.__composerState"
        if (id === "\0virtual:composer-stream") return "export const useStreamChat = () => ({sendMessage: async () => {}, stopGeneration: () => {}})"
        if (id === "\0virtual:composer-locale") return "export const useT = () => (key) => key"
        if (id === "\0virtual:composer-button") return 'export const Button = ({variant, size, ...props}) => globalThis.__composerReact.createElement("button", props)'
      },
    }],
    optimizeDeps: { noDiscovery: true },
  })
})

after(async () => {
  await server?.close()
  delete globals.__composerText
  delete globals.__composerState
  delete globals.__composerReact
  if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument)
  else Reflect.deleteProperty(globalThis, "document")
  if (originalStorage) Object.defineProperty(globalThis, "localStorage", originalStorage)
  else Reflect.deleteProperty(globalThis, "localStorage")
})

async function render(text: string, isOnline: boolean, isStreaming = false, drafts: unknown[] = [], comments: unknown[] = []) {
  globals.__composerText = text
  globals.__composerState = {
    isOnline, isStreaming, thinking: false, webSearch: false,
    activeCollection: "", collections: [], selectedCollections: [],
    activeProvider: null, activeModel: null, providers: [], sessionId: "saved",
    sidebarView: "chat", fetchCollections: async () => {},
    chatAttachmentDrafts: { saved: drafts },
    reportComments: {saved:comments}, reportCommentDrafts:{}, reportCommentFocus:null,
  }
  const { ChatInput } = await server.ssrLoadModule("/src/components/chat/chat-input.tsx")
  Object.defineProperty(globalThis, "document", { configurable: true, value: { body: {} } })
  try {
    return renderToStaticMarkup(React.createElement(ChatInput))
  } finally {
    if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument)
    else Reflect.deleteProperty(globalThis, "document")
  }
}

test("a stale failed health probe cannot prevent a real send attempt", async () => {
  for (const online of [false, true]) {
    const html = await render("Continue the answer", online)
    const send = html.match(/<button\b[^>]*aria-label="common.send"[^>]*>/)?.[0]
    assert.ok(send)
    assert.doesNotMatch(send, /\bdisabled(?:=|\s|>)/, "the send request, rather than a previous health probe, determines connectivity")
    assert.doesNotMatch(html, /<textarea\b[^>]*disabled/)
  }
})

test("blank input stays disabled and active generation retains its stop action", async () => {
  const empty = await render(" \n ", false)
  assert.match(empty, /<button\b[^>]*disabled=""[^>]*aria-label="common.send"/)
  const busy = await render("Queued thought", true, true)
  assert.match(busy, /<textarea\b[^>]*disabled=""/)
  assert.match(busy, /common.cancel/)
  assert.doesNotMatch(busy, /aria-label="common.send"/)
})

test("a ready attachment can send without text, while processing attachments block send", async () => {
  const ready = await render("", true, false, [{ key: "file", filename: "facts.txt", status: "ready" }])
  const send = ready.match(/<button\b[^>]*aria-label="common.send"[^>]*>/)?.[0]
  assert.ok(send)
  assert.doesNotMatch(send, /\bdisabled(?:=|\s|>)/)
  const pending = await render("question", true, false, [{ key: "file", filename: "facts.txt", status: "processing" }])
  assert.match(pending, /<button\b[^>]*disabled=""[^>]*aria-label="common.send"/)
  assert.match(pending, /facts\.txt/)
})


test('saved report comments enable sending without text and keep a navigation-only summary pill',async()=>{
  const html=await render('',true,false,[],[{id:'c',state:'pending',session_id:'saved',report_id:'r',revision:1,text:'Rework this section.',title:'Report',anchor:{quote:'Old text'}}])
  const send=html.match(/<button\b[^>]*aria-label="common.send"[^>]*>/)?.[0]
  assert.ok(send)
  assert.doesNotMatch(send,/\bdisabled(?:=|\s|>)/)
  assert.match(html,/chat.review.pill/)
  assert.doesNotMatch(html,/type="checkbox"/)
})
