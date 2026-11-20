import assert from "node:assert/strict"
import { test } from "node:test"

import {
  citesByGroupN,
  displayedGroupCites,
  enrichGroupCites,
  GROUP_CITE_HOVER_DELAY_MS,
  GROUP_CITE_RE_SOURCE,
  groupCitesFromToolTrace,
  groupMeetingSources,
  findGroupTranscriptIndex,
  mergeGroupCites,
  nextGroupCiteOccurrence,
  parseCitesFromToolBody,
  parseGroupCiteToken,
  parseGroupCites,
  pickGroupCite,
  resolveGroupCite,
} from "../frontend/src/lib/group-cites.ts"

test("group cite tokens strip wrapping brackets and carry sentence k", () => {
  assert.deepEqual(parseGroupCiteToken("[1]"), { n: 1, refN: undefined })
  assert.deepEqual(parseGroupCiteToken("[1:53]"), { n: 1, refN: 53 })
  assert.deepEqual(parseGroupCiteToken("[1:stt_0053]"), { n: 1, refN: 53 })
  assert.deepEqual(parseGroupCiteToken("([1])"), { n: 1, refN: undefined })
  assert.deepEqual(parseGroupCiteToken("（[2:9]）"), { n: 2, refN: 9 })
  assert.deepEqual(parseGroupCiteToken("[[3]]"), { n: 3, refN: undefined })
  assert.equal(parseGroupCiteToken("[ref:1]"), null)
})

test("resolveGroupCite prefers explicit sentence k over retrieve order", () => {
  const cites = [
    { n: 1, meeting_id: "m1", sentence_id: "stt_0003", ref_n: 3 },
    { n: 1, meeting_id: "m1", sentence_id: "stt_0165", ref_n: 165 },
  ]
  assert.equal(resolveGroupCite(cites, 1, { refN: 165 })?.sentence_id, "stt_0165")
  assert.equal(resolveGroupCite(cites, 1, { occurrence: 0 })?.sentence_id, "stt_0003")
})

test("second chip for the same group n maps to the later sentence, not the first", () => {
  const byN = citesByGroupN([
    { n: 2, meeting_id: "m_b", sentence_id: "stt_0006" },
    { n: 1, meeting_id: "m_a", sentence_id: "stt_0012" },
    { n: 2, meeting_id: "m_b", sentence_id: "stt_0009" },
  ])
  assert.equal(pickGroupCite(byN, 2, 0)?.sentence_id, "stt_0006")
  assert.equal(pickGroupCite(byN, 2, 1)?.sentence_id, "stt_0009")
  assert.equal(pickGroupCite(byN, 1, 0)?.sentence_id, "stt_0012")
})

test("occurrence is stamped while walking chips, not on successive clicks of one chip", () => {
  const occ = new Map<number, number>()
  const first = nextGroupCiteOccurrence(occ, 1)
  const second = nextGroupCiteOccurrence(occ, 1)
  const other = nextGroupCiteOccurrence(occ, 4)
  assert.equal(first, 0)
  assert.equal(second, 1)
  assert.equal(other, 0)
})

test("parse and merge cites keep retrieve order and skip duplicate sentence ids", () => {
  const a = parseGroupCites([
    { n: 1, meeting_id: "m", sentence_id: "stt_0003" },
    { n: "1", meeting_id: "m", sentence_id: "stt_0008" },
  ])
  const merged = mergeGroupCites(a, [
    { n: 1, meeting_id: "m", sentence_id: "stt_0008" },
    { n: 1, meeting_id: "m", sentence_id: "stt_0011" },
  ])
  assert.deepEqual(
    merged.map((c) => c.sentence_id),
    ["stt_0003", "stt_0008", "stt_0011"],
  )
})

test("lookup JSON tool body still yields cites when tool_trace omitted them", () => {
  const body = JSON.stringify({
    cites: [
      { n: 4, meeting_id: "m4", sentence_id: "stt_0053" },
      { n: 1, meeting_id: "m1", sentence_id: "stt_0165" },
    ],
  })
  const cites = parseCitesFromToolBody(body)
  assert.equal(cites[1]?.sentence_id, "stt_0165")
})

