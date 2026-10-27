import assert from "node:assert/strict"
import { test } from "node:test"

const evidence = (id: string) => ({ id, kind: "document", title: id, quote: "Original", source: { file_id: id }, location: { page_number: 2 } })

test('a retrieved source opens its own chunk and retains the saved version instead of matching by filename', async () => {
  const { sourceEvidence } = await import('../src/lib/source-evidence.ts')
  const original = { ...evidence('exact'), source: { collection: 'col', file_id: 'f', chunk_id: 'two', version_id: 'old' }, location: { page_number: 6 } }
  const records = [{ ...original, id: 'other', source: { ...original.source, chunk_id: 'one' }, location: { page_number: 2 } }, original]
  const selected = sourceEvidence({text:'Original',metadata:{id:'two',collection:'col',file_id:'f',source_label:'Same.pdf'}}, 0, records)
  assert.equal(selected.id, 'exact')
  assert.equal(selected.location.page_number, 6)
  assert.equal(selected.source.version_id, 'old')
})

test('legacy retrieval sources keep their chunk locations and canonical file identity', async () => {
  const { sourceEvidence } = await import('../src/lib/source-evidence.ts')
  const selected = sourceEvidence({text:'Legacy passage',metadata:{source:'file:abc',source_label:'Research.pdf',collection:'col',id:'chunk',page_number:0,char_offset:0}}, 3)
  assert.equal(selected.quote, 'Legacy passage')
  assert.equal(selected.source.source, '__file__:abc')
  assert.equal(selected.source.file_id, 'abc')
  assert.deepEqual(selected.location, {page_number:0,char_offset:0})
  assert.notEqual(sourceEvidence({text:'A',metadata:{collection:'col',file_id:'a'}},3).id,
    sourceEvidence({text:'B',metadata:{collection:'col',file_id:'b'}},3).id)
})

test('meeting source entries reuse the exact saved sentence rather than another passage in the same meeting', async () => {
  const { sourceEvidence } = await import('../src/lib/source-evidence.ts')
  const records = ['stt_0001','stt_0002'].map((sentence,index)=>({id:sentence,kind:'meeting',title:'Discussion',quote:`Passage ${index}`,source:{meeting_id:'m'},location:{sentence_id:sentence,start_time:index*60}}))
  assert.equal(sourceEvidence({text:'',metadata:{meeting_id:'m',sentence_id:'stt_0002'}},0,records).id,'stt_0002')
})

test('identical source text on separate pages uses the retrieved location', async () => {
  const { sourceEvidence } = await import('../src/lib/source-evidence.ts')
  const records = [2,6].map(page=>({...evidence(String(page)),source:{collection:'col',file_id:'f'},location:{page_number:page}}))
  assert.equal(sourceEvidence({text:'Original',metadata:{collection:'col',file_id:'f',page_number:6}},0,records).location.page_number,6)
})

test('file detail navigation resolves collection names and pins the cited version', async () => {
  const { citationFileTarget } = await import('../src/lib/source-evidence.ts')
  assert.deepEqual(citationFileTarget({...evidence('a'),source:{collection:'Research',source:'file:abc',version_id:'v2'}},[{id:'col',name:'Research'}]), {
    collectionId:'col',fileId:'abc',source:'__file__:abc',versionId:'v2',
  })
  for (const source of [{attachment_id:'upload'}, {url:'https://example.com'}, {file_id:'abc'}])
    assert.equal(citationFileTarget({...evidence('a'),source},[]),null)
})

test("cited text ranges use Python character offsets and recover from stale extract positions", async () => {
  const { citationTextRange } = await import("../src/lib/chat-citations.ts")
  const source = "😀 Intro. Original fact. Suffix."
  assert.deepEqual(citationTextRange(source, "Original fact.", 9), { offset: 10, length: 14 })
  assert.deepEqual(citationTextRange(source, "Original fact.", 999), { offset: 10, length: 14 })
  assert.equal(citationTextRange(source, "Missing source fact.", 9), undefined)
})

test("an open report follows its newest revision and stays hidden in another conversation", async () => {
  const { selectedConversationReport } = await import("../src/lib/chat-reports.ts")
  const selected = { id: "report-a", session_id: "a", title: "Review", revision: 1, updated_at: "now" }
  const latest = { ...selected, revision: 3 }
  const messages = [{ reportChanges: [{ ...selected, revision: 2 }, latest] }]
  assert.deepEqual(selectedConversationReport(selected, "a", messages), latest)
  assert.equal(selectedConversationReport(selected, "b", messages), null)
  assert.equal(selected.revision, 1)
})

test("sentence citations are numbered by appearance rather than retrieval order", async () => {
  const { citationMarkdown } = await import("../src/lib/chat-citations.ts")
  const text = citationMarkdown("Second[cite:e_b] first[cite:e_a] second[cite:e_b]", [evidence("e_a"), evidence("e_b")])
  assert.equal(text, "Second[1](#evidence-e_b) first[2](#evidence-e_a) second[1](#evidence-e_b)")
})

test("unknown citations stay visibly unverified and never become source links", async () => {
  const { citationMarkdown } = await import("../src/lib/chat-citations.ts")
  const text = citationMarkdown("Claim[cite:invented]", [])
  assert.ok(!text.includes("#evidence-") && !text.includes("invented"))
  assert.ok(!citationMarkdown("Claim[cite:invalid identity]", []).includes("cite:"))
})

test("code examples and unfinished markers do not become broken citation chips", async () => {
  const { citationMarkdown } = await import("../src/lib/chat-citations.ts")
  assert.equal(citationMarkdown("`[cite:e_a]`\n```text\n[cite:fake]\n```", [evidence("e_a")]), "`[cite:e_a]`\n```text\n[cite:fake]\n```")
  assert.equal(citationMarkdown("A sentence[cite:e_", [], true), "A sentence")
})

test("report changes reject malformed data and merge revisions without duplicates", async () => {
  const { mergeReportChanges } = await import("../src/lib/chat-reports.ts")
  const a = { id: "a", session_id: "s", title: "Report", revision: 1, updated_at: "now" }
  assert.deepEqual(mergeReportChanges([], [null, {}, a]), [a])
  assert.deepEqual(mergeReportChanges([a], [{ ...a, revision: 2 }]), [{ ...a, revision: 2 }])
})

test("artifact events retain reports while updating the current answer's evidence", async () => {
  const { applyChatArtifacts } = await import("../src/lib/chat-reports.ts")
  const a = { id: "a", session_id: "s", title: "Report", revision: 1, updated_at: "now" }
  const message = { content: "Answer", citations: [evidence("e_a")], reportChanges: [a] }
  const updated = applyChatArtifacts(message, { citations: [evidence("e_b")], report_changes: [{ ...a, revision: 2 }] })
  assert.equal(updated.content, "Answer")
  assert.equal(updated.citations[0].id, "e_b")
  assert.equal(updated.reportChanges[0].revision, 2)
  assert.equal(message.citations[0].id, "e_a")
})

test("long-report navigation follows real headings and excludes code examples", async () => {
  const { reportSections } = await import("../src/lib/chat-reports.ts")
  const source = "```md\n## An example\n```\n\n" + Array.from({ length: 4 }, (_, i) => `## Part ${i + 1}\n\n${"Details. ".repeat(200)}\n\n`).join("")
  const sections = reportSections(source)
  assert.equal(sections.length, 4)
  assert.equal(sections[0].title, "Part 1")
  assert.equal(sections[0].line, 5)
})
