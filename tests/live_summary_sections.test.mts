import assert from "node:assert/strict"
import { test } from "node:test"

import {
  diffEntryStatus,
  groupEntriesByKind,
  relativeAge,
  SECTION_ORDER,
  tailSegments,
} from "../frontend/src/components/meeting/live-summary-sections.ts"

test("section order puts questions first and durable points last", () => {
  assert.deepEqual([...SECTION_ORDER], ["question", "decision", "action", "point"])
})

test("groupEntriesByKind buckets entries by kind", () => {
  const entries = [
    { id: "e1", kind: "point", text: "a", t: 1, status: "active" },
    { id: "e2", kind: "decision", text: "d", t: 2, status: "active" },
    { id: "e3", kind: "question", text: "q", t: 3, status: "resolved" },
    { id: "e4", kind: "action", text: "t", t: 4, status: "active" },
    { id: "e5", kind: "point", text: "b", t: 5, status: "active" },
  ]
  const g = groupEntriesByKind(entries)
  assert.deepEqual(
    g.point.map((e) => e.id),
    ["e5", "e1"],
  )
  assert.deepEqual(
    g.decision.map((e) => e.id),
    ["e2"],
  )
  assert.deepEqual(
    g.question.map((e) => e.id),
    ["e3"],
  )
  assert.deepEqual(
    g.action.map((e) => e.id),
    ["e4"],
  )
})

test("sections list entries newest-first, ties broken by newer id", () => {
  const entries = [
    { id: "e1", kind: "point", text: "old", t: 10, status: "active" },
    { id: "e3", kind: "point", text: "new", t: 50, status: "active" },
    { id: "e2", kind: "point", text: "mid", t: 30, status: "active" },
    { id: "e4", kind: "point", text: "same-round as e3", t: 50, status: "active" },
  ]
  const g = groupEntriesByKind(entries)
  assert.deepEqual(
    g.point.map((e) => e.id),
    ["e4", "e3", "e2", "e1"],
  )
})

test("tailSegments keeps only uncovered tail, capped to the last lines", () => {
  const segs = [
    { start: 0, end: 2, text: "a" },
    { start: 3, end: 5, text: "b" },
    { start: 6, end: 8, text: "c" },
    { start: 9, end: 11, text: "d" },
  ]
  assert.deepEqual(
    tailSegments(segs, 5, 3).map((s) => s.text),
    ["c", "d"],
  )
  assert.deepEqual(
    tailSegments(segs, 0, 2).map((s) => s.text),
    ["c", "d"],
  )
  assert.deepEqual(tailSegments(segs, 11, 3), [])
})

test("relativeAge buckets into coarse ages", () => {
  const now = Date.parse("2026-08-29T12:00:00Z")
  assert.deepEqual(relativeAge("2026-08-29T11:59:58Z", now), { key: "justNow", n: 0 })
  assert.deepEqual(relativeAge("2026-08-29T11:58:55Z", now), { key: "minutesAgo", n: 1 })
  assert.deepEqual(relativeAge("2026-08-29T09:30:00Z", now), { key: "hoursAgo", n: 2 })
  // unparseable / missing → justNow (never render a scary raw date mid-meeting)
  assert.deepEqual(relativeAge("", now), { key: "justNow", n: 0 })
})

test("diffEntryStatus flags added and amended ids", () => {
  const prev = [
    { id: "e1", kind: "point", text: "old", t: 1, status: "active" },
    { id: "e2", kind: "point", text: "keep", t: 2, status: "active" },
  ]
  const next = [
    { id: "e1", kind: "point", text: "new", t: 1, status: "active" },
    { id: "e2", kind: "point", text: "keep", t: 2, status: "active" },
    { id: "e3", kind: "point", text: "fresh", t: 3, status: "active" },
  ]
  const diff = diffEntryStatus(prev, next)
  assert.deepEqual([...diff.added].sort(), ["e3"])
  assert.deepEqual([...diff.amended], ["e1"])
})
