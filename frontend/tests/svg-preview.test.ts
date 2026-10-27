import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { test } from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import ts from "typescript"

async function helpers() {
  const preview = await import("../src/lib/svg-preview.ts").catch((error: { code?: string }) => {
    if (error.code === "ERR_MODULE_NOT_FOUND") return null
    throw error
  })
  assert.ok(preview, "SVG preview helpers must be implemented")
  return preview
}

const drawing = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/></svg>'

const legacyDrawing = `svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  defs>
    marker id="arrow">
      path d="M0 0L10 5L0 10Z"/>
    </marker>
  </defs>
  rect x="1" y="1" width="22" height="22"/>
  text x="2" y="12">中 English &amp; labels</text>
</svg>`

test("viewBox-only image geometry uses the SVG aspect ratio rather than browser fallback dimensions", async () => {
  const { getSvgPreviewSize } = await helpers()
  assert.equal(typeof getSvgPreviewSize, "function")
  const natural = { width: 300, height: 150 }
  for (const source of [
    '<svg viewBox="0 0 900 520"/>',
    "<?xml version='1.0'?><!-- <svg viewBox='0 0 1 1'/> --><svg aria-label='A > B' viewBox='-10,20,9e2,5.2e2'/>",
  ]) {
    assert.deepEqual(getSvgPreviewSize(source, natural), { width: 900, height: 520 })
  }
  assert.deepEqual(getSvgPreviewSize(legacyDrawing, natural), { width: 24, height: 24 })
})

test("explicit image dimensions and missing or invalid root viewBoxes retain decoder dimensions", async () => {
  const { getSvgPreviewSize } = await helpers()
  assert.equal(typeof getSvgPreviewSize, "function")
  const natural = { width: 400, height: 200 }
  for (const source of [
    '<svg width="400" height="200" viewBox="0 0 900 520"/>',
    '<svg width="400" viewBox="0 0 900 520"/>',
    '<svg><svg viewBox="0 0 900 520"/></svg>',
    '<svg/>',
    '<svg viewBox="0 0 900 0"/>',
    '<svg viewBox="0 0 -900 520"/>',
    '<svg viewBox="0 0 Infinity 520"/>',
    '<svg viewBox="0 0 900"/>',
    'plain text',
  ]) {
    assert.deepEqual(getSvgPreviewSize(source, natural), natural)
  }
})

test("the known legacy lost-opening-delimiter form gets a preview-only recovery", async () => {
  const { getSvgPreviewStatus, createSvgPreview } = await helpers()
  assert.equal(getSvgPreviewStatus(legacyDrawing, false), "pending")
  assert.equal(getSvgPreviewStatus(legacyDrawing, true), "recovered")
  const preview = createSvgPreview(legacyDrawing, true)
  assert.ok(preview)
  try {
    const renderedSource = await (await fetch(preview.url)).text()
    assert.equal(renderedSource, `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  <defs>
    <marker id="arrow">
      <path d="M0 0L10 5L0 10Z"/>
    </marker>
  </defs>
  <rect x="1" y="1" width="22" height="22"/>
  <text x="2" y="12">中 English &amp; labels</text>
</svg>`)
    assert.ok(legacyDrawing.startsWith("svg xmlns="), "recovery must not mutate the original source")
  } finally {
    preview.dispose()
  }
})

