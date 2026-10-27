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

test("the expanded diagram has named zoom controls and a keyboard-focusable canvas", async () => {
  const { SvgPreviewCanvas } = await server.ssrLoadModule("/src/components/chat/svg-preview-dialog.tsx")
  const html = renderToStaticMarkup(createElement(SvgPreviewCanvas, { source: '<svg xmlns="http://www.w3.org/2000/svg"><text>中 English</text></svg>' }))
  for (const label of ["Zoom in", "Zoom out", "Fit to window"]) {
    assert.ok(html.includes(`aria-label="${label}"`), label)
  }
  assert.match(html, /role="region"[^>]*aria-label="Diagram canvas"[^>]*tabindex="0"/)
  assert.match(html, /draggable="false"/)
  assert.match(html, /100%/)
})

test("the expanded diagram retains the passive image boundary", async () => {
  const { SvgPreviewCanvas } = await server.ssrLoadModule("/src/components/chat/svg-preview-dialog.tsx")
  const source = '<svg xmlns="http://www.w3.org/2000/svg" onload="evil()"><script>evil()</script><text>Private label</text></svg>'
  const html = renderToStaticMarkup(createElement(SvgPreviewCanvas, { source }))
  assert.match(html, /<img\b/)
  assert.doesNotMatch(html, /<(?:svg|script|iframe|object)\b|\bonload=/)
  assert.doesNotMatch(html, /Private label/)
})

test("an inline diagram gives zoom and pan hints without a dialog close shortcut", async () => {
  const { SvgPreviewCanvas } = await server.ssrLoadModule("/src/components/chat/svg-preview-dialog.tsx")
  const html = renderToStaticMarkup(createElement(SvgPreviewCanvas, {
    source: '<svg xmlns="http://www.w3.org/2000/svg"/>', inline: true,
  }))
  assert.match(html, /Scroll to read/)
  assert.match(html, /Ctrl\/⌘ \+ scroll to zoom/)
  assert.doesNotMatch(html, /Esc to close/)
})
