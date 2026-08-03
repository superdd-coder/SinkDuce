/**
 * Raw-tab preview via @file-viewer (browser-native).
 * Formats: PDF, DOCX, XLSX, PPTX (+ legacy), MD, TXT, CSV.
 * Engines/assets pre-copied into /file-viewer/ by vite-plugin.
 *
 * UI: English (en-US), compact density, host Geist font via styleIsolation none.
 */

import { useEffect, useMemo, useRef, useState } from "react"
import { Download, Loader2 } from "lucide-react"
import FileViewer from "@file-viewer/react"
import officePreset from "@file-viewer/preset-office"
import textRenderer from "@file-viewer/renderer-text"
import { cn } from "@/lib/utils"

/**
 * Re-home PDF chrome (stable — never move zoom/rotate out of `.pdf-shell`):
 * - Sidebar toggle + page → nav head
 * - Zoom + rotate stay in `.pdf-toolbar`, positioned by CSS into the top-left
 *   red-box slot (same visual row as Search). Moving them into the web toolbar
 *   was destroyed whenever File Viewer re-rendered that bar.
 * - Ctrl/⌘+wheel and two-finger pinch on the PDF viewport
 */
function layoutPdfChrome(root: HTMLElement) {
  const pdfTb = root.querySelector<HTMLElement>(".pdf-toolbar")
  const navHead = root.querySelector<HTMLElement>(".pdf-nav-head")
  if (!pdfTb) return

  // If a previous broken layout parked tools in the web toolbar, put them back
  const strandedZoom = root.querySelector<HTMLElement>(
    ".file-viewer-web-toolbar .pdf-toolbar-group--zoom, .sinkduce-pdf-tools-left .pdf-toolbar-group--zoom"
  )
  const strandedRotate = root.querySelector<HTMLElement>(
    ".file-viewer-web-toolbar .pdf-toolbar-group--rotate, .sinkduce-pdf-tools-left .pdf-toolbar-group--rotate"
  )
  if (strandedZoom && strandedZoom.parentElement !== pdfTb) {
    pdfTb.appendChild(strandedZoom)
  }
  if (strandedRotate && strandedRotate.parentElement !== pdfTb) {
    pdfTb.appendChild(strandedRotate)
  }
  // Remove empty left slot if present
  root.querySelectorAll(".sinkduce-pdf-tools-left").forEach((el) => {
    if (!el.querySelector(".pdf-toolbar-group")) el.remove()
  })

  const page = Array.from(
    pdfTb.querySelectorAll<HTMLElement>(":scope > .pdf-toolbar-group")
  ).find(
    (el) =>
      !el.classList.contains("pdf-toolbar-group--zoom") &&
      !el.classList.contains("pdf-toolbar-group--rotate")
  )
  const pageOrphan = root.querySelector<HTMLElement>(
    '.pdf-nav-head .pdf-toolbar-group[data-sinkduce-homed="nav"]'
  )
  const toggle = Array.from(
    pdfTb.querySelectorAll<HTMLElement>(":scope > .pdf-icon-button")
  ).find(
    (el) =>
      el.querySelector(".pdf-panel-icon") ||
      el.getAttribute("aria-pressed") != null
  )
  const toggleOrphan = root.querySelector<HTMLElement>(
    '.pdf-nav-head > .pdf-icon-button[data-sinkduce-homed="nav"]'
  )

  if (navHead) {
    if (!navHead.dataset.sinkduceHomed) {
      navHead.replaceChildren()
      navHead.dataset.sinkduceHomed = "1"
    }
    const toggleEl = toggle || toggleOrphan
    if (toggleEl && toggleEl.parentElement !== navHead) {
      navHead.appendChild(toggleEl)
      toggleEl.dataset.sinkduceHomed = "nav"
    }
    const pageEl = page || pageOrphan
    if (pageEl && pageEl.parentElement !== navHead) {
      navHead.appendChild(pageEl)
      pageEl.dataset.sinkduceHomed = "nav"
    }
  }

  // Mark toolbar for CSS: only zoom/rotate remain here, float into top row
  pdfTb.dataset.sinkduceBar = "top-left"
  delete pdfTb.dataset.sinkduceEmpty

  attachPdfPinchAndWheelZoom(root)
}

