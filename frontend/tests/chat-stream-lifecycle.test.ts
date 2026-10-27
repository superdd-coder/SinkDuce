import assert from "node:assert/strict"
import { after, before, beforeEach, test } from "node:test"
import { fileURLToPath } from "node:url"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { createServer } from "vite"
import type { Message } from "../src/stores/app-store.ts"
import type { ChatAttachment } from "../src/api/chat-attachments.ts"

type StoreModule = typeof import("../src/stores/app-store.ts")
type ChatHook = ReturnType<typeof import("../src/hooks/use-stream.ts").useStreamChat>
let server: Awaited<ReturnType<typeof createServer>>
let module: StoreModule
let store: StoreModule["useAppStore"]
let initialState: ReturnType<typeof store.getState>
let chat: ChatHook
const originalFetch = globalThis.fetch
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage")
const ids = ["lifecycle-a", "lifecycle-b"]

before(async () => {
  const values = new Map<string, string>()
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    },
  })
  const root = fileURLToPath(new URL("../", import.meta.url))
  server = await createServer({
    root, configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: { alias: { "@": root + "src", "#edition": root + "src/edition" } },
    optimizeDeps: { noDiscovery: true },
  })
  module = await server.ssrLoadModule("/src/stores/app-store.ts")
  store = module.useAppStore
  initialState = store.getState()
  const { useStreamChat } = await server.ssrLoadModule("/src/hooks/use-stream.ts")
  function CaptureHook() {
    chat = useStreamChat()
    return null
  }
  renderToStaticMarkup(createElement(CaptureHook))
})

beforeEach(() => {
  for (const id of ids) {
    module._abortStream(id)
    module._invalidateSessionCache(id)
  }
  store.setState({ ...initialState, sessionId: ids[0], sessionHydratedId: ids[0] }, true)
})

after(async () => {
  for (const id of ids) module._abortStream(id)
  globalThis.fetch = originalFetch
  await server?.close()
  await new Promise((resolve) => setTimeout(resolve, 550))
  if (originalStorage) Object.defineProperty(globalThis, "localStorage", originalStorage)
  else Reflect.deleteProperty(globalThis, "localStorage")
})

const settled: Message[] = [{ id: "saved", role: "assistant", content: "Saved SVG answer", isStreaming: false }]

function fixtureStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const stream = new ReadableStream<Uint8Array>({ start(value) { controller = value } })
  const encoder = new TextEncoder()
  return {
    stream,
    event(event: string, data: Record<string, unknown>) {
      controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`))
    },
    close() { controller.close() },
    fail(error: Error) { controller.error(error) },
  }
}

function installResponses(streams: ReturnType<typeof fixtureStream>[]) {
  let postCount = 0
  const signals: AbortSignal[] = []
  globalThis.fetch = async (input, init) => {
    const url = String(input)
    if (init?.method === "POST" && url.endsWith("/messages")) {
      const fixture = streams[postCount++]
      assert.ok(fixture, "every generation is backed by a local stream fixture")
      if (init?.signal) signals.push(init.signal)
      return new Response(fixture.stream, { status: 200 })
    }
    return new Response(JSON.stringify(url.endsWith("/sessions") ? [] : {}), { status: 200 })
  }
  return Object.assign(() => postCount, { signals })
}

async function until(predicate: () => boolean) {
  const deadline = Date.now() + 1000
  while (!predicate()) {
    assert.ok(Date.now() < deadline, "stream fixture reached the expected lifecycle point")
    await new Promise((resolve) => setTimeout(resolve, 1))
  }
}

test("opening a cached completed conversation does not inherit another conversation's busy state", async () => {
  module._registerStream(ids[0], new AbortController())
  store.setState({ messages: [{ id: "live", role: "assistant", content: "Partial", isStreaming: true }], isStreaming: true })
  module._setCachedMessages(ids[1], settled)
  await store.getState().loadSessionMessages(ids[1])
  assert.equal(store.getState().isStreaming, false)
  await store.getState().loadSessionMessages(ids[0])
  assert.equal(store.getState().isStreaming, true, "returning to the live session restores its actual stream state")
})

test("returning to a cached live session restores busy state after visiting an idle session", async () => {
  module._registerStream(ids[0], new AbortController())
  module._setCachedMessages(ids[0], [{ id: "live", role: "assistant", content: "Partial", isStreaming: true }])
  store.setState({ sessionId: ids[1], sessionHydratedId: ids[1], messages: settled, isStreaming: false })
  await store.getState().loadSessionMessages(ids[0])
  assert.equal(store.getState().isStreaming, true)
})

test("cached orphaned partial messages are settled when no controller owns the session", async () => {
  module._setCachedMessages(ids[1], [{ id: "orphan", role: "assistant", content: "Partial", isStreaming: true }])
  await store.getState().loadSessionMessages(ids[1])
  assert.equal(store.getState().isStreaming, false)
  assert.equal(store.getState().messages.at(-1)?.isStreaming, false)
})

test("Stop settles orphaned per-message state even if the global busy flag is already false", () => {
  store.setState({ messages: [{ id: "orphan", role: "assistant", content: "Partial", isStreaming: true }], isStreaming: false })
  chat.stopGeneration()
  assert.equal(store.getState().messages.at(-1)?.isStreaming, false)
  assert.equal(store.getState().isStreaming, false)
})

test("Stop also recovers an orphaned busy state after the selected session was cleared", () => {
  store.setState({ sessionId: null, messages: [], isStreaming: true })
  chat.stopGeneration()
  assert.equal(store.getState().isStreaming, false)
})

test("cached completed turns settle orphaned thinking and tool indicators", async () => {
  module._setCachedMessages(ids[1], [{
    id: "orphan", role: "assistant", content: "Partial", isStreaming: false,
    timeline: [
      { type: "thinking", content: "Reasoning", isStreaming: true },
      { type: "tool", tool: "search_knowledge_base", toolStatus: "running", isStreaming: true },
    ],
  }])
  await store.getState().loadSessionMessages(ids[1])
  assert.equal(store.getState().isStreaming, false)
  assert.ok(store.getState().messages.at(-1)?.timeline?.every((block) => block.isStreaming === false))
  assert.equal(store.getState().messages.at(-1)?.timeline?.at(-1)?.toolStatus, "done")
})

test("an old generation cannot unregister the replacement generation's controller", () => {
  const old = new AbortController()
  const replacement = new AbortController()
  module._registerStream(ids[0], old)
  module._registerStream(ids[0], replacement)
  assert.equal(old.signal.aborted, true)
  module._unregisterStream(ids[0], old)
  module._abortStream(ids[0])
  assert.equal(replacement.signal.aborted, true, "Stop must still find the new controller")
})

test("Stop immediately releases the composer even when the transport read has not settled", async () => {
  const fixture = fixtureStream()
  const count = installResponses([fixture])
  const sending = chat.sendMessage("Draw")
  await until(() => count() === 1)
  fixture.event("token", { content: "Partial diagram" })
  await new Promise((resolve) => setTimeout(resolve, 1))
  try {
    assert.equal(store.getState().isStreaming, true)
    chat.stopGeneration()
    assert.equal(store.getState().isStreaming, false)
    assert.equal(store.getState().messages.at(-1)?.isStreaming, false)
    assert.equal(store.getState().messages.at(-1)?.content, "Partial diagram")
  } finally {
    fixture.close()
    await sending
  }
})

test("a delayed old stream cannot finish or append into a replacement generation", async () => {
  const old = fixtureStream()
  const replacement = fixtureStream()
  const count = installResponses([old, replacement])
  const sendingOld = chat.sendMessage("First")
  await until(() => count() === 1)
  const sendingReplacement = chat.sendMessage("Second")
  await until(() => count() === 2)
  try {
    old.event("token", { content: "Stale answer" })
    old.event("done", {})
    old.close()
    await sendingOld
    assert.equal(store.getState().isStreaming, true)
    assert.equal(store.getState().messages.at(-1)?.isStreaming, true)
    assert.equal(store.getState().messages.at(-1)?.content, "")
    chat.stopGeneration()
    assert.equal(store.getState().isStreaming, false, "the old finally must leave Stop wired to the replacement")
  } finally {
    replacement.close()
    await sendingReplacement
  }
})

test("a failing message cleanup cannot strand the global busy flag or controller", async () => {
  const fixture = fixtureStream()
  const count = installResponses([fixture])
  store.setState({ finishLastMessage: () => { throw new Error("Message cleanup failed") } })
  const sending = chat.sendMessage("Draw")
  await until(() => count() === 1)
  fixture.event("token", { content: "diagram" })
  fixture.event("done", {})
  fixture.close()
  await assert.rejects(sending, /Message cleanup failed/)
  assert.equal(store.getState().isStreaming, false)
  module._abortStream(ids[0])
  assert.equal(count.signals[0].aborted, false, "the completed stream's controller must already be released")
})

test("EOF without done still retains buffered text and releases the composer", async () => {
  const fixture = fixtureStream()
  const count = installResponses([fixture])
  const sending = chat.sendMessage("Draw")
  await until(() => count() === 1)
  fixture.event("token", { content: "Final diagram" })
  fixture.close()
  await sending
  assert.equal(store.getState().messages.at(-1)?.content, "Final diagram")
  assert.equal(store.getState().isStreaming, false)
})

test("Note changes survive stream completion and inactive-session caching", async () => {
  const fixture = fixtureStream()
  const count = installResponses([fixture])
  const sending = chat.sendMessage("Save a Note")
  await until(() => count() === 1)
  try {
    const first = { id: "note-a", title: "Draft", collection: "col-a" }
    fixture.event("tool_result", { tool: "create_note", status: "done", note_changes: [first] })
    await until(() => (store.getState().messages.at(-1) as any)?.noteChanges?.length === 1)
    store.setState({ sessionId: ids[1], messages: settled, isStreaming: false })
    const renamed = { ...first, title: "Final" }
    const second = { id: "note-b", title: "Second", collection: "col-b" }
    fixture.event("tool_result", { tool: "update_note", status: "done", note_changes: [renamed] })
    fixture.event("done", { note_changes: [renamed, second] })
    await until(() => (module._getCachedMessages(ids[0])?.at(-1) as any)?.noteChanges?.length === 2)
    assert.deepEqual((module._getCachedMessages(ids[0])?.at(-1) as any)?.noteChanges, [renamed, second])
    assert.deepEqual(store.getState().messages, settled)
  } finally {
    fixture.close()
    await sending
  }
})

test("Note changes hydrate from persisted assistant metadata", async () => {
  const changes = [{ id: "note-saved", title: "Saved Note", collection: "col-saved" }]
  store.setState({ sessionId: null, sessionHydratedId: null, messages: [] })
  globalThis.fetch = async (input) => new Response(JSON.stringify(String(input).endsWith(`/sessions/${ids[0]}`) ? {
    id: ids[0], config: {}, messages: [{ id: "answer", role: "assistant", content: "Saved", metadata: { note_changes: changes } }],
  } : []), { status: 200 })
  await store.getState().loadSessionMessages(ids[0])
  assert.deepEqual((store.getState().messages.at(-1) as any)?.noteChanges, changes)
})

test("an assistant reply containing only saved Notes remains visible after reload", async () => {
  store.setState({ sessionId: null, sessionHydratedId: null, messages: [] })
  const changes = [{ id: "note-only", title: "Saved", collection: "col-a" }]
  globalThis.fetch = async (input) => new Response(JSON.stringify(String(input).endsWith(`/sessions/${ids[0]}`) ? {
    id: ids[0], config: {}, messages: [{ id: "note-answer", role: "assistant", content: "", metadata: { tool_calls: 1, note_changes: changes } }],
  } : []), { status: 200 })
  await store.getState().loadSessionMessages(ids[0])
  assert.deepEqual((store.getState().messages.at(-1) as any)?.noteChanges, changes)
})

const attachment: ChatAttachment = {
  id: "attached", session_id: ids[0], filename: "facts.txt", size: 10, status: "ready", parser: "local",
  error: "", warnings: [], characters: 10, lines: 1, image_count: 0, bound: false, created_at: "now",
}

test("sending attachments uses IDs and retains them in the optimistic user message", async () => {
  const bodies: any[] = []
  store.getState().setChatAttachmentDrafts(ids[0], () => [{ key: attachment.id, filename: attachment.filename, status: "ready", attachment }])
  globalThis.fetch = async (input, init) => {
    if (String(input).endsWith("/messages")) bodies.push(JSON.parse(init?.body as string))
    return new Response('event: done\ndata: {}\n\n', { status: 200 })
  }
  await chat.sendMessage("Read this", false, false, [attachment])
  assert.deepEqual(bodies[0].attachment_ids, [attachment.id])
  assert.deepEqual(store.getState().messages[0].attachments, [attachment])
  assert.equal(store.getState().chatAttachmentDrafts[ids[0]].length, 0)
})

test("failed send preserves attachment drafts for retry", async () => {
  store.getState().setChatAttachmentDrafts(ids[0], () => [{ key: attachment.id, filename: attachment.filename, status: "ready", attachment }])
  globalThis.fetch = async () => new Response("temporary failure", { status: 503 })
  await chat.sendMessage("Read this", false, false, [attachment])
  assert.equal(store.getState().chatAttachmentDrafts[ids[0]][0].attachment?.id, attachment.id)
})

test("user attachments hydrate from persisted metadata", async () => {
  store.setState({ sessionId: null, sessionHydratedId: null, messages: [] })
  globalThis.fetch = async (input) => new Response(JSON.stringify(String(input).endsWith(`/sessions/${ids[0]}`) ? {
    id: ids[0], config: {}, messages: [{ id: "user-file", role: "user", content: "Read this", metadata: { attachments: [attachment] } }],
  } : []), { status: 200 })
  await store.getState().loadSessionMessages(ids[0])
  assert.deepEqual(store.getState().messages[0].attachments, [attachment])
})

const evidence = { id: "e_0123456789abcdef0123", kind: "document", title: "Facts.pdf", quote: "An original fact.", source: { file_id: "file-a", collection: "col-a" }, location: { page_number: 3 } }
const reportRef = { id: "report-a", session_id: ids[0], title: "Review", revision: 1, updated_at: "now" }

test("citations and saved report revisions follow their stream when the selected conversation changes", async () => {
  const fixture = fixtureStream()
  const count = installResponses([fixture])
  const sending = chat.sendMessage("Review the evidence")
  await until(() => count() === 1)
  try {
    fixture.event("citations", { citations: [evidence] })
    fixture.event("tool_result", { tool: "create_report", status: "done", report_changes: [reportRef] })
    await until(() => store.getState().messages.at(-1)?.reportChanges?.length === 1)
    assert.deepEqual(store.getState().messages.at(-1)?.citations, [evidence])
    store.setState({ sessionId: ids[1], messages: settled, isStreaming: false })
    const revised = { ...reportRef, revision: 2 }
    fixture.event("tool_result", { tool: "update_report", status: "done", report_changes: [revised] })
    fixture.event("done", { citations: [evidence], report_changes: [revised] })
    await until(() => module._getCachedMessages(ids[0])?.at(-1)?.reportChanges?.[0]?.revision === 2)
    assert.deepEqual(module._getCachedMessages(ids[0])?.at(-1)?.citations, [evidence])
    assert.deepEqual(store.getState().messages, settled)
  } finally { fixture.close(); await sending }
})

test("ordinary reply citations and report-only replies both survive a history reload", async () => {
  store.setState({ sessionId: null, sessionHydratedId: null, messages: [] })
  globalThis.fetch = async (input) => new Response(JSON.stringify(String(input).endsWith(`/sessions/${ids[0]}`) ? {
    id: ids[0], config: {}, messages: [
      { id: "fact-answer", role: "assistant", content: `A fact.[cite:${evidence.id}]`, metadata: { citations: [evidence] } },
      { id: "report-answer", role: "assistant", content: "", metadata: { tool_calls: 1, report_changes: [reportRef] } },
    ],
  } : []), { status: 200 })
  await store.getState().loadSessionMessages(ids[0])
  assert.equal(store.getState().messages.length, 2)
  assert.deepEqual(store.getState().messages[0].citations, [evidence])
  assert.deepEqual(store.getState().messages[1].reportChanges, [reportRef])
})

test('a comment-only message submits the frozen list without user selection controls', async () => {
  const fixture=fixtureStream()
  let payload: any
  const comment={id:'c1',session_id:ids[0],report_id:'r1',title:'Report',revision:1,text:'Rework this section.',state:'pending',anchor:{start_line:1,end_line:1,quote:'Old',prefix:'',suffix:''},created_at:'now',updated_at:'now',author:'local'}
  store.setState({reportComments:{[ids[0]]:[comment]}})
  globalThis.fetch=async (input,init)=>{
    if(init?.method==='POST'&&String(input).endsWith('/messages')){payload=JSON.parse(String(init.body));return new Response(fixture.stream,{status:200})}
    return new Response(JSON.stringify([]),{status:200})
  }
  const sending=chat.sendMessage('')
  await new Promise(setImmediate)
  assert.deepEqual(payload.report_reviews,[{report_id:'r1',revision:1,comment_ids:['c1']}])
  assert.equal(store.getState().messages[0].reportReviews[0].comments[0].id,'c1')
  fixture.event('done',{sources:[]});fixture.close();await sending
})

test('deleting a conversation clears only its report comment cache, focus and drafts',()=>{
  const sid=ids[0],other=ids[1]
  store.setState({reportComments:{[sid]:[{id:'old'}],[other]:[{id:'keep'}]},reportCommentDrafts:{[`${sid}/r/1`]:{text:'old'},[`${other}/r/1`]:{text:'keep'}},reportCommentFocus:{report:{session_id:sid}}})
  store.getState().clearReportReviewSession(sid)
  assert.deepEqual(store.getState().reportComments[sid],undefined)
  assert.equal(store.getState().reportCommentFocus,null)
  assert.equal(store.getState().reportCommentDrafts[`${other}/r/1`].text,'keep')
  assert.equal(store.getState().reportComments[other][0].id,'keep')
})

test('a comment-only history message keeps its navigation chip without exposing the internal default request',async()=>{
  globalThis.fetch=async()=>new Response(JSON.stringify({session:{id:ids[0]},messages:[{id:'u',role:'user',content:'Revise the report using my submitted comments.',metadata:{display_content:'',report_reviews:[{id:'b',comments:[{id:'c',text:'Revise'}]}]}}]}),{status:200})
  store.setState({sessionHydratedId:null})
  await store.getState().loadSessionMessages(ids[0])
  assert.equal(store.getState().messages[0].content,'')
  assert.equal(store.getState().messages[0].reportReviews[0].comments[0].id,'c')
})
