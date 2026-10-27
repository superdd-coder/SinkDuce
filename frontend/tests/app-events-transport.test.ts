/**
 * Event-stream transport: URL construction, frame parsing, cursor tracking,
 * resync handling, and reconnect.
 *
 * The backend protocol is documented in
 * development_doc/2026-09-16-multi-device-state-sync-design.md (§6, §7).
 * 2026-09-19 token 收口：钥匙/client id 走请求头（见 event-stream-lifecycle），
 * URL 里只允许游标 `since` —— 这两条现在由测试反向钉死。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

import {
  buildStreamUrl,
  parseFrame,
  parseRawFrame,
  type ChangeEvent,
} from "../src/api/events.ts"

function installStorage(seed: Record<string, string> = {}) {
  const local = new Map<string, string>(Object.entries(seed))
  ;(globalThis as { localStorage: unknown }).localStorage = {
    getItem: (k: string) => (local.has(k) ? (local.get(k) as string) : null),
    setItem: (k: string, v: string) => void local.set(k, v),
    removeItem: (k: string) => void local.delete(k),
    clear: () => local.clear(),
    key: () => null,
    length: 0,
  } as unknown as Storage
  return local
}

beforeEach(() => {
  installStorage()
})

// ── URL construction ────────────────────────────────────────────

test("stream url includes since when provided", () => {
  const url = buildStreamUrl({ since: 42 })
  assert.match(url, /\/api\/events\/stream\?/)
  assert.match(url, /since=42/)
})

test("stream url omits since on a fresh connection", () => {
  const url = buildStreamUrl({})
  assert.doesNotMatch(url, /since=/)
})

test("stream url never carries the pairing token (access-log hygiene)", () => {
  installStorage({
    "sinkduce.pairing.ownerKeys": JSON.stringify({ local: "sd1_keytoken" }),
  })
  const url = buildStreamUrl({ since: 1 })
  assert.doesNotMatch(url, /token=/)
  assert.doesNotMatch(url, /sd1_keytoken/)
})

test("stream url never carries the client id (header now)", () => {
  const url = buildStreamUrl({})
  assert.doesNotMatch(url, /client=/)
})

// ── Raw SSE block parsing ───────────────────────────────────────

test("parseRawFrame extracts event name and data", () => {
  const out = parseRawFrame("event: change\ndata: {\"seq\":1}")
  assert.equal(out?.name, "change")
  assert.equal(out?.data, "{\"seq\":1}")
})

test("parseRawFrame maps a comment to the keepalive sentinel", () => {
  const out = parseRawFrame(": keepalive")
  assert.equal(out?.name, ":")
  assert.equal(out?.data, null)
})

test("parseRawFrame tolerates CRLF and multi-line data", () => {
  const out = parseRawFrame("event: hello\r\ndata: {\"a\":1}\r\ndata: {\"b\":2}")
  assert.equal(out?.name, "hello")
  assert.equal(out?.data, "{\"a\":1}\n{\"b\":2}")
})

// ── Frame parsing ───────────────────────────────────────────────

test("parses a change frame into an event object", () => {
  const frame = parseFrame(
    "change",
    JSON.stringify({
      seq: 7,
      ts: 1.5,
      type: "meeting.updated",
      domain: "meetings",
      scope: { meeting_id: "m1" },
      origin: { client_id: "other" },
      payload: {},
    }),
  )
  assert.equal(frame.kind, "change")
  const ev = (frame as { event: ChangeEvent }).event
  assert.equal(ev.seq, 7)
  assert.equal(ev.domain, "meetings")
  assert.equal(ev.scope.meeting_id, "m1")
})

test("parses hello with the current cursor and ring floor", () => {
  const frame = parseFrame("hello", JSON.stringify({ seq: 3, oldest: 1 }))
  assert.deepEqual(frame, { kind: "hello", seq: 3, oldest: 1 })
})

test("parses a resync frame with its reason", () => {
  assert.deepEqual(parseFrame("resync", JSON.stringify({ reason: "gap" })), {
    kind: "resync",
    reason: "gap",
  })
  assert.deepEqual(parseFrame("resync", JSON.stringify({ reason: "overflow" })), {
    kind: "resync",
    reason: "overflow",
  })
})

test("treats a keepalive comment as a keepalive frame", () => {
  assert.deepEqual(parseFrame(":", null), { kind: "keepalive" })
})

test("returns null for malformed json instead of throwing", () => {
  // A truncated frame must never take down the stream.
  assert.equal(parseFrame("change", "{not json"), null)
})

test("returns null for an unknown frame name", () => {
  assert.equal(parseFrame("something-else", "{}"), null)
})

test("returns null for a change frame missing required fields", () => {
  assert.equal(parseFrame("change", JSON.stringify({ type: "x.y" })), null)
})
