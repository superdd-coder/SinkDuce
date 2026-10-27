import assert from "node:assert/strict"
import { test } from "node:test"

import {
  createImeEnterGuard,
  isChatSubmitEnter,
} from "../frontend/src/lib/ime.ts"

test("plain Enter submits a chat message", () => {
  assert.equal(isChatSubmitEnter({ key: "Enter", shiftKey: false }), true)
})

test("Shift+Enter does not submit (newline)", () => {
  assert.equal(isChatSubmitEnter({ key: "Enter", shiftKey: true }), false)
})

test("non-Enter keys do not submit", () => {
  assert.equal(isChatSubmitEnter({ key: "a", shiftKey: false }), false)
})

test("IME composing Enter (Chinese input) does not submit", () => {
  assert.equal(
    isChatSubmitEnter({
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: true },
    }),
    false,
  )
})

test("IME keyCode 229 Enter does not submit", () => {
  assert.equal(
    isChatSubmitEnter({
      key: "Enter",
      shiftKey: false,
      keyCode: 229,
    }),
    false,
  )
})

test("Chrome/Safari: Enter after compositionend does not submit", () => {
  const guard = createImeEnterGuard()
  guard.onCompositionStart()
  guard.onCompositionEnd()
  assert.equal(
    guard.isSubmitEnter({ key: "Enter", shiftKey: false }),
    false,
  )
  guard.clearJustEnded()
  assert.equal(
    guard.isSubmitEnter({ key: "Enter", shiftKey: false }),
    true,
  )
})

test("second Enter after IME confirm still sends", () => {
  const guard = createImeEnterGuard()
  guard.onCompositionStart()
  assert.equal(
    guard.isSubmitEnter({
      key: "Enter",
      shiftKey: false,
      nativeEvent: { isComposing: true },
    }),
    false,
  )
  guard.onCompositionEnd()
  guard.clearJustEnded()
  assert.equal(guard.isSubmitEnter({ key: "Enter", shiftKey: false }), true)
})
