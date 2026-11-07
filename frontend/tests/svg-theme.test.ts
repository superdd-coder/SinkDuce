import assert from "node:assert/strict"
import { test } from "node:test"
import { themeSvgForPreview } from "../src/lib/svg-theme.ts"
import { createSvgPreview } from "../src/lib/svg-preview.ts"

test("legacy diagrams adopt porcelain/emerald while retaining semantic categories and geometry", () => {
  const source = `<svg viewBox="0 0 900 470"><rect width="900" height="470" fill="#FDFBF7"/>
    <rect x="40" y="60" width="200" height="56" rx="6" fill="#E8F1EB" stroke="#1A5E3D"/>
    <text fill="#2A2E2A">49% <tspan fill="#0F4A2D">项目 A</tspan></text>
    <path fill="none" stroke="#8A6D3B" d="M10 20 L30 40"/>
    <circle fill="#ff0000"/><rect fill="#0066cc"/><rect fill="none"/></svg>`
  const result = themeSvgForPreview(source)
  assert.match(result, /fill="#fafafa"/)
  assert.match(result, /fill="#e9efec" stroke="#174c39"/)
  assert.match(result, /<text fill="#202020">49% <tspan fill="#123e2e">项目 A<\/tspan><\/text>/)
  assert.match(result, /stroke="#806544"/)
  assert.match(result, /x="40" y="60" width="200" height="56" rx="6"/)
  assert.match(result, /d="M10 20 L30 40"/)
  assert.match(result, /<circle fill="#ff0000"\/><rect fill="#0066cc"\/><rect fill="none"\/>/)
  assert.equal(themeSvgForPreview(result), result, "the migration must be idempotent")
})

test("palette-like text, IDs, URLs, comments, embedded assets and nonpaint attributes stay exact", () => {
  const unchanged = `<svg id="#1A5E3D" data-fill="#FDFBF7" aria-label="fill='#1A5E3D'">
    <!-- <rect fill="#FDFBF7"/> -->
    <text>#1A5E3D fill="#FDFBF7" <tspan>RGB 26, 94, 61</tspan></text>
    <path fill="url(#1A5E3D)" stroke="currentColor"/>
    <image href="data:image/svg+xml,%3Csvg fill='#1A5E3D'/%3E"/>
    <script>const text = '<rect fill="#FDFBF7"/>';</script>
    <foreignObject><div style="color:#1A5E3D">HTML</div></foreignObject>
    <![CDATA[<rect fill="#FDFBF7"/>]]>
  </svg>`
  assert.equal(themeSvgForPreview(unchanged), unchanged)
})

test("inline and stylesheet paint declarations adapt without rewriting selectors or CSS content", () => {
  const source = `<svg><style><![CDATA[
    /* .old { fill: #FDFBF7; } */
    #FDFBF7 { fill: #1A5E3D; stroke: url(#0F4A2D); content: '#1A5E3D'; }
    .note { color: #2A2E2A !important; background-color: #FDFBF7; }
  ]]></style><rect style='fill: #DCEBE2; stroke:#0f4a2d; opacity: .4'/></svg>`
  const result = themeSvgForPreview(source)
  assert.match(result, /\/\* \.old \{ fill: #FDFBF7; \} \*\//)
  assert.match(result, /#FDFBF7 \{ fill: #174c39; stroke: url\(#0F4A2D\); content: '#1A5E3D'; \}/)
  assert.match(result, /color: #202020 !important; background-color: #fafafa/)
  assert.match(result, /style='fill: #dfe9e4; stroke:#123e2e; opacity: .4'/)
})

test("gradient stops, alpha and color animation keep their original transparency and timing", () => {
  const source = `<svg><stop stop-color="rgba(26, 94, 61, 0.4)" offset=".5"/>
    <stop stop-color="#1A5E3D80"/><rect fill="rgb(253,251,247)">
    <animate attributeName="fill" from="#E8F1EB" to="#DCEBE2" values="#E8F1EB; #DCEBE2;#E8F1EB" dur="2s"/>
    <animate attributeName="opacity" values="0;.4;1" dur="2s"/></rect></svg>`
  const result = themeSvgForPreview(source)
  assert.match(result, /stop-color="rgba\(23, 76, 57, 0.4\)" offset=".5"/)
  assert.match(result, /stop-color="#174c3980"/)
  assert.match(result, /fill="#fafafa"/)
  assert.match(result, /from="#e9efec" to="#dfe9e4" values="#e9efec; #dfe9e4;#e9efec" dur="2s"/)
  assert.match(result, /attributeName="opacity" values="0;.4;1" dur="2s"/)
})

test("the themed passive image is disposable and never changes the source version", async () => {
  const source = '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="#FDFBF7"/><text fill="#1A5E3D">Private label</text></svg>'
  const original = createSvgPreview(source, true)!
  const themed = createSvgPreview(source, true, "porcelain")!
  assert.ok(original && themed)
  try {
    assert.equal(await (await fetch(original.url)).text(), source)
    assert.equal(await (await fetch(themed.url)).text(), themeSvgForPreview(source))
    assert.equal(createSvgPreview(source, false, "porcelain"), null)
    assert.equal(createSvgPreview("<div>HTML</div>", true, "porcelain"), null)
  } finally {
    original.dispose()
    themed.dispose()
  }
  await assert.rejects(fetch(themed.url))
})
