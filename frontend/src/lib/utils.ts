import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// ── Preview image block transform ────────────────────────────────────────

// Group 3 = ocr_text (optional), Group 4 = description (optional).
// Both fields are omitted from the block when empty.
// (?:(?!:::)[\s\S])*? — non-greedy match that NEVER crosses ::: boundaries,
// preventing the regex from swallowing body text between adjacent blocks.
// ocr_text may be multiline (e.g. long OCR output) — use same pattern as description.
const IMAGE_BLOCK_RE = /:::image[ \t]*\nimage_id:\s*([a-f0-9]+)\nfile_id:\s*([^\n]+)\n(?:ocr_text:\s*((?:(?!:::)[\s\S])*?)\n)?(?:description:\s*((?:(?!:::)[\s\S])*?)\n)?:::/g

/**
 * Replace :::image fenced blocks with markdown image + caption.
 * Each non-empty field appears as a caption line.
 */
export function transformImageBlocks(text: string, collection: string): string {
  return text.replace(
    IMAGE_BLOCK_RE,
    (_full: string, imageId: string, fileId: string, ocrText: string | undefined, desc: string | undefined) => {
      const imgUrl = `/api/documents/${encodeURIComponent(collection)}/${encodeURIComponent(fileId)}/images/${encodeURIComponent(imageId)}`

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
      return `![${alt}](${imgUrl})`
    },
  )
}
