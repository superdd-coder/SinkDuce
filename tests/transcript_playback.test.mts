import assert from "node:assert/strict"
import { test } from "node:test"

import { findPlayingSegmentIndex } from "../frontend/src/lib/transcript-playback.ts"

const segs = [
  { start: 0, end: 1.5 },
  { start: 1.5, end: 4.0 },
  { start: 4.2, end: 8.0 },
]

test("idle time does not highlight a sentence", () => {
  assert.equal(findPlayingSegmentIndex(segs, null), -1)
  assert.equal(findPlayingSegmentIndex(segs, undefined), -1)
})

test("time zero is the first sentence, not idle", () => {
  assert.equal(findPlayingSegmentIndex(segs, 0), 0)
})

test("containing segment wins while playback is inside it", () => {
  assert.equal(findPlayingSegmentIndex(segs, 0.2), 0)
  assert.equal(findPlayingSegmentIndex(segs, 1.5), 1)
  assert.equal(findPlayingSegmentIndex(segs, 3.9), 1)
  assert.equal(findPlayingSegmentIndex(segs, 5), 2)
})

test("gap after a sentence keeps the last started sentence", () => {
  assert.equal(findPlayingSegmentIndex(segs, 4.1), 1)
})

test("past the last sentence keeps the last started sentence", () => {
  assert.equal(findPlayingSegmentIndex(segs, 99), 2)
})

test("empty transcript has no playing index", () => {
  assert.equal(findPlayingSegmentIndex([], 1), -1)
})
