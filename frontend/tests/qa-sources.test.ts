import assert from "node:assert/strict"
import { test } from "node:test"
import type { QaSource } from "../src/api/qa.ts"
import { groupQaSources } from "../src/lib/qa-sources.ts"

function source(values: Partial<QaSource> = {}): QaSource {
  return {
    kind: "project", file_id: "museum-guide", version_id: "edition-one",
    filename: "Museum guide.pdf", chunk_id: "entrance", page_number: 2,
    excerpt: "The entrance opens at nine.", ...values,
  }
}

test("each file version has one ordered group containing all of its distinct passages", () => {
  const entrance = source()
  const annex = source({ file_id: "annex-guide", chunk_id: "annex", excerpt: "The annex is upstairs." })
  const tickets = source({ chunk_id: "tickets", page_number: 4, excerpt: "Tickets are sold at reception." })
  const groups = groupQaSources([entrance, annex, tickets])

  assert.equal(groups.length, 2)
  assert.deepEqual(groups.map(group => group.source.file_id), ["museum-guide", "annex-guide"])
  assert.deepEqual(groups[0].sources, [entrance, tickets])
  assert.deepEqual(groups[1].sources, [annex])
  assert.notEqual(groups[0].key, groups[1].key)
})

test("same filenames and matching file IDs never merge different versions or source kinds", () => {
  const current = source()
  const earlier = source({ version_id: "edition-zero" })
  const material = source({ kind: "material" })
  const otherFile = source({ file_id: "other-guide" })
  const groups = groupQaSources([current, earlier, material, otherFile])

  assert.equal(groups.length, 4)
  assert.equal(new Set(groups.map(group => group.key)).size, 4)
  assert.deepEqual(groups.map(group => group.sources), [[current], [earlier], [material], [otherFile]])
})

test("deduplication removes exact repeated passages and preserves excerpt, page and chunk differences", () => {
  const original = source()
  const changedExcerpt = source({ excerpt: "The entrance opens at ten." })
  const changedPage = source({ page_number: 3 })
  const changedChunk = source({ chunk_id: "another-entrance" })
  const groups = groupQaSources([original, { ...original }, changedExcerpt, changedPage, changedChunk])

  assert.deepEqual(groups[0].sources, [original, changedExcerpt, changedPage, changedChunk])
})

test("sources without a chunk ID deduplicate by exact page and original excerpt", () => {
  const original = source({ chunk_id: "", excerpt: "First line\nSecond line" })
  const otherPage = source({ chunk_id: "", page_number: 5, excerpt: original.excerpt })
  const otherWhitespace = source({ chunk_id: "", excerpt: "First line\n\nSecond line" })
  const groups = groupQaSources([original, { ...original }, otherPage, otherWhitespace])

  assert.deepEqual(groups[0].sources, [original, otherPage, otherWhitespace])
  assert.equal(groups[0].sources[2].excerpt, "First line\n\nSecond line")
})

test("the representative opens the first usable excerpt and can reuse a filename from its group", () => {
  const empty = source({ excerpt: "", chunk_id: "empty", filename: "Gallery guide.pdf" })
  const passage = source({ excerpt: "A guide is available at the desk.", chunk_id: "desk", page_number: 7, filename: "" })
  const later = source({ chunk_id: "later", page_number: 8 })
  const groups = groupQaSources([empty, passage, later])

  assert.equal(groups[0].source.chunk_id, "desk")
  assert.equal(groups[0].source.page_number, 7)
  assert.equal(groups[0].source.excerpt, passage.excerpt)
  assert.equal(groups[0].source.filename, "Gallery guide.pdf")
  assert.deepEqual(groups[0].sources, [empty, passage, later])
  assert.equal(passage.filename, "")
})

test("missing filenames remain empty for the component's localized fallback", () => {
  const only = source({ filename: "", excerpt: "" })
  const groups = groupQaSources([only])

  assert.equal(groups[0].source.filename, "")
  assert.equal(groups[0].source.chunk_id, only.chunk_id)
  assert.deepEqual(groupQaSources([]), [])
})

test("a later duplicate can supply a missing filename without duplicating its passage", () => {
  const first = source({ filename: "" })
  const namedDuplicate = source({ filename: "Visitor guide.pdf" })
  const groups = groupQaSources([first, namedDuplicate])

  assert.deepEqual(groups[0].sources, [first])
  assert.equal(groups[0].source.filename, "Visitor guide.pdf")
  assert.equal(first.filename, "")
})

test("grouping is local to the supplied reply and never mutates the input", () => {
  const first = source()
  const input = Object.freeze([Object.freeze(first)])
  const before = JSON.stringify(input)
  const initial = groupQaSources([...input])
  const otherReply = groupQaSources([source({ excerpt: "A separate reply's passage." })])

  assert.equal(JSON.stringify(input), before)
  assert.notEqual(initial[0].sources, input)
  assert.notEqual(initial[0].sources, otherReply[0].sources)
  assert.equal(initial[0].sources.length, 1)
  assert.equal(initial[0].sources[0].excerpt, first.excerpt)
  assert.equal(otherReply[0].sources[0].excerpt, "A separate reply's passage.")
})
