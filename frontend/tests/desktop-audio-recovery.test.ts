import assert from "node:assert/strict"
import { existsSync } from "node:fs"
import { test } from "node:test"

const moduleUrl = new URL("../src/lib/desktop-audio-errors.ts", import.meta.url)
async function helpers() {
  assert.ok(existsSync(moduleUrl), "Desktop audio errors must preserve recovery codes")
  return import(moduleUrl.href)
}

test("recognizes permission errors from old and new desktop proxies", async () => {
  const { parseDesktopAudioError } = await helpers()
  for (const body of [
    { error: "microphone_permission", message: "Enable microphone" },
    { detail: { code: "microphone_permission", message: "Enable microphone" } },
    { detail: JSON.stringify({ error: "microphone_permission", message: "Enable microphone" }) },
  ]) {
    const error = parseDesktopAudioError(403, body)
    assert.equal(error.code, "microphone_permission")
    assert.equal(error.message, "Enable microphone")
  }
})

test("pending permission waits for the original dialog instead of reporting denial", async () => {
  const { waitForDesktopPcm } = await helpers()
  let calls = 0
  const fetchPcm = async () => ++calls < 3
    ? Response.json({ error: "permission_pending" }, { status: 409 })
    : new Response(new Uint8Array([1, 2]))
  const res = await waitForDesktopPcm(fetchPcm, new AbortController().signal, { retryDelayMs: 0 })
  assert.equal(res.status, 200)
  assert.equal(calls, 3)
})

test("denial is returned once for recovery, with no repeated requests", async () => {
  const { waitForDesktopPcm } = await helpers()
  let calls = 0
  await assert.rejects(waitForDesktopPcm(async () => {
    calls++
    return Response.json({ detail: { code: "microphone_permission", message: "Denied" } }, { status: 403 })
  }, new AbortController().signal, { retryDelayMs: 0 }), { code: "microphone_permission" })
  assert.equal(calls, 1)
})

test("slow helper readiness recovers without reopening the app", async () => {
  const { waitForDesktopPcm } = await helpers()
  let calls = 0
  const res = await waitForDesktopPcm(async () => ++calls < 3
    ? new Response("not ready", { status: 503 })
    : new Response(new Uint8Array([1, 2])), new AbortController().signal, { retryDelayMs: 0 })
  assert.equal(res.status, 200)
  assert.equal(calls, 3)
})

test("cancelling pending authorization stops retries", async () => {
  const { waitForDesktopPcm } = await helpers()
  const ac = new AbortController()
  let calls = 0
  await assert.rejects(waitForDesktopPcm(async () => {
    calls++
    ac.abort()
    return Response.json({ error: "permission_pending" }, { status: 409 })
  }, ac.signal, { retryDelayMs: 0 }), { name: "AbortError" })
  assert.equal(calls, 1)
})

test("recovery restart requires confirmed idle capture", async () => {
  const { canRestartDesktopAudio } = await helpers()
  assert.equal(typeof canRestartDesktopAudio, "function")
  assert.equal(canRestartDesktopAudio(false, null), false)
  assert.equal(canRestartDesktopAudio(true, { capturing: false }), false)
  assert.equal(canRestartDesktopAudio(false, { capturing: true }), false)
  assert.equal(canRestartDesktopAudio(false, { capturing: false }), true)
})

test("a preceding recording can finish releasing its stream before retry", async () => {
  const { waitForDesktopPcm } = await helpers()
  let calls = 0
  const res = await waitForDesktopPcm(async () => ++calls < 2
    ? Response.json({ error: "capture_busy", message: "Finishing previous stream" }, { status: 409 })
    : new Response(new Uint8Array([1, 2])), new AbortController().signal, { retryDelayMs: 0 })
  assert.equal(res.status, 200)
  assert.equal(calls, 2)
})
