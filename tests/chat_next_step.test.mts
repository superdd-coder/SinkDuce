import assert from "node:assert/strict"
import { test } from "node:test"

import { isWaitingForNextStep } from "../frontend/src/lib/chat-next-step.ts"

test("waiting after a finished tool while the turn is still streaming", () => {
  assert.equal(
    isWaitingForNextStep(
      [{ type: "tool", isStreaming: false, toolStatus: "done" }],
      true,
      false,
    ),
    true,
  )
})

test("not waiting while a tool is running", () => {
  assert.equal(
    isWaitingForNextStep(
      [{ type: "tool", isStreaming: true, toolStatus: "running" }],
      true,
      false,
    ),
    false,
  )
})

test("not waiting after the answer has started", () => {
  assert.equal(
    isWaitingForNextStep(
      [{ type: "tool", toolStatus: "done" }],
      true,
      true,
    ),
    false,
  )
})
