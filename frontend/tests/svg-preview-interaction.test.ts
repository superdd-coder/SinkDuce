import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"
import { createElement, isValidElement, type ReactElement } from "react"
import ts from "typescript"

type Tree = ReactElement<Record<string, any>>

function elements(node: unknown): Tree[] {
  if (Array.isArray(node)) return node.flatMap(elements)
  if (!isValidElement(node)) return []
  const element = node as Tree
  return [element, ...elements(element.props.children)]
}

// No browser/DOM dependency: run the real component handlers and retain hook
// state between renders. Image geometry and editor event routing have separate tests.
async function componentHarness(file: string, name: string, previewHost: unknown = null) {
  const cells: any[] = []
  let effects: Array<() => unknown> = []
  let cleanups: Array<() => void> = []
  let cursor = 0
  const key = `__svgInteraction_${file}`
  const globals = globalThis as unknown as Record<string, any>
  globals[key] = {
    previewHost,
    useState(initial: unknown) {
      const i = cursor++
      if (!(i in cells)) cells[i] = initial
      return [cells[i], (next: unknown) => { cells[i] = typeof next === "function" ? next(cells[i]) : next }]
    },
    useRef(initial: unknown) {
      const i = cursor++
      if (!(i in cells)) cells[i] = { current: initial }
      return cells[i]
    },
    useCallback: (fn: unknown) => fn,
    useMemo: (fn: () => unknown) => fn(),
    useContext: () => null,
    createContext: () => ({ Provider: function TestContextProvider() {} }),
    useEffect: (effect: () => unknown) => { effects.push(effect) },
  }
  const dataModule = (source: string) => "data:text/javascript," + encodeURIComponent(source)
  const imports: Record<string, string> = {
    react: dataModule(["useState", "useRef", "useCallback", "useEffect", "useMemo", "useContext", "createContext"].map((hook) =>
      `export const ${hook} = (...args) => globalThis[${JSON.stringify(key)}].${hook}(...args);`,
    ).join("\n")),
    "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
    "lucide-react": import.meta.resolve("lucide-react"),
    "@/i18n/use-t": dataModule("export const useT = () => (key) => key"),
    "@/lib/svg-preview": new URL("../src/lib/svg-preview.ts", import.meta.url).href,
    "@/lib/svg-viewport": new URL("../src/lib/svg-viewport.ts", import.meta.url).href,
    "@/components/chat/svg-preview-dialog": dataModule("export function SvgPreviewDialog(){}; export function SvgPreviewCanvas(){}"),
    "@/components/shared/svg-preview-context": dataModule(`export const useSvgPreviewHost = () => globalThis[${JSON.stringify(key)}].previewHost`),
    "@/components/ui/dialog": dataModule(["Dialog", "DialogContent", "DialogDescription", "DialogHeader", "DialogTitle"].map((n) => `export function ${n}(){}`).join("\n")),
  }
  const source = await readFile(new URL(`../src/components/chat/${file}.tsx`, import.meta.url), "utf8")
  const compiled = ts.transpileModule(source, {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText.replace(/from ["']([^"']+)["']/g, (statement, path: string) =>
    imports[path] ? `from ${JSON.stringify(imports[path])}` : statement,
  )
  const module = await import(dataModule(compiled))
  return {
    render(props: Record<string, unknown>) { cursor = 0; effects = []; return elements(module[name](props)) },
    flushEffects() {
      cleanups.forEach((cleanup) => cleanup())
      cleanups = effects.map((effect) => effect()).filter((cleanup): cleanup is () => void => typeof cleanup === "function")
      effects = []
    },
    dispose() { cleanups.forEach((cleanup) => cleanup()); delete globals[key] },
  }
}

test("clicking the Note's expand icon opens the SVG dialog and closing it restores the inline block", async () => {
  const harness = await componentHarness("svg-code-block", "SvgCodeBlock")
  try {
    const source = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 500"><text>Diagram</text></svg>'
    const props = { source, complete: true, sourceEditor: createElement("code", { "data-node-view-content": "" }) }
    let tree = harness.render(props)
    const toolbar = tree.find((e) => e.props.className === "chat-md-visual-toolbar")!
    let prevented = false
    let stopped = false
    toolbar.props.onMouseDown({ preventDefault() { prevented = true }, stopPropagation() { stopped = true } })
    assert.ok(prevented && stopped, "Editor selection must not consume clicks on nested button icons")
    const button = tree.find((e) => e.props["aria-label"] === "chat.svgExpand")!
    assert.equal(button.props["aria-expanded"], false)
    button.props.onClick()
    tree = harness.render(props)
    assert.equal(tree.find((e) => e.props["aria-label"] === "chat.svgExpand")!.props["aria-expanded"], true)
    const dialog = tree.find((e) => typeof e.type === "function" && e.type.name === "SvgPreviewDialog")!
    assert.ok(dialog, "The click must mount a separate preview window")
    assert.equal(dialog.props.source, source)
    assert.equal(dialog.props.triggerRef, button.props.ref)
    assert.ok(tree.some((e) => e.props["data-node-view-content"] === ""))
    dialog.props.onClose()
    tree = harness.render(props)
    assert.equal(tree.find((e) => e.props["aria-label"] === "chat.svgExpand")!.props["aria-expanded"], false)
  } finally { harness.dispose() }
})

test("Note expansion delegates to the persistent editor host instead of a node-view popup", async () => {
  let opened: { source: string; triggerRef: unknown } | null = null
  const harness = await componentHarness("svg-code-block", "SvgCodeBlock", {
    preview: null,
    open(source: string, triggerRef: unknown) { opened = { source, triggerRef } },
  })
  try {
    const source = '<svg xmlns="http://www.w3.org/2000/svg"/>'
    const props = { source, complete: true, sourceEditor: createElement("code") }
    const button = harness.render(props).find((e) => e.props["aria-label"] === "chat.svgExpand")!
    button.props.onClick()
    assert.ok(opened, "The modal must be owned outside the editor node view")
    assert.equal(opened.source, source)
    assert.equal(opened.triggerRef, button.props.ref)
    assert.ok(!harness.render(props).some((e) => typeof e.type === "function" && e.type.name === "SvgPreviewDialog"))
  } finally { harness.dispose() }
})

test("the editor-level preview survives its originating node view being replaced", async () => {
  const harness = await componentHarness("../shared/svg-preview-context", "SvgPreviewProvider")
  try {
    let tree = harness.render({ children: createElement("div", { "data-editor-node": "" }) })
    const host = tree[0].props.value
    const triggerRef = { current: null }
    host.open("<svg/>", triggerRef)
    tree = harness.render({ children: null })
    const popup = tree.find((e) => typeof e.type === "function" && e.type.name === "SvgPreviewDialog")!
    assert.ok(popup, "The popup must not disappear when editor node views update")
    assert.equal(popup.props.source, "<svg/>")
    popup.props.onClose()
    assert.ok(!harness.render({ children: null }).some((e) => typeof e.type === "function" && e.type.name === "SvgPreviewDialog"))
  } finally { harness.dispose() }
})

test("ordinary wheels scroll the Note while modified wheels zoom the inline SVG", async () => {
  const globals = globalThis as unknown as Record<string, any>
  const originalObserver = globals.ResizeObserver
  globals.ResizeObserver = class { observe() {} disconnect() {} }
  const harness = await componentHarness("svg-preview-dialog", "SvgPreviewCanvas")
  try {
    const props = { source: '<svg xmlns="http://www.w3.org/2000/svg"/>', inline: true }
    const listeners = new Map<string, (event: any) => void>()
    const surface = {
      clientWidth: 360, clientHeight: 220,
      addEventListener(name: string, listener: (event: any) => void) { listeners.set(name, listener) },
      removeEventListener(name: string) { listeners.delete(name) },
      getBoundingClientRect() { return { left: 0, top: 0 } },
    }
    let tree = harness.render(props)
    tree.find((e) => e.props.className === "chat-svg-canvas")!.props.ref.current = surface
    tree.find((e) => e.type === "img")!.props.onLoad({ currentTarget: { naturalWidth: 800, naturalHeight: 400 } })
    tree = harness.render(props)
    harness.flushEffects()
    let prevented = false
    let stopped = false
    const wheel = {
      deltaY: -100, deltaMode: 0, clientX: 180, clientY: 110, ctrlKey: false, metaKey: false,
      preventDefault() { prevented = true }, stopPropagation() { stopped = true },
    }
    listeners.get("wheel")!(wheel)
    assert.equal(prevented, false, "Scrolling across a diagram must not trap the Note scroll")
    assert.equal(stopped, false)
    listeners.get("wheel")!({ ...wheel, ctrlKey: true })
    assert.equal(prevented, true)
    assert.equal(stopped, true)
    tree = harness.render(props)
    assert.ok(tree.find((e) => e.type === "img")!.props.style.width > 360)
  } finally { harness.dispose(); globals.ResizeObserver = originalObserver }
})

for (const inline of [true, false]) {
  test(`fit fills the ${inline ? "inline Note" : "expanded dialog"} canvas for a viewBox-only SVG`, async () => {
    const harness = await componentHarness("svg-preview-dialog", "SvgPreviewCanvas")
    try {
      const props = { source: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 520"><rect width="900" height="520"/></svg>', inline }
      const surface = { clientWidth: 540, clientHeight: 312 }
      let tree = harness.render(props)
      tree.find((e) => e.props.className === "chat-svg-canvas")!.props.ref.current = surface
      // ViewBox-only SVG images can report the browser's fallback intrinsic size.
      tree.find((e) => e.type === "img")!.props.onLoad({ currentTarget: { naturalWidth: 300, naturalHeight: 150 } })
      tree = harness.render(props)
      let image = tree.find((e) => e.type === "img")!
      assert.equal(image.props.style.width, 540)
      assert.equal(image.props.style.height, 312, "Use the SVG viewBox aspect ratio instead of fallback dimensions")
      assert.equal(image.props.style.transform, "translate3d(0px, 0px, 0)")
      tree.find((e) => e.props["aria-label"] === "chat.svgZoomIn")!.props.onClick()
      tree = harness.render(props)
      assert.equal(tree.find((e) => e.type === "img")!.props.style.width, 675)
      tree.find((e) => e.props["aria-label"] === "chat.svgFit")!.props.onClick()
      tree = harness.render(props)
      image = tree.find((e) => e.type === "img")!
      assert.equal(image.props.style.width, 540)
      assert.equal(image.props.style.height, 312)
      assert.equal(image.props.style.transform, "translate3d(0px, 0px, 0)")
    } finally { harness.dispose() }
  })
}

test("the nested SVG popup has its own backdrop and keeps mouse events out of the Note editor", async () => {
  const harness = await componentHarness("svg-preview-dialog", "SvgPreviewDialog")
  try {
    const tree = harness.render({ source: "<svg/>", triggerRef: { current: null }, onClose() {} })
    const popup = tree.find((e) => typeof e.type === "function" && e.type.name === "DialogContent")!
    assert.equal(popup.props.forceOverlay, true)
    for (const action of ["onMouseDown", "onClick"]) {
      let stopped = false
      popup.props[action]({ stopPropagation() { stopped = true } })
      assert.ok(stopped)
    }
  } finally { harness.dispose() }
})
