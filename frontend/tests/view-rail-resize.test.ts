import assert from "node:assert/strict"
import { before, test } from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { createServer } from "vite"
import { fileURLToPath } from "node:url"

let createViewRailResizeController: typeof import("../src/lib/view-rail-resize.ts").createViewRailResizeController
let viewRailKeyboardWidth: typeof import("../src/lib/view-rail-resize.ts").viewRailKeyboardWidth

before(async () => {
  const helpers = await import("../src/lib/view-rail-resize.ts").catch((error) => {
    if (error.code === "ERR_MODULE_NOT_FOUND") return {}
    throw error
  })
  createViewRailResizeController = helpers.createViewRailResizeController
  viewRailKeyboardWidth = helpers.viewRailKeyboardWidth
})

// Exercise the production DOM controller with the CSS declaration and pointer
// capture surfaces it uses; no mocked React hooks or browser dependency.
function styleDeclaration() {
  const values = new Map<string, { value: string; priority: string }>()
  return {
    getPropertyValue(name: string) { return values.get(name)?.value ?? "" },
    getPropertyPriority(name: string) { return values.get(name)?.priority ?? "" },
    setProperty(name: string, value: string, priority = "") { values.set(name, { value, priority }) },
    removeProperty(name: string) {
      const previous = values.get(name)?.value ?? ""
      values.delete(name)
      return previous
    },
  }
}

function harness() {
  assert.equal(typeof createViewRailResizeController, "function", "DOM resize controller must exist")
  const host = { style: styleDeclaration() }
  const documentStyle = styleDeclaration()
  const captures = new Set<number>()
  const commits: number[] = []
  const previews: (number | null)[] = []
  const target = {
    setPointerCapture(id: number) { captures.add(id) },
    hasPointerCapture(id: number) { return captures.has(id) },
    releasePointerCapture(id: number) {
      captures.delete(id)
      controller.cancel(id) // Browsers dispatch lostpointercapture after release.
    },
  }
  const controller = createViewRailResizeController({
    onCommit: (width) => commits.push(width),
    onPreview: (width) => previews.push(width),
  })
  const begin = (overrides = {}) => controller.start({
    pointerId: 1, clientX: 300, button: 0, isPrimary: true,
    target, host, documentStyle, width: 228, ...overrides,
  })
  return { host, documentStyle, captures, commits, previews, controller, begin }
}

test("pointer moves update the shared host live and save exactly once on release", () => {
  const h = harness()
  h.host.style.setProperty("--pm-view-rail-w", "228px")
  assert.equal(h.begin(), true)
  assert.deepEqual([...h.captures], [1])
  h.controller.move(1, 342)
  h.controller.move(1, 352)
  assert.equal(h.host.style.getPropertyValue("--pm-view-rail-w"), "280px")
  assert.deepEqual(h.commits, [], "dragging must not persist per pointer move")
  h.controller.finish(1, 360)
  h.controller.finish(1, 360)
  assert.deepEqual(h.commits, [288])
  assert.equal(h.host.style.getPropertyValue("--pm-view-rail-w"), "288px")
  assert.equal(h.captures.size, 0)
  assert.equal(h.documentStyle.getPropertyValue("cursor"), "")
  assert.equal(h.documentStyle.getPropertyValue("user-select"), "")
})

test("only the primary left pointer starts a drag and unrelated pointers cannot finish it", () => {
  const h = harness()
  assert.equal(h.begin({ button: 2 }), false)
  assert.equal(h.begin({ isPrimary: false }), false)
  assert.equal(h.begin(), true)
  assert.equal(h.begin({ pointerId: 2 }), false)
  h.controller.move(2, 900)
  h.controller.finish(2, 900)
  h.controller.cancel(2)
  assert.equal(h.controller.isDragging(), true)
  assert.deepEqual(h.commits, [])
  h.controller.finish(1, 310)
  assert.deepEqual(h.commits, [238])
})

test("drag width stays within the shared bounds", () => {
  const h = harness()
  h.begin()
  h.controller.move(1, -1000)
  assert.equal(h.host.style.getPropertyValue("--pm-view-rail-w"), "180px")
  h.controller.move(1, 3000)
  assert.equal(h.host.style.getPropertyValue("--pm-view-rail-w"), "480px")
  h.controller.finish(1, 3000)
  assert.deepEqual(h.commits, [480])
})

test("cancellation restores the exact inline width and existing document styles without saving", () => {
  const h = harness()
  h.host.style.setProperty("--pm-view-rail-w", "250px", "important")
  h.documentStyle.setProperty("cursor", "crosshair", "important")
  h.documentStyle.setProperty("user-select", "text")
  h.begin({ width: 250 })
  assert.equal(h.documentStyle.getPropertyValue("cursor"), "col-resize")
  assert.equal(h.documentStyle.getPropertyValue("user-select"), "none")
  h.controller.move(1, 400)
  h.controller.cancel()
  assert.equal(h.host.style.getPropertyValue("--pm-view-rail-w"), "250px")
  assert.equal(h.host.style.getPropertyPriority("--pm-view-rail-w"), "important")
  assert.equal(h.documentStyle.getPropertyValue("cursor"), "crosshair")
  assert.equal(h.documentStyle.getPropertyPriority("cursor"), "important")
  assert.equal(h.documentStyle.getPropertyValue("user-select"), "text")
  assert.deepEqual(h.commits, [])
  assert.equal(h.controller.isDragging(), false)
  assert.equal(h.previews.at(-1), null)
})

