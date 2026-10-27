/**
 * Stream lifecycle: cursor tracking across reconnects, resync handling, and
 * backoff. This is the "don't silently lose changes" contract from
 * development_doc/2026-09-16-multi-device-state-sync-design.md (§7).
 *
 * 2026-09-19 token 收口：传输层从 EventSource 换成 **fetch-stream**（钥匙/
 * client id 走请求头，不再进 query string）。这里用 fetch 假体驱动同样的
 * 生命周期，并额外钉住「钥匙只出现在请求头、绝不出现在 URL」。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

interface FakeConnection {
  url: string
  headers: Record<string, string>
  emit: (name: string, data: string) => void
  emitRaw: (text: string) => void
  fail: () => void
}

let created: FakeConnection[]
let fetchImpl: typeof fetch

function encoder(): TextEncoder {
  return new TextEncoder()
}

function installFakeFetch() {
  created = []
  ;(globalThis as { fetch: unknown }).fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const headers = (init?.headers ?? {}) as Record<string, string>
    let pullResolve: ((v: { done: boolean; value?: Uint8Array }) => void) | null = null
    let closed = false
    const queue: Uint8Array[] = []

    const flush = () => {
      if (!pullResolve) return
      const item = queue.shift()
      if (item !== undefined) {
        const r = pullResolve
        pullResolve = null
        r({ done: false, value: item })
      }
    }
    const reader = {
      read: () =>
        new Promise<{ done: boolean; value?: Uint8Array }>((resolve) => {
          // 队列里的数据先送完再报 done —— 与真实 socket 的缓冲语义一致
          // （close 前已到达的帧仍会送达）。
          if (queue.length) {
            resolve({ done: false, value: queue.shift() as Uint8Array })
            return
          }
          if (closed) {
            resolve({ done: true })
            return
          }
          pullResolve = resolve
        }),
    }

    const conn: FakeConnection = {
      url,
      headers,
      emit: (name, data) => {
        queue.push(encoder().encode(`event: ${name}\ndata: ${data}\n\n`))
        flush()
      },
      emitRaw: (text) => {
        queue.push(encoder().encode(text))
        flush()
      },
      fail: () => {
        // 断流语义：读取端拿到 done:true（与服务器关闭连接同形），
        // 实现据此走重连。
        closed = true
        if (pullResolve) {
          const r = pullResolve
          pullResolve = null
          r({ done: true })
        }
      },
    }
    created.push(conn)

    return Promise.resolve({
      ok: true,
      status: 200,
      body: { getReader: () => reader },
    } as unknown as Response)
  }) as unknown as typeof fetch

  const local = new Map<string, string>()
  ;(globalThis as { localStorage: unknown }).localStorage = {
    getItem: (k: string) => local.get(k) ?? null,
    setItem: (k: string, v: string) => void local.set(k, v),
    removeItem: (k: string) => void local.delete(k),
    clear: () => local.clear(),
    key: () => null,
    length: 0,
  } as unknown as Storage
}

beforeEach(() => {
  installFakeFetch()
})

async function loadEvents() {
  return await import(`../src/api/events.ts?t=${Math.random()}`)
}

function changePayload(seq: number, type = "file.updated") {
  return JSON.stringify({
    seq,
    ts: 1,
    type,
    domain: "files",
    scope: { collection_id: "c1" },
    origin: {},
    payload: {},
  })
}

test("first connection does not send a since cursor", async () => {
  const { openEventStream } = await loadEvents()
  const dispose = openEventStream({ onFrame: () => {} })
  await new Promise((r) => setTimeout(r, 20))
  assert.equal(created.length, 1)
  assert.doesNotMatch(created[0].url, /since=/)
  dispose()
})

test("token and client id travel in headers, never in the url", async () => {
  const local = new Map<string, string>(Object.entries({
    "sinkduce.pairing.ownerKeys": JSON.stringify({ local: "sd1_keytoken" }),
  }))
  ;(globalThis as { localStorage: unknown }).localStorage = {
    getItem: (k: string) => local.get(k) ?? null,
    setItem: (k: string, v: string) => void local.set(k, v),
    removeItem: (k: string) => void local.delete(k),
    clear: () => local.clear(),
    key: () => null,
    length: 0,
  } as unknown as Storage
  const { openEventStream } = await loadEvents()
  const dispose = openEventStream({ onFrame: () => {} })
  await new Promise((r) => setTimeout(r, 20))
  assert.match(created[0].headers["X-Pairing-Token"], /sd1_keytoken/)
  assert.ok(created[0].headers["X-Client-Id"])
  assert.doesNotMatch(created[0].url, /token=/, "钥匙绝不能进 URL（会被访问日志落盘）")
  assert.doesNotMatch(created[0].url, /client=/)
  dispose()
})

test("hello on a fresh connection sets the baseline cursor", async () => {
  const { openEventStream } = await loadEvents()
  const dispose = openEventStream({ onFrame: () => {} })
  await new Promise((r) => setTimeout(r, 20))
  created[0].emit("hello", JSON.stringify({ seq: 10, oldest: 5 }))
  created[0].fail()
  await new Promise((r) => setTimeout(r, 1200))
  assert.equal(created.length, 2)
  assert.match(created[1].url, /since=10/)
  dispose()
})

test("a received change advances the cursor used on reconnect", async () => {
  const { openEventStream } = await loadEvents()
  const dispose = openEventStream({ onFrame: () => {} })
  await new Promise((r) => setTimeout(r, 20))
  created[0].emit("hello", JSON.stringify({ seq: 3, oldest: 1 }))
  created[0].emit("change", changePayload(7))
  created[0].fail()
  await new Promise((r) => setTimeout(r, 1200))
  assert.match(created[1].url, /since=7/, "must resume after the last seen event")
  dispose()
})

test("hello on a reconnect does not rewind the cursor", async () => {
  const { openEventStream } = await loadEvents()
  const dispose = openEventStream({ onFrame: () => {} })
  await new Promise((r) => setTimeout(r, 20))
  created[0].emit("change", changePayload(9))
  created[0].fail()
  await new Promise((r) => setTimeout(r, 1200))
  created[1].emit("hello", JSON.stringify({ seq: 4, oldest: 1 }))
  created[1].fail()
  await new Promise((r) => setTimeout(r, 2200))
  assert.match(created[2].url, /since=9/)
  dispose()
})

test("a resync frame drops the cursor so the next connect is a fresh snapshot", async () => {
  const { openEventStream } = await loadEvents()
  const dispose = openEventStream({ onFrame: () => {} })
  await new Promise((r) => setTimeout(r, 20))
  created[0].emit("change", changePayload(5))
  created[0].emit("resync", JSON.stringify({ reason: "gap" }))
  created[0].fail()
  await new Promise((r) => setTimeout(r, 1200))
  assert.doesNotMatch(
    created[1].url,
    /since=/,
    "after a gap the client must reconcile rather than resume a dead cursor",
  )
  dispose()
})

test("resync and change frames are surfaced to the caller", async () => {
  const { openEventStream } = await loadEvents()
  const frames: string[] = []
  const dispose = openEventStream({
    onFrame: (f) => frames.push(f.kind === "resync" ? `resync:${f.reason}` : f.kind),
  })
  await new Promise((r) => setTimeout(r, 20))
  created[0].emit("hello", JSON.stringify({ seq: 1, oldest: 1 }))
  created[0].emit("change", changePayload(2))
  created[0].emit("resync", JSON.stringify({ reason: "overflow" }))
  await new Promise((r) => setTimeout(r, 10)) // fetch 流按微任务分发
  assert.deepEqual(frames, ["hello", "change", "resync:overflow"])
  dispose()
})

test("a malformed frame does not disturb the cursor or the caller", async () => {
  const { openEventStream } = await loadEvents()
  const frames: string[] = []
  const dispose = openEventStream({ onFrame: (f) => frames.push(f.kind) })
  await new Promise((r) => setTimeout(r, 20))
  created[0].emit("change", changePayload(4))
  created[0].emit("change", "{truncated")
  created[0].fail()
  await new Promise((r) => setTimeout(r, 1200))
  assert.deepEqual(frames, ["change"])
  assert.match(created[1].url, /since=4/)
  dispose()
})

test("keepalive comments arrive as keepalive frames", async () => {
  const { openEventStream } = await loadEvents()
  const frames: string[] = []
  const dispose = openEventStream({ onFrame: (f) => frames.push(f.kind) })
  await new Promise((r) => setTimeout(r, 20))
  created[0].emitRaw(": keepalive\n\n")
  await new Promise((r) => setTimeout(r, 10)) // fetch 流按微任务分发
  assert.deepEqual(frames, ["keepalive"])
  dispose()
})

test("frames split across chunk boundaries are reassembled", async () => {
  const { openEventStream } = await loadEvents()
  const frames: string[] = []
  const dispose = openEventStream({ onFrame: (f) => frames.push(f.kind) })
  await new Promise((r) => setTimeout(r, 20))
  const payload = changePayload(6)
  const text = `event: change\ndata: ${payload}\n\n`
  const bytes = new TextEncoder().encode(text)
  const mid = Math.floor(bytes.length / 2)
  created[0].emitRaw(new TextDecoder().decode(bytes.slice(0, mid)))
  created[0].emitRaw(new TextDecoder().decode(bytes.slice(mid)))
  await new Promise((r) => setTimeout(r, 10)) // fetch 流按微任务分发
  assert.deepEqual(frames, ["change"], "跨块的 SSE 帧必须重组后投递")
  dispose()
})

test("dispose stops reconnection", async () => {
  const { openEventStream } = await loadEvents()
  const dispose = openEventStream({ onFrame: () => {} })
  await new Promise((r) => setTimeout(r, 20))
  dispose()
  created[0].fail()
  await new Promise((r) => setTimeout(r, 1200))
  assert.equal(created.length, 1, "no new connection after dispose")
})
