import { useEffect, useMemo, useRef, useState } from "react"
import { splitExtractParts } from "@/lib/utils"
import { useT } from "@/i18n/use-t"

export interface ParseTextViewerProps {
  text: string
  collectionId: string
  fileId?: string | null
  highlightOffset?: number
  highlightLength?: number
}

function positionedParts(text: string, collectionId: string, fileId?: string | null) {
  let cursor = 0
  const result = []
  for (const part of splitExtractParts(text, collectionId, fileId)) {
    if (part.kind !== "text") { result.push(part); continue }
    const offset = text.indexOf(part.text, cursor)
    if (offset >= 0) cursor = offset + part.text.length
    result.push({ ...part, offset })
  }
  return result
}

/**
 * Read-only Parse body as plain text plus inline document images.
 * No markdown/table engine — a full renderer on large extracts stalled
 * the machine. Image fences used to flatten to "[Image]" so some Source
 * content could not be opened.
 */
export function ParseTextViewer({
  text,
  collectionId,
  fileId,
  highlightOffset,
  highlightLength = 1,
}: ParseTextViewerProps) {
  const t = useT()
  const parts = useMemo(
    () => positionedParts(text, collectionId, fileId),
    [text, collectionId, fileId],
  )
  const [openSrc, setOpenSrc] = useState<string | null>(null)
  const marker = useRef<HTMLElement | null>(null)
  const intersects = (offset: number, length: number) => highlightOffset !== undefined && offset >= 0 && highlightOffset < offset + length && highlightOffset + highlightLength > offset
  const firstHit = parts.findIndex((part) => intersects(part.offset, part.kind === "text" ? part.text.length : part.length))
  useEffect(() => {
    if (highlightOffset === undefined) return
    let next = 0
    const frame = requestAnimationFrame(() => { next = requestAnimationFrame(() => marker.current?.scrollIntoView?.({ block: "center" })) })
    return () => { cancelAnimationFrame(frame); cancelAnimationFrame(next) }
  }, [text, highlightOffset, highlightLength])

  return (
    <div data-parse-root className="h-full overflow-auto">
      {parts.map((part, i) =>
        part.kind === "text" ? (
          <pre key={i} className="pm-ws-parse-pre">
            {intersects(part.offset, part.text.length) ? <>
              {part.text.slice(0, Math.max(0, highlightOffset! - part.offset))}
              <mark data-citation-hit ref={i === firstHit ? marker : undefined}>{part.text.slice(Math.max(0, highlightOffset! - part.offset), Math.min(part.text.length, highlightOffset! + highlightLength - part.offset))}</mark>
              {part.text.slice(Math.min(part.text.length, highlightOffset! + highlightLength - part.offset))}
            </> : part.text}
          </pre>
        ) : (
          <button
            key={part.imageId + String(i)}
            type="button"
            data-citation-hit={intersects(part.offset, part.length) || undefined}
            ref={i === firstHit ? (node) => { marker.current = node } : undefined}
            className="pm-ws-parse-image-btn"
            onClick={() => setOpenSrc(part.src)}
            title={t("fileMgmt.openImage")}
          >
            <img
              src={part.src}
              alt={part.alt}
              className="pm-ws-parse-image"
            />
          </button>
        ),
      )}
      {openSrc ? (
        <button
          type="button"
          className="pm-ws-parse-lightbox"
          onClick={() => setOpenSrc(null)}
          aria-label={t("fileMgmt.closeImage")}
        >
          <img src={openSrc} alt="" className="pm-ws-parse-lightbox-img" />
        </button>
      ) : null}
    </div>
  )
}
