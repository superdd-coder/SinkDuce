import assert from "node:assert/strict"
import { after, before, beforeEach, test } from "node:test"
import { fileURLToPath } from "node:url"
import { createServer } from "vite"
import type { SessionItem } from "../src/api/chat.ts"

interface SessionListState {
  sessions: SessionItem[]
  sessionListStatus: "idle" | "loading" | "ready" | "error"
  refreshSessionList: () => Promise<void>
}

let server: Awaited<ReturnType<typeof createServer>>
let store: {
  getState: () => SessionListState
  setState: (state: SessionListState, replace: true) => void
}
let initialState: SessionListState
const originalFetch = globalThis.fetch
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage")

before(async () => {
  const values = new Map<string, string>()
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    },
  })
  const root = fileURLToPath(new URL("../", import.meta.url))
  server = await createServer({
    root,
    configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: { alias: { "@": root + "src", "#edition": root + "src/edition" } },
    optimizeDeps: { noDiscovery: true },
  })
  ;({ useAppStore: store } = await server.ssrLoadModule("/src/stores/app-store.ts"))
  initialState = store.getState()
})

beforeEach((context) => {
  // Failure fixtures intentionally exercise the temporary development diagnostics.
  context.mock.method(console, "error", () => undefined)
  store.setState(initialState, true)
})

after(async () => {
  globalThis.fetch = originalFetch
  await server?.close()
  // The real store persists preferences on a 500 ms debounce after state changes.
  await new Promise((resolve) => setTimeout(resolve, 550))
  if (originalStorage) Object.defineProperty(globalThis, "localStorage", originalStorage)
  else Reflect.deleteProperty(globalThis, "localStorage")
})

const sessions: SessionItem[] = Array.from({ length: 18 }, (_, index) => ({
  id: `session-${index}`,
  title: `Conversation ${index}`,
  collections: [],
  created_at: "2026-10-15T12:00:00Z",
  updated_at: "2026-10-15T12:01:00Z",
  message_count: 2,
  last_message: "A previous answer",
}))

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

test("the session list starts idle rather than claiming to be an empty result", () => {
  assert.equal(store.getState().sessionListStatus, "idle")
  assert.deepEqual(store.getState().sessions, [])
})

test("a failed first load becomes an error and retry loads all 18 sessions", async () => {
  globalThis.fetch = async () => { throw new TypeError("Network unavailable") }
  await assert.doesNotReject(store.getState().refreshSessionList())
  assert.equal(store.getState().sessionListStatus, "error")
  assert.deepEqual(store.getState().sessions, [])

  globalThis.fetch = async () => jsonResponse(sessions)
  await store.getState().refreshSessionList()
  assert.equal(store.getState().sessionListStatus, "ready")
  assert.deepEqual(store.getState().sessions, sessions)
})

test("a failed refresh preserves the existing session list", async () => {
  globalThis.fetch = async () => jsonResponse(sessions)
  await store.getState().refreshSessionList()
  const previous = store.getState().sessions

  globalThis.fetch = async () => new Response("Unavailable", { status: 503 })
  await assert.doesNotReject(store.getState().refreshSessionList())
  assert.equal(store.getState().sessionListStatus, "error")
  assert.strictEqual(store.getState().sessions, previous)
})

test("an in-flight request is loading while a genuine empty response is ready", async () => {
  let resolveResponse!: (response: Response) => void
  globalThis.fetch = () => new Promise<Response>((resolve) => { resolveResponse = resolve })
  const pending = store.getState().refreshSessionList()
  try {
    assert.equal(store.getState().sessionListStatus, "loading")
    assert.deepEqual(store.getState().sessions, [])
  } finally {
    resolveResponse(jsonResponse([]))
    await pending
  }
  assert.equal(store.getState().sessionListStatus, "ready")
  assert.deepEqual(store.getState().sessions, [])
})

test("background refresh leaves an existing list and its ready state uninterrupted", async () => {
  globalThis.fetch = async () => jsonResponse(sessions)
  await store.getState().refreshSessionList()
  const previous = store.getState().sessions
  let resolveResponse!: (response: Response) => void
  globalThis.fetch = () => new Promise<Response>((resolve) => { resolveResponse = resolve })
  const pending = store.getState().refreshSessionList()
  try {
    assert.equal(store.getState().sessionListStatus, "ready")
    assert.strictEqual(store.getState().sessions, previous)
  } finally {
    resolveResponse(jsonResponse(sessions))
    await pending
  }
})

test("retry with saved rows keeps the error state and old rows until the result arrives", async () => {
  globalThis.fetch = async () => jsonResponse(sessions)
  await store.getState().refreshSessionList()
  globalThis.fetch = async () => new Response("Unavailable", { status: 503 })
  await store.getState().refreshSessionList()
  const previous = store.getState().sessions
  let resolveResponse!: (response: Response) => void
  globalThis.fetch = () => new Promise<Response>((resolve) => { resolveResponse = resolve })
  const pending = store.getState().refreshSessionList()
  try {
    assert.equal(store.getState().sessionListStatus, "error")
    assert.strictEqual(store.getState().sessions, previous)
  } finally {
    resolveResponse(jsonResponse(sessions))
    await pending
  }
  assert.equal(store.getState().sessionListStatus, "ready")
})

test("background revalidation of a confirmed empty list does not restart initial loading", async () => {
  globalThis.fetch = async () => jsonResponse([])
  await store.getState().refreshSessionList()
  let resolveResponse!: (response: Response) => void
  globalThis.fetch = () => new Promise<Response>((resolve) => { resolveResponse = resolve })
  const pending = store.getState().refreshSessionList()
  try {
    assert.equal(store.getState().sessionListStatus, "ready")
  } finally {
    resolveResponse(jsonResponse([]))
    await pending
  }
})

test("retry without any saved rows still communicates an in-flight request", async () => {
  globalThis.fetch = async () => new Response("Unavailable", { status: 503 })
  await store.getState().refreshSessionList()
  let resolveResponse!: (response: Response) => void
  globalThis.fetch = () => new Promise<Response>((resolve) => { resolveResponse = resolve })
  const pending = store.getState().refreshSessionList()
  try {
    assert.equal(store.getState().sessionListStatus, "loading")
  } finally {
    resolveResponse(jsonResponse(sessions))
    await pending
  }
  assert.equal(store.getState().sessionListStatus, "ready")
})
