import assert from "node:assert/strict"
import { test } from "node:test"

import { qcFloatSlideMotion } from "../frontend/src/lib/quick-chat-float.ts"

test("QC slide stays opaque — only translates, never fades", () => {
  const closed = qcFloatSlideMotion(false)
  const opened = qcFloatSlideMotion(true)
  assert.equal(closed.opacity, 1)
  assert.equal(opened.opacity, 1)
  assert.notEqual(closed.transform, opened.transform)
})
