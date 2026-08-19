import { useMemo, useState } from "react"
import { splitExtractParts } from "@/lib/utils"

export interface ParseTextViewerProps {
  text: string
  collectionId: string
  fileId?: string | null
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
}: ParseTextViewerProps) {
  const parts = useMemo(
    () => splitExtractParts(text, collectionId, fileId),
    [text, collectionId, fileId],
  )
  const [openSrc, setOpenSrc] = useState<string | null>(null)

  return (
    <div data-parse-root className="h-full overflow-auto">
      {parts.map((part, i) =>
        part.kind === "text" ? (
          <pre key={i} className="pm-ws-parse-pre">
            {part.text}
          </pre>
        ) : (
          <button
            key={part.imageId + String(i)}
            type="button"
            className="pm-ws-parse-image-btn"
            onClick={() => setOpenSrc(part.src)}
            title="Open image"
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
          aria-label="Close image"
        >
          <img src={openSrc} alt="" className="pm-ws-parse-lightbox-img" />
        </button>
      ) : null}
    </div>
  )
}
