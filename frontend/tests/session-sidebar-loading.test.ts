import assert from "node:assert/strict"
import { after, before, test } from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { createServer } from "vite"
import { fileURLToPath } from "node:url"

let server: Awaited<ReturnType<typeof createServer>>
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage")
const globals = globalThis as typeof globalThis & { __sessionListTestState?: Record<string, unknown> }
before(async () => {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: undefined })
  const root = fileURLToPath(new URL("../", import.meta.url))
  server = await createServer({
    root, configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: { alias: [
      { find: "@/stores/app-store", replacement: "virtual:session-list-store" },
      { find: "@/i18n/use-t", replacement: "virtual:session-list-locale" },
      { find: "@", replacement: root + "src" },
      { find: "#edition", replacement: root + "src/edition" },
    ] },
    plugins: [{ name: "session-list-fixture", resolveId(id) {
      if (id.startsWith("virtual:session-list-")) return "\0" + id
    }, load(id) {
      if (id === "\0virtual:session-list-store") return "export const useAppStore = (select) => select(globalThis.__sessionListTestState); useAppStore.getState = () => globalThis.__sessionListTestState"
      if (id === "\0virtual:session-list-locale") return "export const useT = () => (key) => key"
    } }],
    optimizeDeps: { noDiscovery: true },
  })
})
after(async () => {
  await server?.close()
  delete globals.__sessionListTestState
  if (originalStorage) Object.defineProperty(globalThis, "localStorage", originalStorage)
  else Reflect.deleteProperty(globalThis, "localStorage")
})

async function render(status: string, sessions: unknown[] = []) {
  globals.__sessionListTestState = {
    sessionId: null, sessions, sessionListStatus: status,
    refreshSessionList: async () => undefined, loadSessionMessages: async () => undefined,
    initSession: async () => undefined,
  }
  const { SessionSidebar } = await server.ssrLoadModule("/src/components/chat/session-sidebar.tsx")
  return renderToStaticMarkup(createElement(SessionSidebar))
}

test("idle and loading do not claim that no saved conversations exist", async () => {
  for (const status of ["idle", "loading"]) {
    const html = await render(status)
    assert.match(html, /chat\.sessionsLoading/)
    assert.doesNotMatch(html, /chat\.noSessions/)
  }
})

test("failed initial loads show a retry action rather than an empty history message", async () => {
  const html = await render("error")
  assert.match(html, /role="alert"/)
  assert.match(html, /chat\.sessionsLoadFailed/)
  assert.match(html, /chat\.retrySessions/)
  assert.doesNotMatch(html, /chat\.noSessions/)
})

test("a successful empty response remains an honest empty state", async () => {
  assert.match(await render("ready"), /chat\.noSessions/)
})

test("background refresh states keep exactly the same existing conversation UI", async () => {
  const rows = [{ id: "saved", title: "Saved conversation", updated_at: "2026-10-16T00:00:00Z", last_message: "Previous answer" }]
  const ready = await render("ready", rows)
  assert.match(ready, /Saved conversation/)
  for (const status of ["loading", "error"]) {
    const html = await render(status, rows)
    assert.equal(html, ready, `${status} must not insert a status placeholder above saved rows`)
    assert.doesNotMatch(html, /role="alert"|chat\.sessionsLoadFailed|chat\.sessionsLoading|chat\.noSessions/)
  }
})
