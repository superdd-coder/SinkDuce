import assert from "node:assert/strict"
import { test } from "node:test"

import {
  BRIEF_SECTION_ORDER,
  dropAttendeesWithoutSelection,
  orderBriefSections,
  parseBriefSections,
  parseInlineBold,
} from "../frontend/src/components/meeting/brief-sections.ts"

test("parseInlineBold handles italic runs as well as bold", () => {
  assert.deepEqual(parseInlineBold("*Note: check assumptions*"), [
    { text: "Note: check assumptions", bold: false, italic: true },
  ])
  assert.deepEqual(parseInlineBold("**Zhang** sends *the* file"), [
    { text: "Zhang", bold: true, italic: false },
    { text: " sends ", bold: false, italic: false },
    { text: "the", bold: false, italic: true },
    { text: " file", bold: false, italic: false },
  ])
  assert.deepEqual(parseInlineBold("plain text"), [
    { text: "plain text", bold: false, italic: false },
  ])
})

test("parses fixed H2 tokens into typed sections in order", () => {
  const md = [
    "## Recap",
    '8/12 "Episode 6" — settled plan A.',
    "",
    "## To chase",
    "- API draft — Zhang, 3 weeks",
    "",
    "## Attendees",
    "### Zhang",
    "direct; -> bring a comparison table",
  ].join("\n")
  const sections = orderBriefSections(parseBriefSections(md))
  assert.deepEqual(
    sections.map((s) => s.kind),
    ["recap", "chase", "attendees"],
  )
  assert.ok(sections[0].body.includes("settled plan A"))
  assert.ok(sections[2].body.includes("### Zhang"))
})

test("localized headings still map to known kinds", () => {
  const md = "## 上集回顾\nsettled.\n\n## 参会人\n### Zhang\ndirect"
  const kinds = orderBriefSections(parseBriefSections(md)).map((s) => s.kind)
  assert.deepEqual(kinds, ["recap", "attendees"])
})

test("unknown heading degrades to other with its original title", () => {
  const md = "## Risks\nwatch out"
  const [section] = parseBriefSections(md)
  assert.equal(section.kind, "other")
  assert.equal(section.token, "Risks")
  assert.equal(section.body, "watch out")
})

test("no headings yields a single other section with the full text", () => {
  const md = "just one blob of text\nsecond line"
  const sections = parseBriefSections(md)
  assert.equal(sections.length, 1)
  assert.equal(sections[0].kind, "other")
  assert.equal(sections[0].body, md)
})

test("shuffled input sections come out in canonical order", () => {
  const md = [
    "## Attendees",
    "### Zhang",
    "direct",
    "",
    "## Undecided",
    "API ownership",
    "",
    "## To chase",
    "- draft",
    "",
    "## Recap",
    "settled",
  ].join("\n")
  assert.deepEqual(
    orderBriefSections(parseBriefSections(md)).map((s) => s.kind),
    [...BRIEF_SECTION_ORDER],
  )
})

test("bold-only heading lines are tolerated", () => {
  const md = "**Attendees**\n### Zhang\ndirect"
  const [section] = parseBriefSections(md)
  assert.equal(section.kind, "attendees")
})

test("attendees section drops when nobody is pre-selected", () => {
  const md = [
    "## Recap",
    "settled plan A",
    "",
    "## Attendees",
    "### Zhang",
    "-> bring a comparison table",
  ].join("\n")
  const sections = orderBriefSections(parseBriefSections(md))
  // Legacy briefs may carry an Attendees section; without attendees it hides.
  assert.deepEqual(
    dropAttendeesWithoutSelection(sections, false).map((s) => s.kind),
    ["recap"],
  )
  // With attendees selected nothing is filtered.
  assert.deepEqual(
    dropAttendeesWithoutSelection(sections, true).map((s) => s.kind),
    ["recap", "attendees"],
  )
})

test("parseInlineBold splits bold segments", () => {
  assert.deepEqual(parseInlineBold("plain"), [{ text: "plain", bold: false, italic: false }])
  assert.deepEqual(parseInlineBold("**Zhang** direct"), [
    { text: "Zhang", bold: true, italic: false },
    { text: " direct", bold: false, italic: false },
  ])
  assert.deepEqual(parseInlineBold("a **b** c **d** e"), [
    { text: "a ", bold: false, italic: false },
    { text: "b", bold: true, italic: false },
    { text: " c ", bold: false, italic: false },
    { text: "d", bold: true, italic: false },
    { text: " e", bold: false, italic: false },
  ])
})
