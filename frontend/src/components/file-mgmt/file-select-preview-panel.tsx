/**
 * File preview for "select existing file" flows.
 * Premium soft float card — light title chrome + document body (no tool strip).
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
import { useT } from "@/i18n/use-t"
import { tr } from "@/i18n/tr"

const DOC_FADE_OUT_MS = 140
const DOC_FADE_IN_MS = 180

function previewTitle(file: FileSummary | null): string {
  if (!file) return tr("common.preview")
  return file.display_name || file.filename || tr("common.preview")
}

function previewName(file: FileSummary | null): string {
  if (!file) return "file.bin"
  return resolveRawFilename(
    file.filename,
    file.display_name,
    file.original_ext ? `file.${file.original_ext}` : null
  )
}

export function FileSelectPreviewPanel({
  collectionId,
  file,
  onClose,
  className,
  /** When true, parent drives open slide; body still crossfades on file switch. */
  bodyPhase = "in",
}: {
  collectionId: string
  file: FileSummary | null
  onClose?: () => void
  className?: string
  bodyPhase?: "in" | "out"
}) {
  const t = useT()
  const source =
    file?.source || (file ? `__file__:${file.file_id}` : null)
  const url =
    source && collectionId
      ? getFilePreviewUrl(source, { collection: collectionId })
      : null
  const title = previewTitle(file)
  const name = previewName(file)

  return (
    <div className={cn("pm-select-preview", className)}>
      {/* Soft title chrome — Geist label/title, no hard divider / no tool strip */}
      <div className="pm-select-preview-head">
        <span className="pm-select-preview-title" title={title}>
          {title}
        </span>
        {onClose && (
          <button
            type="button"
            className="pm-select-preview-close"
            onClick={onClose}
            title={t("fileMgmt.closePreview")}
            aria-label={t("fileMgmt.closePreview")}
          >
            <X className="h-3.5 w-3.5" strokeWidth={1.75} />
          </button>
        )}
      </div>
      <div
        className={cn(
          "pm-select-preview-body",
          bodyPhase === "out" ? "is-out" : "is-in"
        )}
      >
        {file && url ? (
          <RawFileViewer
            key={file.file_id}
            url={url}
            filename={name}
            downloadUrl={url}
            hideChrome
            className="h-full border-0 rounded-none bg-transparent"
          />
        ) : (
          <div className="h-full flex items-center justify-center p-6">
            <p className="pm-meta text-[var(--pm-faint)] text-center">
              {t("fileMgmt.clickToPreview")}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

/** A4 portrait width/height (210mm / 297mm). */
export const A4_PORTRAIT_RATIO = 210 / 297
const PREVIEW_GAP = 10
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
 * Open: slide in from the right (toward the rail).
 * File switch: sequential body fade (no hard remount of the shell).
 * Close: X button, or mousedown outside the panel.
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

  /** Keep shell mounted while open; crossfade document when file changes. */
  const [displayFile, setDisplayFile] = useState<FileSummary | null>(file)
  const [bodyPhase, setBodyPhase] = useState<"in" | "out">("in")
  const [shellVisible, setShellVisible] = useState(false)
  const switchGen = useRef(0)

  // Measure anchor → place float
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
  }, [open, displayFile?.file_id, anchorRef])

  // Open shell → slide in; close → unmount after brief exit (parent sets open false)
  useEffect(() => {
    if (open) {
      setShellVisible(true)
      // Seed display file on open
      if (file) setDisplayFile(file)
      setBodyPhase("in")
      return
    }
    setShellVisible(false)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps -- only open gate

  // Silk file switch while shell stays open
  useEffect(() => {
    if (!open || !file) return
    if (file.file_id === displayFile?.file_id) {
      setBodyPhase("in")
      return
    }
    const gen = ++switchGen.current
    setBodyPhase("out")
    const t = window.setTimeout(() => {
      if (gen !== switchGen.current) return
      setDisplayFile(file)
      // Next frame → fade in
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          if (gen !== switchGen.current) return
          setBodyPhase("in")
        })
      })
    }, DOC_FADE_OUT_MS)
    return () => window.clearTimeout(t)
  }, [file, file?.file_id, open, displayFile?.file_id])

  // Click outside panel → close entire floating window
  useEffect(() => {
    if (!open || !onClose) return
    const onPointerDown = (e: PointerEvent) => {
      const t = e.target as Node | null
      if (!t) return
      if (panelRef.current?.contains(t)) return
      if (
        t instanceof Element &&
        t.closest("[data-file-select-tree], [data-file-select-zone]")
      ) {
        return
      }
      onClose()
    }
    const id = window.setTimeout(() => {
      document.addEventListener("pointerdown", onPointerDown, true)
    }, 0)
    return () => {
      window.clearTimeout(id)
      document.removeEventListener("pointerdown", onPointerDown, true)
    }
  }, [open, onClose])

  if (!open || !shellVisible || !box || typeof document === "undefined") {
    return null
  }

  return createPortal(
    <div
      ref={panelRef}
      className="fixed pm-select-preview-float is-open"
      style={{
        top: box.top,
        left: box.left,
        width: box.width,
        height: box.height,
        zIndex: PREVIEW_Z,
        // Expose for CSS timing if needed
        ["--pm-select-doc-in" as string]: `${DOC_FADE_IN_MS}ms`,
        ["--pm-select-doc-out" as string]: `${DOC_FADE_OUT_MS}ms`,
      }}
      data-file-select-preview
    >
      <FileSelectPreviewPanel
        collectionId={collectionId}
        file={displayFile}
        onClose={onClose}
        bodyPhase={bodyPhase}
        className="h-full w-full"
      />
    </div>,
    document.body
  )
}
