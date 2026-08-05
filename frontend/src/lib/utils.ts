import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// ── Preview image block transform ────────────────────────────────────────

// Group 3 = ocr_text (optional), Group 4 = description (optional).
// Both fields are omitted from the block when empty.
// file_id may be blank in parse output (filled later on ingest); allow empty
// and fall back to the managed file_id of the open document.
// (?:(?!:::)[\s\S])*? — non-greedy match that NEVER crosses ::: boundaries,
// preventing the regex from swallowing body text between adjacent blocks.
// ocr_text may be multiline (e.g. long OCR output) — use same pattern as description.
// Use [ \t]* (not \s*) after colons so empty `file_id:` does not swallow the
// following newline and pull `description:` into the file_id capture group.
const IMAGE_BLOCK_RE =
  /:::image[ \t]*\nimage_id:[ \t]*([a-f0-9]+)[ \t]*\nfile_id:[ \t]*([^\n]*)\n(?:ocr_text:[ \t]*((?:(?!:::)[\s\S])*?)\n)?(?:description:[ \t]*((?:(?!:::)[\s\S])*?)\n)?:::/g

/**
 * Replace :::image fenced blocks with an HTML <img> for Source preview.
 *
 * TipTap maps:
 *   - alt              → image caption (once)
 *   - data-visual-desc → green description panel (once)
 *
 * Previously we emitted `![alt with desc+ocr](url)` **and** a trailing
 * italic caption with the same text. TipTap shows `alt` as caption, so the
 * text appeared twice under the image (common for local-parse + OCR/Vision).
 *
 * *fallbackFileId*: used when the block has empty `file_id:` (common in
 * re-parsed / historical extract text before ingest rewrites the field).
 */
export function transformImageBlocks(
  text: string,
  collection: string,
  fallbackFileId?: string | null
): string {
  const fallback = (fallbackFileId || "").trim()
  return text.replace(
    IMAGE_BLOCK_RE,
    (_full: string, imageId: string, fileId: string, ocrText: string | undefined, desc: string | undefined) => {
      const resolvedFileId = (fileId || "").trim() || fallback
      const sanitize = (s: string) => s.trim().replace(/\s*\n\s*/g, " ")
      const d = desc ? sanitize(desc) : ""
      const o = ocrText ? sanitize(ocrText) : ""

      if (!resolvedFileId || !imageId) {
        // Cannot build a valid image URL — leave a short placeholder, not raw fences
        const fallbackText = d || o
        return fallbackText
          ? `\n\n*[Image: ${fallbackText}]*\n\n`
          : `\n\n*[Image ${imageId || "missing"}]*\n\n`
      }
      const imgUrl = `/api/documents/${encodeURIComponent(collection)}/${encodeURIComponent(resolvedFileId)}/images/${encodeURIComponent(imageId)}`

      // HTML attribute escaping (attribute values in double quotes)
      const escAttr = (s: string) =>
        s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;")

      const attrs: string[] = [
        `src="${imgUrl}"`,
        `data-image-id="${imageId}"`,
      ]
      // OCR → caption (alt). Description → visual-desc panel.
      // Each field is rendered exactly once — never also as a trailing paragraph.
      if (o) attrs.push(`alt="${escAttr(o)}"`)
      if (d) attrs.push(`data-visual-desc="${encodeURIComponent(d)}"`)

      // Blank line after <img /> so markdown-it does not swallow the next block
      return `\n\n<img ${attrs.join(" ")} />\n\n`
    },
  )
}
