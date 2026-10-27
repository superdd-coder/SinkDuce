import assert from "node:assert/strict"
import { test, beforeEach } from "node:test"

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
  setWebSearchConfirmAnchor,
} = await import("../frontend/src/lib/web-search-confirm.ts")

const {
  answerTodoDeleteConfirm,
  getTodoDeleteConfirmState,
  promptTodoDeleteConfirm,
  shouldShowTodoDeleteConfirm,
} = await import("../frontend/src/lib/todo-delete-confirm.ts")

const hostA = { id: "a" } as unknown as HTMLElement
const hostB = { id: "b" } as unknown as HTMLElement

beforeEach(() => {
  answerTodoDeleteConfirm(false)
  setWebSearchConfirmAnchor(null)
  store.clear()
})

test("closed todo-delete confirm is not visible", () => {
  assert.equal(shouldShowTodoDeleteConfirm(), false)
})

test("pending todo-delete shows even before a composer claims the session", async () => {
  const p = promptTodoDeleteConfirm("c1", "Weekly report", "chat-a", "Alpha")
  assert.equal(getTodoDeleteConfirmState().open, true)
  assert.equal(getTodoDeleteConfirmState().title, "Weekly report")
  assert.equal(shouldShowTodoDeleteConfirm(), true)

  setWebSearchConfirmAnchor(hostA, "chat-a")
  assert.equal(shouldShowTodoDeleteConfirm(), true)

  setWebSearchConfirmAnchor(hostB, "chat-b")
  assert.equal(shouldShowTodoDeleteConfirm(), false)
  assert.equal(getTodoDeleteConfirmState().open, true)

  setWebSearchConfirmAnchor(hostA, "chat-a")
  assert.equal(shouldShowTodoDeleteConfirm(), true)

  answerTodoDeleteConfirm(true)
  assert.equal(await p, true)
  assert.equal(shouldShowTodoDeleteConfirm(), false)
})

test("quick chat host does not inherit another session's todo-delete dialog", async () => {
  const p = promptTodoDeleteConfirm("c2", "Temp", "chat-a")
  setWebSearchConfirmAnchor(hostA, "chat-a")
  assert.equal(shouldShowTodoDeleteConfirm(), true)

  setWebSearchConfirmAnchor(hostB, "quick_col1")
  assert.equal(shouldShowTodoDeleteConfirm(), false)

  answerTodoDeleteConfirm(false)
  assert.equal(await p, false)
})