/** Ctrl/⌘ + scroll and two-finger pinch → click PDF zoom ± buttons. */
function attachPdfPinchAndWheelZoom(root: HTMLElement) {
  const viewport =
    root.querySelector<HTMLElement>(".pdf-viewport") ||
    root.querySelector<HTMLElement>(".pdf-wrapper")
  if (!viewport || viewport.dataset.sinkduceGestures === "1") return
  viewport.dataset.sinkduceGestures = "1"

  const zoomButtons = () => {
    const group = root.querySelector<HTMLElement>(".pdf-toolbar-group--zoom")
    if (!group)
      return {
        out: null as HTMLButtonElement | null,
        inn: null as HTMLButtonElement | null,
      }
    const btns = Array.from(group.querySelectorAll<HTMLButtonElement>("button"))
    // Structure: [zoomOut, scale/fit, zoomIn]
    return {
      out: btns[0] || null,
      inn: btns[btns.length - 1] || null,
    }
  }

  const wheel = (e: WheelEvent) => {
    if (!e.ctrlKey && !e.metaKey) return
    e.preventDefault()
    e.stopPropagation()
    const { out, inn } = zoomButtons()
    if (e.deltaY < 0) inn?.click()
    else out?.click()
  }
  viewport.addEventListener("wheel", wheel, { passive: false })

  let lastDist = 0
  let pinchAcc = 0
  const onTouchStart = (e: TouchEvent) => {
    if (e.touches.length === 2) {
      lastDist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      )
      pinchAcc = 0
    }
  }
  const onTouchMove = (e: TouchEvent) => {
    if (e.touches.length !== 2 || lastDist <= 0) return
    e.preventDefault()
    const dist = Math.hypot(
      e.touches[0].clientX - e.touches[1].clientX,
      e.touches[0].clientY - e.touches[1].clientY
    )
    pinchAcc += dist - lastDist
    lastDist = dist
    // Require ~28px delta per zoom step to avoid jitter
    const step = 28
    const { out, inn } = zoomButtons()
    while (pinchAcc > step) {
      inn?.click()
      pinchAcc -= step
    }
    while (pinchAcc < -step) {
      out?.click()
      pinchAcc += step
    }
  }
  const onTouchEnd = () => {
    lastDist = 0
    pinchAcc = 0
  }
  viewport.addEventListener("touchstart", onTouchStart, { passive: true })
  viewport.addEventListener("touchmove", onTouchMove, { passive: false })
  viewport.addEventListener("touchend", onTouchEnd, { passive: true })
  viewport.addEventListener("touchcancel", onTouchEnd, { passive: true })
}

/** Extensions we open with File Viewer in Raw. */
const RAW_VIEWER_EXTS = new Set([
  "pdf",
  "docx",
  "doc",
  "docm",
  "dotx",
  "dotm",
  "dot",
  "xlsx",
  "xls",
  "xlsm",
  "xlsb",
  "csv",
  "tsv",
  "pptx",
  "ppt",
  "pptm",
  "ppsx",
  "md",
  "markdown",
  "txt",
  "log",
  "json",
  "jsonl",
  "html",
  "htm",
])

export function isRawViewerSupported(filename: string | null | undefined): boolean {
  if (!filename) return false
  const ext = filename.split(".").pop()?.toLowerCase() || ""
  return RAW_VIEWER_EXTS.has(ext)
}

/**
 * Pick a filename that File Viewer can route by extension.
 * Prefer real storage names; skip __file__: / labels without extension.
 */
export function resolveRawFilename(
  ...candidates: Array<string | null | undefined>
): string {
  const cleaned: string[] = []
  let noteOrMeetingHint = false
  for (const c of candidates) {
    const t = (c || "").trim()
    if (!t) continue
    if (t.startsWith("__meeting__:")) {
      noteOrMeetingHint = true
      continue
    }
    if (t.startsWith("__note__:")) {
      noteOrMeetingHint = true
      continue
    }
    if (t.startsWith("__file__:")) continue
    // Display labels from files.json import (no real extension)
    if (/^note:\s*/i.test(t) || /^meeting:\s*/i.test(t)) {
      noteOrMeetingHint = true
      continue
    }
    const base = t.split(/[/\\]/).pop() || t
    cleaned.push(base)
  }
  for (const base of cleaned) {
    const ext = base.includes(".") ? base.split(".").pop()!.toLowerCase() : ""
    if (ext && RAW_VIEWER_EXTS.has(ext)) return base
  }
  for (const base of cleaned) {
    if (base.includes(".")) return base
  }
  // last resort: bare ext like "docx" or "md"
  for (const c of candidates) {
    const t = (c || "").trim().replace(/^\./, "").toLowerCase()
    if (t && RAW_VIEWER_EXTS.has(t) && !t.includes("/")) {
      return `file.${t}`
    }
  }
  // Notes / meetings always ingest as markdown snapshots (tab_xx.md / Title.md)
  if (noteOrMeetingHint) return "document.md"
  return "file.bin"
}

