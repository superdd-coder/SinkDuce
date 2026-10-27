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

async function renderChunk(text: string, sourcePanel = false) {
  if (sourcePanel) {
    const { SourceChunkBody } = await server.ssrLoadModule("/src/components/chat/source-detail-panel.tsx")
    return renderToStaticMarkup(createElement(SourceChunkBody, {
      chunk: { text, file_id: "file-a", source: "__note__:note-a" }, collection: "col-a",
    }))
  }
  const { ChunkMd } = await server.ssrLoadModule("/src/components/shared/chunk-md.tsx")
  return renderToStaticMarkup(createElement(ChunkMd, { text, collection: "col-a", fileId: "file-a" }))
}

for (const sourcePanel of [false, true]) {
  test(`${sourcePanel ? "Chat source" : "shared"} chunks reject an image marker followed by an active srcdoc iframe`, async () => {
    const html = await renderChunk('Before\n\n<img src="/diagram.png"><iframe srcdoc="&lt;script&gt;window.__chunkAttack=true&lt;/script&gt;"></iframe>\n\nAfter', sourcePanel)
    assert.match(html, /Before/)
    assert.match(html, /After/)
    assert.match(html, /<img\b/)
    assert.doesNotMatch(html, /<(?:iframe|script)\b|\ssrcdoc=/i)
    assert.doesNotMatch(html, /__chunkAttack/)
  })
}

test("chunk HTML cannot create active, embedding, form or style elements", async () => {
  const html = await renderChunk(`<img src="/diagram.png">
<script>window.__chunkAttack=true</script>
<style>body { display: none }</style>
<iframe src="/api/pairing/tokens"></iframe>
<object data="/active.html"></object><embed src="/active.html">
<svg><foreignObject><iframe srcdoc="active"></iframe></foreignObject></svg>
<math><mtext>active</mtext></math>
<form action="/api/config"><input autofocus name="owner-key"><button>Submit</button></form>
<meta http-equiv="refresh" content="0;url=/active.html"><base href="https://example.com/"><link rel="stylesheet" href="/active.css">`)
  assert.doesNotMatch(html, /<(?:script|style|iframe|object|embed|svg|foreignObject|math|form|input|button|meta|base|link)\b/i)
  assert.doesNotMatch(html, /__chunkAttack|display:\s*none/)
})

test("chunk elements keep only document attributes and safe URLs", async () => {
  const html = await renderChunk(`<p id="owner-key" name="owner-key" style="position:fixed;inset:0" onclick="evil()" contenteditable="true">Safe text</p>
<img src="/diagram.png" srcset="/active.png 2x" style="position:fixed" onerror="evil()" srcdoc="active">
<a href="javascript:evil()" style="position:fixed" ping="https://example.com/track" target="_top">Unsafe link</a>
<img src="data:text/html,active"><img src="javascript:evil()">
<a href="https://example.com/reference" title="Reference">Safe link</a>`)
  assert.match(html, /Safe text/)
  assert.match(html, /href="https:\/\/example.com\/reference"/)
  assert.match(html, /title="Reference"/)
  assert.doesNotMatch(html, /\s(?:style|srcdoc|srcset|on\w+|contenteditable|ping|target|id|name)=/i)
  assert.doesNotMatch(html, /(?:href|src)="(?:javascript:|data:)/i)
})

test("safe HTML tables and GFM tables, checkboxes, lists and code remain readable", async () => {
  const html = await renderChunk(`<table style="position:fixed"><caption>中文表格</caption><tbody><tr><th colspan="2">Header</th></tr><tr><td rowspan="2" align="right">Value</td><td>42</td></tr></tbody></table>

| Item | Value |
| --- | --- |
| A | 2 |

- [x] Complete
- **Strong**

\`\`\`html
<iframe srcdoc="example"></iframe>
\`\`\`
`)
  assert.match(html, /<caption>中文表格<\/caption>/)
  assert.match(html, /<th colSpan="2">Header<\/th>/)
  assert.match(html, /<td rowSpan="2" style="text-align:right">Value<\/td>/)
  assert.match(html, /<td>2<\/td>/)
  assert.match(html, /<input(?=[^>]*type="checkbox")(?=[^>]*disabled="")(?=[^>]*checked="")[^>]*>/)
  assert.match(html, /<strong>Strong<\/strong>/)
  assert.match(html, /&lt;iframe srcdoc=/)
  assert.doesNotMatch(html, /<iframe\b|position:fixed/)
})

test("managed chunk images keep their PNG URL and escaped OCR/description text", async () => {
  const html = await renderChunk(':::image\nimage_id: diagram-a\nfile_id: file-a\nocr_text: 投资人 A → B <iframe srcdoc="example">\ndescription: Diagram <script>example</script>\n:::\n')
  assert.match(html, /\/api\/documents\/col-a\/file-a\/images\/diagram-a/)
  assert.match(html, /投资人 A → B/)
  assert.match(html, /Diagram &lt;script&gt;example&lt;\/script&gt;/)
  assert.doesNotMatch(html, /<(?:script|iframe)\b/)
})

test("image-fence identifiers cannot inject HTML into generated image attributes", async () => {
  const { transformImageBlocks } = await server.ssrLoadModule("/src/lib/utils.ts")
  const text = ':::image\nimage_id: diagram" /><iframe srcdoc="active"></iframe><img data-attack="yes\nfile_id: file-a\nocr_text: Label\n:::\n'
  const expanded = transformImageBlocks(text, "col-a", "file-a")
  assert.doesNotMatch(expanded, /<iframe\b/)
  const html = await renderChunk(text)
  assert.equal((html.match(/<img\b/g) || []).length, 1)
  assert.doesNotMatch(html, /<iframe\b|\sdata-attack="/)
  assert.match(html, /Label/)
})