test("history tool_trace restores cites onto the assistant message", () => {
  const cites = groupCitesFromToolTrace({
    tool_trace: [
      { tool: "lookup_group_transcript", cites: [{ n: 4, meeting_id: "m4", sentence_id: "stt_0053" }] },
      { tool: "lookup_group_transcript", cites: [{ n: 1, meeting_id: "m1", sentence_id: "stt_0165" }] },
    ],
  })
  assert.equal(cites[0]?.sentence_id, "stt_0053")
  assert.equal(cites[1]?.sentence_id, "stt_0165")
})

test("displayed group cites number chips 1,2,3 in appearance order, not roster n", () => {
  const cites = [
    { n: 4, meeting_id: "m4", sentence_id: "stt_0053", ref_n: 53, title: "Kickoff", date: "2026-08-01" },
    { n: 1, meeting_id: "m1", sentence_id: "stt_0165", ref_n: 165, title: "Review", date: "2026-08-12" },
    { n: 4, meeting_id: "m4", sentence_id: "stt_0009", ref_n: 9, title: "Kickoff", date: "2026-08-01" },
  ]
  const shown = displayedGroupCites("See [4:53] then [1:165] then [4:9].", cites)
  assert.deepEqual(shown.map((s) => s.displayIndex), [1, 2, 3])
  assert.deepEqual(shown.map((s) => s.n), [4, 1, 4])
  assert.deepEqual(
    shown.map((s) => s.sentence_id),
    ["stt_0053", "stt_0165", "stt_0009"],
  )
  assert.equal(shown[0]?.title, "Kickoff")
  assert.equal(shown[1]?.date, "2026-08-12")
})

test("displayed group cites skip tokens without a live sentence", () => {
  const cites = [{ n: 2, meeting_id: "m2", sentence_id: "stt_0006", ref_n: 6 }]
  const shown = displayedGroupCites("Ghost [9] or [9:6] then real [2:6].", cites)
  assert.equal(shown.length, 1)
  assert.equal(shown[0]?.displayIndex, 1)
  assert.equal(shown[0]?.sentence_id, "stt_0006")
})

test("displayed group cites mix [n] occurrence with [ref:k] in document order", () => {
  const cites = [
    { n: 2, meeting_id: "m_b", sentence_id: "stt_0006", ref_n: 6 },
    { n: 2, meeting_id: "m_b", sentence_id: "stt_0009", ref_n: 9 },
  ]
  const shown = displayedGroupCites("First [2] then [ref:9].", cites)
  assert.deepEqual(shown.map((s) => s.sentence_id), ["stt_0006", "stt_0009"])
  assert.deepEqual(shown.map((s) => s.displayIndex), [1, 2])
})

test("parseGroupCites keeps meeting title and date", () => {
  const cites = parseGroupCites([
    { n: 1, meeting_id: "m", sentence_id: "stt_0003", title: "Kickoff", date: "2026-08-01" },
  ])
  assert.equal(cites[0]?.title, "Kickoff")
  assert.equal(cites[0]?.date, "2026-08-01")
})

test("enrichGroupCites fills title and date from the roster lookup", () => {
  const cites = parseGroupCites([{ n: 1, meeting_id: "m1", sentence_id: "stt_0003" }])
  const enriched = enrichGroupCites(cites, [
    { id: "m1", title: "Review", created_at: "2026-08-12T10:00:00Z" },
  ])
  assert.equal(enriched[0]?.title, "Review")
  assert.equal(enriched[0]?.date, "2026-08-12")
})

test("cite tooltip hover delay is a debounce, not instant", () => {
  assert.ok(GROUP_CITE_HOVER_DELAY_MS >= 280)
})