test("legacy CSS and SMIL animation openings are recovered in the passive image copy", async () => {
  const { createSvgPreview } = await helpers()
  const source = `svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  style>
    @keyframes spin { to { transform: rotate(360deg); } }
    .wheel { animation: spin 1s linear infinite; }
  </style>
  path class="wheel" d="M0 0L10 10">
    animate attributeName="d" dur="1s" repeatCount="indefinite"
      values="M0 0L10 10;M0 0L20 20;M0 0L10 10"/>
    animateTransform attributeName="transform" type="translate" dur="1s"
      repeatCount="indefinite" values="0 0;10 10;0 0"/>
  </path>
</svg>`
  const preview = createSvgPreview(source, true)
  assert.ok(preview)
  try {
    assert.equal(await (await fetch(preview.url)).text(), `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <style>
    @keyframes spin { to { transform: rotate(360deg); } }
    .wheel { animation: spin 1s linear infinite; }
  </style>
  <path class="wheel" d="M0 0L10 10">
    <animate attributeName="d" dur="1s" repeatCount="indefinite"
      values="M0 0L10 10;M0 0L20 20;M0 0L10 10"/>
    <animateTransform attributeName="transform" type="translate" dur="1s"
      repeatCount="indefinite" values="0 0;10 10;0 0"/>
  </path>
</svg>`)
    assert.ok(source.includes("\n  style>"), "the original source must retain the reported defect")
  } finally {
    preview.dispose()
  }
})

test("correctly tagged SVG animations reach the image decoder unchanged", async () => {
  const { getSvgPreviewStatus, createSvgPreview } = await helpers()
  const source = '<svg xmlns="http://www.w3.org/2000/svg"><style>@keyframes bob { to { transform: translateY(2px) } }</style><g><animate attributeName="opacity" values="0;1;0" dur="1s" repeatCount="indefinite"/><animateTransform attributeName="transform" type="rotate" values="0;360" dur="1s" repeatCount="indefinite"/></g></svg>'
  assert.equal(getSvgPreviewStatus(source, true), "ready")
  const preview = createSvgPreview(source, true)
  assert.ok(preview)
  try {
    assert.equal(await (await fetch(preview.url)).text(), source)
  } finally {
    preview.dispose()
  }
})

test("legacy recovery requires the exact lost-root signature and a real closing root", async () => {
  const { getSvgPreviewStatus, createSvgPreview } = await helpers()
  for (const source of [
    'svg width="24" height="24">\nrect width="24" height="24"/>\n</svg>',
    'svg xmlns="http://www.w3.org/2000/svg">\nrect width="24" height="24"/>',
    `An explanation\n${legacyDrawing}`,
    `${legacyDrawing}\nUnrelated trailing text`,
    'svg xmlns="http://www.w3.org/2000/svg">\n<svg />\n</svg>',
  ]) {
    assert.equal(getSvgPreviewStatus(source, true), "not-svg")
    assert.equal(createSvgPreview(source, true), null)
  }
})

test("legacy recovery does not rewrite unknown tags or tag-like text inside a line", async () => {
  const { createSvgPreview } = await helpers()
  const source = 'svg xmlns="http://www.w3.org/2000/svg">\n  arbitrary data="untouched"/>\n  text>words path d="unchanged"/></text>\n</svg>'
  const preview = createSvgPreview(source, true)
  assert.ok(preview)
  try {
    assert.equal(await (await fetch(preview.url)).text(), '<svg xmlns="http://www.w3.org/2000/svg">\n  arbitrary data="untouched"/>\n  <text>words path d="unchanged"/></text>\n</svg>')
  } finally {
    preview.dispose()
  }
})

test("the preview byte limit also counts delimiters inserted into a legacy image", async () => {
  const { getSvgPreviewStatus, createSvgPreview, MAX_SVG_PREVIEW_BYTES } = await helpers()
  const prefix = 'svg xmlns="http://www.w3.org/2000/svg">\ntext>'
  const suffix = "</text>\n</svg>"
  const source = `${prefix}${"x".repeat(MAX_SVG_PREVIEW_BYTES - prefix.length - suffix.length)}${suffix}`
  assert.equal(new Blob([source]).size, MAX_SVG_PREVIEW_BYTES)
  assert.equal(getSvgPreviewStatus(source, true), "too-large")
  assert.equal(createSvgPreview(source, true), null)
})

test("already-tagged SVG, including malformed XML, reaches the decoder unchanged", async () => {
  const { createSvgPreview } = await helpers()
  for (const source of [
    '<svg xmlns="http://www.w3.org/2000/svg"><text>\npath d="literal text"/>\n</text></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><path></svg>',
  ]) {
    const preview = createSvgPreview(source, true)
    assert.ok(preview)
    try {
      assert.equal(await (await fetch(preview.url)).text(), source)
    } finally {
      preview.dispose()
    }
  }
})

