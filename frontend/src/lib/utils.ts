import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// ── Preview image block transform ────────────────────────────────────────

const IMAGE_BLOCK_RE = /:::image[ \t]*\nimage_id:\s*([a-f0-9]+)\nfile_id:\s*([^\n]+)\n(?:ocr_text:[^\n]*\n)?description:\s*([\s\S]*?)\n:::/g

/**
 * Replace :::image fenced blocks with standard markdown image syntax.
 * The image URL uses file_id (not doc_source) — matches the
 * GET /api/documents/{collection}/{file_id}/images/{image_id} endpoint.
 */
export function transformImageBlocks(text: string, collection: string): string {
  return text.replace(IMAGE_BLOCK_RE, (_full, imageId: string, fileId: string, desc: string) => {
    const alt = desc?.trim() || "Image"
    return `![${alt}](/api/documents/${encodeURIComponent(collection)}/${encodeURIComponent(fileId)}/images/${encodeURIComponent(imageId)})`
  })
}
