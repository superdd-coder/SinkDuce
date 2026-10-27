import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"
import * as React from "react"
import ts from "typescript"

function elements(node: any): any[] {
  if (Array.isArray(node)) return node.flatMap(elements)
  return React.isValidElement(node) ? [node, ...elements((node.props as any).children)] : []
}

async function harness(file: string, name: string, api: Record<string, any>) {
  const key = `__reports_${Math.random()}`
  const global = globalThis as any
  const cells: any[] = [], effects: (() => void)[] = []
  let cursor = 0
  global[key] = { ...api, Children:React.Children,
    createPortal:(children:any,container:any)=>React.createElement('report-toolbar-portal',{container},children),
    useState(initial: any) { const i = cursor++; if (!(i in cells)) cells[i] = typeof initial === "function" ? initial() : initial; return [cells[i], (v: any) => { cells[i] = typeof v === "function" ? v(cells[i]) : v }] },
    useRef(initial: any) { const i = cursor++; if (!(i in cells)) cells[i] = { current: initial }; return cells[i] },
    useEffect(effect: () => any, deps: any[]) { const i = cursor++; if (cells[i] && deps?.every((v, j) => Object.is(v, cells[i].deps[j]))) return; effects.push(() => { cells[i]?.cleanup?.(); cells[i] = { deps, cleanup: effect() } }) },
    useLayoutEffect(effect:()=>any,deps:any[]) { return this.useEffect(effect,deps) },
    useMemo: (fn: () => any) => fn(), useCallback: (fn: any) => fn, useId: () => key,
    lazy: () => function LazyComponent(){}, Suspense: () => null,
  }
  const data = (s: string) => "data:text/javascript," + encodeURIComponent(s)
  const stub = (names: string[]) => data(names.map((n) => `export function ${n}(){}`).join("\n"))
  const imports: Record<string, string> = {
    react: data(["useState", "useRef", "useEffect", "useLayoutEffect", "useMemo", "useCallback", "useId", "lazy", "Suspense"].map((n) => `export const ${n}=(...a)=>globalThis[${JSON.stringify(key)}].${n}(...a);`).join("\n")+`\nexport const Children=globalThis[${JSON.stringify(key)}].Children;`),
    "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
    "react-dom":data(`export const createPortal=(...args)=>globalThis[${JSON.stringify(key)}].createPortal(...args)`),
    "lucide-react": import.meta.resolve("lucide-react"),
    "@/api/client": data([...new Set([...Object.keys(api), "getChatReports", "getChatReportVersions", "getChatAttachments", "getChatAttachmentPreview", "getChatAttachmentSource", "getChatAttachmentText", "getFileDetail", "getFilePreviewUrl", "getExtractedText", "getMeetingTranscript", "addReportComment", "editReportComment", "deleteReportComment", "getChatReport", "createChatReportExport", "downloadChatReport", "restoreChatReport", "decideReportSuggestion", "getReportFollowUpContext", "followUpReportSuggestion"])].map((n) => `export const ${n}=(...a)=>globalThis[${JSON.stringify(key)}].${n}(...a);`).join("\n")),
    "@/i18n/use-t": data("const t=key=>key; export const useT=()=>t"),
    "@/components/ui/dialog": stub(["Dialog", "DialogContent", "DialogTitle", "DialogDescription", "DialogClose"]),
    "@/components/shared/chunk-md": stub(["ChunkMd"]),
    "@/components/ui/button": stub(["Button"]),
    "@/components/ui/dropdown-select": stub(["DropdownSelect"]),
    "@/components/ui/popover": data("export const Popover = { Root(){},Trigger(){},Portal(){},Positioner(){},Popup(){},Title(){},Close(){} }"),
    "./report-resize-handle": stub(["ReportResizeHandle"]),
    "@/stores/app-store": data(`const fallback={reportChatRatio:0.46,chatAttachmentDrafts:{},reportComments:{},reportCommentDrafts:{},reportCommentFocus:null};export const useAppStore=select=>select({...fallback,...globalThis[${JSON.stringify(key)}].state});useAppStore.getState=()=>globalThis[${JSON.stringify(key)}].state||fallback;`),
    "@/lib/report-comment-layout": new URL("../src/lib/report-comment-layout.ts",import.meta.url).href,
    "@/lib/report-review": new URL("../src/lib/report-review.ts",import.meta.url).href,
    "@/lib/report-review-dom": new URL("../src/lib/report-review-dom.ts",import.meta.url).href,
    "@/hooks/use-report-review": data(`export const refreshReportComments=(...args)=>globalThis[${JSON.stringify(key)}].refreshReportComments?.(...args)`),
    "./answer-markdown": stub(["AnswerMarkdown"]),
    "./report-review-panel": stub(["ReportReviewPanel"]),
    "./report-review-card": stub(["CommentCard"]),
    "./report-discussion-changes": stub(["ReportDiscussionChanges"]),
    "./report-tracked-body": stub(["ReportTrackedBody"]),
    "@/lib/report-revision-tracking": new URL("../src/lib/report-revision-tracking.ts",import.meta.url).href,
    "@/lib/report-discussion-history": new URL("../src/lib/report-discussion-history.ts",import.meta.url).href,
    "./report-comment-rail": stub(["ReportCommentRail"]),
    "./citation-scope": stub(["CitationScope", "CitationLink"]),
    "./citation-source-preview": stub(["CitationSourcePreview"]),
    "@/lib/source-evidence": new URL("../src/lib/source-evidence.ts", import.meta.url).href,
    "./report-viewer": stub(["ReportViewer"]),
    "./document-mark": stub(["DocumentMark"]),
    "@/lib/chat-document-display": data('export const documentDate=()=>null; export const documentLabel=()=>"PDF";'),
    "./report-history-menu": stub(["ReportHistoryMenu"]),
    "./chat-attachment-preview": stub(["ChatAttachmentPreview"]),
    "./citation-context": data(`export function CitationContext(){};export const useCitations=()=>({records:globalThis[${JSON.stringify(key)}].citations||[],open:(...args)=>globalThis[${JSON.stringify(key)}].openCitation?.(...args)})`),
    "@/components/file-mgmt/raw-file-viewer": stub(["RawFileViewer"]),
    "@/components/file-mgmt/file-detail/parse-text-viewer": stub(["ParseTextViewer"]),
    "@/lib/download": new URL("../src/lib/download.ts", import.meta.url).href,
    "@/lib/chat-reports": new URL("../src/lib/chat-reports.ts", import.meta.url).href,
    "@/lib/chat-citations": new URL("../src/lib/chat-citations.ts", import.meta.url).href,
    "@/lib/conversation-assets": new URL("../src/lib/conversation-assets.ts", import.meta.url).href,
    "@/lib/chat-attachments": data("export const chatAttachmentLabel=()=> 'PDF'; export const isChatAttachmentImage=()=>false"),
  }
  const source = await readFile(new URL(`../src/components/chat/${file}.tsx`, import.meta.url), "utf8")
  const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
    .replace(/import ["'][^"']+\.css["'];?/g, "")
    .replace(/from ["']([^"']+)["']/g, (statement, path) => imports[path] ? `from ${JSON.stringify(imports[path])}` : statement)
  const module = await import(data(compiled))
  return { render(props: any) { cursor = 0; return elements(module[name](props)) },
    async flush() { effects.splice(0).forEach((fn) => fn()); await new Promise(setImmediate) },
    dispose() { cells.forEach((cell) => cell?.cleanup?.()); delete global[key] } }
}

const report = { id: "report-a", session_id: "session-a", title: "Review", revision: 1, updated_at: "now" }

test('the answer source list uses the evidence dialog rather than a preview sidebar or direct web navigation', async () => {
  let opened:any
  const h = await harness('sources-card','SourcesCard',{state:{collections:[]},openCitation:(e:any)=>{opened=e}})
  try {
    const props={sources:[{text:'Selected passage',score:1,metadata:{id:'chunk',collection:'col',file_id:'f',source_label:'Research.pdf',page_number:6}}]}
    const row=h.render(props).find(e=>e.props.className?.includes('pm-chat-source-row'))
    row.props.onClick()
    assert.equal(opened?.quote,'Selected passage')
    assert.equal(opened?.location.page_number,6)
    assert.equal(opened?.source.file_id,'f')
  } finally {h.dispose()}
})

test('evidence dialog opens the existing file details for the cited collection and historical version', async () => {
  const oldDocument=Object.getOwnPropertyDescriptor(globalThis,'document')
  const oldButton=Object.getOwnPropertyDescriptor(globalThis,'HTMLButtonElement')
  Object.defineProperty(globalThis,'document',{configurable:true,value:{activeElement:null}})
  Object.defineProperty(globalThis,'HTMLButtonElement',{configurable:true,value:class{}})
  const h=await harness('citation-scope','CitationScope',{state:{collections:[{id:'col',name:'Research'}]}})
  try {
    const props={sessionId:'s',citations:[],children:null}
    h.render(props).find(e=>e.props.value?.open).props.value.open({id:'e',kind:'document',title:'Research.pdf',quote:'Passage',source:{collection:'Research',file_id:'file',version_id:'v2'},location:{page_number:6}})
    let tree=h.render(props)
    tree.find(e=>e.props.className==='pm-citation-detail-button').props.onClick()
    tree=h.render(props)
    const details=tree.find(e=>e.props.collectionId==='col'&&e.props.open)
    assert.equal(details?.props.fileId,'file')
    assert.equal(details?.props.versionId,'v2')
    details.props.onOpenChange(false)
    assert.ok(h.render(props).some(e=>e.props.open===true),'Closing file details keeps the excerpt available')
  } finally {
    h.dispose()
    if(oldDocument)Object.defineProperty(globalThis,'document',oldDocument);else Reflect.deleteProperty(globalThis,'document')
    if(oldButton)Object.defineProperty(globalThis,'HTMLButtonElement',oldButton);else Reflect.deleteProperty(globalThis,'HTMLButtonElement')
  }
})

test("the conversation card shows separate Output and Sources counts and opens outputs without exporting", async () => {
  let opened: unknown
  const h = await harness("conversation-assets", "ConversationAssets", {
    getChatReports: async () => [report],
    getChatAttachments: async () => [{ id: "file", session_id: "session-a", filename: "Brief.pdf", status: "ready" }],
  })
  try {
    const props = { sessionId: "session-a", refreshKey: "1", messages: [], onOpenReport(value: unknown) { opened = value } }
    h.render(props); await h.flush()
    const tree = h.render(props)
    assert.equal(tree.find((e) => e.props["data-output-count"] !== undefined)?.props["data-output-count"], 1)
    assert.equal(tree.find((e) => e.props["data-source-count"] !== undefined)?.props["data-source-count"], 1)
    const panel = tree.find((e) => e.props.onOutput && e.props.uploads)
    assert.equal(panel?.props.retrievedOpen, false)
    panel.props.onOutput(report)
    assert.equal(opened, report)
    assert.ok(!tree.some((e) => e.props.children === "chat.reports.report"))
  } finally { h.dispose() }
})

test("the asset surface grows from the trigger's own bounds and keeps the trigger hidden through its exit", async () => {
  const h = await harness("conversation-assets", "ConversationAssets", { getChatReports: async () => [], getChatAttachments: async () => [] })
  try {
    const props = { sessionId: "s", refreshKey: "1", messages: [], onOpenReport() {} }
    let tree = h.render(props)
    tree.find((e) => e.props.className === "pm-assets-trigger").props.ref.current = { getBoundingClientRect: () => ({ width: 178, height: 28 }) }
    tree.find((e) => typeof e.props.open === "boolean" && e.props.onOpenChange).props.onOpenChange(true, { reason: "trigger-press" })
    tree = h.render(props)
    assert.equal(tree.find((e) => e.props.className === "pm-assets-positioner")?.props.sideOffset, -28)
    assert.equal(tree.find((e) => e.props.className === "pm-assets-card")?.props.style["--pm-assets-origin-w"], "178px")
    const root = tree.find((e) => typeof e.props.open === "boolean" && e.props.onOpenChange)
    root.props.onOpenChange(false, { reason: "outside-press" })
    tree = h.render(props)
    assert.equal(tree.find((e) => e.props.className === "pm-assets-trigger")?.props["data-surface-active"], true)
    tree.find((e) => typeof e.props.open === "boolean" && e.props.onOpenChangeComplete).props.onOpenChangeComplete(false)
    assert.equal(h.render(props).find((e) => e.props.className === "pm-assets-trigger")?.props["data-surface-active"], undefined)
  } finally { h.dispose() }
})