test("SVG remains pending until the real Markdown fence is closed", async () => {
  const { getSvgPreviewStatus, createSvgPreview } = await helpers()
  assert.equal(getSvgPreviewStatus(drawing, false), "pending")
  assert.equal(createSvgPreview(drawing, false), null)
  assert.equal(getSvgPreviewStatus(drawing, true), "ready")
})

test("non-SVG content stays available as source instead of receiving a preview URL", async () => {
  const { getSvgPreviewStatus, createSvgPreview } = await helpers()
  for (const source of ["", "plain text", "<div>HTML</div>", "<svg-script>text</svg-script>"]) {
    assert.equal(getSvgPreviewStatus(source, true), "not-svg")
    assert.equal(createSvgPreview(source, true), null)
  }
})

test("SVG candidate detection accepts an XML declaration and comments without pretending to validate XML", async () => {
  const { getSvgPreviewStatus } = await helpers()
  assert.equal(getSvgPreviewStatus(`<?xml version="1.0"?>\n<!-- diagram -->\n${drawing}`, true), "ready")
  assert.equal(getSvgPreviewStatus("<svg><path></svg>", true), "ready")
})

test("SVG preview limit measures UTF-8 bytes, including multibyte labels", async () => {
  const { getSvgPreviewStatus, MAX_SVG_PREVIEW_BYTES, createSvgPreview } = await helpers()
  const prefix = '<svg xmlns="http://www.w3.org/2000/svg"><text>'
  const suffix = "</text></svg>"
  const boundary = `${prefix}${"x".repeat(MAX_SVG_PREVIEW_BYTES - prefix.length - suffix.length)}${suffix}`
  assert.equal(getSvgPreviewStatus(boundary, true), "ready")
  assert.equal(getSvgPreviewStatus(`${boundary} `, true), "too-large")
  const multibyte = `${prefix}${"图".repeat(100_000)}${suffix}`
  assert.ok(multibyte.length < MAX_SVG_PREVIEW_BYTES)
  assert.equal(getSvgPreviewStatus(multibyte, true), "too-large")
  assert.equal(createSvgPreview(multibyte, true), null)
})

test("SVG preview creates a passive image Blob with the original source and revokes it", async () => {
  const { createSvgPreview } = await helpers()
  const source = '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(1)</script><text>diagram</text></svg>'
  const preview = createSvgPreview(source, true)
  assert.ok(preview)
  assert.ok(preview.url.startsWith("blob:"))
  const response = await fetch(preview.url)
  assert.equal(response.headers.get("content-type"), "image/svg+xml;charset=utf-8")
  assert.equal(await response.text(), source)
  preview.dispose()
  preview.dispose()
  await assert.rejects(fetch(preview.url), TypeError)
})

