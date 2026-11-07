import assert from "node:assert/strict"
import { afterEach, beforeEach, test } from "node:test"

const originalFetch = globalThis.fetch
let calls: { signal: AbortSignal | null | undefined; respond: () => void }[]

beforeEach(() => {
  calls = []
  globalThis.fetch = (_input, init) => new Promise((resolve, reject) => {
    const signal = init?.signal
    const abort = () => reject(new DOMException("Aborted", "AbortError"))
    if (signal?.aborted) { abort(); return }
    signal?.addEventListener("abort", abort, { once: true })
    calls.push({ signal, respond: () => {
      signal?.removeEventListener("abort", abort)
      resolve(Response.json({ lists: [{ id: "one" }] }))
    } })
  })
})

afterEach(() => { globalThis.fetch = originalFetch })

async function loadHttp() {
  return import(`../src/api/http.ts?cancellation=${Math.random()}`)
}

for (const flavor of ["client", "file-mgmt"] as const) {
  test(`${flavor}: a remount must not reuse the request aborted by effect cleanup`, async () => {
    const { request } = await loadHttp()
    const previous = new AbortController()
    const first = request("/lists", { flavor, signal: previous.signal })
    const aborted = assert.rejects(first, { name: "AbortError" })
    previous.abort()
    const next = request("/lists", { flavor, signal: new AbortController().signal })
    const settled = Promise.allSettled([next])
    calls.at(-1)!.respond()
    await aborted
    assert.deepEqual(await settled, [{ status: "fulfilled", value: { lists: [{ id: "one" }] } }])
    assert.equal(calls.length, 2)
  })
}

for (const signalledFirst of [true, false]) {
  test(`cancellation stays local when a signalled GET ${signalledFirst ? "starts before" : "joins"} an ordinary GET`, async () => {
    const { request } = await loadHttp()
    const controller = new AbortController()
    const signalled = () => request("/lists", { signal: controller.signal })
    const ordinary = () => request("/lists")
    const first = signalledFirst ? signalled() : ordinary()
    const second = signalledFirst ? ordinary() : signalled()
    const settled = Promise.allSettled([first, second])
    controller.abort()
    calls.forEach(call => call.respond())
    const results = await settled
    assert.equal(results[signalledFirst ? 0 : 1].status, "rejected")
    assert.deepEqual(results[signalledFirst ? 1 : 0], { status: "fulfilled", value: { lists: [{ id: "one" }] } })
    assert.equal(calls.length, 2)
  })
}

test("ordinary concurrent GETs still coalesce and isolate mutable results", async () => {
  const { request } = await loadHttp()
  const first = request("/lists")
  const second = request("/lists")
  assert.equal(calls.length, 1)
  calls[0].respond()
  const [a, b] = await Promise.all([first, second])
  assert.deepEqual(a, b)
  a.lists[0].id = "changed"
  assert.equal(b.lists[0].id, "one")
})
