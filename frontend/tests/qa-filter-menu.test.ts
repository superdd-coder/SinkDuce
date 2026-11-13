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

const props = {
  count: 19, status: "all", omitted: false,
  statusOptions: [{ value: "open", label: "Awaiting confirmation" }],
  onStatusChange() {}, onOmittedChange() {}, onClear() {},
}

test("the question count and named icon trigger are available without active filters", async () => {
  const { QaQuestionFilter } = await server.ssrLoadModule("/src/components/qa/qa-question-filter.tsx")
  const html = renderToStaticMarkup(createElement(QaQuestionFilter, props))
  assert.match(html, /aria-live="polite"[^>]*>19<\/span>/)
  assert.match(html, /<button[^>]*aria-label="[^"]+"[^>]*aria-haspopup="menu"/)
  assert.doesNotMatch(html, /aria-expanded="true"/)
  assert.doesNotMatch(html, /is-active|Awaiting confirmation|>Clarify</)
})

test("active status is named accessibly beside the filtered result count", async () => {
  const { QaQuestionFilter } = await server.ssrLoadModule("/src/components/qa/qa-question-filter.tsx")
  const html = renderToStaticMarkup(createElement(QaQuestionFilter, { ...props, count: 3, status: "open" }))
  assert.match(html, /class="qa-question-filter is-active"/)
  assert.match(html, /aria-live="polite"[^>]*>3<\/span>/)
  assert.match(html, /aria-label="[^"]*Awaiting confirmation[^"]*"/)
  assert.doesNotMatch(html, /aria-pressed=/)
})

test("legacy category data cannot activate the filter", async () => {
  const { QaQuestionFilter } = await server.ssrLoadModule("/src/components/qa/qa-question-filter.tsx")
  const legacyProps = { ...props, type: "clarify", typeOptions: [{ value: "clarify", label: "Clarify" }] }
  const html = renderToStaticMarkup(createElement(QaQuestionFilter, legacyProps))
  assert.doesNotMatch(html, /qa-question-filter is-active|Clarify|Awaiting confirmation/)
})

test("showing omitted questions activates the same filter control and names its scope", async () => {
  const { QaQuestionFilter } = await server.ssrLoadModule("/src/components/qa/qa-question-filter.tsx")
  const html = renderToStaticMarkup(createElement(QaQuestionFilter, { ...props, count: 21, omitted: true }))
  assert.match(html, /qa-question-filter is-active/)
  assert.match(html, /aria-label="[^"]*Show omitted questions[^"]*"/)
  assert.match(html, /aria-live="polite"[^>]*>21<\/span>/)
  assert.doesNotMatch(html, /Awaiting confirmation/)
})

for (const reading of [false, true]) {
  test(`the ${reading ? "narrow" : "wide"} list header keeps one search before its count and filter`, async () => {
    const { QaQuestionListHeader } = await server.ssrLoadModule("/src/components/qa/qa-question-filter.tsx")
    const html = renderToStaticMarkup(createElement(QaQuestionListHeader, {
      ...props, query: "museum entrance", onQueryChange() {},
      onCollapse: reading ? () => {} : undefined,
    }))
    assert.equal((html.match(/<input\b/g) || []).length, 1)
    assert.match(html, /value="museum entrance"/)
    assert.match(html, /aria-label="Search questions or replies"/)
    assert.ok(html.indexOf("<input") < html.indexOf("qa-question-count"))
    assert.doesNotMatch(html, />Question list</)
    if (reading) assert.match(html, /aria-label="Collapse question details"/)
    else assert.doesNotMatch(html, /aria-label="Collapse question details"/)
  })
}