test("collapsing the asset surface restores keyboard focus without stealing an outside click", async () => {
  const original = globalThis.requestAnimationFrame
  globalThis.requestAnimationFrame = ((fn) => { fn(0); return 1 }) as typeof requestAnimationFrame
  let focused = 0
  const h = await harness("conversation-assets", "ConversationAssets", { getChatReports: async () => [], getChatAttachments: async () => [] })
  try {
    const props = { sessionId: "s", refreshKey: "1", messages: [], onOpenReport() {} }
    const tree = h.render(props)
    tree.find((e) => e.props.className === "pm-assets-trigger").props.ref.current = { getBoundingClientRect: () => ({ width: 178, height: 28 }), focus() { focused++ } }
    const root = tree.find((e) => typeof e.props.open === "boolean" && e.props.onOpenChangeComplete)
    root.props.onOpenChange(true, { reason: "trigger-press" })
    root.props.onOpenChange(false, { reason: "escape-key" })
    root.props.onOpenChangeComplete(false)
    assert.equal(focused, 1)
    root.props.onOpenChange(true, { reason: "trigger-press" })
    root.props.onOpenChange(false, { reason: "outside-press" })
    root.props.onOpenChangeComplete(false)
    assert.equal(focused, 1)
  } finally { h.dispose(); globalThis.requestAnimationFrame = original }
})

test("asset requests finishing after a session switch cannot replace the new session's files", async () => {
  let finishOld: (rows: unknown[]) => void = () => {}
  const old = new Promise((resolve) => { finishOld = resolve })
  const h = await harness("conversation-assets", "ConversationAssets", {
    getChatReports: async (sid) => sid === "old" ? old : [],
    getChatAttachments: async (sid) => [{ id: sid, session_id: sid, filename: `${sid}.pdf`, status: "ready" }],
  })
  try {
    const props = { sessionId: "old", refreshKey: "1", messages: [], onOpenReport() {} }
    h.render(props); await h.flush()
    const next = { ...props, sessionId: "new" }
    h.render(next); await h.flush()
    finishOld([{ ...report, session_id: "old" }]); await h.flush()
    const panel = h.render(next).find((e) => e.props.onOutput && e.props.uploads)
    assert.deepEqual(panel?.props.uploads.map((r) => r.filename), ["new.pdf"])
    assert.equal(panel?.props.outputs.length, 0)
  } finally { h.dispose() }
})

test("the source section starts collapsed and opening a source preserves its version and location", async () => {
  const events: string[] = []
  let opened: unknown
  const h = await harness("conversation-assets", "ConversationAssetsPanel", { openCitation: (value) => { opened = value; events.push("open") } })
  try {
    const evidence = { id: "e", title: "Reference.pdf", quote: "Fact", source: { file_id: "file", version_id: "original" }, location: { page_number: 7 } }
    const props = { outputs: [], uploads: [], retrieved: [{ id: "file", title: "Reference.pdf", evidence }], retrievedOpen: false, onToggleRetrieved() {}, onOutput() {}, onUpload() {}, onSource() { events.push("close") } }
    const tree = h.render(props)
    assert.equal(tree.find((e) => e.props.className === "pm-assets-retrieved-toggle")?.props["aria-expanded"], false)
    assert.equal(tree.find((e) => e.props.className === "pm-assets-retrieved-fold")?.props.inert, true)
    const expanded = h.render({ ...props, retrievedOpen: true })
    assert.equal(expanded.find((e) => e.props.className === "pm-assets-retrieved-fold")?.props.inert, false)
    expanded.find((e) => e.type === "button" && e.props.className === "pm-assets-row").props.onClick()
    assert.equal(opened, evidence)
    assert.deepEqual(events, ["close", "open"])
  } finally { h.dispose() }
})

test("the asset card caps itself at two thirds of its own stage and tracks stage resizing", async () => {
  const original = globalThis.ResizeObserver
  let callback: () => void = () => {}, disconnected = false, height = 600
  globalThis.ResizeObserver = class { constructor(fn) { callback = fn } observe() {} disconnect() { disconnected = true } } as any
  const h = await harness("conversation-assets", "ConversationAssets", { getChatReports: async () => [], getChatAttachments: async () => [] })
  try {
    const props = { sessionId: "s", refreshKey: "1", messages: [], onOpenReport() {} }
    const tree = h.render(props)
    tree.find((e) => e.props.className === "pm-assets-trigger").props.ref.current = { closest: () => ({ getBoundingClientRect: () => ({ height }) }) }
    await h.flush(); callback()
    assert.equal(h.render(props).find((e) => e.props.className === "pm-assets-card")?.props.style.maxHeight, 400)
    height = 900; callback()
    assert.equal(h.render(props).find((e) => e.props.className === "pm-assets-card")?.props.style.maxHeight, 600)
  } finally { h.dispose(); globalThis.ResizeObserver = original }
  assert.equal(disconnected, true)
})

for (const extension of ["pdf", "docx"]) test(`${extension} citations open the original even without a page and toggle directly to the parsed range`, async () => {
  const h = await harness("citation-source-preview", "CitationSourcePreview", {
    getFileDetail: async () => ({ filename: `Reference.${extension}` }),
    getFilePreviewUrl: () => `/original.${extension}`,
    getExtractedText: async () => ({ text: "Prefix. Original fact. Suffix." }),
  })
  try {
    const props = { sessionId: "s", evidence: { id: "e", title: `Reference.${extension}`, quote: "Original fact.", kind: "document", source: { collection: "col", file_id: "file" }, location: { char_offset: 8 } } }
    h.render(props); await h.flush()
    let tree = h.render(props)
    assert.ok(tree.some((e) => e.props.url === `/original.${extension}`), "A text locator must not override original preview")
    tree.find((e) => e.type === "button" && e.props.className === "pm-citation-view-toggle").props.onClick()
    tree = h.render(props)
    assert.ok(tree.some((e) => e.props.highlightOffset === 8 && e.props.text === "Prefix. Original fact. Suffix."))
    tree.find((e) => e.type === "button" && e.props.className === "pm-citation-view-toggle").props.onClick()
    assert.ok(h.render(props).some((e) => e.props.url === `/original.${extension}`))
  } finally { h.dispose() }
})

test("meeting evidence locates the timed segment when transcript formatting differs from the saved quote", async () => {
  const h = await harness("citation-source-preview", "CitationSourcePreview", {
    getMeetingTranscript: async () => ({ segments: [
      { start: 0, end: 5, text: "Introduction" },
      { start: 65, end: 70, text: "The original discussion." },
    ] }),
  })
  try {
    const props = { sessionId: "s", evidence: { id: "e", title: "Meeting", quote: "Speaker: The original discussion.", kind: "meeting", source: { meeting_id: "meeting" }, location: { start_time: 65 } } }
    h.render(props); await h.flush()
    const body = h.render(props).find((e) => typeof e.props.text === "string")
    assert.equal(body?.props.highlightOffset, "0:00 Introduction\n\n".length)
    assert.equal(body?.props.highlightLength, "1:05 The original discussion.".length)
  } finally { h.dispose() }
})

test("web citations retain a safe source link without pretending to be a file preview", async () => {
  const h = await harness("citation-source-preview", "CitationSourcePreview", {})
  try {
    const props = { sessionId: "s", evidence: { id: "e", title: "Public source", quote: "A fact", kind: "web", source: { url: "https://example.com/source" }, location: {} } }
    h.render(props); await h.flush()
    const link = h.render(props).find((e) => e.type === "a")
    assert.equal(link?.props.href, "https://example.com/source")
    assert.equal(link?.props.rel, "noopener noreferrer")
  } finally { h.dispose() }
})

test("source previews reject executable citation URLs", async () => {
  const h = await harness("citation-source-preview", "CitationSourcePreview", {})
  try {
    const props = { sessionId: "s", evidence: { id: "e", title: "Source", quote: "A fact", kind: "web", source: { url: "javascript:alert(1)" }, location: {} } }
    h.render(props); await h.flush()
    const tree = h.render(props)
    assert.ok(tree.some((e) => e.props.role === "alert"))
    assert.ok(!tree.some((e) => e.type === "a" || e.props.url))
  } finally { h.dispose() }
})

test("report actions share one toolbar with history instead of numbered versions", async () => {
  const h = await harness("report-viewer", "ReportViewer", {
    getChatReport: async () => ({ ...report, markdown: "Body", citations: [] }),
    getChatReportVersions: async () => [report],
    getChatReportExport: async () => ({ status: "idle" }),
    createChatReportExport: async () => { throw new Error("Preview must not export") },
    downloadChatReport: async () => { throw new Error("Preview must not download") },
  })
  try {
    const props = { report, onClose() {} }
    h.render(props); await h.flush()
    const tree = h.render(props)
    assert.ok(tree.some((e) => Array.isArray(e.props.entries) && e.props.onSelect), 'Use one history menu')
    assert.ok(!tree.some((e) => e.props.className === "pm-report-version"))
    const header = tree.find((e) => e.type === "header" && e.props.className === "pm-report-head")
    assert.ok(elements(header).some((e) => e.props.className === "pm-report-export"))
    assert.ok(!tree.some((e) => e.type === "footer" || e.props.className === "pm-report-controls"))
    assert.ok(!tree.some((e) => e.type === "p" && e.props.children === "chat.reports.inConversation"))
  } finally { h.dispose() }
})

test("report card opens an internal preview and never downloads automatically", async () => {
  let downloads = 0
  const h = await harness("report-cards", "ReportCards", { downloadChatReport: async () => { downloads++; return new Blob() } })
  try {
    const props = { reports: [report] }
    const button = h.render(props).find((e) => e.type === "button")
    button.props.onClick({ currentTarget: { focus() {} } })
    assert.ok(h.render(props).some((e) => e.props.report?.id === report.id && e.props.onClose))
    assert.equal(downloads, 0)
  } finally { h.dispose() }
})

test("report preview loads its chosen version without starting exports or downloads", async () => {
  let exports = 0, downloads = 0
  const versions: (number | undefined)[] = []
  const h = await harness("report-viewer", "ReportViewer", {
    getChatReport: async (_sid, _rid, revision) => { versions.push(revision); return { ...report, revision: revision ?? 2, markdown: "## Body", citations: [] } },
    getChatReportVersions: async () => [report, { ...report, revision: 2 }],
    getChatReportExport: async (_s, _r, revision, format) => ({ format, revision, status: "idle", error: "" }),
    createChatReportExport: async () => { exports++; return { status: "ready" } },
    downloadChatReport: async () => { downloads++; return new Blob() },
  })
  try {
    const props = { report, onClose() {}, triggerRef: { current: null } }
    h.render(props); await h.flush()
    const tree = h.render(props)
    assert.ok(tree.some((e) => e.props.source === "## Body"))
    assert.equal(exports, 0); assert.equal(downloads, 0)
    const select = tree.find((e) => e.props.entries && e.props.onSelect)
    select.props.onSelect(1)
    h.render(props); await h.flush(); h.render(props)
    assert.deepEqual(versions, [undefined, undefined, 1], 'Refresh the current draft independently of the chosen historical snapshot')
    assert.equal(exports, 0); assert.equal(downloads, 0)
  } finally { h.dispose() }
})

test("an explicit export downloads the selected historical version once", async () => {
  const calls: any[] = [], anchors: any[] = []
  const original = Object.getOwnPropertyDescriptor(globalThis, "document")
  Object.defineProperty(globalThis, "document", { configurable: true, value: {
    body: { appendChild() {} }, createElement() { const anchor = { click() {}, remove() {} }; anchors.push(anchor); return anchor },
  } })
  const h = await harness("report-viewer", "ReportViewer", {
    getChatReport: async (_s, _r, revision=3) => ({ ...report, revision, markdown: "Body", citations: [] }),
    getChatReportVersions: async () => [report, { ...report, revision: 2 }, { ...report, revision: 3 }],
    getChatReportExport: async () => ({ status: "idle" }),
    createChatReportExport: async (...args) => { calls.push(["prepare", ...args]); return { status: "ready" } },
    downloadChatReport: async (...args) => { calls.push(["download", ...args]); return new Blob(["Word"]) },
  })
  try {
    const props = { report, onClose() {}, triggerRef: { current: null } }
    h.render(props); await h.flush()
    h.render(props).find((e) => e.props.entries && e.props.onSelect).props.onSelect(2)
    h.render(props); await h.flush()
    h.render(props).find((e) => e.props.className === "pm-report-export").props.onChange("docx")
    await h.flush()
    assert.deepEqual(calls, [["prepare", "session-a", "report-a", 2, "docx"], ["download", "session-a", "report-a", 2, "docx"]])
    assert.equal(anchors.length, 1)
    assert.equal(anchors[0].download, "Review.docx")
  } finally { h.dispose(); if (original) Object.defineProperty(globalThis, "document", original); else Reflect.deleteProperty(globalThis, "document") }
})