/** Best-effort filename from Content-Disposition / X-File-Name. */
function filenameFromResponseHeaders(res: Response): string | null {
  const xName = res.headers.get("x-file-name") || res.headers.get("X-File-Name")
  if (xName && xName.trim()) return xName.trim()
  const cd = res.headers.get("content-disposition") || ""
  // filename*=UTF-8''... or filename="..."
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(cd)
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^["']|["']$/g, ""))
    } catch {
      return star[1].trim()
    }
  }
  const plain = /filename\s*=\s*("?)([^";]+)\1/i.exec(cd)
  if (plain?.[2]) return plain[2].trim()
  return null
}

export interface RawFileViewerProps {
  /** Same-origin preview URL, or blob:/data: URL. */
  url: string | null
  filename: string
  className?: string
  downloadUrl?: string | null
}

const MIME_BY_EXT: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  doc: "application/msword",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  xls: "application/vnd.ms-excel",
  csv: "text/csv",
  tsv: "text/tab-separated-values",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ppt: "application/vnd.ms-powerpoint",
  md: "text/markdown",
  markdown: "text/markdown",
  txt: "text/plain",
  log: "text/plain",
  json: "application/json",
  html: "text/html",
  htm: "text/html",
}

function mimeForName(name: string, fallback = "application/octet-stream"): string {
  const ext = name.split(".").pop()?.toLowerCase() || ""
  return MIME_BY_EXT[ext] || fallback
}

/** Map fetch / API failures to a single short line (never dump JSON). */
function friendlyRawError(status: number | null, body: string): string {
  const lower = (body || "").toLowerCase()
  const isMissingBlob =
    status === 404 ||
    lower.includes("version blob not found") ||
    lower.includes("file not found") ||
    lower.includes("not found")

  if (isMissingBlob) return "Original file is not available for preview"
  if (status === 400) return "Cannot open this file in Raw"
  if (status === 403) return "Preview not allowed"
  if (status !== null && status >= 500) return "Preview temporarily unavailable"
  return "Could not load preview"
}

