import assert from "node:assert/strict"
import { test } from "node:test"

test("attachment-only messages send only after every attachment is ready", async () => {
  const { canSendAttachments } = await import("../src/lib/chat-attachments.ts")
  assert.equal(canSendAttachments("", [{ status: "ready" }]), true)
  assert.equal(canSendAttachments("question", [{ status: "processing" }]), false)
  assert.equal(canSendAttachments("question", [{ status: "failed" }]), false)
  assert.equal(canSendAttachments("", []), false)
  assert.equal(canSendAttachments("question", []), true)
})

test("server refresh preserves uploads and cannot resurrect consumed drafts", async () => {
  const { mergeAttachmentDrafts } = await import("../src/lib/chat-attachments.ts")
  const row = { id: "ready", filename: "facts.txt", status: "ready", bound: false }
  const local = [{ key: "upload", filename: "second.txt", status: "uploading" }]
  const drafts = mergeAttachmentDrafts(local, [row], [])
  assert.equal(drafts.length, 2)
  assert.equal(drafts[0].key, "upload")
  assert.equal(mergeAttachmentDrafts(drafts, [row], ["ready"]).length, 1)
})

test("a poll racing the upload response produces one attachment card and one send ID", async () => {
  const { mergeAttachmentDrafts, settleAttachmentUpload } = await import("../src/lib/chat-attachments.ts")
  const a = { id: "server-file", filename: "facts.txt", status: "processing", bound: false }
  const local = [{ key: "temporary", filename: "facts.txt", status: "uploading" }]
  const raced = mergeAttachmentDrafts(local, [a], [])
  assert.equal(raced.length, 2)
  const settled = settleAttachmentUpload(raced, "temporary", a)
  assert.equal(settled.length, 1)
  assert.equal(settled[0].key, "temporary")
  assert.equal(settled[0].attachment?.id, a.id)
})
