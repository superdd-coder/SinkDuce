import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"

const body = readFileSync(
  new URL("../frontend/src/components/chat/streaming-answer-body.tsx", import.meta.url),
  "utf8",
)

test("Main Chat / Quick Chat answer renderer strips leaked meeting cite markers", () => {
  // [n:k] / [ref:N] belong to Group/Meeting Chat chips — never raw text in Chat.
  assert.ok(
    body.includes('(?:n|ref)\\s*:\\s*[^\\]]*'),
    "cite-marker regex must exist in streaming-answer-body"
  )
})

test("Strip is applied on done, streaming head and tail paths", () => {
  const calls = body.match(/stripCiteMarkers\(/g) ?? []
  assert.ok(calls.length >= 3, `expected >=3 strip calls, got ${calls.length}`)
})