test('history restoration rewinds the menu to the selected node and keeps the current draft editable', async()=>{
  const calls:any[]=[]
  let head=3, refreshed=0
  const h=await harness('report-viewer','ReportViewer',{
    getChatReportVersions:async()=>head===4?[report]:Array.from({length:head},(_,i)=>({...report,revision:head-i})),
    getChatReport:async(_s,_r,revision=head)=>({...report,revision,...(revision===4?{history_revision:1}:{}),markdown:`Body ${revision===4?1:revision}`,citations:[]}),
    getChatReportExport:async()=>({status:'idle'}),
    restoreChatReport:async(...args)=>{calls.push(args);head=4;return {...report,revision:4,history_revision:1,markdown:'Body 1',citations:[]}},
    state:{bumpReportHistory:()=>{refreshed++}},
  })
  try{
    const props={report,onClose(){}}
    h.render(props);await h.flush()
    let menu=h.render(props).find(e=>e.props.entries&&e.props.onSelect)
    assert.equal(menu.props.currentRevision,3)
    assert.equal(menu.props.selectedRevision,3,'Opening an old message card still opens the current file')
    menu.props.onSelect(1);h.render(props);await h.flush()
    menu=h.render(props).find(e=>e.props.entries&&e.props.onSelect)
    assert.equal(menu.props.selectedRevision,1)
    await menu.props.onRestore()
    menu=h.render(props).find(e=>e.props.entries&&e.props.onSelect)
    assert.deepEqual(menu.props.entries.map((e:any)=>e.revision),[1],'Drop future rows immediately')
    await h.flush();h.render(props);await h.flush()
    menu=h.render(props).find(e=>e.props.entries&&e.props.onSelect)
    assert.equal(calls.length,1)
    assert.deepEqual(calls[0].slice(0,4),['session-a','report-a',1,3])
    assert.equal(menu.props.currentRevision,1)
    assert.equal(menu.props.selectedRevision,1)
    assert.equal(menu.props.entries.length,1)
    assert.equal(h.render(props).find(e=>e.props.onReportChange).props.report.markdown,'Body 1')
    assert.equal(h.render(props).find(e=>e.props.onReportChange).props.latest,true)
    assert.equal(refreshed,1)
  }finally{h.dispose()}
})

test('a restored report reopened from an older message marks its restored node as current',async()=>{
  const h=await harness('report-viewer','ReportViewer',{
    getChatReportVersions:async()=>[{...report,revision:2},report],
    getChatReport:async(_s,_r,revision=5)=>({...report,revision,history_revision:2,markdown:'Restored draft',citations:[]}),
    getChatReportExport:async()=>({status:'idle'}),
  })
  try{
    const props={report:{...report,revision:4},onClose(){}}
    h.render(props);await h.flush()
    let tree=h.render(props),menu=tree.find(e=>e.props.entries&&e.props.onSelect)
    assert.equal(menu.props.currentRevision,2)
    assert.equal(menu.props.selectedRevision,2)
    assert.equal(tree.find(e=>e.props.onReportChange).props.report.revision,5,'Write identity stays independent of the visible history cursor')
    assert.equal(tree.find(e=>e.props.onReportChange).props.latest,true)
    menu.props.onSelect(1);h.render(props);await h.flush()
    menu=h.render(props).find(e=>e.props.entries&&e.props.onSelect)
    menu.props.onSelect(2);h.render(props);await h.flush()
    tree=h.render(props)
    assert.equal(tree.find(e=>e.props.onReportChange).props.latest,true)
    assert.equal(tree.find(e=>e.props.onReportChange).props.report.revision,5)
  }finally{h.dispose()}
})

test('restoring clears unsaved comment drafts beyond the chosen history node without clearing other reports',async()=>{
  let head=3
  const dropped:string[]=[]
  const state={reportCommentDrafts:{'session-a/report-a/1':{},'session-a/report-a/3':{},'session-a/other/3':{}},
    bumpReportHistory(){},setReportCommentDraft(key:string,value:unknown){if(value===null)dropped.push(key)}}
  const h=await harness('report-viewer','ReportViewer',{
    getChatReportVersions:async()=>head===4?[report]:[{...report,revision:3},report],
    getChatReport:async(_s,_r,revision=head)=>({...report,revision,history_revision:head===4?1:revision,markdown:'Body',citations:[]}),
    getChatReportExport:async()=>({status:'idle'}),
    restoreChatReport:async()=>{head=4;return {...report,revision:4,history_revision:1,markdown:'Body',citations:[]}},state,
  })
  try{
    const props={report,onClose(){}}
    h.render(props);await h.flush()
    h.render(props).find(e=>e.props.entries&&e.props.onSelect).props.onSelect(1)
    h.render(props);await h.flush()
    await h.render(props).find(e=>e.props.entries&&e.props.onSelect).props.onRestore()
    assert.deepEqual(dropped,['session-a/report-a/3'])
  }finally{h.dispose()}
})

test('opening an old report card reads the actual current draft even when the history list hides its head',async()=>{
  const h=await harness('report-viewer','ReportViewer',{
    getChatReportVersions:async()=>[{...report,revision:2},report],
    getChatReport:async(_s,_r,revision=3)=>({...report,revision,markdown:revision===3?'Current draft':'Old draft',citations:[]}),
    getChatReportExport:async()=>({status:'idle'}),
  })
  try{
    const props={report,onClose(){}}
    h.render(props);await h.flush()
    const tree=h.render(props),menu=tree.find(e=>e.props.entries&&e.props.onSelect)
    assert.equal(menu.props.currentRevision,3)
    assert.equal(menu.props.selectedRevision,3)
    assert.equal(tree.find(e=>e.props.onReportChange).props.report.markdown,'Current draft')
    assert.equal(tree.find(e=>e.props.onReportChange).props.latest,true)
  }finally{h.dispose()}
})

test('restoring a decision-only state shows the returned draft despite an unchanged filtered history list',async()=>{
  let head=3
  const calls:any[]=[]
  const read=(revision=head)=>({...report,revision,markdown:'Same body',citations:[],
    suggestions:[{id:'suggestion',decision:revision===4?'pending':'accepted'}]})
  const h=await harness('report-viewer','ReportViewer',{
    getChatReportVersions:async()=>[{...report,revision:3},report],
    getChatReport:async(_s,_r,revision)=>read(revision),
    getChatReportExport:async()=>({status:'idle'}),
    restoreChatReport:async(...args)=>{calls.push(args);head=4;return read()},
    state:{bumpReportHistory(){}},
  })
  try{
    const props={report,onClose(){}}
    h.render(props);await h.flush()
    h.render(props).find(e=>e.props.entries&&e.props.onSelect).props.onSelect(1)
    h.render(props);await h.flush()
    await h.render(props).find(e=>e.props.entries&&e.props.onSelect).props.onRestore()
    let tree=h.render(props)
    assert.equal(tree.find(e=>e.props.onReportChange)?.props.report.revision,4,'Apply the restore response immediately')
    await h.flush();tree=h.render(props)
    assert.equal(tree.find(e=>e.props.entries&&e.props.onSelect).props.currentRevision,4)
    assert.equal(tree.find(e=>e.props.entries&&e.props.onSelect).props.selectedRevision,4)
    assert.equal(tree.find(e=>e.props.onReportChange).props.report.suggestions[0].decision,'pending')
    assert.deepEqual(calls[0].slice(0,4),['session-a','report-a',1,3])
    h.render(props).find(e=>e.props.entries&&e.props.onSelect).props.onSelect(1)
    h.render(props);await h.flush()
    await h.render(props).find(e=>e.props.entries&&e.props.onSelect).props.onRestore()
    assert.equal(calls[1][3],4,'The next restore uses the new head')
  }finally{h.dispose()}
})

test('a concurrent restore conflict refreshes the head without overwriting it and an explicit retry can succeed',async()=>{
  let head=3
  const calls:any[]=[]
  const h=await harness('report-viewer','ReportViewer',{
    getChatReportVersions:async()=>Array.from({length:head},(_,i)=>({...report,revision:head-i})),
    getChatReport:async(_s,_r,revision=head)=>({...report,revision,markdown:`Body ${revision}`,citations:[]}),
    getChatReportExport:async()=>({status:'idle'}),
    restoreChatReport:async(...args)=>{
      calls.push(args)
      if(args[3]!==head)throw Object.assign(new Error('Stale restore'),{status:409,code:'report_version_conflict'})
      head++;return {...report,revision:head,markdown:'Restored body',citations:[]}
    },
    state:{bumpReportHistory(){}},
  })
  try{
    const props={report,onClose(){}}
    h.render(props);await h.flush()
    h.render(props).find(e=>e.props.entries&&e.props.onSelect).props.onSelect(1)
    h.render(props);await h.flush()
    head=4
    await h.render(props).find(e=>e.props.entries&&e.props.onSelect).props.onRestore()
    let tree=h.render(props),menu=tree.find(e=>e.props.entries&&e.props.onSelect)
    assert.equal(calls.length,1,'A conflict must not silently retry an overwrite')
    assert.equal(menu.props.currentRevision,4)
    assert.equal(menu.props.selectedRevision,1,'Keep the chosen historical preview')
    assert.equal(tree.find(e=>e.props.role==='alert').props.children,'chat.reports.restoreConflict')
    await menu.props.onRestore();tree=h.render(props)
    assert.equal(calls[1][3],4)
    assert.notEqual(calls[1][4],calls[0][4],'A refreshed explicit restore is a new operation')
    assert.equal(tree.find(e=>e.props.onReportChange)?.props.report.revision,5)
  }finally{h.dispose()}
})

test('history menu offers dated actions and restores only on explicit click',async()=>{
  const chosen:number[]=[],restores:number[]=[]
  const h=await harness('report-history-menu','ReportHistoryMenu',{})
  try{
    const props={entries:[{...report,revision:2,kind:'edit'},{...report,kind:'create'}],currentRevision:2,selectedRevision:1,
      onSelect:(n:number)=>chosen.push(n),onRestore:()=>restores.push(1),restoring:false}
    const tree=h.render(props)
    assert.equal(restores.length,0)
    assert.equal(tree.filter(e=>e.props['data-history-revision']!==undefined).length,2)
    tree.find(e=>e.props['data-history-revision']===2).props.onClick()
    assert.deepEqual(chosen,[2])
    tree.find(e=>e.props.className==='pm-report-history-restore').props.onClick()
    assert.equal(restores.length,1)
    assert.ok(!tree.some(e=>e.props.children==='chat.reports.version'))
  }finally{h.dispose()}
})

test('history describes acceptance and rejection instead of a generic review decision',async()=>{
  const h=await harness('report-history-menu','ReportHistoryMenu',{})
  try{
    const props={entries:[{...report,revision:3,kind:'decision',decision:'rejected',label:'Clarify the wording'},{...report,revision:2,kind:'decision',decision:'accepted',label:'Shorten the summary'}],currentRevision:3,selectedRevision:3,onSelect(){},onRestore(){},restoring:false}
    const tree=h.render(props)
    const titles=tree.filter(e=>e.props.className==='pm-report-history-entry-title')
    assert.equal(titles[0].props.children[0],'chat.reports.historyDecision.rejected')
    assert.equal(titles[1].props.children[0],'chat.reports.historyDecision.accepted')
  }finally{h.dispose()}
})

