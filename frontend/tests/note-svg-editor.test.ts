import assert from "node:assert/strict"
import { before, after, test } from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { ReactNodeViewContentProvider } from "@tiptap/react"
import { createServer } from "vite"
import { fileURLToPath } from "node:url"

const drawing = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><text x="10" y="50">投资人 A → B</text></svg>'
let server: Awaited<ReturnType<typeof createServer>>
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage")

before(async () => {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: undefined })
  const root = fileURLToPath(new URL("../", import.meta.url))
  server = await createServer({
    root, configFile: false,
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

async function renderBlock(source = drawing, language = "svg") {
  const { EditorCodeBlock } = await server.ssrLoadModule("/src/components/ui/tiptap-editor.tsx")
  assert.equal(typeof EditorCodeBlock, "function", "Note editor code blocks need SVG previews")
  return renderToStaticMarkup(createElement(ReactNodeViewContentProvider, {
    content: source,
    children: createElement(EditorCodeBlock, { node: { attrs: { language }, textContent: source } }),
  }))
}

test("Note SVG code blocks show an image and retain a hidden editable source DOM", async () => {
  const html = await renderBlock()
  assert.match(html, /pm-editor-svg/)
  assert.match(html, /<img\b/)
  assert.match(html, /chat-svg-expand-button/)
  assert.match(html, /chat-svg-expand-button[^>]*aria-haspopup="dialog"/)
  assert.match(html, /<pre\b[^>]*hidden=""[^>]*>/)
  assert.match(html, /data-node-view-content=""/)
  assert.match(html, /投资人 A/)
  assert.doesNotMatch(html, /<svg\b/)
})

test("SVG toolbar icon clicks are kept out of ProseMirror while source edits remain editable", async () => {
  const { stopSvgPreviewControlEvent } = await server.ssrLoadModule("/src/components/ui/tiptap-editor.tsx")
  assert.equal(typeof stopSvgPreviewControlEvent, "function", "Nested button icons must not become editor selection clicks")
  const control = {}
  // In the browser closest() finds the preview control host even for a span
  // inside its button. Source code lies outside that host.
  for (const type of ["mousedown", "click", "keydown"]) {
    const iconEvent = { type, target: { closest: () => control } }
    assert.equal(stopSvgPreviewControlEvent({ event: iconEvent }), true)
    const sourceEvent = { type, target: { closest: () => null } }
    assert.equal(stopSvgPreviewControlEvent({ event: sourceEvent }), false)
  }
})

for (const [width, height] of [[2400, 600], [600, 2400]]) {
  test(`Note ${width}×${height} SVG uses a fitted zoom canvas directly in the editor`, async () => {
    const html = await renderBlock(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}"><text>Diagram</text></svg>`)
    for (const label of ["Zoom in", "Zoom out", "Fit to window"]) {
      assert.ok(html.includes(`aria-label="${label}"`), `Note preview needs ${label} without opening a dialog`)
    }
    assert.match(html, /chat-md-visual-preview--interactive/)
    assert.match(html, /class="chat-svg-canvas"[^>]*tabindex="0"/)
    assert.match(html, /class="chat-svg-canvas-image"[^>]*style="width:0;height:0;/)
    assert.match(html, /data-node-view-content=""/)
    assert.doesNotMatch(html, /Esc to close/)
  })
}

test("invalid Note SVG keeps the original editable source visible", async () => {
  const html = await renderBlock('<invalid>Source text</invalid>')
  assert.doesNotMatch(html, /<img\b|<invalid>/)
  assert.match(html, /&lt;invalid&gt;Source text&lt;\/invalid&gt;/)
  assert.doesNotMatch(html, /<pre\b[^>]*hidden=/)
  assert.match(html, /data-node-view-content=""/)
})

test("ordinary editor code blocks keep their editable code without SVG controls", async () => {
  const html = await renderBlock('print("<hello>")', "python")
  assert.match(html, /<pre\b/)
  assert.match(html, /language-python/)
  assert.match(html, /&lt;hello&gt;/)
  assert.match(html, /data-node-view-content=""/)
  assert.doesNotMatch(html, /chat-md-visual|<img\b/)
})

test("SVG previews preserve codeBlock identity and serialize complete SVG after edits", async () => {
  const { TiptapStarterKit } = await server.ssrLoadModule("/src/components/ui/tiptap-editor.tsx")
  assert.ok(TiptapStarterKit, "The mounted editor must register the SVG code block view")
  const { Editor } = await import("@tiptap/core")
  const { Markdown } = await import("tiptap-markdown")
  const editor = new Editor({
    element: null,
    extensions: [TiptapStarterKit, Markdown],
    content: { type: "doc", content: [{ type: "codeBlock", attrs: { language: "svg" }, content: [{ type: "text", text: drawing }] }] },
  })
  try {
    assert.equal(typeof editor.extensionManager.nodeViews.codeBlock, "function")
    const original = editor.storage.markdown.getMarkdown()
    assert.equal(original.trim(), "```svg\n" + drawing + "\n```")
    const revisedDrawing = drawing.replace("投资人 A", "修改后的投资人")
    const edited = editor.state.apply(editor.state.tr.insertText(revisedDrawing, 1, 1 + drawing.length))
    const saved = editor.storage.markdown.serializer.serialize(edited.doc)
    assert.equal(saved.trim(), "```svg\n" + revisedDrawing + "\n```")
    assert.doesNotMatch(saved, /blob:|data-node-view|chat-md-visual/)
  } finally {
    editor.destroy()
  }
})
