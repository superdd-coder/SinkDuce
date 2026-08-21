/**
 * Shared chunk body renderer — same pipeline as Recall result cards:
 * :::image fences → <img>, GFM tables, markdown/HTML via rehype-raw.
 */
import { memo } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import rehypeRaw from "rehype-raw"
import { cn, transformImageBlocks } from "@/lib/utils"
import { tr } from "@/i18n/tr"

function fileIdFromSource(source?: string): string | undefined {
  const s = (source || "").trim()
  if (s.startsWith("__file__:")) return s.slice("__file__:".length).trim() || undefined
  if (s.startsWith("file:")) return s.slice("file:".length).trim() || undefined
  return undefined
}

/**
 * Collapse the huge vertical gaps that appear after image transform:
 * original fences already sit between blank lines, and transformImageBlocks
 * adds another \n\n around <img>, which markdown turns into empty <p>s.
 *
 * Keep a single blank line before GFM tables (required by the parser) but
 * never more than that after an image.
 */
function tightenChunkMarkdown(text: string): string {
  let s = text.replace(/\r\n/g, "\n")
  // Collapse 3+ newlines → one paragraph break
  s = s.replace(/\n{3,}/g, "\n\n")
  // Collapse all blank lines immediately before/after raw <img>
  s = s.replace(/\n{2,}(<img\b[^>]*\/?>)/gi, "\n$1")
  s = s.replace(/(<img\b[^>]*\/?>)(?:[ \t]*\n){2,}/gi, "$1\n\n")
  // Image immediately followed by GFM table / HTML table → one blank line only
  s = s.replace(
    /(<img\b[^>]*\/?>)\n+(?=\|)/gi,
    "$1\n\n",
  )
  s = s.replace(
    /(<img\b[^>]*\/?>)\n+(?=<table\b)/gi,
    "$1\n\n",
  )
  return s.trim()
}

export function looksLikeChunkMarkdown(text: string): boolean {
  return (
    /<\/?(table|tr|td|th|thead|tbody|ul|ol|li|h[1-6]|pre|code|img)\b/i.test(
      text,
    ) ||
    /^#{1,6}\s/m.test(text) ||
    /\|.+\|/.test(text) ||
    /\*\*[^*]+\*\*/.test(text) ||
    /!\[[^\]]*\]\([^)]+\)/.test(text) ||
    text.includes(":::image")
  )
}

export interface ChunkMdProps {
  text: string
  collection?: string
  /** Managed file id — preferred for image URL resolution */
  fileId?: string
  /** Qdrant source key (__file__:{id}) — used when fileId is omitted */
  source?: string
  className?: string
  /** When rendering a list, skip image_ids already shown (table-source slices). */
  skipImageIds?: Set<string>
  /** Record newly rendered image_ids into skipImageIds. */
  recordSeen?: boolean
}

/**
 * Render chunk text: expand :::image fences → <img>, markdown/HTML tables.
 * Images load from /api/documents/{collection}/{file_id}/images/{image_id}.
 */
export const ChunkMd = memo(function ChunkMd({
  text,
  collection,
  fileId,
  source,
  className,
  skipImageIds,
  recordSeen,
}: ChunkMdProps) {
  const raw = text || ""
  const col = (collection || "").trim()
  const fallbackFid = (fileId || "").trim() || fileIdFromSource(source)
  const expanded = tightenChunkMarkdown(
    col && raw.includes(":::image")
      ? transformImageBlocks(raw, col, fallbackFid, { skipImageIds, recordSeen })
      : raw,
  )

  // Always use the same shell so plain vs markdown paths never mix fonts
  // (e.g. parent .pm-ws-prose-item forces serif; tables would stay sans).
  if (!looksLikeChunkMarkdown(expanded)) {
    return (
      <div className={cn("pm-recall-md", className)}>
        <p className="pm-recall-md-plain">{expanded}</p>
      </div>
    )
  }

  return (
    <div className={cn("pm-recall-md", className)}>
      <ReactMarkdown
        /* No remark-breaks: PDF/Excel extracts have many soft newlines that
         * otherwise become tall stacks of <br> between image and table. */
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw]}
        components={{
          img: ({ src, alt, ...props }) => {
            const extra = props as {
              "data-visual-desc"?: string
              "data-ocr-text"?: string
            }
            const decodeAttr = (raw?: string) => {
              if (!raw) return ""
              try {
                return decodeURIComponent(raw)
              } catch {
                return raw
              }
            }
            const desc = decodeAttr(extra["data-visual-desc"])
            const ocr = decodeAttr(extra["data-ocr-text"]) || (alt || "").trim()
            return (
              <>
                {/* eslint-disable-next-line @next/next/no-img-element -- API-served document images */}
                <img
                  src={src}
                  alt={ocr || desc || "Document figure"}
                  loading="lazy"
                  className="pm-recall-md-img"
                  {...props}
                />
                {ocr ? (
                  <span className="pm-recall-md-figocr">
                    <span className="pm-recall-md-figlabel">OCR</span>
                    {ocr}
                  </span>
                ) : null}
                {desc ? (
                  <span className="pm-recall-md-figdesc">
                    <span className="pm-recall-md-figlabel">{tr("common.description")}</span>
                    {desc}
                  </span>
                ) : null}
              </>
            )
          },
          // Drop empty paragraphs; mark figure-only paragraphs for tight CSS.
          p: ({ children, node, ...props }) => {
            const empty =
              children == null ||
              children === false ||
              (Array.isArray(children) &&
                children.every(
                  (c) =>
                    c == null ||
                    c === false ||
                    (typeof c === "string" && !c.trim()),
                ))
            if (empty) return null
            const elKids = (
              node as { children?: { type?: string; tagName?: string }[] }
            )?.children
            const elementKids = Array.isArray(elKids)
              ? elKids.filter((c) => c?.type === "element" || c?.tagName)
              : []
            const hastOnlyImg =
              elementKids.length === 1 && elementKids[0]?.tagName === "img"
            if (hastOnlyImg) {
              return (
                <p className="pm-recall-md-figure" {...props}>
                  {children}
                </p>
              )
            }
            return <p {...props}>{children}</p>
          },
          table: ({ children, ...props }) => (
            <div className="pm-recall-md-table-wrap">
              <table {...props}>{children}</table>
            </div>
          ),
        }}
      >
        {expanded}
      </ReactMarkdown>
    </div>
  )
})