test('closing during a restore does not leave its controls busy when the report reopens',async()=>{
  let finish!:()=>void,head=2
  const response=new Promise<void>(resolve=>{finish=()=>{head=3;resolve()}})
  const h=await harness('report-viewer','ReportViewer',{
    getChatReportVersions:async()=>Array.from({length:head},(_,i)=>({...report,revision:head-i})),
    getChatReport:async(_s,_r,revision=head)=>({...report,revision,markdown:'Body',citations:[]}),
    getChatReportExport:async()=>({status:'idle'}),restoreChatReport:async()=>{await response;return {...report,revision:3}},
    state:{bumpReportHistory(){}},
  })
  try{
    const props={report,onClose(){},active:true}
    h.render(props);await h.flush()
    h.render(props).find(e=>e.props.entries&&e.props.onSelect).props.onSelect(1)
    h.render(props);await h.flush()
    const pending=h.render(props).find(e=>e.props.entries&&e.props.onSelect).props.onRestore()
    h.render({...props,active:false});await h.flush();finish();await pending
    h.render(props);await h.flush()
    assert.equal(h.render(props).find(e=>e.props.entries&&e.props.onSelect).props.restoring,false)
  }finally{h.dispose()}
})

test("closing a preview ignores a download response that arrives later", async () => {
  const anchors: any[] = []
  let resolveDownload!: (blob: Blob) => void
  const original = Object.getOwnPropertyDescriptor(globalThis, "document")
  Object.defineProperty(globalThis, "document", { configurable: true, value: {
    body: { appendChild() {} }, createElement() { const anchor = { click() {}, remove() {} }; anchors.push(anchor); return anchor },
  } })
  const h = await harness("report-viewer", "ReportViewer", {
    getChatReport: async () => ({ ...report, markdown: "Body", citations: [] }),
    getChatReportVersions: async () => [report],
    getChatReportExport: async () => ({ status: "idle" }),
    createChatReportExport: async () => ({ status: "ready" }),
    downloadChatReport: async () => new Promise<Blob>((resolve) => { resolveDownload = resolve }),
  })
  try {
    const props = { report, onClose() {}, triggerRef: { current: null } }
    h.render(props); await h.flush()
    const tree = h.render(props)
    tree.find((e) => e.props.className === "pm-report-export").props.onChange("docx")
    await h.flush()
    tree.find((e) => e.props.onOpenChange)?.props.onOpenChange(false, { reason: "close-press", cancel() {} })
    resolveDownload(new Blob(["Word"]))
    await h.flush()
    assert.equal(anchors.length, 0)
  } finally { h.dispose(); if (original) Object.defineProperty(globalThis, "document", original); else Reflect.deleteProperty(globalThis, "document") }
})

test("an embedded report stays mounted during its close motion and unmounts after it completes", async () => {
  let closed = 0
  const h = await harness("report-viewer", "ReportViewer", {
    getChatReport: async () => ({ ...report, markdown: "Body", citations: [] }),
    getChatReportVersions: async () => [report], getChatReportExport: async () => ({ status: "idle" }),
    createChatReportExport: async () => ({ status: "ready" }), downloadChatReport: async () => new Blob(),
  })
  try {
    const props = { report, embedded: true, onClose() { closed++ } }
    h.render(props); await h.flush()
    h.render(props).find((e) => e.props["aria-label"] === "common.close").props.onClick()
    assert.equal(closed, 0, "The pane must remain mounted while its close transition runs")
    assert.ok(h.render(props).some((e) => e.type === "section" && e.props.className.includes("is-closing")))
    await new Promise((resolve) => setTimeout(resolve, 350))
    assert.equal(closed, 1)
  } finally { h.dispose() }
})

test("closing the Quick Chat report workspace uses the shared dialog exit before releasing its chat", async () => {
  let closed = 0
  const h = await harness("report-workspace", "ReportWorkspace", {})
  try {
    const props = { report, chat: "Live chat", onClose() { closed++ } }
    let tree = h.render(props)
    tree.find((e) => e.props.onOpenChange).props.onOpenChange(false)
    tree = h.render(props)
    assert.ok(tree.some((e) => e.props.open === false))
    assert.equal(closed, 0)
    tree.find((e) => e.props.onOpenChangeComplete).props.onOpenChangeComplete(false)
    assert.equal(closed, 1)
  } finally { h.dispose() }
})

test("citation file previews load automatically at the saved PDF page and pin the cited version", async () => {
  const calls: unknown[][] = []
  const h = await harness("citation-source-preview", "CitationSourcePreview", {
    getFileDetail: async (...args) => { calls.push(["detail", ...args]); return { filename: "Reference.pdf", current_version_id: "new" } },
    getFilePreviewUrl: (...args) => { calls.push(["preview", ...args]); return "/authorized-preview.pdf" },
    getExtractedText: async () => ({ text: "Full source text" }),
  })
  try {
    const evidence = { id: "e_a", title: "Reference.pdf", kind: "document", quote: "Original", source: { collection: "col-a", file_id: "file-a", version_id: "cited" }, location: { page_number: 3, char_offset: 8 } }
    const props = { evidence, sessionId: "session-a" }
    h.render(props); await h.flush()
    const tree = h.render(props)
    const viewer = tree.find((e) => e.props.url === "/authorized-preview.pdf")
    assert.equal(viewer?.props.initialPage, 3)
    assert.deepEqual(calls[1], ["preview", "__file__:file-a", { collection: "col-a", versionId: "cited" }])
    assert.ok(!tree.some((e) => e.props.children === "chat.reports.openOriginal"))
  } finally { h.dispose() }
})

test('an old attachment citation without a page resolves its exact offset through the native page map',async()=>{
  const h=await harness('citation-source-preview','CitationSourcePreview',{
    getChatAttachments:async()=>[{id:'a',filename:'Source.pdf'}],getChatAttachmentSource:async()=>new Blob(['pdf'],{type:'application/pdf'}),
    getChatAttachmentText:async()=>({text:'😀 Intro.\n\nStructure details.',position_map:[{char_offset:0,page_number:1},{char_offset:10,page_number:6}]}),
  })
  try{
    const props={sessionId:'s',evidence:{id:'e',kind:'attachment',title:'Source.pdf',source:{attachment_id:'a'},location:{char_offset:10},quote:'Structure details.'}}
    h.render(props);await h.flush();await h.flush()
    assert.equal(h.render(props).find(e=>e.props.filename==='Source.pdf'&&e.props.initialPage)?.props.initialPage,6)
  }finally{h.dispose()}
})

test("citation text previews receive the exact saved range for automatic highlight and location", async () => {
  const h = await harness("citation-source-preview", "CitationSourcePreview", {
    getFileDetail: async () => ({ filename: "Reference.txt" }),
    getFilePreviewUrl: () => "/authorized-preview.txt",
    getExtractedText: async () => ({ text: "Prefix. Original fact. Suffix." }),
  })
  try {
    const evidence = { id: "e_a", title: "Reference.txt", kind: "document", quote: "Original fact.", source: { collection: "col-a", file_id: "file-a" }, location: { char_offset: 8 } }
    const props = { evidence, sessionId: "session-a" }
    h.render(props); await h.flush()
    const tree = h.render(props)
    assert.ok(tree.some((e) => e.props.text === "Prefix. Original fact. Suffix." && e.props.highlightOffset === 8 && e.props.highlightLength === 14))
  } finally { h.dispose() }
})

test('the comments pill opens a navigation list and selecting a row requests the exact anchor', async()=>{
  let focus:any
  const c={id:'c1',session_id:'s',report_id:'r',title:'Review',revision:2,text:'Rebuild the argument.',anchor:{quote:'selected'},state:'pending',updated_at:'now'}
  const h=await harness('report-review-chip','ReportReviewChip',{state:{focusReportComment:(value:any)=>{focus=value}}})
  try {
    let tree=h.render({comments:[c]})
    tree.find(e=>typeof e.props.onOpenChange==='function')!.props.onOpenChange(true)
    tree=h.render({comments:[c]})
    assert.ok(tree.find(e=>e.props.open===true))
    const row=tree.find(e=>e.props.className==='pm-review-nav-row')
    row.props.onClick()
    assert.equal(focus.comment_id,'c1');assert.equal(focus.report.revision,2)
    assert.ok(!h.render({comments:[]}).length)
  } finally {h.dispose()}
})

test('current comments and previous revision tracking can open independently or together',async()=>{
  const current={id:'new',session_id:'s',report_id:'r',revision:2,text:'A new comment.',anchor:{start_line:1,end_line:1,quote:'Revised'},state:'pending'}
  const previous={...current,id:'old',revision:1,text:'Earlier requirement.'}
  const revised={...report,id:'r',session_id:'s',revision:2,markdown:'Revised.',citations:[],parent_revision:1,changes:[],review:{comments:[previous],results:[{comment_id:'old',status:'applied',summary:'Rebuilt the argument to explain the decision.',edit_ids:['e'],change_ids:['change-0']}]}}
  const h=await harness('report-review-panel','ReportReviewPanel',{
    getReportComments:async()=>[current],getReportReviewHistory:async()=>[],getChatReport:async()=>({...revised,revision:1}),
    addReportComment:async()=>current,editReportComment:async()=>current,deleteReportComment:async()=>{},
  })
  try {
    h.render({report:revised});await h.flush()
    let tree=h.render({report:revised})
    const toggle=(label:string)=>h.render({report:revised}).find(e=>e.props['aria-label']===label)
    toggle('chat.review.comments').props.onClick()
    tree=h.render({report:revised})
    assert.deepEqual(tree.filter(e=>e.props.comment).map(e=>e.props.comment.id),['new'])
    toggle('chat.review.track').props.onClick()
    tree=h.render({report:revised})
    const cards=tree.filter(e=>e.props.comment)
    assert.deepEqual(cards.map(e=>e.props.comment.id).sort(),['new','old'])
    assert.equal(cards.find(e=>e.props.comment.id==='old').props.outcome.summary,'Rebuilt the argument to explain the decision.')
    assert.equal(cards.find(e=>e.props.comment.id==='old').props.history,true)
    assert.equal(cards.find(e=>e.props.comment.id==='new').props.history,false)
    assert.equal(toggle('chat.review.comments').props['aria-pressed'],true)
    assert.equal(toggle('chat.review.track').props['aria-pressed'],true)
    toggle('chat.review.comments').props.onClick()
    assert.deepEqual(h.render({report:revised}).filter(e=>e.props.comment).map(e=>e.props.comment.id),['old'])
    toggle('chat.review.track').props.onClick()
    assert.equal(h.render({report:revised}).filter(e=>e.props.comment).length,0)
    toggle('chat.review.comments').props.onClick()
    assert.deepEqual(h.render({report:revised}).filter(e=>e.props.comment).map(e=>e.props.comment.id),['new'])
  }finally{h.dispose()}
})

test('a completed review without a new version appears only under tracking',async()=>{
  const old={id:'old',session_id:'s',report_id:'r',revision:1,text:'Earlier request',anchor:{start_line:1,end_line:1,quote:'Body'},state:'pending'}
  const data={...report,id:'r',session_id:'s',markdown:'Body',citations:[]}
  const h=await harness('report-review-panel','ReportReviewPanel',{
    getReportComments:async()=>[],getReportReviewHistory:async()=>[{id:'batch',status:'unchanged',revision:1,comments:[old],results:[{comment_id:'old',status:'unchanged',summary:'Kept the supported wording because the source already states it precisely.'}]}],
  })
  try{
    h.render({report:data});await h.flush()
    let tree=h.render({report:data})
    tree.find(e=>e.props['aria-label']==='chat.review.comments').props.onClick()
    tree=h.render({report:data})
    assert.equal(tree.filter(e=>e.props.comment).length,0,'Previous resolved requests must not inflate the current comments view')
    tree.find(e=>e.props['aria-label']==='chat.review.track').props.onClick()
    tree=h.render({report:data})
    assert.equal(tree.find(e=>e.props.comment)?.props.comment.id,'old')
    assert.equal(tree.find(e=>e.props.comment)?.props.history,true)
  }finally{h.dispose()}
})

test('review controls render in the supplied report header slot',async()=>{
  const target={id:'report-header'}
  const h=await harness('report-review-panel','ReportReviewPanel',{getReportComments:async()=>[],getReportReviewHistory:async()=>[]})
  try{
    const tree=h.render({report:{...report,markdown:'Body',citations:[],parent_revision:1},toolbarTarget:target})
    const portal=tree.find(e=>e.type==='report-toolbar-portal')
    assert.equal(portal?.props.container,target)
    assert.ok(elements(portal).some(e=>e.props['aria-label']==='chat.review.track'))
    assert.ok(elements(portal).some(e=>e.props['aria-label']==='chat.review.comments'))
  }finally{h.dispose()}
})

