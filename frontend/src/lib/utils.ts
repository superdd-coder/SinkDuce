import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// ── Preview image block transform ────────────────────────────────────────

type ImageFence = {
  start: number
  end: number
  imageId: string
  fileId: string
  ocrText: string
  description: string
}

function parseImageFences(text: string): ImageFence[] {
  if (!text || !text.includes(":::image")) return []
  const fences: ImageFence[] = []
  let i = 0
  while (i < text.length) {
    const idx = text.indexOf(":::image", i)
    if (idx < 0) break
    let pos = idx
    let imageId = ""
    let fileId = ""
    let ocrText = ""
    let description = ""
    let closeEnd: number | null = null
    let first = true
    while (pos < text.length) {
      const nl = text.indexOf("\n", pos)
      const lineEnd = nl < 0 ? text.length : nl
      const line = text.slice(pos, lineEnd).replace(/\r$/, "")
      const next = nl < 0 ? text.length : nl + 1
      const stripped = line.trim()
      if (!first && stripped === ":::") {
        closeEnd = next
        break
      }
      const lower = stripped.toLowerCase()
      if (lower.startsWith("image_id:")) imageId = stripped.slice(stripped.indexOf(":") + 1).trim()
      else if (lower.startsWith("file_id:")) fileId = stripped.slice(stripped.indexOf(":") + 1).trim()
      else if (lower.startsWith("ocr_text:")) ocrText = stripped.slice(stripped.indexOf(":") + 1).trim()
      else if (lower.startsWith("description:")) description = stripped.slice(stripped.indexOf(":") + 1).trim()
      first = false
      if (nl < 0) break
      pos = next
    }
    if (closeEnd == null) break
    if (imageId) {
      fences.push({ start: idx, end: closeEnd, imageId, fileId, ocrText, description })
    }
    i = closeEnd
  }
  return fences
}

/**
 * Replace :::image fenced blocks with an HTML <img> for Source preview.
 *
 * TipTap maps:
 *   - alt              → image caption (once)
 *   - data-visual-desc → green description panel (once)
 *
 * *fallbackFileId*: used when the block has empty `file_id:` (common in
 * parse output before ingest rewrites the field).
 */
/** Text with all ``:::image`` fences removed. */
export function stripImageBlocks(text: string): string {
  const fences = parseImageFences(text)
  if (!fences.length) return (text || "").trim()
  let out = ""
  let cursor = 0
  for (const f of fences) {
    out += text.slice(cursor, f.start)
    cursor = f.end
  }
  out += text.slice(cursor)
  return out.trim()
}

export function chunkHasImageFence(text?: string | null): boolean {
  return !!(text || "").includes(":::image")
}

/** True when the chunk is only one or more image fences (no table/prose). */
export function isImageOnlyChunk(text?: string | null): boolean {
  const raw = text || ""
  if (!raw.includes(":::image")) return false
  return !stripImageBlocks(raw)
}

function renderImageHtml(
  collection: string,
  fallbackFileId: string,
  imageId: string,
  fileId: string,
  ocrText: string,
  desc: string,
): string {
  const resolvedFileId = (fileId || "").trim() || fallbackFileId
  const sanitize = (s: string) => s.trim().replace(/\s*\n\s*/g, " ")
  const d = desc ? sanitize(desc) : ""
  const o = ocrText ? sanitize(ocrText) : ""
  if (!resolvedFileId || !imageId) {
    const fallbackText = d || o
    return fallbackText
      ? `\n\n*[Image: ${fallbackText}]*\n\n`
      : `\n\n*[Image ${imageId || "missing"}]*\n\n`
  }
  const imgUrl = `/api/documents/${encodeURIComponent(collection)}/${encodeURIComponent(resolvedFileId)}/images/${encodeURIComponent(imageId)}`
  const escAttr = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;")
  const attrs: string[] = [`src="${imgUrl}"`, `data-image-id="${imageId}"`]
  if (o) {
    attrs.push(`alt="${escAttr(o)}"`)
    attrs.push(`data-ocr-text="${encodeURIComponent(o)}"`)
  }
  if (d) attrs.push(`data-visual-desc="${encodeURIComponent(d)}"`)
  return `\n\n<img ${attrs.join(" ")} />\n\n`
}

export function listImageFenceFields(text?: string | null) {
  return parseImageFences(text || "").map((f) => ({
    imageId: f.imageId,
    fileId: f.fileId,
    ocrText: f.ocrText,
    description: f.description,
  }))
}

export function transformImageBlocks(
  text: string,
  collection: string,
  fallbackFileId?: string | null,
  options?: { skipImageIds?: Set<string>; recordSeen?: boolean },
): string {
  const fallback = (fallbackFileId || "").trim()
  const skip = options?.skipImageIds
  const fences = parseImageFences(text)
  if (!fences.length) return text
  const hasProse = !!stripImageBlocks(text)
  let out = ""
  let cursor = 0
  for (const f of fences) {
    out += text.slice(cursor, f.start)
    if (f.imageId && skip?.has(f.imageId) && hasProse) {
      out += "\n\n"
    } else {
      if (f.imageId && options?.recordSeen) skip?.add(f.imageId)
      out += renderImageHtml(
        collection, fallback, f.imageId, f.fileId, f.ocrText, f.description,
      )
    }
    cursor = f.end
  }
  out += text.slice(cursor)
  return out
}