export function RawFileViewer({
  url,
  filename,
  className,
  downloadUrl,
}: RawFileViewerProps) {
  /** File Viewer routes by File.name / filename — prefer File over bare blob URL. */
  const [viewerFile, setViewerFile] = useState<File | null>(null)
  const [loading, setLoading] = useState(false)
  const [errorTitle, setErrorTitle] = useState<string | null>(null)
  const hostRef = useRef<HTMLDivElement>(null)

  const supported = isRawViewerSupported(filename)
  const safeName = resolveRawFilename(filename)

  // PDF chrome layout: top row search+zoom+rotate; sidebar head = toggle + 1/24
  useEffect(() => {
    if (!viewerFile || !hostRef.current) return
    const root = hostRef.current
    layoutPdfChrome(root)
    const mo = new MutationObserver(() => {
      layoutPdfChrome(root)
    })
    mo.observe(root, { childList: true, subtree: true })
    // Re-run after library finishes async PDF mount
    const t1 = window.setTimeout(() => layoutPdfChrome(root), 50)
    const t2 = window.setTimeout(() => layoutPdfChrome(root), 300)
    const t3 = window.setTimeout(() => layoutPdfChrome(root), 1000)
    return () => {
      mo.disconnect()
      window.clearTimeout(t1)
      window.clearTimeout(t2)
      window.clearTimeout(t3)
    }
  }, [viewerFile])

  useEffect(() => {
    if (!url || !supported) {
      setViewerFile(null)
      setErrorTitle(null)
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setErrorTitle(null)
    setViewerFile(null)

    ;(async () => {
      try {
        const res = await fetch(url, { credentials: "same-origin" })
        if (!res.ok) {
          let detail = ""
          try {
            detail = await res.text()
          } catch {
            /* ignore */
          }
          // Prefer JSON detail string when present
          let detailText = detail.slice(0, 400)
          try {
            const j = JSON.parse(detail) as { detail?: unknown; error?: unknown }
            if (typeof j.detail === "string") detailText = j.detail
            else if (typeof j.error === "string") detailText = j.error
          } catch {
            /* keep raw */
          }
          if (cancelled) return
          setErrorTitle(friendlyRawError(res.status, detailText))
          setViewerFile(null)
          return
        }
        const buf = await res.arrayBuffer()
        if (cancelled) return
        // Prefer real on-disk name from preview response (notes/meetings often
        // have display labels like "Meeting: …" without .md in the client state).
        const headerName = filenameFromResponseHeaders(res)
        const finalName = resolveRawFilename(headerName, safeName, filename)
        // Prefer server Content-Type, but force a known Office MIME by extension
        // so File Viewer never treats a mislabeled blob as plain text.
        const headerType = (res.headers.get("content-type") || "")
          .split(";")[0]
          .trim()
        const type = mimeForName(
          finalName,
          headerType &&
            !headerType.startsWith("text/plain") &&
            headerType !== "application/octet-stream"
            ? headerType
            : "application/octet-stream"
        )
        // text/markdown from preview is valid for note/meeting .md
        const finalType =
          headerType.startsWith("text/markdown") || headerType.startsWith("text/plain")
            ? headerType.split(";")[0].trim() || type
            : type
        const file = new File([buf], finalName, { type: finalType || type })
        setViewerFile(file)
      } catch {
        if (!cancelled) {
          setErrorTitle(friendlyRawError(null, ""))
          setViewerFile(null)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [url, supported, safeName])

  const viewerOptions = useMemo(
    () =>
      ({
        preset: officePreset,
        renderers: [textRenderer as never],
        // Force English chrome (toolbar, empty/error states)
        locale: "en-US" as const,
        // Allow host CSS (Geist + compact size / green thin type) to style chrome
        styleIsolation: "none" as const,
        theme: "light" as const,
        ui: {
          density: "compact" as const,
          surfaceBackground: "transparent",
        },
        // Compact top strip. Zoom kept for Office/text; PDF uses its own
        // zoom row — CSS hides the global zoom group when .pdf-shell is open
        // so scale controls never double up. 1:1 / zoom-reset is always off.
        toolbar: {
          position: "top" as const,
          exportHtml: false,
          theme: false,
          print: true,
          download: true,
          zoom: true,
          search: true,
          items: {
            "zoom-reset": false,
          },
          permissions: {
            "zoom-reset": false,
          },
        },
        pdf: {
          navigation: true,
          defaultNavigationVisible: true,
          toolbar: true,
          // No thumbnail tiles — host CSS also hides number squares
          thumbnails: false,
        },
      }) as const,
    []
  )

  if (!url) {
    return (
      <div
        className={cn(
          "h-full flex items-center justify-center text-sm text-muted-foreground p-6",
          className
        )}
      >
        No file to preview.
      </div>
    )
  }

  if (!supported) {
    return (
      <div
        className={cn(
          "h-full flex flex-col items-center justify-center gap-3 p-6",
          className
        )}
      >
        <p className="text-sm text-foreground/90 font-light text-center">
          Preview not available for this file type
        </p>
        {(downloadUrl || url) && (
          <a
            href={downloadUrl || url}
            download={safeName}
            className="inline-flex items-center h-7 gap-1 rounded-lg border border-border bg-background px-2.5 text-[0.8rem] hover:bg-muted"
          >
            <Download className="h-3.5 w-3.5" />
            Download
          </a>
        )}
      </div>
    )
  }

  if (loading) {
    return (
      <div
        className={cn(
          "h-full flex items-center justify-center text-muted-foreground gap-2",
          className
        )}
      >
        <Loader2 className="h-4 w-4 animate-spin" />
        <span className="text-sm">Loading preview…</span>
      </div>
    )
  }

  if (errorTitle || !viewerFile) {
    return (
      <div
        className={cn(
          "h-full flex flex-col items-center justify-center gap-3 p-6",
          className
        )}
      >
        <p className="text-sm text-foreground/90 font-light text-center max-w-md">
          {errorTitle || "Could not load preview"}
        </p>
        {(downloadUrl || url) && (
          <a
            href={downloadUrl || url}
            download={safeName}
            className="inline-flex items-center h-7 gap-1 rounded-lg border border-border bg-background px-2.5 text-[0.8rem] hover:bg-muted"
          >
            <Download className="h-3.5 w-3.5" />
            Download
          </a>
        )}
      </div>
    )
  }

  return (
    <div
      ref={hostRef}
      className={cn(
        "sinkduce-file-viewer h-full min-h-0 w-full overflow-hidden rounded-lg border border-border",
        className
      )}
    >
      <FileViewer
        key={`${viewerFile.name}:${viewerFile.size}:${viewerFile.lastModified}`}
        file={viewerFile}
        filename={safeName}
        name={safeName}
        options={viewerOptions}
        style={{ height: "100%", width: "100%", display: "block" }}
      />
    </div>
  )
}