test('the report header supplies the review slot beside history and export controls',async()=>{
  const h=await harness('report-viewer','ReportViewer',{
    getChatReport:async()=>({...report,markdown:'Body',citations:[]}),getChatReportVersions:async()=>[report],
    getChatReportExport:async()=>({status:'idle'}),createChatReportExport:async()=>({status:'ready'}),downloadChatReport:async()=>new Blob(),
  })
  try{
    const props={report,embedded:true,onClose(){}}
    h.render(props);await h.flush()
    let tree=h.render(props)
    const toolbar=tree.find(e=>e.props.className==='pm-report-toolbar')
    const slot=elements(toolbar).find(e=>e.props.className==='pm-report-review-toolbar')
    assert.ok(slot)
    const target={id:'toolbar'}
    slot.props.ref(target)
    tree=h.render(props)
    assert.equal(tree.find(e=>e.props.onRailOpenChange).props.toolbarTarget,target)
    assert.ok(elements(toolbar).some(e=>e.props.entries&&e.props.onSelect))
    assert.ok(elements(toolbar).some(e=>e.props.className==='pm-report-export'))
  }finally{h.dispose()}
})

test('tracking preserves a new draft and keeps a continued comment separate from its frozen original',async()=>{
  const current={id:'continued',session_id:'s',report_id:'r',revision:2,text:'Remaining request',state:'pending',anchor:{start_line:1,end_line:1,quote:'Body'}}
  const previous={...current,id:'same',revision:1,text:'Original request'}
  const state={reportCommentDrafts:{'s/r/2':{anchor:current.anchor,text:'A new draft'}},reportComments:{},reportCommentFocus:null}
  const data={...report,id:'r',session_id:'s',revision:2,markdown:'Body',citations:[],parent_revision:1,changes:[{id:'change-0',new_start:8}],review:{comments:[previous],results:[{comment_id:'same',status:'partial',summary:'Adjusted the argument; the missing figure still needs a source.',change_ids:['change-0']}]}}
  const h=await harness('report-review-panel','ReportReviewPanel',{state,getReportComments:async()=>[current],getReportReviewHistory:async()=>[]})
  try{
    const props={report:data}
    h.render(props);await h.flush()
    let tree=h.render(props)
    tree.find(e=>e.props['aria-label']==='chat.review.comments').props.onClick()
    tree=h.render(props)
    tree.find(e=>e.props['aria-label']==='chat.review.track').props.onClick()
    tree=h.render(props)
    const entries=tree.find(e=>e.props.entries).props.entries
    assert.equal(new Set(entries.map((e:any)=>e.id)).size,entries.length)
    assert.equal(entries.find((e:any)=>e.history).targetLine,8)
    const cards=tree.filter(e=>e.props.comment)
    assert.equal(cards.length,2)
    assert.equal(typeof cards.find(e=>!e.props.history).props.onSave,'function')
    assert.equal(cards.find(e=>e.props.history).props.onSave,undefined)
    assert.equal(tree.find(e=>e.type==='textarea').props.value,'A new draft')
  }finally{h.dispose()}
})

test('a processed comment remains only in tracking while new comments remain editable',async()=>{
  const processed={id:'processed',batch_id:'batch',session_id:'s',report_id:'r',revision:2,text:'Old request',state:'pending',anchor:{start_line:1,end_line:1,quote:'Body'}}
  const fresh={...processed,id:'new',batch_id:undefined,text:'New request'}
  const data={...report,id:'r',session_id:'s',revision:2,markdown:'Body',citations:[],parent_revision:1,
    review:{id:'batch',comments:[processed],results:[{comment_id:'processed',status:'partial',summary:'Changed the supported part.',change_ids:[]}]}}
  const h=await harness('report-review-panel','ReportReviewPanel',{state:{reportCommentDrafts:{},reportComments:{},reportCommentFocus:null},
    getReportComments:async()=>[processed,fresh],getReportReviewHistory:async()=>[]})
  try{
    const props={report:data};h.render(props);await h.flush()
    let tree=h.render(props);tree.find(e=>e.props['aria-label']==='chat.review.comments').props.onClick()
    tree=h.render(props)
    assert.deepEqual(tree.filter(e=>e.props.comment).map(e=>e.props.comment.id),['new'])
    tree.find(e=>e.props['aria-label']==='chat.review.track').props.onClick();tree=h.render(props)
    const cards=tree.filter(e=>e.props.comment)
    assert.equal(cards.length,2)
    assert.equal(cards.find(e=>e.props.comment.id==='processed').props.history,true)
    assert.equal(cards.find(e=>e.props.comment.id==='new').props.history,false)
  }finally{h.dispose()}
})

test('a later reply-only review supersedes the saved revision review in tracking',async()=>{
  const old={id:'old',session_id:'s',report_id:'r',revision:1,text:'Earlier request',state:'resolved',anchor:{start_line:1,end_line:1,quote:'Body'}}
  const latest={...old,id:'latest',revision:2,text:'Verify this claim'}
  const data={...report,id:'r',session_id:'s',revision:2,markdown:'Body',citations:[],parent_revision:1,
    review:{id:'old-batch',comments:[old],results:[{comment_id:'old',status:'applied',summary:'Reworded the claim.',change_ids:[]}]}}
  const h=await harness('report-review-panel','ReportReviewPanel',{state:{reportCommentDrafts:{},reportComments:{},reportCommentFocus:null},getReportComments:async()=>[],
    getReportReviewHistory:async()=>[{id:'new-batch',revision:2,result_revision:2,status:'unchanged',comments:[latest],results:[{comment_id:'latest',status:'unchanged',summary:'Confirmed the claim using the cited evidence.',edit_ids:[]}]}]})
  try{
    const props={report:data};h.render(props);await h.flush()
    let tree=h.render(props);tree.find(e=>e.props['aria-label']==='chat.review.track').props.onClick();tree=h.render(props)
    assert.deepEqual(tree.filter(e=>e.props.comment).map(e=>e.props.comment.id),['latest'])
  }finally{h.dispose()}
})

test('unchanged reviews from another revision never refresh the displayed historical snapshot',async()=>{
  const data={...report,revision:1,markdown:'Historical sample body.',citations:[],suggestions:[]}
  const comment={id:'later-comment',revision:2,text:'Verify the sample claim',anchor:{start_line:1,end_line:1,quote:'Sample claim'}}
  const reads:any[]=[],changes:any[]=[],refreshes:any[]=[]
  const h=await harness('report-review-panel','ReportReviewPanel',{
    getReportComments:async()=>[],
    getReportReviewHistory:async()=>[{id:'later-batch',revision:2,result_revision:2,status:'unchanged',comments:[comment]}],
    getChatReport:async(...args:any[])=>{reads.push(args);return data},
  })
  try{
    const props={report:data,latest:false,onReportChange:(updated:any)=>changes.push(updated),onReportRefresh:(updated:any)=>refreshes.push(updated)}
    h.render(props);await h.flush();h.render(props);await h.flush()
    assert.deepEqual(reads,[],'A later review is not evidence that an older snapshot is stale')
    assert.deepEqual(changes,[])
    assert.deepEqual(refreshes,[])
  }finally{h.dispose()}
})

for(const latest of [false,true])test(`an applicable unchanged review refreshes its snapshot without publishing a mutation (latest=${latest})`,async()=>{
  const comment={id:'sample-comment',revision:2,text:'Verify the sample claim',anchor:{start_line:1,end_line:1,quote:'Sample claim'}}
  const data={...report,revision:2,markdown:'Sample claim.',citations:[],suggestions:[]}
  const refreshed={...data,suggestions:[{id:'answered',comments:[comment],patches:[],decision:'answered'}]}
  const reads:any[]=[],changes:any[]=[],refreshes:any[]=[]
  const h=await harness('report-review-panel','ReportReviewPanel',{
    getReportComments:async()=>[],getReportReviewHistory:async()=>[{id:'sample-batch',revision:2,result_revision:2,status:'unchanged',comments:[comment]}],
    getChatReport:async(...args:any[])=>{reads.push(args);return refreshed},
  })
  try{
    h.render({report:data,latest,onReportChange:(updated:any)=>changes.push(updated),onReportRefresh:(updated:any)=>refreshes.push(updated)})
    await h.flush()
    assert.deepEqual(reads,[[report.session_id,report.id,2]])
    assert.deepEqual(refreshes,[refreshed])
    assert.deepEqual(changes,[],'A projection refresh is not a new current-report edit')
  }finally{h.dispose()}
})

test('refreshing a historical review preserves the history cursor and keeps restore available',async()=>{
  let head=3
  const restoreCalls:any[]=[]
  const h=await harness('report-viewer','ReportViewer',{
    getChatReportVersions:async()=>head===4?[report]:[3,2,1].map(revision=>({...report,revision})),
    getChatReport:async(_sid:string,_rid:string,revision=head)=>({...report,revision,markdown:`Sample body ${revision}`,citations:[],suggestions:[]}),
    getChatReportExport:async()=>({status:'idle'}),
    restoreChatReport:async(...args:any[])=>{restoreCalls.push(args);head=4;return {...report,revision:4,history_revision:1,markdown:'Sample body 1',citations:[],suggestions:[]}},
    state:{bumpReportHistory(){}},
  })
  try{
    const props={report,onClose(){}}
    h.render(props);await h.flush()
    h.render(props).find(e=>e.props.entries&&e.props.onSelect).props.onSelect(1)
    h.render(props);await h.flush()
    let tree=h.render(props)
    const oldPanel=tree.find(e=>e.props.onReportChange)
    assert.equal(typeof oldPanel.props.onReportRefresh,'function')
    const refreshed={...oldPanel.props.report,suggestions:[{id:'sample-answer',decision:'answered',patches:[],comments:[]}]}
    oldPanel.props.onReportRefresh(refreshed)
    tree=h.render(props)
    let menu=tree.find(e=>e.props.entries&&e.props.onSelect)
    assert.equal(menu.props.selectedRevision,1)
    assert.equal(menu.props.currentRevision,3)
    assert.equal(tree.find(e=>e.props.onReportChange).props.latest,false)
    assert.deepEqual(tree.find(e=>e.props.onReportChange).props.report,refreshed)
    menu.props.onSelect(2);h.render(props);await h.flush()
    oldPanel.props.onReportRefresh(refreshed)
    tree=h.render(props)
    assert.equal(tree.find(e=>e.props.entries&&e.props.onSelect).props.selectedRevision,2)
    assert.equal(tree.find(e=>e.props.onReportChange).props.report.revision,2,'Late refreshes cannot replace a different historical snapshot')
    tree.find(e=>e.props.entries&&e.props.onSelect).props.onSelect(1)
    h.render(props);await h.flush()
    menu=h.render(props).find(e=>e.props.entries&&e.props.onSelect)
    await menu.props.onRestore()
    assert.deepEqual(restoreCalls[0].slice(0,4),[report.session_id,report.id,1,3])
    assert.equal(h.render(props).find(e=>e.props.onReportChange).props.report.markdown,'Sample body 1')
  }finally{h.dispose()}
})

test('a late reply-only snapshot refresh cannot overwrite a newer decision in the current draft',async()=>{
  const h=await harness('report-viewer','ReportViewer',{
    getChatReportVersions:async()=>[{...report,revision:2},report],
    getChatReport:async()=>({...report,revision:2,markdown:'Sample candidate.',citations:[],suggestions:[]}),
    getChatReportExport:async()=>({status:'idle'}),
  })
  try{
    const props={report,onClose(){}}
    h.render(props);await h.flush()
    const oldPanel=h.render(props).find(e=>e.props.onReportChange)
    oldPanel.props.onReportChange({...oldPanel.props.report,revision:3,markdown:'Accepted sample.'})
    assert.equal(h.render(props).find(e=>e.props.onReportChange).props.report.revision,3)
    oldPanel.props.onReportRefresh({...oldPanel.props.report,suggestions:[{id:'late-answer',decision:'answered'}]})
    const current=h.render(props).find(e=>e.props.onReportChange).props.report
    assert.equal(current.revision,3)
    assert.equal(current.markdown,'Accepted sample.')
  }finally{h.dispose()}
})

