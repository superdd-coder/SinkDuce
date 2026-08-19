import assert from "node:assert/strict"
import { test } from "node:test"

import { computeCaretScrollDelta } from "../frontend/src/lib/editor-scroll.ts"

const view = { top: 200, bottom: 700 }

test("visible one-line caret does not scroll", () => {
  assert.equal(computeCaretScrollDelta({ top: 360, bottom: 384 }, view), 0)
})

test("caret just above the view scrolls only the overflow", () => {
  assert.equal(computeCaretScrollDelta({ top: 180, bottom: 204 }, view), -28)
})

test("caret just below the view scrolls only the overflow", () => {
  assert.equal(computeCaretScrollDelta({ top: 690, bottom: 714 }, view), 22)
})

test("empty-heading tall caret in the middle does not jump to the top", () => {
  // After "## " the empty heading often reports a box from the line to the
  // editor pad (min-height: 100%). Default ProseMirror then pins rect.top
  // to the scroller top — the line leaps upward.
  assert.equal(computeCaretScrollDelta({ top: 420, bottom: 900 }, view), 0)
})

test("implausibly tall caret never scrolls even if its top is above the view", () => {
  // Safari / WKWebView empty headings often report top at the editor pad.
  // Using that top would still yank the current line to the scroller start.
  assert.equal(computeCaretScrollDelta({ top: 160, bottom: 800 }, view), 0)
})
