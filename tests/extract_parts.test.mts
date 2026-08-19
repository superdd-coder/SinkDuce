import assert from "node:assert/strict"
import { test } from "node:test"

import { splitExtractParts } from "../frontend/src/lib/utils.ts"

test("plain extract stays one text part", () => {
  const parts = splitExtractParts("hello\nworld", "col", "file1")
  assert.deepEqual(parts, [{ kind: "text", text: "hello\nworld" }])
})

test("image fence becomes a same-origin img src using file_id", () => {
  const raw = [
    "before",
    ":::image",
    "image_id: img-9",
    "file_id: file1",
    "ocr_text: chart",
    "description: a chart",
    ":::",
    "after",
  ].join("\n")
  const parts = splitExtractParts(raw, "col-a", "file1")
  assert.equal(parts.length, 3)
  assert.equal(parts[0].kind, "text")
  assert.equal(parts[1].kind, "image")
  if (parts[1].kind === "image") {
    assert.equal(
      parts[1].src,
      "/api/documents/col-a/file1/images/img-9",
    )
    assert.equal(parts[1].imageId, "img-9")
  }
  assert.equal(parts[2].kind, "text")
})

test("empty file_id in fence falls back to the document file id", () => {
  const raw = [
    ":::image",
    "image_id: img-2",
    "file_id:",
    ":::",
  ].join("\n")
  const parts = splitExtractParts(raw, "c", "fid")
  assert.equal(parts[0].kind, "image")
  if (parts[0].kind === "image") {
    assert.equal(parts[0].src, "/api/documents/c/fid/images/img-2")
  }
})