async function component() {
  const source = await readFile(new URL("../src/components/chat/svg-code-block.tsx", import.meta.url), "utf8")
    .catch((error: { code?: string }) => {
      if (error.code === "ENOENT") return null
      throw error
    })
  assert.ok(source, "SVG code block component must be implemented")
  // Isolate the locale subscription from the global browser store. The real
  // React renderer and Blob helper are used; only translated labels are stubbed.
  const translations = "data:text/javascript," + encodeURIComponent("export const useT = () => (key) => key")
  const modal = "data:text/javascript," + encodeURIComponent("export const SvgPreviewDialog = () => null; export const SvgPreviewCanvas = () => null")
  const imports: Record<string, string> = {
    react: import.meta.resolve("react"),
    "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
    "lucide-react": import.meta.resolve("lucide-react"),
    "@/i18n/use-t": translations,
    "@/lib/svg-preview": new URL("../src/lib/svg-preview.ts", import.meta.url).href,
    "@/components/shared/svg-preview-context": "data:text/javascript,export const useSvgPreviewHost = () => null",
    "@/components/chat/svg-preview-dialog": modal,
  }
  const compiled = ts.transpileModule(source, {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText.replace(/from ["']([^"']+)["']/g, (statement, path: string) =>
    imports[path] ? `from ${JSON.stringify(imports[path])}` : statement,
  )
  return import("data:text/javascript," + encodeURIComponent(compiled))
}

test("completed SVG offers an accessible expand button while pending and invalid sources disable it", async () => {
  const { SvgCodeBlock } = await component()
  for (const [source, complete, disabled] of [[drawing, true, false], [drawing, false, true], ["invalid", true, true]] as const) {
    const html = renderToStaticMarkup(createElement(SvgCodeBlock, { source, complete }))
    const button = html.match(/<button\b[^>]*aria-label="chat\.svgExpand"[^>]*>/)?.[0]
    assert.ok(button, "the SVG toolbar must expose the enlarged preview action")
    assert.equal(/\bdisabled(?:=|\s|>)/.test(button), disabled)
  }
})

test("complete SVG renders only a passive img, without placing active SVG in the page", async () => {
  const { SvgCodeBlock } = await component()
  const source = '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(1)</script></svg>'
  const html = renderToStaticMarkup(createElement(SvgCodeBlock, { source, complete: true }))
  assert.match(html, /<img\b/)
  assert.doesNotMatch(html, /<(?:svg|script|iframe|object)\b/)
  assert.doesNotMatch(html, /\bonload=/)
})

test("an unfinished SVG shows generating status and never attaches an image", async () => {
  const { SvgCodeBlock } = await component()
  const html = renderToStaticMarkup(createElement(SvgCodeBlock, { source: drawing, complete: false }))
  assert.match(html, /chat\.svgGenerating/)
  assert.doesNotMatch(html, /<img\b/)
})

test("pending SVG status occupies the same named image viewport as the completed preview", async () => {
  const { SvgCodeBlock } = await component()
  const pending = renderToStaticMarkup(createElement(SvgCodeBlock, { source: drawing, complete: false }))
  const completed = renderToStaticMarkup(createElement(SvgCodeBlock, { source: drawing, complete: true }))
  const viewport = /<div class="chat-md-visual-preview" role="region" aria-label="chat\.svgImage" tabindex="0">/
  assert.match(pending, viewport)
  assert.match(completed, viewport)
  assert.match(pending, /chat-md-visual-preview[^>]*><p[^>]*role="status">chat\.svgGenerating<\/p><\/div>/)
  assert.doesNotMatch(pending, /<img\b/)
})

test("a recovered SVG preview is disclosed without exposing active markup", async () => {
  const { SvgCodeBlock } = await component()
  const html = renderToStaticMarkup(createElement(SvgCodeBlock, { source: legacyDrawing, complete: true }))
  assert.match(html, /chat\.svgRecovered/)
  assert.match(html, /<img\b/)
  assert.doesNotMatch(html, /<(?:svg|script|iframe|object)\b/)
})

test("invalid SVG candidates fall back to escaped original source", async () => {
  const { SvgCodeBlock } = await component()
  const html = renderToStaticMarkup(createElement(SvgCodeBlock, { source: "<script>alert(1)</script>", complete: true }))
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/)
  assert.match(html, /chat\.svgInvalid/)
  assert.doesNotMatch(html, /<(?:img|script)\b/)
})

test("fallback SVG source is a named keyboard-focusable scroll region", async () => {
  const { SvgCodeBlock } = await component()
  const html = renderToStaticMarkup(createElement(SvgCodeBlock, { source: "<invalid>diagram source</invalid>", complete: true }))
  const sourceRegion = html.match(/<pre\b[^>]*class="chat-md-visual-source"[^>]*>/)?.[0]
  assert.ok(sourceRegion, "fallback must preserve the source scroll region")
  assert.match(sourceRegion, /\brole="region"/)
  assert.match(sourceRegion, /\baria-label="chat\.svgSource"/)
  assert.match(sourceRegion, /\btabindex="0"/)
  assert.match(html, /&lt;invalid&gt;diagram source&lt;\/invalid&gt;/)
})
