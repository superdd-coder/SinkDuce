import assert from "node:assert/strict"
import { test } from "node:test"
import { createQaProgressSampler, readQaDraftStream, type QaDraftProgress } from "../src/lib/qa-draft-progress.ts"

const encode = (text: string) => new TextEncoder().encode(text)
const result = (value: unknown) => {
  assert.equal(typeof value, "object")
  assert.ok(value && "body" in value && typeof value.body === "string")
  return value as { body: string }
}

test("draft progress survives split SSE frames, UTF-8 bytes, heartbeats and CRLF", async () => {
  const text = ': heartbeat\r\n\r\nevent: progress\r\ndata: {"stage":"retrieving","count":4,"private_reasoning":"omitted"}\r\n\r\nevent: progress\r\ndata: {"stage":"drafting",\r\ndata: "operation":"revise"}\r\n\r\nevent: result\r\ndata: {"body":"Confirm the café entrance."}\r\n\r\n'
  const bytes = encode(text)
  const stream = new ReadableStream<Uint8Array>({ start(controller) {
    for (let i = 0; i < bytes.length; i += 3) controller.enqueue(bytes.slice(i, i + 3))
    controller.close()
  } })
  const updates: QaDraftProgress[] = []
  const proposal = await readQaDraftStream(stream, progress => updates.push(progress), result)
  assert.equal(proposal.body, "Confirm the café entrance.")
  assert.deepEqual(updates, [{ stage: "retrieving", count: 4 }, { stage: "drafting", operation: "revise" }])
})

test("unknown progress is ignored, server errors and truncated streams reject", async () => {
  const read = (text: string) => readQaDraftStream(new ReadableStream({ start(controller) { controller.enqueue(encode(text)); controller.close() } }), () => {}, result)
  assert.deepEqual(await read('event: progress\ndata: {"stage":"invented"}\n\nevent: result\ndata: {"body":"Ready."}\n\n'), { body: "Ready." })
  await assert.rejects(read('event: error\ndata: {"code":"ai_repeated_reply"}\n\n'), /ai_repeated_reply/)
  await assert.rejects(read('event: progress\ndata: {"stage":"drafting"}\n\n'), /qa_incomplete_stream/)
  await assert.rejects(read('event: result\ndata: broken\n\n'), /qa_invalid_stream/)
})

test("retrieval planning and evidence grading expose only safe stage metadata", async () => {
  const text = 'event: progress\ndata: {"stage":"planning","content":"private retrieval plan","raw_reasoning":"private trace"}\n\nevent: progress\ndata: {"stage":"grading","count":3,"content":"private evidence review"}\n\nevent: result\ndata: {"body":"Please confirm the cabinet record."}\n\n'
  const updates: QaDraftProgress[] = []
  const stream = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(encode(text)); controller.close() } })
  await readQaDraftStream(stream, progress => updates.push(progress), result)
  assert.deepEqual(updates, [{ stage: "planning" }, { stage: "grading", count: 3 }])
  assert.doesNotMatch(JSON.stringify(updates), /private|content|reasoning/)
})

test("aborting cancels the reader and prevents late progress or results", async () => {
  const controller = new AbortController()
  let cancelled = false
  const updates: QaDraftProgress[] = []
  const stream = new ReadableStream<Uint8Array>({
    start(sink) { sink.enqueue(encode('event: progress\ndata: {"stage":"intent"}\n\nevent: result\ndata: {"body":"Late."}\n\n')) },
    cancel() { cancelled = true },
  })
  await assert.rejects(readQaDraftStream(stream, progress => { updates.push(progress); controller.abort() }, result, controller.signal), { name: "AbortError" })
  assert.equal(cancelled, true)
  assert.deepEqual(updates, [{ stage: "intent" }])
})

test("progress sampler publishes only the latest real event once per half second and stops cleanly", context => {
  context.mock.timers.enable({ apis: ["setInterval"] })
  const updates: QaDraftProgress[] = []
  const sampler = createQaProgressSampler(progress => updates.push(progress))
  sampler.update({ stage: "intent" })
  context.mock.timers.tick(250)
  sampler.update({ stage: "retrieving" })
  context.mock.timers.tick(249)
  assert.deepEqual(updates, [])
  context.mock.timers.tick(1)
  assert.deepEqual(updates, [{ stage: "retrieving" }])
  context.mock.timers.tick(1000)
  assert.equal(updates.length, 1)
  sampler.update({ stage: "drafting", operation: "polish" })
  sampler.stop()
  context.mock.timers.tick(500)
  sampler.update({ stage: "finalizing" })
  context.mock.timers.tick(500)
  assert.equal(updates.length, 1)
})
