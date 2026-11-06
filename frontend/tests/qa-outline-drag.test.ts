import { test } from "node:test"
import assert from "node:assert/strict"
import { qaDropPlan, qaMovingIds, applyQaMove } from "../src/lib/qa-outline-drag.ts"

const rows = [
  { id: "g1", kind: "group", text: "Garden" },
  { id: "a", kind: "question", group_id: "g1" },
  { id: "a1", kind: "question", group_id: "g1", parent_id: "a" },
  { id: "n", kind: "note", group_id: "g1" },
  { id: "b", kind: "question", group_id: "g1" },
  { id: "g2", kind: "group", text: "Workshop" },
  { id: "c", kind: "question", group_id: "g2" },
] as const

test("moving a question after another keeps its children together and targets the next sibling", () => {
  const plan = qaDropPlan(rows, "b", "a", "after")!
  assert.equal(plan.anchor, "a1")
  assert.deepEqual(plan.payload, { group_id: "g1", parent_id: "", before_id: "n" })
  assert.deepEqual(applyQaMove(rows, "b", plan.payload).map(r => r.id), ["g1", "a", "a1", "b", "n", "g2", "c"])
})

test("cross-group moves carry descendants, and dropping on a group appends to that group", () => {
  const plan = qaDropPlan(rows, "a", "g2", "before")!
  const result = applyQaMove(rows, "a", plan.payload)
  assert.deepEqual(result.map(r => r.id), ["g1", "n", "b", "g2", "c", "a", "a1"])
  assert.equal(result.at(-1)?.group_id, "g2")
  assert.equal(result.at(-1)?.parent_id, "a")
})

test("group dragging carries every member and can land after the final group", () => {
  const plan = qaDropPlan(rows, "g1", "g2", "after")!
  assert.deepEqual(applyQaMove(rows, "g1", plan.payload).map(r => r.id), ["g2", "c", "g1", "a", "a1", "n", "b"])
  assert.equal(qaDropPlan(rows, "g1", "a", "before"), null)
})

test("cannot move into descendants or silently create a parent relationship", () => {
  assert.deepEqual([...qaMovingIds(rows, "a")], ["a", "a1"])
  assert.equal(qaDropPlan(rows, "a", "a1", "after"), null)
  const plan = qaDropPlan(rows, "b", "a1", "before")!
  assert.equal(plan.payload.parent_id, "")
  assert.equal(plan.payload.before_id, "a")
  assert.equal(qaDropPlan(rows, "a1", "b", "before"), null)
})

test("ungrouping is explicit, immutable and places the complete question at the top", () => {
  const plan = qaDropPlan(rows, "a", "__ungrouped", "after")!
  const result = applyQaMove(rows, "a", plan.payload)
  assert.deepEqual(result.slice(0, 2).map(r => r.id), ["a", "a1"])
  assert.equal(result[0].group_id, "")
  assert.equal(rows[1].group_id, "g1")
})
