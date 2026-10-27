import assert from "node:assert/strict"
import { after, before, test } from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { createServer } from "vite"
import { fileURLToPath } from "node:url"

let server: Awaited<ReturnType<typeof createServer>>
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage")
before(async () => {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: undefined })
  const root = fileURLToPath(new URL("../", import.meta.url))
  server = await createServer({
    root,
    configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: { alias: { "@": root + "src", "#edition": root + "src/edition" } },
    optimizeDeps: { noDiscovery: true },
  })
})
after(async () => {
  await server?.close()
  if (originalStorage) Object.defineProperty(globalThis, "localStorage", originalStorage)
  else Reflect.deleteProperty(globalThis, "localStorage")
})

test('historical redlines render a numeric correction inside one rich-text paragraph',async()=>{
  const {AnswerMarkdown}=await server.ssrLoadModule('/src/components/chat/answer-markdown.tsx')
  const html=renderToStaticMarkup(createElement(AnswerMarkdown,{source:'Stock is **36 units**. Keep this sentence.',previousSource:'Stock is **24 units**. Keep this sentence.'}))
  assert.match(html,/<del[^>]*><strong>24<\/strong><\/del>/)
  assert.match(html,/<ins[^>]*><strong>36<\/strong><\/ins>/)
  assert.equal(html.split('Keep this sentence.').length-1,1)
})

test('the report tracking surface renders a small correction in the original paragraph',async()=>{
  const {ReportTrackedBody}=await server.ssrLoadModule('/src/components/chat/report-tracked-body.tsx')
  const markdown='The sample is ready for inspection.'
  const html=renderToStaticMarkup(createElement(ReportTrackedBody,{tracking:true,report:{
    id:'report-test',session_id:'session-test',markdown,citations:[],blocks:[{id:'block-test',kind:'paragraph',start:0,end:markdown.length,start_line:1,end_line:1,hash:'sample'}],
    suggestions:[{id:'change-test',decision:'pending',citations:[],patches:[{field:'markdown',start:14,end:19,before:'queued',after:'ready'}]}],
  }}))
  assert.equal(html.split('The sample is ').length-1,1)
  assert.match(html,/<del[^>]*>queued<\/del><ins[^>]*>ready<\/ins>/)
  assert.equal((html.match(/<p\b/g)||[]).length,1)
})

test('a report artifact keeps its complete accessible title and last-updated date',async()=>{
  const {ReportCards}=await server.ssrLoadModule('/src/components/chat/report-cards.tsx')
  const title='A long investment report title that should stay accessible in a narrow chat'
  const html=renderToStaticMarkup(createElement(ReportCards,{reports:[{
    id:'r',session_id:'s',title,revision:3,updated_at:'2026-10-22T05:34:00Z',
  }]}))
  assert.ok(html.includes(`title="${title}"`))
  assert.match(html,/<time[^>]*dateTime="2026-10-22T05:34:00Z"/)
  assert.match(html,/data-kind="report"/)
  assert.doesNotMatch(html,/Invalid Date/)
})

test('asset rows distinguish file kinds and show the uploaded file size',async()=>{
  const {ConversationAssetsPanel}=await server.ssrLoadModule('/src/components/chat/conversation-assets.tsx')
  const html=renderToStaticMarkup(createElement(ConversationAssetsPanel,{
    outputs:[],uploads:[{id:'pdf',filename:'Research.PDF',status:'ready',attachment:{
      id:'pdf',session_id:'s',filename:'Research.PDF',size:177152,status:'ready',
    }}],retrieved:[{id:'note',title:'Research note',evidence:{id:'e',kind:'note',title:'Research note',quote:'Text',source:{},location:{}}}],
    retrievedOpen:true,onToggleRetrieved(){},onOutput(){},onUpload(){},onSource(){},
  }))
  assert.match(html,/data-kind="pdf"/)
  assert.match(html,/data-kind="note"/)
  assert.match(html,/173 KB/)
  assert.ok(html.includes('Research.PDF'))
})

test('the comments pill exposes its count in the accessible name as well as the visible badge',async()=>{
  const {ReportReviewChip}=await server.ssrLoadModule('/src/components/chat/report-review-chip.tsx')
  const html=renderToStaticMarkup(createElement(ReportReviewChip,{history:true,comments:Array.from({length:3},(_,i)=>({
    id:`c${i}`,text:'Clarify this.',title:'Report',session_id:'s',report_id:'r',revision:1,
    anchor:{quote:'Passage',start_line:1,end_line:1},
  }))}))
  assert.match(html,/<button[^>]*aria-label="[^"]*3[^"]*"/)
  assert.match(html,/class="pm-review-chip-count"[^>]*>3</)
})

test("optional help is a named keyboard-focusable control, without inline explanatory copy", async () => {
  const { HelpHint } = await server.ssrLoadModule("/src/components/shared/help-hint.tsx")
  const html = renderToStaticMarkup(createElement(HelpHint, { label: "About model selection" }, "Supplementary explanation"))
  assert.match(html, /<button[^>]*type="button"[^>]*aria-label="About model selection"/)
  assert.doesNotMatch(html, /Supplementary explanation/)
})

