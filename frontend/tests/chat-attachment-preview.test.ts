import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"
import * as React from "react"
import ts from "typescript"

type Element = React.ReactElement<Record<string, any>>
function elements(node: unknown): Element[] {
  if (Array.isArray(node)) return node.flatMap(elements)
  if (!React.isValidElement(node)) return []
  const element = node as Element
  return [element, ...elements(element.props.children)]
}

// Exercise the real event/effect code without installing a DOM or using a browser.
async function harness(file: string, name: string, api: Record<string, (...args: any[]) => any> = {}) {
  const key = `__attachment_${file}_${Math.random()}`
  const globals = globalThis as unknown as Record<string, any>
  const cells: any[] = []
  const effects: Array<() => void> = []
  let cursor = 0
  globals[key] = {
    ...React, ...api,
    useState(initial: any) {
      const i = cursor++
      if (!(i in cells)) cells[i] = typeof initial === "function" ? initial() : initial
      return [cells[i], (value: any) => { cells[i] = typeof value === "function" ? value(cells[i]) : value }]
    },
    useRef(initial: any) {
      const i = cursor++
      if (!(i in cells)) cells[i] = { current: initial }
      return cells[i]
    },
    useEffect(effect: () => unknown, deps: unknown[]) {
      const i = cursor++
      if (cells[i] && deps?.every((dep, index) => Object.is(dep, cells[i].deps[index]))) return
      effects.push(() => {
        cells[i]?.cleanup?.()
        cells[i] = { deps, cleanup: effect() }
      })
    },
  }
  const data = (source: string) => "data:text/javascript," + encodeURIComponent(source)
  const imports: Record<string, string> = {
    react: data(["useState", "useRef", "useEffect", "lazy", "Suspense"].map((n) =>
      `export const ${n} = (...args) => globalThis[${JSON.stringify(key)}].${n}(...args);`,
    ).join("\n")),
    "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
    "lucide-react": import.meta.resolve("lucide-react"),
    "sonner": data("export const toast = {error() {}}"),
    "@/i18n/use-t": data("export const useT = () => (key) => key"),
    "@/api/client": data(["getChatAttachmentSource", "getChatAttachmentPreview"].map((n) =>
      `export const ${n} = (...args) => globalThis[${JSON.stringify(key)}].${n}(...args);`,
    ).join("\n")),
    "@/lib/chat-attachments": new URL("../src/lib/chat-attachments.ts", import.meta.url).href,
    "@/components/ui/button": data("export function Button() {}"),
    "@/components/ui/dialog": data(["Dialog", "DialogContent", "DialogTitle", "DialogDescription"].map((n) => `export function ${n}() {}`).join("\n")),
    "@/components/file-mgmt/raw-file-viewer": data("export function RawFileViewer() {}"),
    "./chat-attachment-preview": data("export function ChatAttachmentPreview() {}"),
  }
  const source = await readFile(new URL(`../src/components/chat/${file}.tsx`, import.meta.url), "utf8")
  const compiled = ts.transpileModule(source, {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText
    .replace(/import ["'][^"']+\.css["'];?/g, "")
    .replace(/from ["']([^"']+)["']/g, (statement, path: string) => imports[path] ? `from ${JSON.stringify(imports[path])}` : statement)
    .replace(/import\(["']([^"']+)["']\)/g, (statement, path: string) => imports[path] ? `import(${JSON.stringify(imports[path])})` : statement)
  const module = await import(data(compiled))
  return {
    render(props: Record<string, unknown>) { cursor = 0; return elements(module[name](props)) },
    async flush() { effects.splice(0).forEach((effect) => effect()); await new Promise(setImmediate) },
    dispose() { cells.forEach((cell) => cell?.cleanup?.()); delete globals[key] },
  }
}

const attachment = { id: "file", session_id: "session", filename: "brief.pdf", size: 177152, warnings: [], status: "ready" }

test("opening a citation passes its page to the internal PDF preview", async () => {
  const h = await harness("chat-attachment-preview", "ChatAttachmentPreview", {
    getChatAttachmentSource: async () => new Blob(["PDF"], { type: "application/pdf" }),
  })
  try {
    const props = { attachment, initialPage: 3, onClose() {}, triggerRef: { current: null } }
    h.render(props); await h.flush()
    const viewer = h.render(props).find((e) => e.props.filename === "brief.pdf" && e.props.url)
    assert.equal(viewer?.props.initialPage, 3)
  } finally { h.dispose() }
})

test("clicking a sent file opens its preview without downloading the source", async () => {
  let sourceCalls = 0
  const h = await harness("chat-attachments", "ChatAttachmentCards", {
    getChatAttachmentSource: async () => { sourceCalls++; return new Blob(["PDF"]) },
  })
  try {
    const props = { attachments: [attachment] }
    let tree = h.render(props)
    const button = tree.find((e) => e.type === "button")!
    button.props.onClick({ currentTarget: { focus() {} } })
    tree = h.render(props)
    assert.equal(sourceCalls, 0, "Opening a card must not invoke the download action")
    const preview = tree.find((e) => e.props.attachment === attachment && e.props.onClose)
    assert.ok(preview, "Click must mount an internal preview")
    preview.props.onClose()
    assert.ok(!h.render(props).some((e) => e.props.attachment === attachment && e.props.onClose))
  } finally { h.dispose() }
})

test("PDF preview loads authenticated source bytes and revokes its URL on close", async () => {
  const calls: string[] = []
  const revoked: string[] = []
  const originalRevoke = URL.revokeObjectURL
  URL.revokeObjectURL = (url) => { revoked.push(url); originalRevoke(url) }
  const h = await harness("chat-attachment-preview", "ChatAttachmentPreview", {
    getChatAttachmentSource: async (sid, aid) => { calls.push(`${sid}/${aid}`); return new Blob(["PDF"]) },
  })
  try {
    const props = { attachment, onClose() {}, triggerRef: { current: null } }
    h.render(props)
    await h.flush()
    const viewer = h.render(props).find((e) => e.props.filename === "brief.pdf" && e.props.url)
    assert.ok(viewer, "The existing viewer receives a blob URL and the original filename")
    assert.match(viewer.props.url, /^blob:/)
    assert.deepEqual(calls, ["session/file"])
    h.dispose()
    assert.ok(revoked.includes(viewer.props.url))
  } finally { URL.revokeObjectURL = originalRevoke }
})

test("PPT preview uses converted PDF pages while explicit download keeps the original presentation", async () => {
  const preview = new Blob(["converted PDF"], { type: "application/pdf" })
  const original = new Blob(["original PPTX"])
  let previewCalls = 0
  let sourceCalls = 0
  const urls = new Map<string, Blob>()
  const anchors: any[] = []
  const createUrl = URL.createObjectURL
  URL.createObjectURL = (blob) => { const url = createUrl(blob); urls.set(url, blob); return url }
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "document")
  Object.defineProperty(globalThis, "document", { configurable: true, value: {
    body: { appendChild() {} },
    createElement() { const anchor = { click() {}, remove() {} }; anchors.push(anchor); return anchor },
  } })
  const h = await harness("chat-attachment-preview", "ChatAttachmentPreview", {
    getChatAttachmentPreview: async () => { previewCalls++; return preview },
    getChatAttachmentSource: async () => { sourceCalls++; return original },
  })
  try {
    const props = { attachment: { ...attachment, filename: "slides.PPTX" }, onClose() {}, triggerRef: { current: null } }
    h.render(props)
    await h.flush()
    const tree = h.render(props)
    const viewer = tree.find((e) => e.props.filename === "slides.pdf" && e.props.url)
    assert.ok(viewer, "Converted slides must use the PDF viewer rather than the PPTX renderer")
    assert.equal(urls.get(viewer.props.url), preview)
    assert.equal(previewCalls, 1)
    assert.equal(sourceCalls, 0)
    await tree.find((e) => e.props["aria-label"] === "chat.attachments.download")!.props.onClick()
    assert.equal(sourceCalls, 1)
    assert.equal(anchors[0].download, "slides.PPTX")
    assert.equal(urls.get(anchors[0].href), original)
  } finally {
    h.dispose()
    URL.createObjectURL = createUrl
    urls.forEach((_, url) => URL.revokeObjectURL(url))
    if (descriptor) Object.defineProperty(globalThis, "document", descriptor)
    else Reflect.deleteProperty(globalThis, "document")
  }
})

test("PPTX preview without conversion retains the existing Office viewer", async () => {
  const h = await harness("chat-attachment-preview", "ChatAttachmentPreview", {
    getChatAttachmentPreview: async () => new Blob(["PPTX"], {
      type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }),
    getChatAttachmentSource: async () => { throw new Error("Preview must use its own endpoint") },
  })
  try {
    const props = { attachment: { ...attachment, filename: "slides.pptx" }, onClose() {}, triggerRef: { current: null } }
    h.render(props)
    await h.flush()
    assert.ok(h.render(props).some((e) => e.props.filename === "slides.pptx" && e.props.url))
  } finally { h.dispose() }
})

test("image preview uses a normalized rendition; explicit download retains original bytes and filename", async () => {
  let sourceCalls = 0
  let previewCalls = 0
  let clicks = 0
  const original = new Blob(["original TIFF bytes"])
  const normalized = new Blob(["preview PNG"], { type: "image/png" })
  const anchors: any[] = []
  const urls = new Map<string, Blob>()
  const createUrl = URL.createObjectURL
  URL.createObjectURL = (blob) => { const url = createUrl(blob); urls.set(url, blob); return url }
  const documentDescriptor = Object.getOwnPropertyDescriptor(globalThis, "document")
  Object.defineProperty(globalThis, "document", { configurable: true, value: {
    body: { appendChild() {} },
    createElement() { const a = { click() { clicks++ }, remove() {} }; anchors.push(a); return a },
  } })
  const h = await harness("chat-attachment-preview", "ChatAttachmentPreview", {
    getChatAttachmentPreview: async () => { previewCalls++; return normalized },
    getChatAttachmentSource: async () => { sourceCalls++; return original },
  })
  try {
    const props = { attachment: { ...attachment, filename: "scan.tiff" }, onClose() {}, triggerRef: { current: null } }
    h.render(props)
    await h.flush()
    const tree = h.render(props)
    assert.equal(previewCalls, 1)
    assert.equal(sourceCalls, 0)
    assert.equal(clicks, 0)
    assert.ok(tree.some((e) => e.type === "img" && e.props.alt === "scan.tiff"))
    await tree.find((e) => e.props["aria-label"] === "chat.attachments.download")!.props.onClick()
    assert.equal(sourceCalls, 1)
    assert.equal(clicks, 1)
    assert.equal(anchors[0].download, "scan.tiff")
    assert.equal(urls.get(anchors[0].href), original)
  } finally {
    h.dispose()
    URL.createObjectURL = createUrl
    urls.forEach((_, url) => URL.revokeObjectURL(url))
    if (documentDescriptor) Object.defineProperty(globalThis, "document", documentDescriptor)
    else Reflect.deleteProperty(globalThis, "document")
  }
})

test("closing while preview loads ignores the late response and creates no leaked URL", async () => {
  let resolve!: (blob: Blob) => void
  const h = await harness("chat-attachment-preview", "ChatAttachmentPreview", {
    getChatAttachmentSource: () => new Promise<Blob>((done) => { resolve = done }),
  })
  h.render({ attachment, onClose() {}, triggerRef: { current: null } })
  await h.flush()
  h.dispose()
  let created = 0
  const createUrl = URL.createObjectURL
  URL.createObjectURL = (blob) => { created++; return createUrl(blob) }
  try {
    resolve(new Blob(["late PDF"]))
    await new Promise(setImmediate)
    assert.equal(created, 0)
  } finally { URL.createObjectURL = createUrl }
})

test("failed preview stays inside the dialog and retry loads the viewer", async () => {
  let calls = 0
  const h = await harness("chat-attachment-preview", "ChatAttachmentPreview", {
    getChatAttachmentSource: async () => {
      if (++calls === 1) throw new Error("Unavailable")
      return new Blob(["PDF"])
    },
  })
  try {
    const props = { attachment, onClose() {}, triggerRef: { current: null } }
    h.render(props)
    await h.flush()
    let tree = h.render(props)
    assert.ok(tree.some((e) => e.props.role === "alert"))
    assert.ok(tree.some((e) => e.props["aria-label"] === "chat.attachments.download"))
    assert.equal(calls, 1, "A failed preview must not silently trigger a source download")
    const retry = tree.find((e) => e.props.onClick && e.props.children?.includes?.("chat.attachments.retry"))!
    assert.ok(retry)
    retry.props.onClick()
    h.render(props)
    await h.flush()
    tree = h.render(props)
    assert.equal(calls, 2)
    assert.ok(tree.some((e) => e.props.filename === "brief.pdf" && e.props.url))
    assert.ok(!tree.some((e) => e.props.role === "alert"))
  } finally { h.dispose() }
})

test("closing the preview restores focus through the dialog's original trigger", async () => {
  const h = await harness("chat-attachment-preview", "ChatAttachmentPreview")
  try {
    let closed = 0
    const triggerRef = { current: { focus() {} } }
    const props = { attachment, triggerRef, onClose() { closed++ } }
    let tree = h.render(props)
    assert.ok(tree.some((e) => e.props.finalFocus === triggerRef && e.props.forceOverlay))
    const dialog = tree.find((e) => e.props.onOpenChange)!
    dialog.props.onOpenChange(false)
    tree = h.render(props)
    assert.ok(tree.some((e) => e.props.open === false))
    dialog.props.onOpenChangeComplete(false)
    assert.equal(closed, 1)
  } finally { h.dispose() }
})

test("local preparation limitations are localized inside collapsed parsing details", async () => {
  const h = await harness("chat-attachments", "ChatAttachmentCards")
  try {
    const file = { ...attachment, parser: "local", warnings: [
      "Whole-slide rendering is unavailable; presentation text and embedded images are available, but charts and layout may be incomplete.",
      "Some images have no complete text reading; charts and layout may be incomplete for this model.",
    ] }
    const tree = h.render({ attachments: [file] })
    const details = tree.find((e) => e.props.attachment === file && typeof e.type === "function" && e.type.name === "AttachmentParseDetails")!
    const rendered = elements((details.type as Function)(details.props))
    assert.ok(rendered.some((e) => e.type === "details" && !e.props.open))
    const labels = rendered.filter((e) => e.type === "p").map((e) => e.props.children)
    assert.ok(labels.includes("chat.attachments.slideRenderUnavailable"))
    assert.ok(labels.includes("chat.attachments.incompleteTextReading"))
  } finally { h.dispose() }
})
