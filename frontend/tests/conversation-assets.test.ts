import assert from "node:assert/strict"
import { test } from "node:test"

const file = (id: string, extra = {}) => ({ id, session_id: "s", filename: `${id}.pdf`, status: "ready", bound: true, size: 20, created_at: "now", ...extra })
const cite = (id: string, extra = {}) => ({ id, kind: "document", title: "Reference.pdf", quote: id, source: { collection: "col", file_id: "file" }, location: {}, ...extra })

test("session sources group repeated chunks by file and version, retaining the latest excerpt", async () => {
  const { conversationSources } = await import("../src/lib/conversation-assets.ts")
  const result = conversationSources("s", [
    { citations: [cite("a"), cite("b", { source: { collection: "col", file_id: "file", version_id: "old" } })] },
    { citations: [cite("c"), cite("uploaded", { kind: "attachment", source: { attachment_id: "upload" } })] },
  ], [file("upload")], [])
  assert.equal(result.uploads.length, 1)
  assert.equal(result.retrieved.length, 2)
  assert.equal(result.retrieved.find((r) => !r.evidence.source.version_id)?.evidence.id, "c")
})

test("uploaded files merge history, server status and pending drafts without leaking another session", async () => {
  const { conversationSources } = await import("../src/lib/conversation-assets.ts")
  const result = conversationSources("s", [{ attachments: [file("a")] }], [file("a", { status: "processing" }), file("foreign", { session_id: "elsewhere" })], [
    { key: "transfer", filename: "Uploading.pdf", status: "uploading" },
    { key: "a", filename: "a.pdf", status: "ready", attachment: file("a", { status: "processing" }) },
  ])
  assert.equal(result.uploads.length, 2)
  assert.equal(result.uploads.find((r) => r.id === "a")?.status, "processing")
  assert.equal(result.uploads.find((r) => r.id === "transfer")?.attachment, undefined)
  assert.ok(!result.uploads.some((r) => r.id === "foreign"))
})

test("legacy retrieved sources retain identity and location alongside modern citations", async () => {
  const { conversationSources } = await import("../src/lib/conversation-assets.ts")
  const result = conversationSources("s", [{ sources: [
    { text: "Legacy excerpt", metadata: { collection: "col", source: "__file__:file", source_label: "Reference.pdf", page_number: 4 } },
  ], citations: [cite("current")] }], [], [])
  assert.equal(result.retrieved.length, 1)
  assert.equal(result.retrieved[0].evidence.id, "current")
  const legacy = conversationSources("s", [{ sources: [{ text: "Legacy", metadata: { collection: "col", file_id: "legacy", filename: "Legacy.pdf", char_offset: 17 } }] }], [], [])
  assert.equal(legacy.retrieved[0].title, "Legacy.pdf")
  assert.equal(legacy.retrieved[0].evidence.location.char_offset, 17)
})

test("live attachment preparation replaces an older processing snapshot", async () => {
  const { conversationSources } = await import("../src/lib/conversation-assets.ts")
  const result = conversationSources("s", [], [file("a", { status: "processing" })], [
    { key: "a", filename: "a.pdf", status: "ready", attachment: file("a") },
  ])
  assert.equal(result.uploads[0].status, "ready")
})

test("source rows keep human titles and conceal opaque storage keys", async () => {
  const { conversationSources } = await import("../src/lib/conversation-assets.ts")
  const result = conversationSources("s", [{ sources: [
    { text: "Excerpt", metadata: { source: "1234567890abcdef1234567890abcdef", collection: "col" } },
    { text: "Web excerpt", metadata: { url: "https://example.com/doc", title: "Public reference" } },
  ] }], [], [])
  assert.equal(result.retrieved[0].title, "")
  assert.equal(result.retrieved[1].title, "Public reference")
})

test("legacy source identities stay unique and stable as additional chunks arrive", async () => {
  const { conversationSources } = await import("../src/lib/conversation-assets.ts")
  const source = (file_id: string, text: string) => ({ text, metadata: { collection: "col", file_id } })
  const original = conversationSources("s", [{ sources: [source("a", "first")] }], [], [])
  const updated = conversationSources("s", [{ sources: [source("a", "first"), source("a", "second"), source("b", "third")] }], [], [])
  assert.equal(updated.retrieved[0].evidence.id, original.retrieved[0].evidence.id)
  assert.notEqual(updated.retrieved[0].evidence.id, updated.retrieved[1].evidence.id)
})
