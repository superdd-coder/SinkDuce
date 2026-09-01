import assert from "node:assert/strict"
import { test } from "node:test"

import { tailBilingualRows } from "../frontend/src/components/meeting/live-summary-sections.ts"

type Seg = { start: number; end: number; text: string; translation?: string | null }

const seg = (start: number, text: string, translation?: string): Seg => ({
  start,
  end: start + 1,
  text,
  translation,
})

test("each segment renders as one source line plus one translation line", () => {
  const rows = tailBilingualRows(
    [seg(0, "hello", "你好")] as never[],
    0,
    "",
    undefined,
  )
  // Block layout: source lines on top, translation lines below.
  assert.deepEqual(
    rows.map((r) => [r.kind, r.text]),
    [
      ["source", "hello"],
      ["translation", "你好"],
    ],
  )
  assert.equal(rows[0].partial, false)
})

test("segment without translation contributes a single line", () => {
  const rows = tailBilingualRows([seg(0, "hello")] as never[], 0, "", undefined)
  assert.deepEqual(rows.map((r) => r.text), ["hello"])
})

test("without any translation the tail shows up to four source lines", () => {
  const rows = tailBilingualRows(
    [
      seg(0, "one"),
      seg(1, "two"),
      seg(2, "three"),
      seg(3, "four"),
      seg(4, "five"),
    ] as never[],
    0,
    "",
    undefined,
  )
  assert.equal(rows.length, 4)
  assert.deepEqual(rows.map((r) => [r.text, r.kind]), [
    ["two", "source"],
    ["three", "source"],
    ["four", "source"],
    ["five", "source"],
  ])
})

test("a single translation anywhere switches back to pair mode", () => {
  const rows = tailBilingualRows(
    [
      seg(0, "one"),
      seg(1, "two"),
      seg(2, "three"),
      seg(3, "four"),
      seg(4, "five", "五"),
    ] as never[],
    0,
    "",
    undefined,
  )
  // Pair mode keeps the newest two units: four (no translation yet) + five.
  assert.deepEqual(rows.map((r) => [r.text, r.kind]), [
    ["four", "source"],
    ["five", "source"],
    ["五", "translation"],
  ])
})

test("caps at two source lines and two translation lines", () => {
  // Two completed pairs + an in-flight partial pair: only the newest two
  // UNITS survive, laid out as a source block on top, translations below.
  const rows = tailBilingualRows(
    [
      seg(0, "one", "一"),
      seg(1, "two", "二"),
    ] as never[],
    0,
    "speaking…",
    "正在说…",
  )
  assert.equal(rows.length, 4)
  assert.deepEqual(
    rows.map((r) => [r.text, r.kind, r.partial]),
    [
      ["two", "source", false],
      ["speaking…", "source", true],
      ["二", "translation", false],
      ["正在说…", "translation", true],
    ],
  )
})

test("late translations can never crowd source lines out", () => {
  // Translations arriving after newer sources must not fill the area:
  // each kept unit contributes at most one line per kind.
  const rows = tailBilingualRows(
    [
      seg(0, "one"),
      seg(1, "two", "二"),
    ] as never[],
    0,
    "",
    undefined,
  )
  assert.deepEqual(
    rows.map((r) => [r.text, r.kind]),
    [
      ["one", "source"],
      ["two", "source"],
      ["二", "translation"],
    ],
  )
})

test("partial without translation still keeps both completed lines", () => {
  const rows = tailBilingualRows(
    [
      seg(0, "one", "一"),
      seg(1, "two", "二"),
    ] as never[],
    0,
    "speaking…",
    undefined,
  )
  assert.deepEqual(
    rows.map((r) => r.text),
    ["two", "speaking…", "二"],
  )
})

test("partial appends its own source line and translation line", () => {
  const rows = tailBilingualRows(
    [seg(0, "done", "完成")] as never[],
    0,
    "speaking…",
    "正在说…",
  )
  // Block layout: both source lines first, then both translation lines.
  assert.deepEqual(
    rows.map((r) => [r.text, r.partial]),
    [
      ["done", false],
      ["speaking…", true],
      ["完成", false],
      ["正在说…", true],
    ],
  )
})

test("partial translation line only when present", () => {
  const rows = tailBilingualRows([] as never[], 0, "speaking…", undefined)
  assert.deepEqual(rows.map((r) => r.text), ["speaking…"])
})

test("no content yields no rows", () => {
  const rows = tailBilingualRows([] as never[], 0, "", undefined)
  assert.deepEqual(rows, [])
})
