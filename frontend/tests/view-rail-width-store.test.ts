import assert from "node:assert/strict"
import { after, before, beforeEach, test } from "node:test"
import { fileURLToPath } from "node:url"
import { createServer } from "vite"

let server: Awaited<ReturnType<typeof createServer>>
let reload = 0
const values = new Map<string, string>()
const writes: Array<[string, string]> = []
let denyReads = false
let denyWrites = false
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage")
const originalFetch = globalThis.fetch

before(async () => {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem(key: string) {
        if (denyReads) throw new Error("Storage unavailable")
        return values.get(key) ?? null
      },
      setItem(key: string, value: string) {
        if (denyWrites) throw new Error("Storage unavailable")
        values.set(key, value)
        writes.push([key, value])
      },
      removeItem: (key: string) => values.delete(key),
    },
  })
  globalThis.fetch = async () => { throw new Error("Rail preferences must not call an API") }
  const root = fileURLToPath(new URL("../", import.meta.url))
  server = await createServer({
    root, configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: { alias: { "@": root + "src", "#edition": root + "src/edition" } },
    optimizeDeps: { noDiscovery: true },
  })
})

beforeEach(() => {
  values.clear()
  writes.length = 0
  denyReads = false
  denyWrites = false
})

after(async () => {
  denyReads = false
  denyWrites = false
  await server?.close()
  // Real store instances debounce the existing unrelated chat preferences.
  await new Promise((resolve) => setTimeout(resolve, 550))
  globalThis.fetch = originalFetch
  if (originalStorage) Object.defineProperty(globalThis, "localStorage", originalStorage)
  else Reflect.deleteProperty(globalThis, "localStorage")
})

async function loadStore() {
  const { useAppStore } = await server.ssrLoadModule(`/src/stores/app-store.ts?rail-test=${++reload}`)
  assert.equal(typeof useAppStore.getState().setViewRailWidth, "function", "the shared rail preference must be implemented")
  return useAppStore as typeof import("../src/stores/app-store.ts").useAppStore
}

async function helpers() {
  const module = await import("../src/lib/view-rail-width.ts").catch((error: { code?: string }) => {
    if (error.code === "ERR_MODULE_NOT_FOUND") return null
    throw error
  })
  assert.ok(module, "rail width normalization must be shared with drag controls")
  return module
}

test("shared width constants and normalization retain valid widths and clamp finite values", async () => {
  const { DEFAULT_VIEW_RAIL_WIDTH, MIN_VIEW_RAIL_WIDTH, MAX_VIEW_RAIL_WIDTH, normalizeViewRailWidth } = await helpers()
  assert.equal(DEFAULT_VIEW_RAIL_WIDTH, 228)
  assert.equal(MIN_VIEW_RAIL_WIDTH, 180)
  assert.equal(MAX_VIEW_RAIL_WIDTH, 480)
  for (const [input, expected] of [[0, 180], [179, 180], [180, 180], [228, 228], [300.5, 300.5], [480, 480], [900, 480]]) {
    assert.equal(normalizeViewRailWidth(input), expected)
  }
  for (const input of [NaN, Infinity, -Infinity, undefined, null, true, "300", {}, []]) {
    assert.equal(normalizeViewRailWidth(input), 228)
  }
})

test("a new browser uses the shared 228 pixel default", async () => {
  const store = await loadStore()
  assert.equal(store.getState().viewRailWidth, 228)
  assert.equal(values.has("rag_viewRailWidth"), false)
})

test("the report split ratio persists across reloads independently of sidebar width", async () => {
  const first = await loadStore()
  assert.equal(first.getState().reportChatRatio, 0.46)
  assert.equal(typeof first.getState().setReportChatRatio, "function")
  first.getState().setReportChatRatio(0.57)
  assert.equal(values.get("rag_reportChatRatio"), "0.57")
  assert.equal(first.getState().viewRailWidth, 228)
  const restored = await loadStore()
  assert.equal(restored.getState().reportChatRatio, 0.57)
})

test("resizing persists JSON immediately and a fresh store restores the same width", async () => {
  const first = await loadStore()
  first.getState().setViewRailWidth(312)
  assert.equal(first.getState().viewRailWidth, 312)
  assert.equal(values.get("rag_viewRailWidth"), "312")
  assert.deepEqual(writes.filter(([key]) => key.includes("viewRailWidth")), [["rag_viewRailWidth", "312"]])
  const restored = await loadStore()
  assert.notStrictEqual(restored, first, "reloading creates a new store rather than reading retained state")
  assert.equal(restored.getState().viewRailWidth, 312)
})

test("store setters normalize every persisted width", async () => {
  const store = await loadStore()
  for (const [input, expected] of [[-20, 180], [900, 480], [NaN, 228], [Infinity, 228], [257.25, 257.25]]) {
    store.getState().setViewRailWidth(input)
    assert.equal(store.getState().viewRailWidth, expected)
    assert.equal(values.get("rag_viewRailWidth"), JSON.stringify(expected))
  }
})

test("restored preferences are validated rather than trusted or coerced", async () => {
  for (const [raw, expected] of [["160", 180], ["900", 480], ["300.5", 300.5], ['"300"', 228], ["null", 228], ["true", 228], ["{}", 228], ["NaN", 228], ["{broken", 228]] as const) {
    values.set("rag_viewRailWidth", raw)
    assert.equal((await loadStore()).getState().viewRailWidth, expected)
  }
})

test("the width stays browser-wide when another backend source is selected", async () => {
  values.set("sinkduce.pairing.activeSource", "box-a")
  const store = await loadStore()
  store.getState().setViewRailWidth(340)
  values.set("sinkduce.pairing.activeSource", "box-b")
  assert.equal((await loadStore()).getState().viewRailWidth, 340)
  assert.deepEqual(writes.filter(([key]) => key.includes("viewRailWidth")), [["rag_viewRailWidth", "340"]])
})

test("unavailable browser storage leaves loading and resizing usable", async () => {
  denyReads = true
  let store: Awaited<ReturnType<typeof loadStore>>
  try {
    store = await loadStore()
    assert.equal(store.getState().viewRailWidth, 228)
  } finally {
    denyReads = false
  }
  denyWrites = true
  try {
    assert.doesNotThrow(() => store.getState().setViewRailWidth(320))
    assert.equal(store.getState().viewRailWidth, 320)
  } finally {
    denyWrites = false
  }
})