test('a sent comments pill grows left and down from its own bounds and stays hidden until the surface closes',async()=>{
  const h=await harness('report-review-chip','ReportReviewChip',{})
  const props={history:true,comments:[{id:'c1',text:'Make this clearer',title:'Report',anchor:{quote:'Passage'}}]}
  try{
    let tree=h.render(props)
    const trigger=tree.find(e=>e.props.className?.includes('pm-review-chip'))
    assert.ok(trigger.props.ref,'The expanding surface needs the original pill bounds')
    trigger.props.ref.current={getBoundingClientRect:()=>({width:152,height:30}),closest:()=>({getBoundingClientRect:()=>({width:280})})}
    const root=tree.find(e=>e.props.onOpenChange)
    root.props.onOpenChange(true,{reason:'trigger-press'})
    tree=h.render(props)
    const positioner=tree.find(e=>e.props.className==='pm-review-popover-positioner')
    assert.equal(positioner.props.side,'bottom');assert.equal(positioner.props.align,'end');assert.equal(positioner.props.sideOffset,-30)
    const surface=tree.find(e=>e.props.className?.includes('pm-review-history-popover'))
    assert.equal(surface.props.style['--pm-review-origin-w'],'152px')
    assert.equal(surface.props.style['--pm-review-origin-h'],'30px')
    assert.equal(surface.props.style.width,280)
    assert.ok(tree.some(e=>e.props.className==='pm-review-collapse'))
    root.props.onOpenChange(false,{reason:'outside-press'})
    assert.equal(h.render(props).find(e=>e.props.className?.includes('pm-review-chip')).props['data-surface-active'],true)
    root.props.onOpenChangeComplete(false)
    assert.equal(h.render(props).find(e=>e.props.className?.includes('pm-review-chip')).props['data-surface-active'],undefined)
  }finally{h.dispose()}
})

test('cancelling an empty draft releases the side rail and allows another text selection',async()=>{
  const draftKey='s/r/1'
  const state:any={reportCommentDrafts:{[draftKey]:{anchor:{start_line:1,end_line:1,quote:'Original',prefix:'',suffix:''},text:''}},reportComments:{},reportCommentFocus:null}
  state.setReportCommentDraft=(key:string,value:any)=>{const next={...state.reportCommentDrafts};if(value)next[key]=value;else delete next[key];state.reportCommentDrafts=next}
  const h=await harness('report-review-panel','ReportReviewPanel',{state,getReportComments:async()=>[],getReportReviewHistory:async()=>[]})
  const saved=new Map(['window','document','Element','requestAnimationFrame','cancelAnimationFrame'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]))
  try{
    Object.defineProperty(globalThis,'window',{configurable:true,value:{getSelection:()=>({removeAllRanges(){}})}})
    const blocks=[{id:'b_target',hash:'target_hash',kind:'paragraph',start:0,end:11,start_line:1,end_line:1}]
    const props={report:{...report,id:'r',session_id:'s',markdown:'Fresh text.',citations:[],blocks}}
    let tree=h.render(props)
    tree.find(e=>e.props['aria-label']==='chat.review.comments').props.onClick()
    tree=h.render(props)
    tree.find(e=>e.props.children==='common.cancel').props.onClick()
    tree=h.render(props)
    assert.ok(!tree.some(e=>typeof e.props.className==='string'&&e.props.className.includes('has-comments')),'Cancel must release the empty rail')
    class Block{dataset={reportLine:'1',reportEnd:'1'};parentElement=null;closest(selector:string){return selector==='[data-report-line]'?this:null}}
    const block=new Block()
    Object.defineProperty(globalThis,'Element',{configurable:true,value:Block})
    const range={startContainer:block,endContainer:block,startOffset:0,endOffset:11,cloneContents:()=>({querySelectorAll:()=>[],textContent:'Fresh text.'}),cloneRange(){return this},getBoundingClientRect:()=>({top:260,left:30})}
    Object.defineProperty(globalThis,'window',{configurable:true,value:{getSelection:()=>({isCollapsed:false,rangeCount:1,getRangeAt:()=>range,removeAllRanges(){}}),matchMedia:()=>({matches:true})}})
    Object.defineProperty(globalThis,'document',{configurable:true,value:{createRange:()=>({selectNodeContents(){},setStart(){},setEnd(){},cloneContents:()=>({querySelectorAll:()=>[],textContent:''})})}})
    Object.defineProperty(globalThis,'requestAnimationFrame',{configurable:true,value:(callback:any)=>{callback();return 1}})
    Object.defineProperty(globalThis,'cancelAnimationFrame',{configurable:true,value:()=>{}})
    tree.find(e=>typeof e.props.className==='string'&&e.props.className.startsWith('pm-review-surface')).props.ref.current={getBoundingClientRect:()=>({left:0,top:0,width:1000})}
    const body=tree.find(e=>e.props.className==='pm-review-document')
    body.props.ref.current={contains:()=>true,focus(){}}
    body.props.onKeyUp({})
    assert.ok(h.render(props).some(e=>e.props.className==='pm-review-selection'),'Selecting again must offer Add comment')
    h.render(props).find(e=>e.props.className==='pm-review-selection').props.onClick()
    assert.equal(state.reportCommentDrafts[draftKey].anchor.block_id,blocks[0].id)
    assert.equal(state.reportCommentDrafts[draftKey].anchor.block_hash,blocks[0].hash)
  }finally{h.dispose();for(const [key,value]of saved){if(value)Object.defineProperty(globalThis,key,value);else Reflect.deleteProperty(globalThis,key)}}
})

test('nearby comment cards fold around the active card and can expand all without losing navigation',async()=>{
  const h=await harness('report-comment-rail','ReportCommentRail',{})
  let activated=''
  const props={entries:[{id:'a',anchor:{start_line:1,end_line:1,quote:'First'},text:'First comment',index:0},{id:'b',anchor:{start_line:1,end_line:1,quote:'Second'},text:'Second comment',index:1}],activeId:'b',open:true,tracking:false,documentRef:{current:null},railRef:{current:null},draftRange:{current:null},onActivate:(id:string)=>{activated=id},children:[React.createElement('div',{'data-expanded-card':'a',key:'a'},'Full first card'),React.createElement('div',{'data-expanded-card':'b',key:'b'},'Full second card')]}
  try{
    let tree=h.render(props)
    assert.equal(tree.find(e=>e.props['data-review-card']==='a').props['data-expanded'],false)
    assert.equal(tree.find(e=>e.props['data-review-card']==='b').props['data-expanded'],true)
    tree.find(e=>e.props.className==='pm-review-compact').props.onClick()
    assert.equal(activated,'a')
    tree.find(e=>e.props.className==='pm-review-group-toggle').props.onClick()
    tree=h.render(props)
    assert.equal(tree.filter(e=>e.props['data-expanded']===true).length,2)
    tree.find(e=>e.props.className==='pm-review-group-toggle').props.onClick()
    tree=h.render({...props,activeId:'a'})
    assert.equal(tree.find(e=>e.props['data-review-card']==='a').props['data-expanded'],true,'Navigating to a folded card opens it')
    assert.equal(tree.find(e=>e.props['data-review-card']==='b').props['data-expanded'],false)
  }finally{h.dispose()}
})

test('a group can fold its active card and expanding it again retains the same card instance',async()=>{
  const h=await harness('report-comment-rail','ReportCommentRail',{})
  const props={entries:['a','b'].map((id,index)=>({id,index,anchor:{start_line:1,end_line:1,quote:id},text:id})),activeId:'a',open:true,tracking:true,documentRef:{current:null},railRef:{current:null},draftRange:{current:null},onActivate(){},children:['a','b'].map(id=>React.createElement('div',{'data-card-instance':id,key:id}))}
  try{
    let tree=h.render(props)
    tree.find(e=>e.props.className==='pm-review-group-toggle').props.onClick()
    tree=h.render(props)
    assert.equal(tree.find(e=>e.props['data-review-card']==='b').props['data-expanded'],true)
    tree.find(e=>e.props.className==='pm-review-group-toggle').props.onClick()
    tree=h.render(props)
    assert.equal(tree.find(e=>e.props['data-review-card']==='a').props['data-expanded'],false,'Collapse must work even for the active card')
    assert.ok(tree.some(e=>e.props['data-card-instance']==='a'),'Folding must preserve the editor and native details state')
  }finally{h.dispose()}
})

test('clicking revision card text locates its passage but nested controls do not trigger navigation',async()=>{
  let focused=0
  const h=await harness('report-review-card','CommentCard',{state:{reportCommentDrafts:{}}})
  try{
    const tree=h.render({comment:{id:'c',session_id:'s',report_id:'r',anchor:{quote:'Old.'},text:'Shorten.'},index:0,active:false,history:true,onFocus(){focused++}})
    const card=tree.find(e=>e.type==='section')
    assert.equal(typeof card.props.onClick,'function')
    card.props.onClick({target:{closest:()=>null}})
    card.props.onClick({target:{closest:()=>({})}})
    assert.equal(focused,1)
  }finally{h.dispose()}
})

test('revision status stays in the header and discussion rounds form one chronological list',async()=>{
  const h=await harness('report-review-card','CommentCard',{state:{reportCommentDrafts:{}}})
  const c={id:'c',session_id:'s',report_id:'r',anchor:{quote:'Old.'},text:'Shorten.'}
  const round={id:'old',decision:'accepted',comments:[c],summary:'Removed repetition.',patches:[{before:'Old.',after:'New.'}]}
  try{
    const tree=h.render({comment:c,index:0,active:true,history:true,suggestion:{...round,id:'now',decision:'pending'},rounds:[round],onFocus(){}})
    const header=tree.find(e=>e.props.className==='pm-review-comment-head')
    assert.ok(elements(header).some(e=>e.props.className?.includes('pm-review-status')))
    const history=tree.find(e=>e.props.className==='pm-review-round-history')
    assert.equal(elements(history).find(e=>e.props['data-discussion-round']==='old')?.type,'li')
    assert.ok(elements(history).some(e=>e.props.className==='pm-review-round-number'))
    assert.ok(!elements(history).some(e=>e.type==='blockquote'),'Earlier rounds show request and reply, without the original selection')
    assert.ok(!tree.some(e=>e.props.className==='pm-review-outcome'&&elements(e).some(n=>n.type==='span')),'The body should not repeat the header status')
  }finally{h.dispose()}
})

test('the selected revision card exposes one clear current state',async()=>{
  const h=await harness('report-review-card','CommentCard',{state:{reportCommentDrafts:{}}})
  try{
    const props={comment:{id:'c',session_id:'s',report_id:'r',anchor:{quote:'Sample'},text:'Clarify.'},index:0,history:true,onFocus(){}}
    const selected=h.render({...props,active:true}).find(e=>e.type==='section')
    assert.equal(selected.props['aria-current'],'true')
    assert.match(selected.props.className,/is-active/)
    assert.equal(h.render({...props,active:false}).find(e=>e.type==='section').props['aria-current'],undefined)
  }finally{h.dispose()}
})

test('historical revisions show old and new text together without requiring an extra click',async()=>{
  let loads=0
  const group={id:'g',base_revision:1,result_revision:2,decision:'pending',comments:[{text:'Shorten this.'}],summary:'Shortened the overview.',results:[{change_ids:['own']}],patches:[{field:'markdown',start:4,end:5,before:'A',after:'B'}]}
  const change={id:'own',kind:'replace',before:'The A sentence.',after:'The B sentence.',old_start:1,new_start:1,removed:[{quote:'A',prefix:'The ',suffix:' sentence.'}],added:[{quote:'B',prefix:'The ',suffix:' sentence.'}]}
  const h=await harness('report-discussion-changes','ReportDiscussionChanges',{getChatReport:async(_s:any,_r:any,n:number)=>{loads++;return {...report,revision:n,markdown:n===1?'The A sentence.':'The B sentence.',citations:[],blocks:[{start:0,end:15}],changes:[change],suggestions:[group]}}})
  try{
    const props={sessionId:'s',reportId:'r',round:{...group,result_revision:2}}
    let tree=h.render(props)
    assert.equal(loads,0)
    assert.ok(!tree.some(e=>e.type==='details'))
    await tree.find(e=>e.props.className==='pm-review-compare-trigger').props.onClick()
    await h.flush();tree=h.render(props)
    assert.equal(loads,2,'Load both immutable citation snapshots')
    assert.ok(tree.some(e=>e.props.source==='The B sentence.'&&e.props.previousSource==='The A sentence.'),'The initial view must expose both sides through one inline redline')
    assert.ok(tree.some(e=>e.props.className==='pm-review-comparison-dialog'))
    assert.ok(tree.some(e=>e.props.children==='chat.review.legendRemoved'))
    assert.ok(tree.some(e=>e.props.children==='chat.review.legendAdded'))
    assert.ok(tree.some(e=>e.props.children==='Shortened the overview.'))
    assert.equal(tree.filter(e=>e.props['data-comparison-pair']).length,1)
    assert.ok(!tree.some(e=>e.props.className==='pm-review-show-before'),'The old sentence must not be hidden behind another disclosure')
  }finally{h.dispose()}
})

