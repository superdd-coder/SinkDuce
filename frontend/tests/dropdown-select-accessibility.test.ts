import assert from "node:assert/strict"
import { after, before, test } from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { createServer } from "vite"
import { fileURLToPath } from "node:url"

let server: Awaited<ReturnType<typeof createServer>>
before(async () => {
  const root = fileURLToPath(new URL("../", import.meta.url))
  server = await createServer({
    root, configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: { alias: { "@": root + "src" } },
    optimizeDeps: { noDiscovery: true },
  })
})
after(async () => { await server?.close() })

test("a soft select keeps its field name and label association on the keyboard trigger", async () => {
  const { DropdownSelect } = await server.ssrLoadModule("/src/components/ui/dropdown-select.tsx")
  const html = renderToStaticMarkup(createElement(DropdownSelect, {
    id: "question-status", "aria-label": "Question status", disabled: true,
    value: "waiting", options: [{ value: "waiting", label: "Awaiting reply" }], onChange() {},
  }))
  const trigger = html.match(/<button\b[^>]*>/)?.[0] || ""
  assert.match(trigger, /id="question-status"/)
  assert.match(trigger, /aria-label="Question status"/)
  assert.match(trigger, /aria-haspopup="listbox"/)
  assert.match(trigger, /disabled=""/)
  assert.match(html, /Awaiting reply/)
})
