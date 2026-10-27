/**
 * Client id for event-stream echo suppression.
 *
 * The load-bearing property is **per-tab scope, not per-origin**: echo
 * suppression means "I already applied this change locally", which is true for
 * the tab that made it and false for another tab of the same browser. Because
 * two windows on one machine is exactly the two-client case this feature exists
 * for, an id shared across tabs would make them blind to each other.
 *
 * Each test builds a fresh module instance, so the module-level fallback does not
 * leak between cases.
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { before, beforeEach, test } from "node:test"

const STORAGE_KEY = "sinkduce.clientId"

interface FakeStorage {
  data: Map<string, string>
  throwOnSet: boolean
}

/** Per-tab store, mirroring sessionStorage semantics. */
function makeStorage(seed?: Record<string, string>): {
  api: Storage
  state: FakeStorage
} {
  const state: FakeStorage = {
    data: new Map(Object.entries(seed ?? {})),
    throwOnSet: false,
  }
  const api = {
    getItem: (k: string) => state.data.get(k) ?? null,
    setItem: (k: string, v: string) => {
      if (state.throwOnSet) throw new Error("quota exceeded")
      state.data.set(k, v)
    },
    removeItem: (k: string) => void state.data.delete(k),
    clear: () => state.data.clear(),
    key: () => null,
    length: 0,
  } as unknown as Storage
  return { api, state }
}

let tabA: ReturnType<typeof makeStorage>
let tabB: ReturnType<typeof makeStorage>

beforeEach(() => {
  // Two independent per-tab stores, as a browser would give two windows.
  tabA = makeStorage()
  tabB = makeStorage()
  const g = globalThis as { sessionStorage?: unknown }
  g.sessionStorage = tabA.api
})

/** Import a fresh module instance (so module-level fallback starts empty). */
async function loadClientId() {
  return await import(`../src/api/client-id.ts?t=${Math.random()}`)
}

test("generates and persists an id on first use", async () => {
  const { getClientId } = await loadClientId()
  const id = getClientId()
  assert.match(id, /^[0-9a-f-]{8,}$/i)
  assert.equal(tabA.state.data.get(STORAGE_KEY), id)
})

test("returns the same id across calls", async () => {
  const { getClientId } = await loadClientId()
  assert.equal(getClientId(), getClientId())
})

test("reuses an id already in this tab's storage (survives reload)", async () => {
  tabA = makeStorage({ [STORAGE_KEY]: "existing-id-123" })
  ;(globalThis as { sessionStorage: unknown }).sessionStorage = tabA.api
  const { getClientId } = await loadClientId()
  assert.equal(getClientId(), "existing-id-123")
})

test("ignores a blank stored value and generates a fresh id", async () => {
  tabA = makeStorage({ [STORAGE_KEY]: "   " })
  ;(globalThis as { sessionStorage: unknown }).sessionStorage = tabA.api
  const { getClientId } = await loadClientId()
  const id = getClientId()
  assert.notEqual(id.trim(), "")
  assert.notEqual(id, "   ")
})

test("still returns an id when storage writes fail", async () => {
  tabA.state.throwOnSet = true
  const { getClientId } = await loadClientId()
  assert.match(getClientId(), /^[0-9a-f-]{8,}$/i)
})

test("is stable for the session when storage is unavailable", async () => {
  delete (globalThis as { sessionStorage?: unknown }).sessionStorage
  const { getClientId } = await loadClientId()
  assert.equal(getClientId(), getClientId())
})

// ── The reason this is sessionStorage and not localStorage ──────

test("two tabs of the same browser get DIFFERENT ids", async () => {
  // Both tabs share one localStorage (as a real browser does) but have their own
  // sessionStorage. If the id were read from the shared store, the two tabs would
  // collapse to one identity and suppress each other's changes.
  const shared = makeStorage()
  const g = globalThis as { localStorage?: unknown; sessionStorage?: unknown }
  g.localStorage = shared.api

  g.sessionStorage = tabA.api
  const a = await loadClientId()
  const idA = a.getClientId()

  g.sessionStorage = tabB.api
  const b = await loadClientId()
  const idB = b.getClientId()

  assert.notEqual(
    idA,
    idB,
    "tabs must be distinguishable or they will suppress each other's changes",
  )
  // And neither should have leaked into the shared store.
  assert.equal(shared.state.data.get(STORAGE_KEY), undefined)
})

test("a browser does not put the id in localStorage (would be shared by tabs)", async () => {
  const shared = makeStorage()
  const g = globalThis as { localStorage?: unknown; sessionStorage?: unknown }
  g.localStorage = shared.api
  g.sessionStorage = tabA.api

  const { getClientId } = await loadClientId()
  const id = getClientId()

  assert.equal(tabA.state.data.get(STORAGE_KEY), id, "written to this tab's store")
  assert.equal(
    shared.state.data.get(STORAGE_KEY),
    undefined,
    "must not be written to localStorage, which every tab shares",
  )
})