test('block comment action stays beside its visual instead of at the far edge of the rail',async()=>{
  const h=await harness('report-review-panel','ReportReviewPanel',{getReportComments:async()=>[],getReportReviewHistory:async()=>[]})
  try{
    const props={report:{...report,markdown:'| A |\n|---|',citations:[]}}
    let tree=h.render(props)
    tree.find(e=>e.props.className==='pm-review-surface').props.ref.current={getBoundingClientRect:()=>({top:0,left:0,width:1000}),contains:()=>true}
    const block={dataset:{reportLine:'1',reportEnd:'2'},matches:()=>true,getBoundingClientRect:()=>({top:80,left:0,right:600})}
    const body=tree.find(e=>e.props.className==='pm-review-document')
    body.props.ref.current={contains:()=>true}
    body.props.onPointerMove({target:{closest:()=>block},buttons:0})
    tree=h.render(props)
    const action=tree.find(e=>e.props.className==='pm-review-block-action')
    assert.equal(action.props.style.left,606)
    assert.ok(action.props.style.top>=80)
    let captured:any
    tree.find(e=>e.props.className==='pm-review-surface').props.ref.current.contains=()=>true
    body.props.onPointerLeave({relatedTarget:{nodeType:1,closest:()=>null}})
    captured=h.render(props).find(e=>e.props.className==='pm-review-block-action')
    assert.ok(captured,'Crossing the small gap inside the report must keep the action reachable')
  }finally{h.dispose()}
})

test('adding a whole-block comment clears the earlier text selection anchor',async()=>{
  const saved=Object.getOwnPropertyDescriptor(globalThis,'window')
  Object.defineProperty(globalThis,'window',{configurable:true,value:{getSelection:()=>({removeAllRanges(){}})}})
  const state:any={reportCommentDrafts:{},reportComments:{},reportCommentFocus:null}
  state.setReportCommentDraft=(key:string,value:any)=>{state.reportCommentDrafts={...state.reportCommentDrafts,[key]:value}}
  const h=await harness('report-review-panel','ReportReviewPanel',{state,getReportComments:async()=>[],getReportReviewHistory:async()=>[]})
  try{
    const props={report:{...report,markdown:'| Table |\n| --- |',citations:[]}}
    let tree=h.render(props)
    const oldRange={startContainer:{}}
    tree.find(e=>e.props.draftRange).props.draftRange.current=oldRange
    tree.find(e=>e.props.className==='pm-review-surface').props.ref.current={getBoundingClientRect:()=>({top:0})}
    const body=tree.find(e=>e.props.className==='pm-review-document')
    body.props.ref.current={contains:()=>true}
    const block={dataset:{reportLine:'1',reportEnd:'2'},matches:()=>true,getBoundingClientRect:()=>({top:80})}
    body.props.onPointerMove({target:{closest:()=>block},buttons:0})
    tree=h.render(props)
    tree.find(e=>e.props.className==='pm-review-block-action').props.onClick()
    tree=h.render(props)
    assert.equal(tree.find(e=>e.props.draftRange).props.draftRange.current,null,'A block comment must align to its own block, not an earlier text range')
  }finally{h.dispose();if(saved)Object.defineProperty(globalThis,'window',saved);else Reflect.deleteProperty(globalThis,'window')}
})

test('the draft card follows the selected text when the report reflows',async()=>{
  const saved=new Map(['window','document','ResizeObserver','requestAnimationFrame','cancelAnimationFrame'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]))
  const callbacks:(()=>void)[]=[], frames:(()=>void)[]=[]
  let selectedTop=340
  const source={querySelectorAll:()=>[],contains:()=>true,getBoundingClientRect:()=>({height:900})}
  const rail={getBoundingClientRect:()=>({top:100}),querySelector:()=>null}
  const props={entries:[{id:'draft',anchor:{start_line:1,end_line:1,quote:'Selected text'},text:'',index:0,draft:true}],activeId:'draft',open:true,tracking:false,documentRef:{current:source},railRef:{current:rail},draftRange:{current:{startContainer:{},getClientRects:()=>[{top:selectedTop,width:80,height:20}]}},onActivate(){},children:React.createElement('form',{},'Editor')}
  Object.defineProperty(globalThis,'window',{configurable:true,value:{addEventListener(){},removeEventListener(){}}})
  Object.defineProperty(globalThis,'document',{configurable:true,value:{}})
  Object.defineProperty(globalThis,'ResizeObserver',{configurable:true,value:class{constructor(callback:()=>void){callbacks.push(callback)}observe(){}disconnect(){}}})
  Object.defineProperty(globalThis,'requestAnimationFrame',{configurable:true,value:(callback:()=>void)=>{frames.push(callback);return frames.length}})
  Object.defineProperty(globalThis,'cancelAnimationFrame',{configurable:true,value:()=>{}})
  const h=await harness('report-comment-rail','ReportCommentRail',{})
  try{
    let tree=h.render(props)
    tree.find(e=>e.props.className==='pm-review-positioned-card').props.ref({dataset:{expanded:'true'},getBoundingClientRect:()=>({height:244})})
    await h.flush()
    tree=h.render(props)
    assert.equal(tree.find(e=>e.props.className==='pm-review-positioned-card').props.style.top,240)
    assert.equal(tree.find(e=>e.type==='aside').props.style.height,900)
    selectedTop=460
    callbacks.at(-1)!()
    frames.splice(0).forEach(callback=>callback())
    tree=h.render(props)
    assert.equal(tree.find(e=>e.props.className==='pm-review-positioned-card').props.style.top,360,'A width change must remeasure the live selected range')
  }finally{h.dispose();for(const [key,value]of saved){if(value)Object.defineProperty(globalThis,key,value);else Reflect.deleteProperty(globalThis,key)}}
})

test('opening and closing the comment rail updates the embedded workspace without changing its chosen split',async()=>{
  const state={reportChatRatio:0.52}
  const h=await harness('report-viewer','ReportViewer',{
    state,getChatReport:async()=>({...report,markdown:'Body',citations:[]}),
    getChatReportVersions:async()=>[report],getChatReportExport:async()=>({status:'idle'}),
    createChatReportExport:async()=>({status:'ready'}),downloadChatReport:async()=>new Blob(),
  })
  try{
    const props={report,embedded:true,onClose(){}}
    h.render(props);await h.flush()
    let tree=h.render(props)
    tree.find(e=>e.props.onRailOpenChange).props.onRailOpenChange(true)
    tree=h.render(props)
    assert.ok(tree.some(e=>e.type==='section'&&e.props.className.includes('is-reviewing')))
    assert.equal(state.reportChatRatio,0.52)
    tree.find(e=>e.props.onRailOpenChange).props.onRailOpenChange(false)
    assert.ok(!h.render(props).some(e=>e.type==='section'&&e.props.className.includes('is-reviewing')))
  }finally{h.dispose()}
})

test('revision cards offer accept reject and follow-up without changing the frozen old quote',async()=>{
  const c={id:'c',session_id:'s',report_id:'r',anchor:{quote:'Original A',start_line:1,end_line:1},text:'Make this clearer.'}
  const suggestion={id:'g',decision:'pending',comments:[c],results:[],patches:[{before:'A',after:'B'}]}
  const decisions:string[]=[]
  const cardState:any={reportCommentDrafts:{},setReportCommentDraft(key:string,value:any){if(value)this.reportCommentDrafts[key]=value;else delete this.reportCommentDrafts[key]}}
  const h=await harness('report-review-card','CommentCard',{state:cardState})
  try{
    const props={comment:c,index:0,active:false,history:true,suggestion,latest:true,onFocus(){},onDecision:async(d:string)=>{decisions.push(d)},onPrepareFollowUp:async()=>({current_text:'Candidate B',deleted:false}),onFollowUp:async()=>{}}
    let tree=h.render(props)
    const accept=tree.find(e=>e.props['aria-label']==='chat.review.accept')
    assert.ok(accept)
    assert.ok(tree.find(e=>e.props['aria-label']==='chat.review.reject'))
    await accept.props.onClick();await h.flush()
    assert.deepEqual(decisions,['accepted'])
    tree=h.render(props)
    await tree.find(e=>e.props['aria-label']==='chat.review.followUp').props.onClick();await h.flush();tree=h.render(props)
    assert.equal(tree.find(e=>e.props.className==='pm-review-quote').props.children,'Original A')
    assert.ok(tree.some(e=>e.props['data-followup-quote']===true&&e.props.children==='Candidate B'))
    const accepted=h.render({...props,suggestion:{...suggestion,decision:'accepted'}})
    assert.ok(!accepted.find(e=>e.props['aria-label']==='chat.review.accept'))
    assert.ok(accepted.find(e=>e.props['aria-label']==='chat.review.followUp'))
  }finally{h.dispose()}
})

test('every revision card exposes its own comparison even without earlier discussion rounds',async()=>{
  const comment={id:'sample-comment',session_id:'s',report_id:'r',anchor:{quote:'Original.',start_line:1,end_line:1},text:'Clarify this.'}
  const h=await harness('report-review-card','CommentCard',{state:{reportCommentDrafts:{}}})
  try{
    for(const decision of ['pending','accepted','rejected']){
      const suggestion={id:'sample-group',decision,comments:[comment],results:[],patches:[{before:'Original.',after:'Revised.'}]}
      const tree=h.render({comment,index:0,active:false,history:true,suggestion,rounds:[],onFocus(){}})
      assert.ok(tree.some(e=>e.props.round===suggestion),`${decision} revisions need a comparison entry`)
    }
  }finally{h.dispose()}
})

test('an embedded follow-up focuses once without bubbling into the parent card',async()=>{
  const comment={id:'followup',session_id:'s',report_id:'r',text:'Explain the reason.',anchor:{quote:'Candidate.'},state:'pending'}
  let focused=0,stopped=0
  const h=await harness('report-review-card','CommentCard',{state:{reportCommentDrafts:{}}})
  try{
    const tree=h.render({comment,index:0,active:false,embedded:true,onFocus(){focused++}})
    tree.find(e=>e.type==='section').props.onClick({target:{closest(){return null}},stopPropagation(){stopped++}})
    assert.equal(focused,1)
    assert.equal(stopped,1)
    assert.ok(!tree.some(e=>e.props.className==='pm-review-quote'),'Embedded replies do not repeat the selected passage')
  }finally{h.dispose()}
})

test('a pending follow-up shares its parent revision card only when both views are open',async()=>{
  const original={id:'original',session_id:'s',report_id:'r',revision:1,text:'Clarify this.',state:'resolved',anchor:{start_line:3,end_line:3,quote:'Original.'}}
  const followup={...original,id:'followup',revision:2,text:'Explain the reason.',state:'pending',parent_group_id:'parent',thread_id:'original',current_text:'Candidate.'}
  const suggestion={id:'parent',thread_ids:['original'],decision:'pending',base_revision:1,result_revision:2,comments:[original],results:[],patches:[{field:'markdown',start:10,end:20,before:'Original.',after:'Candidate.'}],citations:[]}
  const data={...report,id:'r',session_id:'s',revision:2,markdown:'Opening.\n\nCandidate.',citations:[],suggestions:[suggestion]}
  const h=await harness('report-review-panel','ReportReviewPanel',{getReportComments:async()=>[followup],getReportReviewHistory:async()=>[],refreshReportComments:async()=>{}})
  try{
    const props={report:data}
    h.render(props);await h.flush()
    const toggle=(key:string)=>h.render(props).find(e=>e.props['aria-label']===key)!.props.onClick()
    toggle('chat.review.comments')
    let cards=h.render(props).filter(e=>e.props.comment)
    assert.equal(cards.length,1,'Linked follow-ups belong inside the parent card')
    assert.equal(cards[0].props.suggestion.id,'parent')
    assert.deepEqual(cards[0].props.followUps.map((c:any)=>c.id),['followup'])
    let rail=h.render(props).find(e=>e.props.entries)
    assert.deepEqual(rail.props.entries.map((entry:any)=>entry.id),['history-parent'])
    toggle('chat.review.track')
    cards=h.render(props).filter(e=>e.props.comment)
    assert.deepEqual(cards.map(e=>e.props.comment.id),['followup'])
    assert.equal(cards[0].props.history,false)
    toggle('chat.review.track');toggle('chat.review.comments')
    cards=h.render(props).filter(e=>e.props.comment)
    assert.deepEqual(cards.map(e=>e.props.comment.id),['original'])
    assert.equal(cards[0].props.followUps?.length||0,0)
  }finally{h.dispose()}
})

