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
 * Replace :::image fenced blocks with markdown image + caption.
 * Each non-empty field appears as a caption line.
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
      if (!resolvedFileId || !imageId) {
        // Cannot build a valid image URL — leave a short placeholder, not raw fences
        const d = (desc || "").trim()
        return d
          ? `\n\n*[Image: ${d.replace(/\s*\n\s*/g, " ")}]*\n\n`
          : `\n\n*[Image ${imageId || "missing"}]*\n\n`
      }
      const imgUrl = `/api/documents/${encodeURIComponent(collection)}/${encodeURIComponent(resolvedFileId)}/images/${encodeURIComponent(imageId)}`

      const parts: string[] = []
      // Collapse internal newlines + escape [ ] which would break markdown
      // image syntax (![alt](url)) by being misinterpreted as link delimiters.
      const sanitize = (s: string) =>
        s.trim().replace(/\s*\n\s*/g, " ").replace(/\[/g, "\\[").replace(/\]/g, "\\]")
      const d = desc ? sanitize(desc) : ""
      const o = ocrText ? sanitize(ocrText) : ""
      if (d) parts.push(`image description: ${d}`)
      if (o) parts.push(`ocr_text: ${o}`)
      const alt = parts.join(" | ") || "Image"
      // Caption under image so description/OCR remain visible in Source
      const caption =
        parts.length > 0
          ? `\n\n*${parts.join(" · ")}*\n\n`
          : "\n\n"
      return `![${alt}](${imgUrl})${caption}`
    },
  )
}