test("assistant renders each changed Note after the answer and before Sources", async () => {
  const { MessageBubble } = await server.ssrLoadModule("/src/components/chat/message-bubble.tsx")
  const html = renderToStaticMarkup(createElement(MessageBubble, {
    message: {
      id: "answer", role: "assistant", content: "Saved answer",
      noteChanges: [
        { id: "note-a", title: "First Note", collection: "col-a" },
        { id: "note-b", title: "Second Note", collection: "col-b" },
      ],
      sources: [{ text: "Source excerpt", score: 1, metadata: { source: "source.pdf" } }],
    },
  }))
  assert.match(html, /<button[^>]*data-note-id="note-a"/)
  assert.match(html, /<button[^>]*data-note-id="note-b"/)
  assert.ok(html.indexOf("Saved answer") < html.indexOf("First Note"))
  assert.ok(html.indexOf("Second Note") < html.indexOf("pm-chat-sources"))
})

test("ordinary answers render sentence-adjacent source buttons and a saved report card", async () => {
  const { MessageBubble } = await server.ssrLoadModule("/src/components/chat/message-bubble.tsx")
  const html = renderToStaticMarkup(createElement(MessageBubble, { message: {
    id: "answer", role: "assistant", content: "Supported fact.[cite:e_a]",
    citations: [{ id: "e_a", title: "Reference.pdf", kind: "document", quote: "Original fact.", source: { file_id: "a" }, location: { page_number: 2 } }],
    reportChanges: [{ id: "report-a", session_id: "s", title: "Saved report", revision: 1 }],
  } }))
  assert.match(html, /<button[^>]*class="pm-citation"[^>]*>1<\/button>/)
  assert.doesNotMatch(html, /\[cite:e_a\]|#evidence-e_a/)
  assert.match(html, /Saved report/)
})

test('report reading distinguishes its opening title, subheadings and figure captions without shifting source anchors',async()=>{
  const {AnswerMarkdown}=await server.ssrLoadModule('/src/components/chat/answer-markdown.tsx')
  const source='# Draft title\n\n**A focused introduction**\n\n## Analysis\n\n**Funding details**\n\nParagraph text.\n\n来源：原始资料。'
  const html=renderToStaticMarkup(createElement(AnswerMarkdown,{source,report:true,reviewAnchors:true}))
  assert.match(html,/data-report-line="1"[^>]*class="chat-report-title-anchor"[^>]*hidden/)
  assert.doesNotMatch(html,/<h1\b/)
  assert.match(html,/class="chat-report-deck"/)
  assert.match(html,/class="chat-report-subheading"/)
  assert.match(html,/class="chat-report-caption"/)
  assert.match(html,/data-report-line="5"/)
})

test('report diagrams use named icon controls rather than a row of raw SVG labels',async()=>{
  const {AnswerMarkdown}=await server.ssrLoadModule('/src/components/chat/answer-markdown.tsx')
  const html=renderToStaticMarkup(createElement(AnswerMarkdown,{source:'```svg\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><rect width="20" height="20"/></svg>\n```',report:true}))
  assert.match(html,/chat-md-visual--report/)
  assert.match(html,/<button[^>]+aria-label=/)
  assert.doesNotMatch(html,/>SVG<|>源码<|>复制<|>预览</)
  assert.doesNotMatch(html,/<code class="language-svg"/)
})

test('report paragraphs expose their server block identity without putting it in visible text',async()=>{
  const {AnswerMarkdown}=await server.ssrLoadModule('/src/components/chat/answer-markdown.tsx')
  const source='Repeated.\n\nRepeated.'
  const html=renderToStaticMarkup(createElement(AnswerMarkdown,{source,reviewAnchors:true,blocks:[
    {id:'b_a',hash:'first-hash',start_line:1,end_line:1},
    {id:'b_b',hash:'second-hash',start_line:3,end_line:3},
  ]}))
  assert.match(html,/<p[^>]*data-report-block="b_a"[^>]*data-report-hash="first-hash"[^>]*>Repeated\.<\/p>/)
  assert.match(html,/<p[^>]*data-report-block="b_b"[^>]*data-report-hash="second-hash"[^>]*>Repeated\.<\/p>/)
  assert.doesNotMatch(html,/>b_a<|>first-hash</)
})

test("a streaming tail keeps citation numbering from the complete answer", async () => {
  const { CitationScope, CitationText } = await server.ssrLoadModule("/src/components/chat/citation-scope.tsx")
  const citations = ["a", "b"].map((id) => ({ id: "e_" + id, title: id, kind: "document", quote: "Fact", source: {}, location: {} }))
  const html = renderToStaticMarkup(createElement(CitationScope, { citations, sessionId: "s", source: "First[cite:e_a]\nSecond[cite:e_b]" },
    createElement(CitationText, { source: "Second[cite:e_b]", streaming: true })))
  assert.match(html, /<button[^>]*class="pm-citation"[^>]*>2<\/button>/)
})

test("citation excerpts render managed images and Markdown tables using the saved source identity", async () => {
  const { CitationExcerpt } = await server.ssrLoadModule("/src/components/chat/citation-scope.tsx")
  assert.equal(typeof CitationExcerpt, "function", "The citation dialog needs a rich original excerpt renderer")
  const html = renderToStaticMarkup(createElement(CitationExcerpt, { evidence: {
    id: "e_a", kind: "document", title: "Reference.pdf", source: { collection: "col-a", file_id: "file-a" }, location: { page_number: 3 },
    quote: ":::image\nimage_id: figure-a\nfile_id:\n:::\n\n| Project | Value |\n| --- | --- |\n| Example | 42 |",
  } }))
  assert.match(html, /<img\b[^>]*src="[^\"]*\/api\/documents\/col-a\/file-a\/images\/figure-a/)
  assert.match(html, /<table\b/)
  assert.match(html, /<td>42<\/td>/)
  assert.doesNotMatch(html, /:::image|image_id:|file_id:/)
})

test('a Sources excerpt clipped inside image metadata still renders the original image', async () => {
  const { CitationExcerpt } = await server.ssrLoadModule('/src/components/chat/citation-scope.tsx')
  const quote = (':::image\nimage_id: diagram-a\nfile_id: file-a\nocr_text: Original diagram text\ndescription: ' + 'Document details. '.repeat(350)).slice(0,4000)
  const html = renderToStaticMarkup(createElement(CitationExcerpt,{evidence:{
    id:'clipped',kind:'document',title:'Research.pdf',source:{collection:'col-a',file_id:'file-a'},location:{page_number:6},quote,
  }}))
  assert.match(html,/<img\b[^>]*src="[^\"]*\/api\/documents\/col-a\/file-a\/images\/diagram-a/)
  assert.doesNotMatch(html,/:::image|image_id:|file_id:/)
  assert.match(html,/<button[^>]*aria-expanded="false"/)
  assert.match(html,/<span[^>]*hidden=""[^>]*>/)
})

test('successive clipped image blocks retain separate image identities and preserve surrounding prose', async () => {
  const { CitationExcerpt } = await server.ssrLoadModule('/src/components/chat/citation-scope.tsx')
  const html = renderToStaticMarkup(createElement(CitationExcerpt,{evidence:{
    id:'two',kind:'document',title:'Research.pdf',source:{collection:'col-a',file_id:'file-a'},location:{},
    quote:'Original paragraph.\n\n:::image\nimage_id: first\nfile_id: file-a\ndescription: First diagram\n:::image\nimage_id: second\nfile_id: file-a\ndescription: Second diagram',
  }}))
  assert.match(html,/Original paragraph\./)
  assert.match(html,/\/images\/first/)
  assert.match(html,/\/images\/second/)
  assert.doesNotMatch(html,/:::image|image_id:/)
})

test("rich citation excerpts retain original text while rejecting active source HTML", async () => {
  const { CitationExcerpt } = await server.ssrLoadModule("/src/components/chat/citation-scope.tsx")
  assert.equal(typeof CitationExcerpt, "function")
  const html = renderToStaticMarkup(createElement(CitationExcerpt, { evidence: {
    id: "e_a", kind: "document", title: "Reference", source: {}, location: {},
    quote: '**Original fact.**\n\n<script>window.__quoteAttack=true</script><iframe srcdoc="active"></iframe>',
  } }))
  assert.match(html, /<strong>Original fact\.<\/strong>/)
  assert.doesNotMatch(html, /<script|<iframe|srcdoc=|__quoteAttack/)
})

test("the file-detail text viewer highlights the exact cited range while preserving the full source", async () => {
  const { ParseTextViewer } = await server.ssrLoadModule("/src/components/file-mgmt/file-detail/parse-text-viewer.tsx")
  const html = renderToStaticMarkup(createElement(ParseTextViewer, {
    text: "Prefix. Original fact. Suffix.", collectionId: "col-a", fileId: "file-a", highlightOffset: 8, highlightLength: 14,
  }))
  assert.match(html, /Prefix\. <mark[^>]*>Original fact\.<\/mark> Suffix\./)
})

test("report previews never fetch arbitrary Markdown image URLs", async () => {
  const { AnswerMarkdown } = await server.ssrLoadModule("/src/components/chat/answer-markdown.tsx")
  const html = renderToStaticMarkup(createElement(AnswerMarkdown, { source: "![Remote image](https://invalid.test/tracker.png)", restrictImages: true }))
  assert.doesNotMatch(html, /<img|src="https:/)
  assert.match(html, /Remote image/)
})

test("attachment tools have readable labels in the Chat process trail", async () => {
  const { MessageBubble } = await server.ssrLoadModule("/src/components/chat/message-bubble.tsx")
  const html = renderToStaticMarkup(createElement(MessageBubble, {
    message: { id: "answer", role: "assistant", content: "Answer", timeline: [
      { id: "read", type: "tool", tool: "read_chat_attachment", toolStatus: "done", toolResult: "Read the file" },
    ] },
  }))
  assert.doesNotMatch(html, />read_chat_attachment</)
})

const attachment = {
  id: "file-a", session_id: "session-a", filename: "brief.pdf", size: 177152,
  status: "ready", parser: "local", error: "", bound: true,
  warnings: ["MinerU failed; local parsing is used when available."],
  characters: 100, lines: 5, image_count: 0, created_at: "2026-10-19",
}

test("sent attachment cards offer preview and keep parsing notices in collapsed details", async () => {
  const { ChatAttachmentCards } = await server.ssrLoadModule("/src/components/chat/chat-attachments.tsx")
  const html = renderToStaticMarkup(createElement(ChatAttachmentCards, { attachments: [attachment] }))
  assert.match(html, /<button[^>]*aria-haspopup="dialog"/)
  assert.match(html, /PDF · 173 KB/)
  assert.match(html, /<details[^>]*>[\s\S]*<summary/)
  assert.doesNotMatch(html, /<details[^>]*\bopen(?:=|\s|>)/)
  const previewButton = html.match(/<button\b[^>]*>[\s\S]*?<\/button>/)?.[0] ?? ""
  assert.doesNotMatch(previewButton, /MinerU|Download|Parsing details/)
  assert.doesNotMatch(html, /MinerU failed; local parsing is used when available/)
})

test("file-only user messages have no empty green text bubble", async () => {
  const { MessageBubble } = await server.ssrLoadModule("/src/components/chat/message-bubble.tsx")
  const html = renderToStaticMarkup(createElement(MessageBubble, {
    message: { id: "file-only", role: "user", content: "", attachments: [attachment] },
  }))
  assert.match(html, /brief\.pdf/)
  assert.doesNotMatch(html, /pm-chat-msg-user-body/)
})

test("a user's attachment sits after the green text bubble", async () => {
  const { MessageBubble } = await server.ssrLoadModule("/src/components/chat/message-bubble.tsx")
  const html = renderToStaticMarkup(createElement(MessageBubble, {
    message: { id: "file-and-text", role: "user", content: "Review this", attachments: [attachment] },
  }))
  assert.match(html, /class="pm-chat-msg-user-body"><p>Review this<\/p><\/div><div class="pm-attachments/)
})

test("a failed attachment never claims that local parsing completed", async () => {
  const { ChatAttachmentDrafts } = await server.ssrLoadModule("/src/components/chat/chat-attachments.tsx")
  const html = renderToStaticMarkup(createElement(ChatAttachmentDrafts, {
    manager: { drafts: [{ key: "failed", filename: "legacy.doc", status: "failed", error: "No local parser available",
      attachment: { ...attachment, status: "failed", parser: "" } }] },
  }))
  assert.match(html, /role="alert">No local parser available/)
  assert.match(html, /<details/)
  assert.doesNotMatch(html, /Local parsing completed|Parsed with/)
})

test("Chat source chunks preview a complete ingested SVG as PNG with searchable labels", async () => {
  const { SourceChunkBody } = await server.ssrLoadModule("/src/components/chat/source-detail-panel.tsx")
  assert.equal(typeof SourceChunkBody, "function", "Source chunks need the shared image renderer")
  const html = renderToStaticMarkup(createElement(SourceChunkBody, {
    chunk: { text: ":::image\nimage_id: svg-a\nfile_id: file-note\nocr_text: 投资人 A → Entity B\ndescription: Financing route\n:::\n", source: "__note__:note-a" },
    collection: "col-a",
  }))
  assert.match(html, /<img\b/)
  assert.match(html, /\/api\/documents\/col-a\/file-note\/images\/svg-a/)
  assert.match(html, /投资人 A/)
  assert.match(html, /Financing route/)
  assert.doesNotMatch(html, /:::image|&lt;svg/)
})

async function render(content: string, options: {
  richContent?: boolean
  className?: string
  isStreaming?: boolean
} = { richContent: true, className: "pm-chat-prose" }) {
  const { StreamingAnswerBody } = await server.ssrLoadModule("/src/components/chat/streaming-answer-body.tsx")
  return renderToStaticMarkup(createElement(StreamingAnswerBody, {
    content,
    isStreaming: false,
    ...options,
  }))
}

test("Markdown tables get their own scroll container", async () => {
  const html = await render("| Item | Value |\n| --- | --- |\n| 中文 | 123 |")
  assert.match(html, /class="[^"]*chat-md-table-wrap/)
  assert.match(html, /<table/)
  assert.match(html, /<th>.*?Item.*?<\/th>/)
  assert.match(html, /<td>.*?中文.*?<\/td>/)
})

test("complete SVG fences produce an image preview instead of plain code", async () => {
  const html = await render("Before\n\n```svg\n<svg xmlns=\"http://www.w3.org/2000/svg\"><text>中 English</text></svg>\n```\n\nAfter")
  assert.match(html, /chat-md-visual/)
  assert.match(html, /<img/)
  assert.match(html, /<p>Before<\/p>/)
  assert.match(html, /<p>After<\/p>/)
})

test("Quick Chat previews completed SVGs with its compact answer layout", async () => {
  const { QuickChatAnswer } = await server.ssrLoadModule("/src/components/database/quick-chat.tsx")
  assert.equal(typeof QuickChatAnswer, "function", "Quick Chat needs an answer renderer with SVG preview")
  const html = renderToStaticMarkup(createElement(QuickChatAnswer, {
    content: 'Before\n\n```svg\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><text>图</text></svg>\n```\n\nAfter',
    isStreaming: false,
  }))
  assert.match(html, /pm-qc-answer/)
  assert.match(html, /chat-md-visual/)
  assert.match(html, /<img\b/)
  assert.match(html, /<p>Before<\/p>/)
  assert.match(html, /<p>After<\/p>/)
  assert.match(html, /Source<\/button>/)
  assert.match(html, /chat-svg-expand-button/)
  assert.doesNotMatch(html, /<svg[^>]*xmlns=|&lt;svg|language-svg/)
})

test("Quick Chat hides unfinished SVG source while keeping surrounding answer text", async () => {
  const { QuickChatAnswer } = await server.ssrLoadModule("/src/components/database/quick-chat.tsx")
  assert.equal(typeof QuickChatAnswer, "function", "Quick Chat needs an SVG-aware streaming renderer")
  const html = renderToStaticMarkup(createElement(QuickChatAnswer, {
    content: 'Before\n\n```svg\n<svg><polyline points="1,2', isStreaming: true,
  }))
  assert.match(html, /Before/)
  assert.doesNotMatch(html, /polyline|points=|&lt;svg|```svg|<img\b/)
})

test("Quick Chat keeps ordinary code and tables when SVG previews are enabled", async () => {
  const { QuickChatAnswer } = await server.ssrLoadModule("/src/components/database/quick-chat.tsx")
  assert.equal(typeof QuickChatAnswer, "function", "Quick Chat needs its shared answer renderer")
  const html = renderToStaticMarkup(createElement(QuickChatAnswer, {
    content: '```python\nprint("Hello")\n```\n\n| Item | Value |\n| --- | --- |\n| A | 2 |', isStreaming: false,
  }))
  assert.match(html, /language-python/)
  assert.match(html, /Hello/)
  assert.match(html, /chat-md-table-wrap/)
  assert.match(html, /<td><div[^>]*>2<\/div><\/td>/)
})

test("model SVG stays out of the HTML document", async () => {
  const html = await render("```svg\n<svg onload=\"evil()\"><script>evil()</script></svg>\n```")
  assert.match(html, /chat-md-visual/)
  assert.doesNotMatch(html, /<script>evil\(\)<\/script>/)
  assert.doesNotMatch(html, /<svg[^>]*onload=/)
  assert.doesNotMatch(html, /<(?:iframe|object)\b/)
})

test("very tall SVG uses a bounded preview container", async () => {
  const html = await render("```svg\n<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"100000\" />\n```")
  assert.match(html, /class="chat-md-visual-preview"/)
  assert.match(html, /<img/)
})

test("an unfinished SVG fence remains source and pending", async () => {
  const html = await render("```svg\n<svg xmlns=\"http://www.w3.org/2000/svg\" />")
  assert.match(html, /chat-md-visual/)
  assert.doesNotMatch(html, /<img/)
  assert.match(html, /chat-md-visual-status/)
  assert.match(html, /<button[^>]*aria-pressed="false"[^>]*>Source<\/button>/)
})

test("a legacy SVG with lost opening brackets can preview with a recovery notice", async () => {
  const source = '```svg\nsvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">\n  rect width="24" height="24" fill="white"/>\n  text x="2" y="12">中文 English</text>\n</svg>\n```'
  const html = await render(source)
  assert.match(html, /<img\b/)
  assert.match(html, /The preview was restored/)
  assert.doesNotMatch(html, /This diagram could not be previewed|<(?:svg|script|iframe|object)\b/)
})

test("the first streaming frame keeps unfinished SVG source out of the live prose tail", async () => {
  const source = 'Before diagram.\n\n```svg\n<svg xmlns="http://www.w3.org/2000/svg">\n<polyline points="10,10 20,20"'
  const html = await render(source, { richContent: true, className: "pm-chat-prose", isStreaming: true })
  assert.match(html, /Before diagram\./)
  assert.doesNotMatch(html, /polyline|points=|&lt;svg|```svg/)
})

test("ordinary code retains its source and language", async () => {
  const html = await render("```python\nprint('Hello 世界')\n```")
  assert.match(html, /chat-md-code-block/)
  assert.match(html, /python/)
  assert.match(html, /Hello 世界/)
})

test("raw HTML is escaped while heading, emphasis and list semantics survive", async () => {
  const html = await render("## Findings\n\n**Clear** answer\n\n1. First\n2. Second\n\n<script>evil()</script>")
  assert.match(html, /<h2>Findings<\/h2>/)
  assert.match(html, /<strong>Clear<\/strong>/)
  assert.match(html, /<ol>/)
  assert.doesNotMatch(html, /<script>evil\(\)<\/script>/)
})

test("a standalone bold lead line stays visually separate from its supporting paragraph", async () => {
  const source = "**结论：检查通过。**\n示例 A 共完成 3 项检查，结果为 **通过**。"
  const html = await render(source)
  assert.match(html, /<p class="chat-md-lead-paragraph"><strong>结论：检查通过。<\/strong>\n示例 A/)
  assert.match(html, /<strong>通过<\/strong>/)
  assert.equal((html.match(/<strong>/g) ?? []).length, 2)

  const { AnswerMarkdown } = await server.ssrLoadModule("/src/components/chat/answer-markdown.tsx")
  const streamed = renderToStaticMarkup(createElement(AnswerMarkdown, { source, streaming: true }))
  assert.ok(html.includes(streamed), "formatted streaming and completed paragraphs keep the same lead layout")
})

test("standalone leads support Windows line endings and ignore a whitespace-only continuation", async () => {
  const windows = await render("**Finding**\r\nSupporting text.")
  assert.match(windows, /<p class="chat-md-lead-paragraph"><strong>Finding<\/strong>/)
  assert.ok(windows.includes("Supporting text."))

  const empty = await render("**Finding**\n \t")
  assert.match(empty, /<strong>Finding<\/strong>/)
  assert.doesNotMatch(empty, /chat-md-lead-paragraph/)
})

test("inline emphasis and an ordinary line wrap do not become a standalone lead", async () => {
  for (const source of [
    "**Important** detail with **12%** emphasis.",
    "**Important** detail\nContinues on another line.",
    "A line ending in **emphasis**\nContinues normally.",
    "**A multiline\nstrong span**\nSupporting text.",
  ]) {
    const html = await render(source)
    assert.doesNotMatch(html, /chat-md-lead-paragraph/)
    assert.match(html, /<strong>/)
  }
})

test("standalone leads preserve links, emphasis and inline code in the supporting content", async () => {
  const html = await render("**Finding `API`**\n[Details](https://example.com) with *context* and **12%**.")
  assert.match(html, /<p class="chat-md-lead-paragraph"><strong>Finding <code>API<\/code><\/strong>/)
  assert.match(html, /<a href="https:\/\/example.com">Details<\/a>/)
  assert.match(html, /<em>context<\/em>/)
  assert.match(html, /<strong>12%<\/strong>/)
})

test("punctuation outside strong and explicit hard breaks retain their normal layout", async () => {
  const colon = await render("**Finding**:\nSupporting text.")
  assert.match(colon, /<strong>Finding<\/strong>:\nSupporting text\./)
  assert.doesNotMatch(colon, /chat-md-lead-paragraph/)

  const hardBreak = await render("**Finding**  \nSupporting text.")
  assert.match(hardBreak, /<strong>Finding<\/strong><br\/>\nSupporting text\./)
  assert.doesNotMatch(hardBreak, /chat-md-lead-paragraph/)

  const code = await render("```markdown\n**Finding**\nSupporting text.\n```")
  assert.match(code, /\*\*Finding\*\*\nSupporting text\./)
  assert.doesNotMatch(code, /chat-md-lead-paragraph/)
})

test("long emphasis is preserved and real headings keep their existing semantics", async () => {
  const phrase = "This complete sentence was explicitly emphasized by the author and must remain intact."
  const html = await render(`## **Finding**\n\nBefore **${phrase}** afterwards.`)
  assert.match(html, /<h2><strong>Finding<\/strong><\/h2>/)
  assert.ok(html.includes(`<strong>${phrase}</strong>`))
  assert.doesNotMatch(html, /chat-md-lead-paragraph/)
})

test("completed emphasis spacing matches streaming without changing literal code", async () => {
  const source = "A ** 重点 ** and `** literal **`."
  const html = await render(source)
  assert.match(html, /<strong>重点<\/strong>/)
  assert.match(html, /<code>\*\* literal \*\*<\/code>/)

  const { AnswerMarkdown } = await server.ssrLoadModule("/src/components/chat/answer-markdown.tsx")
  const streamed = renderToStaticMarkup(createElement(AnswerMarkdown, { source, streaming: true }))
  assert.ok(html.includes(streamed), "emphasis does not disappear when streaming finishes")
})

test("standalone bold lead layout is limited to the opted-in rich renderer", async () => {
  const html = await render("**Finding**\nSupporting text.", { richContent: false })
  assert.match(html, /<p><strong>Finding<\/strong>\nSupporting text\.<\/p>/)
  assert.doesNotMatch(html, /chat-md-lead-paragraph/)
})

test("the default renderer retains ordinary tables and code without rich content", async () => {
  const html = await render("Finding [ref:12].\n\n| Item | Value |\n| --- | --- |\n| 中文 | 123 |\n\n```svg\n<svg><text>[ref:12]</text></svg>\n```", {})
  assert.match(html, /^<div class="prose prose-sm /)
  assert.match(html, /<table>/)
  assert.match(html, /<pre><code class="language-svg">/)
  assert.match(html, /&lt;svg&gt;&lt;text&gt;&lt;\/text&gt;&lt;\/svg&gt;/)
  assert.doesNotMatch(html, /chat-answer-markdown|chat-md-table-wrap|chat-md-code-block|chat-md-visual|<img\b|\[ref:12\]/)
})

test("Quick Chat retains its existing classes when rich content is disabled", async () => {
  const className = "pm-qc-answer break-words [&_table]:text-[11px] [&_th]:px-1.5 [&_th]:py-0.5 [&_td]:px-1.5 [&_td]:py-0.5 [&_table]:block [&_table]:overflow-x-auto [&_pre]:text-[11px]"
  const html = await render("| Item | Value |\n| --- | --- |\n| 中文 | 123 |", { className, richContent: false })
  assert.match(html, /class="prose prose-sm /)
  assert.ok(html.includes(className.replaceAll("&", "&amp;")))
  assert.match(html, /<table>/)
  assert.doesNotMatch(html, /chat-answer-markdown|chat-md-table-wrap/)
})

test("the main Chat class alone does not opt into SVG preview", async () => {
  const html = await render("```svg\n<svg />\n```", { className: "pm-chat-prose" })
  assert.match(html, /^<div class="pm-chat-prose">/)
  assert.match(html, /<pre><code class="language-svg">/)
  assert.doesNotMatch(html, /chat-answer-markdown|chat-md-visual|<img\b/)
})

test("default streaming retains legacy citation cleanup", async () => {
  const html = await render("```text\n[ref:12] literal\n", { isStreaming: true })
  assert.match(html, /```text\n literal\n/)
  assert.doesNotMatch(html, /chat-answer-markdown|chat-md-visual|\[ref:12\]/)
})

test('report review renderer exposes original line anchors without changing ordinary answers', async () => {
  const { AnswerMarkdown } = await server.ssrLoadModule('/src/components/chat/answer-markdown.tsx')
    const html = renderToStaticMarkup(createElement(AnswerMarkdown, {source:'## Title\n\n**Bold** text.\n\n| A | B |\n| - | - |\n| 1 | 2 |', reviewAnchors:true}))
    assert.match(html, /data-report-line="3"/)
    assert.match(html, /data-report-line="5"/)
    const ordinary = renderToStaticMarkup(createElement(AnswerMarkdown, {source:'Normal reply'}))
    assert.doesNotMatch(ordinary, /data-report-line/)
})


test('report comment pill is navigation-only and history retains its submitted snapshot', async () => {
  const {ReportReviewChip} = await server.ssrLoadModule('/src/components/chat/report-review-chip.tsx')
  const c={id:'c1',text:'Rework the argument',title:'Review',report_id:'r',session_id:'s',revision:1,anchor:{quote:'Old text'},state:'pending'}
  const html=renderToStaticMarkup(createElement(ReportReviewChip,{comments:[c]}))
  assert.match(html,/Report comments · 1/)
  assert.doesNotMatch(html,/checkbox|Select all|Clear|Exclude/)
  assert.equal(renderToStaticMarkup(createElement(ReportReviewChip,{comments:[]})), '')
})


test("an image citation anchors the managed image itself in the parsed source", async () => {
  const { ParseTextViewer } = await server.ssrLoadModule("/src/components/file-mgmt/file-detail/parse-text-viewer.tsx")
  const text = "Before.\n\n:::image\nimage_id: img-a\nfile_id: file-a\n:::\n\nAfter."
  const html = renderToStaticMarkup(createElement(ParseTextViewer, {
    text, collectionId: "col-a", fileId: "file-a", highlightOffset: 9, highlightLength: 45,
  }))
  assert.match(html, /<button[^>]*data-citation-hit[^>]*class="pm-ws-parse-image-btn"/)
  assert.match(html, /images%2Fimg-a|images\/img-a/)
})

test('pending deletions render yellow strike elements at their original position and stay out of the clean document',async()=>{
  const {ReportTrackedBody}=await server.ssrLoadModule('/src/components/chat/report-tracked-body.tsx')
  const report:any={id:'r',session_id:'s',revision:2,markdown:'Keep.\n\nNext.',blocks:[{start:0,end:6,start_line:1,end_line:1,id:'b1',hash:'a'},{start:7,end:12,start_line:3,end_line:3,id:'b2',hash:'b'}],suggestions:[{id:'g',decision:'pending',patches:[{field:'markdown',start:7,end:7,before:'Deleted paragraph.\n\n',after:'',removed:[{quote:'Deleted paragraph.',prefix:'',suffix:''}]}],citations:[]}]}
  const tracked=renderToStaticMarkup(createElement(ReportTrackedBody,{report,tracking:true}))
  assert.match(tracked,/<del class="pm-review-removed">Deleted paragraph\.<\/del>/)
  assert.ok(tracked.indexOf('Keep.')<tracked.indexOf('Deleted paragraph.'))
  assert.ok(tracked.indexOf('Deleted paragraph.')<tracked.indexOf('Next.'))
  assert.match(tracked,/data-report-line="3"/)
  assert.doesNotMatch(tracked.match(/data-review-deletion="true"[^]*?<\/div>/)?.[0]||'',/data-report-line=/)
  const clean=renderToStaticMarkup(createElement(ReportTrackedBody,{report,tracking:false}))
  assert.doesNotMatch(clean,/Deleted paragraph|<del/)
  const accepted=renderToStaticMarkup(createElement(ReportTrackedBody,{report:{...report,suggestions:[{...report.suggestions[0],decision:'accepted'}]},tracking:true}))
  assert.doesNotMatch(accepted,/Deleted paragraph|<del/)
})

function tableRevision() {
  const before='| Item | Detail |\n| --- | --- |\n| Inventory | Stock is **18 crates**. Keep this note. |\n| Inspection | Inspect monthly. |\n| Owner | Example team |'
  const after=before.replace('18 crates','27 crates').replace('Inspect monthly.','Inspect weekly.')
  const markdown='## Stock review\n\n'+after+'\n\nClosing paragraph.'
  const changes=[['18 crates','27 crates'],['Inspect monthly.','Inspect weekly.']]
  return {id:'sample-report',session_id:'sample-session',revision:2,markdown,citations:[],
    blocks:[{id:'table-block',hash:'table-hash',kind:'table',start:17,end:17+after.length,start_line:3,end_line:7}],
    suggestions:changes.map(([oldText,newText],index)=>({id:`group-${index}`,decision:'pending',citations:[],patches:[{
      field:'markdown',start:markdown.indexOf(newText),end:markdown.indexOf(newText)+newText.length,
      before:oldText,after:newText,removed:[{quote:oldText,prefix:'',suffix:''}],added:[{quote:newText,prefix:'',suffix:''}],
    }]})),
  }
}

test('independent edits in table cells render inside one table and preserve current source anchors',async()=>{
  const {ReportTrackedBody}=await server.ssrLoadModule('/src/components/chat/report-tracked-body.tsx')
  const report=tableRevision()
  const html=renderToStaticMarkup(createElement(ReportTrackedBody,{report,tracking:true}))
  assert.equal((html.match(/<table\b/g)||[]).length,1,'A cell revision must not repeat the table')
  assert.equal((html.match(/<tr\b/g)||[]).length,4)
  assert.equal(html.split('Keep this note.').length-1,1)
  assert.equal(html.split('Example team').length-1,1)
  const cells=[...html.matchAll(/<td\b[^>]*>([\s\S]*?)<\/td>/g)].map(match=>match[1])
  assert.ok(cells.some(cell=>/<del[^>]*><strong>18<\/strong><\/del>/.test(cell)&&/<ins[^>]*><strong>27<\/strong><\/ins>/.test(cell)))
  assert.ok(cells.some(cell=>/<del[^>]*>monthly<\/del>/.test(cell)&&/<ins[^>]*>weekly<\/ins>/.test(cell)))
  assert.match(html,/data-review-group="group-0 group-1"/)
  assert.match(html,/data-report-line="3"[^>]*data-report-end="7"[^>]*data-report-block="table-block"[^>]*data-report-hash="table-hash"/)
  assert.match(html,/<del[^>]*data-review-deletion="true"/,'Historical cell text stays outside new comment selections')
})

test('table redlines retain clickable evidence on both sides without renumbering current citations',async()=>{
  const {ReportTrackedBody}=await server.ssrLoadModule('/src/components/chat/report-tracked-body.tsx')
  const {CitationScope}=await server.ssrLoadModule('/src/components/chat/citation-scope.tsx')
  const before='| Item | Detail |\n| --- | --- |\n| Inventory | Old count.[cite:previous] |'
  const after=before.replace('Old count.[cite:previous]','New count.[cite:current]')
  const markdown='Opening evidence.[cite:opening]\n\n'+after
  const evidence=(id:string)=>({id,title:`Sample ${id}`,kind:'document',quote:'Fictional evidence.',source:{},location:{}})
  const report={id:'sample-report',session_id:'sample-session',revision:2,markdown,citations:[evidence('opening'),evidence('current')],
    blocks:[{id:'table-block',hash:'table-hash',kind:'table',start:markdown.indexOf(after),end:markdown.length,start_line:3,end_line:5}],
    suggestions:[{id:'group',decision:'pending',citations:[evidence('previous')],patches:[{field:'markdown',start:markdown.indexOf(after),end:markdown.length,before,after}]}],
  }
  const html=renderToStaticMarkup(createElement(CitationScope,{citations:report.citations,sessionId:report.session_id,source:markdown},
    createElement(ReportTrackedBody,{report,tracking:true})))
  assert.equal((html.match(/<table\b/g)||[]).length,1)
  assert.match(html,/<button[^>]*title="Sample previous"[^>]*>/)
  assert.match(html,/<button[^>]*title="Sample current"[^>]*>2<\/button>/)
  assert.doesNotMatch(html,/〔\?〕|pm-citation-missing|\[cite:/)
})

test('a table-wide patch with display fragments still renders one table with cell-level changes',async()=>{
  const {ReportTrackedBody}=await server.ssrLoadModule('/src/components/chat/report-tracked-body.tsx')
  const report=tableRevision()
  const block=report.blocks[0]
  const after=report.markdown.slice(block.start,block.end)
  const before=after.replace('27 crates','18 crates').replace('Inspect weekly.','Inspect monthly.')
  const display_patches=report.suggestions.flatMap(group=>group.patches.map(patch=>({
    start:patch.start-block.start,end:patch.end-block.start,before:patch.before,after:patch.after,
  })))
  const suggestions=[{id:'whole-table',decision:'pending',citations:[],patches:[{
    field:'markdown',start:block.start,end:block.end,before,after,display_patches,
  }]}]
  const html=renderToStaticMarkup(createElement(ReportTrackedBody,{report:{...report,suggestions},tracking:true}))
  assert.equal((html.match(/<table\b/g)||[]).length,1)
  assert.equal((html.match(/<tr\b/g)||[]).length,4)
  assert.equal(html.split('Keep this note.').length-1,1)
  assert.match(html,/<del[^>]*><strong>18<\/strong><\/del>/)
  assert.match(html,/<ins[^>]*><strong>27<\/strong><\/ins>/)
  assert.match(html,/<del[^>]*>monthly<\/del><ins[^>]*>weekly<\/ins>/)
})

test('table decisions and a clean reading view hide obsolete redlines while preserving pending cell edits',async()=>{
  const {ReportTrackedBody}=await server.ssrLoadModule('/src/components/chat/report-tracked-body.tsx')
  const report=tableRevision()
  const accepted={...report,suggestions:report.suggestions.map((group,index)=>index?group:{...group,decision:'accepted'})}
  const partlyReviewed=renderToStaticMarkup(createElement(ReportTrackedBody,{report:accepted,tracking:true}))
  assert.equal((partlyReviewed.match(/<table\b/g)||[]).length,1)
  assert.doesNotMatch(partlyReviewed,/18 crates/)
  assert.match(partlyReviewed,/<del[^>]*>monthly<\/del>/)
  assert.match(partlyReviewed,/Stock is <strong>27 crates<\/strong>/)
  for(const tracking of [false,true]){
    const completed={...report,suggestions:report.suggestions.map(group=>({...group,decision:'accepted'}))}
    const html=renderToStaticMarkup(createElement(ReportTrackedBody,{report:tracking?completed:report,tracking}))
    assert.equal((html.match(/<table\b/g)||[]).length,1)
    assert.doesNotMatch(html,/<del\b|<ins\b|18 crates|Inspect monthly/)
  }
})