test('a reply-only discussion stays with its still-pending proposal and retains its decision controls',async()=>{
  const comment={id:'original',session_id:'s',report_id:'r',revision:1,text:'Clarify this.',state:'resolved',anchor:{start_line:1,end_line:1,quote:'Original.'}}
  const proposal={id:'proposal',thread_ids:['original'],decision:'pending',base_revision:1,result_revision:2,comments:[comment],results:[{summary:'Clarified the wording.'}],summary:'Clarified the wording.',patches:[{field:'markdown',start:0,end:10,before:'Original.',after:'Candidate.'}],citations:[]}
  const reply={...proposal,id:'reply',decision:'answered',base_revision:2,result_revision:2,comments:[{...comment,id:'question',text:'Why this wording?',parent_group_id:'proposal',thread_id:'original'}],patches:[],results:[{summary:'The source supports this wording.'}],summary:'The source supports this wording.'}
  const data={...report,id:'r',session_id:'s',revision:2,markdown:'Candidate.',citations:[],suggestions:[proposal,reply]}
  const h=await harness('report-review-panel','ReportReviewPanel',{getReportComments:async()=>[],getReportReviewHistory:async()=>[]})
  try{
    const props={report:data}
    h.render(props);await h.flush()
    const cards=h.render(props).filter(e=>e.props.comment)
    assert.equal(cards.length,1,'An answer without another text edit is still the same discussion')
    assert.equal(cards[0].props.suggestion.id,'proposal')
    assert.equal(cards[0].props.comment.text,'Why this wording?')
    assert.equal(cards[0].props.outcome.summary,'The source supports this wording.')
    assert.deepEqual(cards[0].props.rounds.map((round:any)=>round.id),['proposal'])
    assert.equal(typeof cards[0].props.onDecision,'function')
  }finally{h.dispose()}
})

test('a late comment refresh cannot replace a follow-up anchor after its parent was rejected',async()=>{
  const original={id:'original',session_id:'s',report_id:'r',revision:1,text:'Revise this.',state:'resolved',anchor:{start_line:3,end_line:3,quote:'Original.'}}
  const followup={...original,id:'followup',revision:2,text:'Explain why.',state:'pending',parent_group_id:'parent',thread_id:'original',anchor:{start_line:3,end_line:3,quote:'Candidate.'}}
  const group={id:'parent',thread_ids:['original'],decision:'pending',base_revision:1,result_revision:2,comments:[original],results:[],patches:[{field:'markdown',start:10,end:20,before:'Original.',after:'Candidate.'}],citations:[]}
  const first={...report,id:'r',session_id:'s',revision:2,markdown:'Opening.\n\nCandidate.',citations:[],suggestions:[group]}
  const updated={...first,revision:3,markdown:'Opening.\n\nOriginal.',suggestions:[{...group,decision:'rejected'}]}
  const relocated={...followup,revision:3,anchor:original.anchor}
  let hold=false,release!:(rows:any[])=>void
  const h=await harness('report-review-panel','ReportReviewPanel',{
    getReportComments:async(_sid:string,_rid:string,revision:number)=>revision===3?[relocated]:hold?new Promise(resolve=>{release=resolve}):[followup],
    getReportReviewHistory:async()=>[],editReportComment:async()=>followup,refreshReportComments:async()=>{},
  })
  try{
    h.render({report:first});await h.flush()
    h.render({report:first}).find(e=>e.props['aria-label']==='chat.review.comments').props.onClick()
    const parent=h.render({report:first}).find(e=>e.props.suggestion)
    hold=true
    const saving=parent.props.onSaveFollowUp(followup,'Explain the reason more clearly.')
    await new Promise(setImmediate)
    h.render({report:updated});await h.flush()
    release([followup]);await saving
    const card=h.render({report:updated}).find(e=>e.props.suggestion)
    assert.equal(card.props.followUps[0].revision,3)
    assert.equal(card.props.followUps[0].anchor.quote,'Original.')
  }finally{h.dispose()}
})

test('an answer without text edits follows its verified block instead of moving to the top',async()=>{
  const comment={id:'question',session_id:'s',report_id:'r',revision:2,text:'Why this statement?',state:'resolved',anchor:{block_id:'explanation',start_line:3,end_line:3,quote:'Explanation.'}}
  const group={id:'answer',thread_ids:['question'],decision:'answered',base_revision:2,result_revision:2,comments:[comment],results:[{summary:'The evidence supports the statement.'}],patches:[],citations:[]}
  const data={...report,id:'r',session_id:'s',revision:3,markdown:'Opening.\n\nNew context.\n\nExplanation.',citations:[],suggestions:[group],blocks:[{id:'explanation',start_line:5,end_line:5}]}
  const h=await harness('report-review-panel','ReportReviewPanel',{getReportComments:async()=>[],getReportReviewHistory:async()=>[]})
  try{
    h.render({report:data});await h.flush()
    h.render({report:data}).find(e=>e.props['aria-label']==='chat.review.track').props.onClick()
    const rail=h.render({report:data}).find(e=>e.props.entries)
    assert.equal(rail.props.entries[0].targetLine,5)
  }finally{h.dispose()}
})

test('a card decision updates the report body and history while keeping the decision bound to its suggestion',async()=>{
  const c={id:'old',state:'resolved',session_id:'s',report_id:'r',revision:1,text:'Delete this.',anchor:{start_line:1,end_line:1,quote:'Old',prefix:'',suffix:''}}
  const g={id:'g',thread_ids:['old'],decision:'pending',comments:[c],results:[{comment_id:'old',status:'applied',summary:'Removed the passage.',edit_ids:['e']}],patches:[{field:'markdown',start:0,end:0,before:'Old',after:''}],citations:[]}
  const data={...report,id:'r',session_id:'s',revision:2,markdown:'Body',citations:[],suggestions:[g]}
  const updated={...data,revision:3,markdown:'OldBody',suggestions:[{...g,decision:'rejected'}]}
  let saved:any,request:any,bumps=0
  const state={reportCommentDrafts:{},reportComments:{},reportCommentFocus:null,bumpReportHistory(){bumps++}}
  const h=await harness('report-review-panel','ReportReviewPanel',{state,getReportComments:async()=>[],getReportReviewHistory:async()=>[],decideReportSuggestion:async(...args:any[])=>{request=args;return updated},refreshReportComments:async()=>{}})
  try{
    const props={report:data,onReportChange:(value:any)=>{saved=value}}
    h.render(props);await h.flush();const tree=h.render(props)
    assert.equal(tree.find(e=>e.props['aria-label']==='chat.review.track').props['aria-pressed'],true)
    const card=tree.find(e=>e.props.suggestion)
    await card.props.onDecision('rejected','decision-id')
    assert.deepEqual(request,['s','r','g',2,'rejected','decision-id'])
    assert.equal(saved.markdown,'OldBody');assert.equal(bumps,1)
  }finally{h.dispose()}
})

test('a late decision response cannot replace a different report that was opened meanwhile',async()=>{
  const h=await harness('report-viewer','ReportViewer',{getChatReportVersions:async(_sid:string,rid:string)=>[{...report,id:rid,revision:2}],getChatReport:async(_sid:string,rid:string)=>({...report,id:rid,revision:2,markdown:rid,citations:[]}),getChatReportExport:async()=>({status:'idle'})})
  try{
    const props={report:{...report,id:'old'},onClose(){}}
    h.render(props);await h.flush();let tree=h.render(props)
    const oldPanel=tree.find(e=>e.props.onReportChange)
    const next={...props,report:{...report,id:'new'}}
    h.render(next);await h.flush();tree=h.render(next)
    assert.equal(tree.find(e=>e.props.onReportChange).props.report.id,'new')
    oldPanel.props.onReportChange({...report,id:'old',revision:3,markdown:'late response',citations:[]})
    assert.equal(h.render(next).find(e=>e.props.onReportChange)?.props.report.id,'new')
  }finally{h.dispose()}
})

test('instruction acceptance is nested with only its location list and remains separate from per-location actions',async()=>{
  const comment={id:'c',session_id:'s',report_id:'r',anchor:{quote:'amber'},text:'Replace amber throughout.'}
  const first={id:'one',instruction_id:'terms',decision:'pending',location_quote:'Amber parcel.',comments:[comment],patches:[{before:'amber',after:'violet'}]}
  const second={...first,id:'two',location_quote:'Amber crate.'}
    let accepted=0,local=0
  const h=await harness('report-review-card','CommentCard',{state:{reportCommentDrafts:{}}})
  try{
    const props={comment,index:0,active:true,history:true,suggestion:first,relatedSuggestions:[first,second],onFocus(){},onDecision:async()=>{local++},onAcceptInstruction:async()=>{accepted++}}
    const tree=h.render(props)
    const bounded=tree.find(e=>e.props['data-instruction-id']==='terms')
    assert.equal(bounded.type,'details')
    const inGroup=elements(bounded)
    assert.equal(inGroup.filter(e=>e.type==='li').length,2)
    const batch=inGroup.find(e=>e.props.className==='pm-review-instruction-accept')
    assert.ok(batch)
    assert.ok(!inGroup.some(e=>e.props['aria-label']==='chat.review.accept'))
    await batch.props.onClick();await h.flush()
    assert.equal(accepted,1);assert.equal(local,0)
    await tree.find(e=>e.props['aria-label']==='chat.review.reject').props.onClick();await h.flush()
    assert.equal(local,1)
    assert.ok(!h.render({...props,relatedSuggestions:[{...first,decision:'accepted'},second]}).some(e=>e.props.className==='pm-review-instruction-accept'),'One remaining location already has its own accept button')
  }finally{h.dispose()}
})

test('each instruction location remains a separate panel card and batch decisions carry explicit instruction scope',async()=>{
  const comment={id:'c',state:'resolved',session_id:'s',report_id:'r',revision:1,text:'Use violet throughout.',anchor:{start_line:1,end_line:1,quote:'Amber',prefix:'',suffix:''}}
  const group=(id:string,start:number)=>({id,location_id:id,instruction_id:'terms',parent_ids:[],thread_ids:['c'],decision:'pending',comments:[comment],results:[],patches:[{field:'markdown',start,end:start+6,before:'Amber',after:'Violet'}],citations:[]})
  const data={...report,id:'r',session_id:'s',revision:2,markdown:'Violet parcel.\n\nViolet crate.',citations:[],suggestions:[group('one',0),group('two',16),{...group('other',0),instruction_id:'other-instruction'}]}
  let request:any
  const h=await harness('report-review-panel','ReportReviewPanel',{state:{reportCommentDrafts:{},reportComments:{},bumpReportHistory(){}},getReportComments:async()=>[],getReportReviewHistory:async()=>[],decideReportSuggestion:async(...args:any[])=>{request=args;return data},refreshReportComments:async()=>{}})
  try{
    const props={report:data,onReportChange(){}}
    h.render(props);await h.flush()
    const cards=h.render(props).filter(e=>e.props.suggestion)
    assert.equal(cards.length,3)
    assert.deepEqual(cards[0].props.relatedSuggestions.map((g:any)=>g.id),['one','two'])
    await cards[0].props.onAcceptInstruction('instruction-op')
    assert.deepEqual(request,['s','r','one',2,'accepted','instruction-op','instruction'])
    await cards[1].props.onDecision('rejected','location-op')
    assert.deepEqual(request,['s','r','two',2,'rejected','location-op'])
  }finally{h.dispose()}
})
