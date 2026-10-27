import assert from "node:assert/strict"
import { test } from "node:test"

import {
  mergeTodoUpdateInPlace,
  splitTodoSections,
} from "../frontend/src/lib/todo-list-state.ts"

const item = (id: string, done: boolean) =>
  ({ todo_id: id, title: id, done }) as {
    todo_id: string
    title: string
    done: boolean
  }

test("checking a todo does not change list order", () => {
  const before = [item("a", false), item("b", false), item("c", false)]
  const after = mergeTodoUpdateInPlace(before, item("b", true))
  assert.deepEqual(
    after.map((t) => t.todo_id),
    ["a", "b", "c"],
  )
  assert.equal(after[1].done, true)
})

test("just-completed stays in open until hold is cleared", () => {
  const todos = [item("a", false), item("b", true), item("c", false)]
  const held = splitTodoSections(todos, ["b"])
  assert.deepEqual(
    held.open.map((t) => t.todo_id),
    ["a", "b", "c"],
  )
  assert.deepEqual(held.completed, [])

  const afterRefresh = splitTodoSections(todos, [])
  assert.deepEqual(
    afterRefresh.open.map((t) => t.todo_id),
    ["a", "c"],
  )
  assert.deepEqual(
    afterRefresh.completed.map((t) => t.todo_id),
    ["b"],
  )
})