test("range tokens [n:k-l] parse as the range start sentence", () => {
  assert.deepEqual(parseGroupCiteToken("[1:41-45]"), { n: 1, refN: 41 })
  assert.deepEqual(parseGroupCiteToken("[2:41–45]"), { n: 2, refN: 41 })
  assert.deepEqual(parseGroupCiteToken("[1:stt_0041-0045]"), { n: 1, refN: 41 })
})

test("range token becomes one chip anchored at the range start sentence", () => {
  const cites = [{ n: 1, meeting_id: "m1", sentence_id: "stt_0041", ref_n: 41 }]
  const shown = displayedGroupCites("Settling failed [1:41-45].", cites)
  assert.equal(shown.length, 1)
  assert.equal(shown[0]?.sentence_id, "stt_0041")
  assert.equal(shown[0]?.tokenRefN, 41)
})

test("group answer sources collect distinct passages under each meeting", () => {
  const cites = groupCitesFromToolTrace({ tool_trace: [{ cites: [
    { n: 1, meeting_id: "m_a", sentence_id: "stt_0002", ref_n: 2, start_time: 0 },
    { n: 1, meeting_id: "m_a", sentence_id: "stt_0007", ref_n: 7, start_time: 82 },
    { n: 2, meeting_id: "m_b", sentence_id: "stt_0002", ref_n: 2 },
    { n: 2, meeting_id: "m_b", sentence_id: "stt_0042", ref_n: 42 },
  ] }] })
  const roster = [
    { id: "m_a", title: "Rehearsal review", created_at: "2024-03-01" },
    { id: "m_b", title: "Rehearsal review", created_at: "2024-03-02" },
  ]
  const rows = groupMeetingSources("Compare [1:2], [1:2], [1:7], [2:2] and [8:2].", cites, [], roster)
  assert.deepEqual(rows.map((row) => [row.id, row.title, row.date]), [
    ["m_a", "Rehearsal review", "2024-03-01"],
    ["m_b", "Rehearsal review", "2024-03-02"],
  ])
  assert.deepEqual(rows[0]?.excerpts.map((evidence) => evidence.location.start_time), [0, 82])
  assert.deepEqual(rows[1]?.excerpts.map((evidence) => evidence.location.sentence_id), ["stt_0002"])
  assert.equal(groupMeetingSources("See [ref:2].", cites, [], roster).length, 0)
})

test("saved evidence supplies the quote for the same legacy passage and can anchor by time", () => {
  const saved = { id: "e_a", kind: "meeting", title: "Rehearsal review", quote: "Pick up the blue keys.",
    source: { meeting_id: "m_a" }, location: { sentence_id: "m_a_stt_0002", start_time: 14 } }
  const timed = { ...saved, id: "e_b", quote: "Switch on the lights.", location: { start_time: 35 } }
  const unused = { ...saved, id: "e_c", location: { start_time: 49 } }
  const rows = groupMeetingSources("See [1:2], [cite:e_a] and [cite:e_b].",
    [{ n: 1, meeting_id: "m_a", sentence_id: "stt_0002", ref_n: 2 }], [saved, timed, unused])
  assert.deepEqual(rows[0]?.excerpts, [saved, timed])
})

test("group transcript jumps to the saved sentence, with time and unnumbered transcript fallbacks", () => {
  const rows = [
    { sentence_id: "m_a_stt_0006", start: 0, end: 10 },
    { sentence_id: "m_a_stt_0016", start: 10, end: 19 },
    { sentence_id: "s-last", start: 25, end: 31 },
  ]
  assert.equal(findGroupTranscriptIndex(rows, "16", 0), 1)
  assert.equal(findGroupTranscriptIndex(rows, "s-old", 10), 1)
  assert.equal(findGroupTranscriptIndex(rows, undefined, 27), 2)
  assert.equal(findGroupTranscriptIndex(rows, undefined, 22), -1)
  assert.equal(findGroupTranscriptIndex(rows, "stt_0002"), -1)
  assert.equal(findGroupTranscriptIndex(rows.map(row => ({ ...row, sentence_id: "" })), "stt_0002"), 1)
})
