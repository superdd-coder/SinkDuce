import assert from "node:assert/strict"
import { test } from "node:test"

import { isDesktopExternalUrl } from "../frontend/src/lib/desktop-external-url.ts"

const origin = "http://127.0.0.1:18910"

test("mineru and other https docs are external", () => {
  assert.equal(
    isDesktopExternalUrl("https://mineru.net/apiManage/token", origin),
    true,
  )
  assert.equal(isDesktopExternalUrl("https://tavily.com", origin), true)
})

test("same-origin and relative paths stay in the app", () => {
  assert.equal(isDesktopExternalUrl("/settings", origin), false)
  assert.equal(isDesktopExternalUrl("http://127.0.0.1:18910/api/version", origin), false)
  assert.equal(isDesktopExternalUrl("", origin), false)
})

test("javascript, file, and blank windows are not external", () => {
  assert.equal(isDesktopExternalUrl("javascript:alert(1)", origin), false)
  assert.equal(isDesktopExternalUrl("file:///tmp/x", origin), false)
  assert.equal(isDesktopExternalUrl("about:blank", origin), false)
})
