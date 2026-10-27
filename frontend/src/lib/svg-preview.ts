export const MAX_SVG_PREVIEW_BYTES = 256 * 1024

export type SvgPreviewStatus = "pending" | "too-large" | "not-svg" | "ready" | "recovered"

const svgStart = /^(?:<\?xml[\s\S]*?\?>\s*)?(?:<!--[\s\S]*?-->\s*)*<svg(?:[\s/>])/i

function inspectSvgPreview(source: string, complete: boolean): {
  status: SvgPreviewStatus
  imageSource?: string
} {
  if (!complete) return { status: "pending" }
  if (new Blob([source]).size > MAX_SVG_PREVIEW_BYTES) return { status: "too-large" }
  if (svgStart.test(source.trim())) return { status: "ready", imageSource: source }

  // Compatibility for one confirmed old streaming bug: an opening '<' was
  // lost at chunk boundaries. Only this missing-root signature is eligible;
  // restore one delimiter at line-leading known SVG tags in the image copy.
  // This is not general XML repair or sanitization. The image decoder still
  // decides validity, and the stored/displayed/copied source stays untouched.
  if (!/^svg[\t ]+(?:xmlns|viewBox)[\t ]*=/.test(source.trim())
    || !/<\/svg>\s*$/.test(source)
    || /<svg(?:[\s/>])/i.test(source)) return { status: "not-svg" }

  const imageSource = source.replace(
    /^([\t ]*)(svg|g|defs|marker|path|rect|circle|ellipse|line|polyline|polygon|text|tspan|title|desc|clipPath|mask|pattern|linearGradient|radialGradient|stop|symbol|use|image|style|animate|animateTransform)(?=[\t />])/gm,
    "$1<$2",
  )
  if (!svgStart.test(imageSource.trim())) return { status: "not-svg" }
  if (new Blob([imageSource]).size > MAX_SVG_PREVIEW_BYTES) return { status: "too-large" }
  return { status: "recovered", imageSource }
}

/**
 * A format hint, never an XML validator or security sanitizer. Browser image
 * decoding decides whether the candidate is valid SVG. Active markup remains
 * source text and is only ever supplied to an <img> as an SVG image Blob.
 */
export function getSvgPreviewStatus(source: string, complete: boolean): SvgPreviewStatus {
  return inspectSvgPreview(source, complete).status
}

/** ViewBox-only SVGs can decode at a fallback 300×150 intrinsic size. */
export function getSvgPreviewSize(source: string, naturalSize: { width: number; height: number }): { width: number; height: number } {
  const { imageSource } = inspectSvgPreview(source, true)
  const root = imageSource?.trim().match(/^(?:<\?xml[\s\S]*?\?>\s*)?(?:<!--[\s\S]*?-->\s*)*<svg\b((?:[^"'<>]|"[^"]*"|'[^']*')*)>/i)
  if (!root) return naturalSize
  const attributes = new Map(Array.from(
    root[1].matchAll(/([^\s=]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g),
    (match) => [match[1], match[2] ?? match[3]],
  ))
  // Declared dimensions define the SVG's outer viewport; retain that intent.
  if (attributes.has("width") || attributes.has("height")) return naturalSize
  const box = attributes.get("viewBox")?.trim().split(/[\s,]+/).map(Number)
  if (!box || box.length !== 4 || !box.every(Number.isFinite) || box[2] <= 0 || box[3] <= 0) return naturalSize
  return { width: box[2], height: box[3] }
}

/** Own one passive SVG image URL; call dispose when its image is detached. */
export function createSvgPreview(source: string, complete: boolean): {
  url: string
  dispose: () => void
} | null {
  const { imageSource } = inspectSvgPreview(source, complete)
  if (imageSource === undefined) return null
  const blob = new Blob([imageSource], { type: "image/svg+xml;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  let disposed = false
  return {
    url,
    dispose: () => {
      if (disposed) return
      disposed = true
      URL.revokeObjectURL(url)
    },
  }
}
