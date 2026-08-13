import assert from "node:assert/strict"
import { test } from "node:test"

import { createSessionSseParser } from "../frontend/src/api/session-sse.ts"

test("parses a complete event+data pair", () => {
  const p = createSessionSseParser()
  const msgs = p.push('event: token\ndata: {"content":"hi"}\n')
  assert.deepEqual(msgs, [{ event: "token", data: { content: "hi" } }])
})

test("holds a split event across chunks", () => {
  const p = createSessionSseParser()
  assert.deepEqual(p.push("event: tok"), [])
  assert.deepEqual(p.push('en\ndata: {"content":"a"}\n'), [
    { event: "token", data: { content: "a" } },
  ])
})

test("holds a split data line across chunks", () => {
  const p = createSessionSseParser()
  assert.deepEqual(p.push('event: token\ndata: {"con'), [])
  assert.deepEqual(p.push('tent":"xy"}\n'), [
    { event: "token", data: { content: "xy" } },
  ])
})

test("skips malformed JSON and keeps going", () => {
  const p = createSessionSseParser()
  const msgs = p.push(
    'event: token\ndata: not-json\nevent: token\ndata: {"content":"ok"}\n',
  )
  assert.deepEqual(msgs, [{ event: "token", data: { content: "ok" } }])
})

test("ignores data lines before any event", () => {
  const p = createSessionSseParser()
  assert.deepEqual(p.push('data: {"content":"orphan"}\n'), [])
})

test("emits multiple events from one chunk", () => {
  const p = createSessionSseParser()
  const msgs = p.push(
    'event: thinking\ndata: {"content":"t"}\nevent: token\ndata: {"content":"x"}\n',
  )
  assert.equal(msgs.length, 2)
  assert.equal(msgs[0].event, "thinking")
  assert.equal(msgs[1].event, "token")
})
