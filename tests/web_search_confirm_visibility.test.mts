import assert from "node:assert/strict"
import { test, beforeEach } from "node:test"

// Node has no DOM storage / window; the module only touches them in try/catch
// except promptWebSearchConfirm's timeout.
const store = new Map<string, string>()
;(globalThis as any).sessionStorage = {
  getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string) => {
    store.set(k, v)
  },
  removeItem: (k: string) => {
    store.delete(k)
  },
}
;(globalThis as any).window = {
  setTimeout: (fn: () => void, ms: number) => globalThis.setTimeout(fn, ms),
}

const {
  answerWebSearchConfirm,
  getWebSearchConfirmState,
  promptWebSearchConfirm,
  setWebSearchConfirmAnchor,
  shouldShowWebSearchConfirm,
} = await import("../frontend/src/lib/web-search-confirm.ts")

const hostA = { id: "a" } as unknown as HTMLElement
const hostB = { id: "b" } as unknown as HTMLElement

beforeEach(() => {
  answerWebSearchConfirm(false)
  setWebSearchConfirmAnchor(null)
  store.clear()
})

test("closed confirm is not visible", () => {
  assert.equal(shouldShowWebSearchConfirm(), false)
})

test("pending confirm stays hidden until the matching session is on screen", async () => {
  const p = promptWebSearchConfirm("c1", "weather", "chat-a")
  assert.equal(getWebSearchConfirmState().open, true)
  assert.equal(shouldShowWebSearchConfirm(), false)

  setWebSearchConfirmAnchor(hostA, "chat-a")
  assert.equal(shouldShowWebSearchConfirm(), true)

  setWebSearchConfirmAnchor(hostB, "chat-b")
  assert.equal(shouldShowWebSearchConfirm(), false)
  assert.equal(getWebSearchConfirmState().open, true)

  setWebSearchConfirmAnchor(hostA, "chat-a")
  assert.equal(shouldShowWebSearchConfirm(), true)

  answerWebSearchConfirm(true)
  assert.equal(await p, true)
  assert.equal(shouldShowWebSearchConfirm(), false)
})

test("quick chat host does not inherit another session's dialog", async () => {
  const p = promptWebSearchConfirm("c2", "news", "chat-a")
  setWebSearchConfirmAnchor(hostA, "chat-a")
  assert.equal(shouldShowWebSearchConfirm(), true)

  setWebSearchConfirmAnchor(hostB, "quick_col1")
  assert.equal(shouldShowWebSearchConfirm(), false)

  answerWebSearchConfirm(false)
  assert.equal(await p, false)
})