test("cancel also removes temporary width when the host originally inherited its width", () => {
  const h = harness()
  h.begin()
  h.controller.move(1, 400)
  h.controller.cancel(1)
  assert.equal(h.host.style.getPropertyValue("--pm-view-rail-w"), "")
  assert.equal(h.captures.size, 0)
  assert.deepEqual(h.commits, [])
  h.controller.cancel() // Escape, view-switch and unmount cleanup can overlap.
  assert.deepEqual(h.commits, [])
})

test("failed pointer capture leaves host and document untouched", () => {
  const h = harness()
  assert.equal(h.begin({ target: {
    setPointerCapture() { throw new Error("detached handle") },
    hasPointerCapture() { return false },
    releasePointerCapture() {},
  } }), false)
  assert.equal(h.controller.isDragging(), false)
  assert.equal(h.host.style.getPropertyValue("--pm-view-rail-w"), "")
  assert.equal(h.documentStyle.getPropertyValue("cursor"), "")
  assert.deepEqual(h.commits, [])
})

test("stationary clicks do not save before a double-click reset", () => {
  const h = harness()
  h.begin()
  h.controller.finish(1, 300)
  h.begin()
  h.controller.finish(1, 300)
  assert.deepEqual(h.commits, [])
  assert.equal(h.host.style.getPropertyValue("--pm-view-rail-w"), "")
})

test("a failed commit still releases capture and restores temporary document styles", () => {
  const h = harness()
  const controller = createViewRailResizeController({
    onPreview() {},
    onCommit() { throw new Error("persistence unavailable") },
  })
  controller.start({
    pointerId: 1, clientX: 300, button: 0, isPrimary: true,
    target: {
      setPointerCapture(id) { h.captures.add(id) },
      hasPointerCapture(id) { return h.captures.has(id) },
      releasePointerCapture(id) { h.captures.delete(id); controller.cancel(id) },
    },
    host: h.host, documentStyle: h.documentStyle, width: 228,
  })
  assert.throws(() => controller.finish(1, 350), /persistence unavailable/)
  assert.equal(controller.isDragging(), false)
  assert.equal(h.captures.size, 0)
  assert.equal(h.documentStyle.getPropertyValue("cursor"), "")
  assert.equal(h.documentStyle.getPropertyValue("user-select"), "")
})

test("keyboard resizing uses ten-pixel steps, forty with Shift, and Home/End bounds", () => {
  assert.equal(typeof viewRailKeyboardWidth, "function")
  assert.equal(viewRailKeyboardWidth(228, "ArrowLeft", false), 218)
  assert.equal(viewRailKeyboardWidth(228, "ArrowRight", false), 238)
  assert.equal(viewRailKeyboardWidth(228, "ArrowLeft", true), 188)
  assert.equal(viewRailKeyboardWidth(228, "ArrowRight", true), 268)
  assert.equal(viewRailKeyboardWidth(228, "Home", false), 180)
  assert.equal(viewRailKeyboardWidth(228, "End", false), 480)
  assert.equal(viewRailKeyboardWidth(185, "ArrowLeft", false), 180)
  assert.equal(viewRailKeyboardWidth(475, "ArrowRight", true), 480)
  assert.equal(viewRailKeyboardWidth(228, "Enter", false), undefined)
})

test("the real handle exposes its current shared width and keyboard-focusable separator semantics", async () => {
  const frontendRoot = fileURLToPath(new URL("../", import.meta.url))
  const server = await createServer({
    root: frontendRoot, configFile: false,
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    resolve: { alias: { "@": `${frontendRoot}src` } },
    optimizeDeps: { noDiscovery: true },
    plugins: [{
      name: "rail-resize-test-store",
      enforce: "pre",
      resolveId(id) {
        if (id.endsWith("/stores/app-store") || id.endsWith("/stores/app-store.ts")) return "\0rail-resize-test-store"
      },
      load(id) {
        if (id === "\0rail-resize-test-store") return `
          const state = { locale: "zh-CN", sidebarView: "chat", viewRailWidth: 310, setViewRailWidth() {} };
          export const useAppStore = selector => selector(state);
        `
      },
    }],
  })
  try {
    const { ViewRailResizeHandle } = await server.ssrLoadModule("/src/components/shared/view-rail-resize-handle.tsx")
    const html = renderToStaticMarkup(createElement(ViewRailResizeHandle))
    assert.match(html, /role="separator"/)
    assert.match(html, /aria-orientation="vertical"/)
    assert.match(html, /aria-valuenow="310"/)
    assert.match(html, /aria-valuemin="180"/)
    assert.match(html, /aria-valuemax="480"/)
    assert.match(html, /tabindex="0"/)
    assert.match(html, /aria-label="调整侧边栏宽度"/)
  } finally {
    await server.close()
  }
})
