import assert from "node:assert/strict"
import { test } from "node:test"

import { collapsedChunkPreview } from "../frontend/src/lib/utils.ts"

test("strips image fences and keeps surrounding prose", () => {
  const raw = [
    "Intro paragraph.",
    ":::image",
    "image_id: img-1",
    "file_id: f1",
    "ocr_text: chart",
    "description: a chart",
    ":::",
    "Closing sentence.",
  ].join("\n")
  const preview = collapsedChunkPreview(raw)
  assert.match(preview, /Intro paragraph/)
  assert.match(preview, /Closing sentence/)
  assert.doesNotMatch(preview, /:::image/)
  assert.doesNotMatch(preview, /image_id/)
  assert.doesNotMatch(preview, /<img/)
})

test("image-only chunk yields an empty preview", () => {
  const raw = [
    ":::image",
    "image_id: img-9",
    "file_id: f1",
    ":::",
  ].join("\n")
  assert.equal(collapsedChunkPreview(raw), "")
})

test("flattens markdown tables and emphasis to readable text", () => {
  const raw = [
    "## Heading",
    "",
    "A **bold** claim.",
    "",
    "| Col A | Col B |",
    "| --- | --- |",
    "| 1 | 2 |",
  ].join("\n")
  const preview = collapsedChunkPreview(raw)
  assert.match(preview, /Heading/)
  assert.match(preview, /bold claim/)
  assert.doesNotMatch(preview, /\*\*/)
  assert.doesNotMatch(preview, /\|/)
  assert.match(preview, /Col A/)
  assert.match(preview, /1/)
})

test("truncates long prose with an ellipsis near a word boundary", () => {
  const raw = "alpha ".repeat(80)
  const preview = collapsedChunkPreview(raw, 80)
  assert.ok(preview.length <= 82)
  assert.match(preview, /…$/)
  assert.doesNotMatch(preview, /alpha alpha alpha alpha alpha alpha alpha alpha alpha alpha alpha alpha alpha alpha alpha/)
})
