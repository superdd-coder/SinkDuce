import assert from "node:assert/strict"
import { after, before, test } from "node:test"
import { fileURLToPath } from "node:url"
import { createServer } from "vite"

type StreamModule = typeof import("../src/hooks/use-blueprint-stream.ts")
let server: Awaited<ReturnType<typeof createServer>>
let summary: StreamModule
const originalFetch = globalThis.fetch

before(async () => {
  const root = fileURLToPath(new URL("../", import.meta.url))
  server = await createServer({
    root, configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: { alias: { "@": root + "src", "#edition": root + "src/edition" } },
    optimizeDeps: { noDiscovery: true },
  })
  summary = await server.ssrLoadModule("/src/hooks/use-blueprint-stream.ts")
})

after(async () => {
  globalThis.fetch = originalFetch
  await server?.close()
})

function openStream(meetingId: string) {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const stream = new ReadableStream<Uint8Array>({ start(value) { controller = value } })
  const encoder = new TextEncoder()
  globalThis.fetch = async (input) => {
    assert.ok(String(input).endsWith(`/meetings/${meetingId}/blueprint/stream`))
    return new Response(stream, { status: 200 })
  }
  summary.startStream(meetingId)
  return {
    event(event: string, data: unknown) {
      controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`))
    },
    close() { controller.close() },
    state: () => summary.getBlueprintStreamState(meetingId),
    visible: (cached = "Old saved summary.") => summary.resolveBlueprintSummary(summary.getBlueprintStreamState(meetingId), cached),
  }
}

async function until(predicate: () => boolean) {
  const deadline = Date.now() + 1000
  while (!predicate()) {
    assert.ok(Date.now() < deadline, "the local summary stream reached its expected state")
    await new Promise((resolve) => setTimeout(resolve, 1))
  }
}

test("regeneration stays on new tokens across stale refreshes and the final cache handoff", async () => {
  const mid = "summary-regenerate"
  const staleLoadSnapshot = summary.getBlueprintStreamState(mid)
  assert.equal(summary.resolveBlueprintSummary(staleLoadSnapshot, "Old saved summary."), "Old saved summary.")
  const stream = openStream(mid)
  assert.equal(stream.visible(), "", "prefilling must not expose the old document")

  stream.event("state", { summary: "streaming", blueprint: "prefilling" })
  stream.event("token", "First new sentence.")
  await until(() => stream.state().streamingMd.length > 0)
  assert.equal(stream.visible(), "First new sentence.")
  assert.notEqual(stream.state(), staleLoadSnapshot, "an earlier file request is invalidated by generation")

  // A poll reloads the previous persisted file after the first words appear.
  assert.equal(stream.visible("Old saved summary returned by polling."), "First new sentence.")
  stream.event("token", " Second new sentence.")
  await until(() => stream.state().streamingMd.includes("Second"))
  assert.equal(stream.visible(), "First new sentence. Second new sentence.")

  const finalMd = "# Final new summary\n\nFirst new sentence. Second new sentence."
  stream.event("summary_done", { general_md: finalMd })
  await until(() => stream.state().settledSummaryMd === finalMd)
  assert.equal(stream.visible(), finalMd, "use the finalized summary while the blueprint is still running")
  stream.event("state", { summary: "idle", blueprint: "idle" })
  await until(() => !stream.state().isStreaming)
  assert.equal(stream.visible(), finalMd, "completion must not flash the old cache before React seeds the new one")

  summary.dismissBlueprintStream(mid)
  assert.equal(stream.visible(finalMd), finalMd)
  assert.equal(stream.visible("User edited the new summary."), "User edited the new summary.", "settled stream text must not override later edits")
  stream.close()
})

test("late completion cannot dismiss a newly started regeneration", async () => {
  const mid = "summary-late-dismiss"
  const stream = openStream(mid)
  stream.event("token", "A new generation is still writing.")
  await until(() => !!stream.state().streamingMd)
  summary.dismissBlueprintStream(mid)
  assert.equal(stream.state().isStreaming, true)
  assert.equal(stream.visible(), "A new generation is still writing.")
  stream.event("state", { summary: "idle", blueprint: "idle" })
  await until(() => !stream.state().isStreaming)
  summary.dismissBlueprintStream(mid)
  stream.close()
})

test("generation errors release the live view so the saved document can be read", async () => {
  const stream = openStream("summary-error")
  stream.event("token", "Incomplete replacement")
  await until(() => !!stream.state().streamingMd)
  stream.event("error", { message: "Generation failed" })
  await until(() => !stream.state().isStreaming)
  assert.equal(stream.visible(), "Old saved summary.")
  stream.close()
})

test("a background meeting stream does not replace another meeting's summary", async () => {
  const stream = openStream("summary-background")
  stream.event("token", "Background meeting's new summary")
  await until(() => !!stream.state().streamingMd)
  const other = summary.getBlueprintStreamState("summary-other")
  assert.equal(summary.resolveBlueprintSummary(other, "Other meeting's saved summary"), "Other meeting's saved summary")
  assert.equal(stream.visible(), "Background meeting's new summary")
  stream.event("state", { summary: "idle", blueprint: "idle" })
  await until(() => !stream.state().isStreaming)
  stream.close()
})
