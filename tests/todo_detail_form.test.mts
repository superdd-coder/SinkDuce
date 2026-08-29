import assert from "node:assert/strict"
import { test } from "node:test"

import {
  seedFromTodo,
  shouldMountTodoDetailEditor,
} from "../frontend/src/lib/todo-detail-form.ts"

const todoA = {
  todo_id: "todo-a",
  title: "First",
  body: "body of first todo",
  ddl: "2026-08-01T00:00:00",
  chain_id: "chain-1",
}

const todoB = {
  todo_id: "todo-b",
  title: "Second",
  body: "body of second todo",
  ddl: null as string | null,
  chain_id: "chain-1",
}

test("seed copies the opened todo body, not a previous todo", () => {
  const a = seedFromTodo(todoA)
  const b = seedFromTodo(todoB)
  assert.equal(a.body, "body of first todo")
  assert.equal(b.body, "body of second todo")
  assert.notEqual(a.body, b.body)
  assert.equal(a.ddl, "2026-08-01")
  assert.equal(b.ddl, "")
  assert.equal(seedFromTodo({ ...todoB, body: null }).body, "")
})

test("editor stays unmounted while seed still belongs to the previous todo", () => {
  // First paint after clicking todo B: dialog open, form seed is still A.
  // Mounting TipTap here would freeze A's body as initial content.
  assert.equal(
    shouldMountTodoDetailEditor(true, todoB.todo_id, todoA.todo_id),
    false,
  )
  assert.equal(
    shouldMountTodoDetailEditor(true, todoB.todo_id, null),
    false,
  )
})

test("editor mounts only after seed matches the open todo", () => {
  const seed = seedFromTodo(todoB)
  assert.equal(
    shouldMountTodoDetailEditor(true, todoB.todo_id, seed.todoId),
    true,
  )
})

test("closed dialog does not keep the editor mounted", () => {
  assert.equal(
    shouldMountTodoDetailEditor(false, todoA.todo_id, todoA.todo_id),
    false,
  )
})
