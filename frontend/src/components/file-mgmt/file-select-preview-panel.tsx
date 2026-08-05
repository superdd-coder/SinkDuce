/**
 * File preview for "select existing file" flows.
 * - Inline: embed in dialog left column
 * - Floating: fixed portal left of an anchor (node detail), avoids overflow clip
 */
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react"
import { createPortal } from "react-dom"
import { X } from "lucide-react"
import { cn } from "@/lib/utils"
import type { FileSummary } from "@/types/file-mgmt"
import { getFilePreviewUrl } from "@/api/client"
import {
  RawFileViewer,
  resolveRawFilename,
} from "@/components/file-mgmt/raw-file-viewer"

export function FileSelectPreviewPanel({
  collectionId,
  file,
  onClose,
  className,
}: {
  collectionId: string
  file: FileSummary | null
  onClose?: () => void
  className?: string
}) {
  const source =
    file?.source || (file ? `__file__:${file.file_id}` : null)
  const url =
    source && collectionId
      ? getFilePreviewUrl(source, { collection: collectionId })
      : null
  const name = file
    ? resolveRawFilename(
        file.filename,
        file.display_name,
        file.original_ext ? `file.${file.original_ext}` : null
      )
    : "file.bin"

  return (
    <div
      className={cn(
        "flex flex-col min-h-0 min-w-0 overflow-hidden rounded-xl border border-border bg-background shadow-lg",
        className
      )}
    >
      <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-border">
        <span
          className="flex-1 min-w-0 text-[11px] font-medium truncate"
          title={file ? file.display_name || file.filename : "Preview"}
        >
          {file ? file.display_name || file.filename : "Preview"}
        </span>
        {onClose && (
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground shrink-0 p-0.5"
            onClick={onClose}
            title="Close preview"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">
        {file && url ? (
          <RawFileViewer
            key={file.file_id}
            url={url}
            filename={name}
            downloadUrl={url}
            className="h-full border-0 rounded-none"
          />
        ) : (
          <div className="h-full flex items-center justify-center p-4 text-[11px] text-muted-foreground/60 text-center">
            Click a file to preview
          </div>
        )}
      </div>
    </div>
  )
}

/** A4 portrait width/height (210mm / 297mm). */
export const A4_PORTRAIT_RATIO = 210 / 297
const PREVIEW_GAP = 8
/** Above timeline chrome; high enough to clear overflow stacks. */
const PREVIEW_Z = 80

/**
 * Width for an A4-portrait frame given a target height, clamped to viewport.
 */
export function a4PortraitWidth(
  heightPx: number,
  maxWidthPx: number
): number {
  const ideal = Math.max(120, heightPx) * A4_PORTRAIT_RATIO
  // Prefer A4; never exceed available space; keep a usable minimum
  return Math.max(260, Math.min(ideal, maxWidthPx))
}

/**
 * Fixed-position preview to the left of *anchorRef*, same top/height as the
 * anchor, width ≈ A4 portrait ratio. Portaled to document.body so parent
 * overflow-hidden cannot clip it.
 *
 * Close: X button, or mousedown outside the panel (blank / other UI).
 */
export function FileSelectPreviewFloating({
  collectionId,
  file,
  open,
  anchorRef,
  onClose,
}: {
  collectionId: string
  file: FileSummary | null
  open: boolean
  anchorRef: RefObject<HTMLElement | null>
  onClose?: () => void
}) {
  const [box, setBox] = useState<{
    top: number
    height: number
    left: number
    width: number
  } | null>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    if (!open || !anchorRef.current) {
      setBox(null)
      return
    }
    const el = anchorRef.current
    const update = () => {
      const r = el.getBoundingClientRect()
      const height = Math.max(120, r.height)
      const maxW = Math.max(200, r.left - PREVIEW_GAP - 8)
      const width = a4PortraitWidth(height, maxW)
      let left = r.left - PREVIEW_GAP - width
      if (left < 8) left = 8
      setBox({ top: r.top, height, left, width })
    }
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    window.addEventListener("resize", update)
    window.addEventListener("scroll", update, true)
    return () => {
      ro.disconnect()
      window.removeEventListener("resize", update)
      window.removeEventListener("scroll", update, true)
    }
  }, [open, file?.file_id, anchorRef])

  // Click outside panel → close entire floating window
  useEffect(() => {
    if (!open || !onClose) return
    const onPointerDown = (e: PointerEvent) => {
      const t = e.target as Node | null
      if (!t) return
      // Inside the floating panel (including X)
      if (panelRef.current?.contains(t)) return
      // Inside the file tree / select zone — keep open so multi-select works
      if (
        t instanceof Element &&
        t.closest("[data-file-select-tree], [data-file-select-zone]")
      ) {
        return
      }
      onClose()
    }
    // Capture so we run before other handlers; slight delay avoids the same
    // click that opened the panel from immediately closing it.
    const id = window.setTimeout(() => {
      document.addEventListener("pointerdown", onPointerDown, true)
    }, 0)
    return () => {
      window.clearTimeout(id)
      document.removeEventListener("pointerdown", onPointerDown, true)
    }
  }, [open, onClose])

  if (!open || !box || typeof document === "undefined") return null

  return createPortal(
    <div
      ref={panelRef}
      className="fixed animate-in slide-in-from-right-2 fade-in-0 duration-200"
      style={{
        top: box.top,
        left: box.left,
        width: box.width,
        height: box.height,
        zIndex: PREVIEW_Z,
      }}
      data-file-select-preview
    >
      <FileSelectPreviewPanel
        collectionId={collectionId}
        file={file}
        onClose={onClose}
        className="h-full w-full"
      />
    </div>,
    document.body
  )
}
