/**
 * Raw-tab preview via @file-viewer (browser-native).
 * Formats: PDF, DOCX, XLSX, PPTX (+ legacy), MD, TXT, CSV.
 * Engines/assets pre-copied into /file-viewer/ by vite-plugin.
 *
 * UI: English (en-US), compact density, host Geist font via styleIsolation none.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
} from "react"
import { Download, Loader2 } from "lucide-react"
import FileViewer from "@file-viewer/react"
import officePreset from "@file-viewer/preset-office"
import textRenderer from "@file-viewer/renderer-text"
import { cn } from "@/lib/utils"

/** Inline SVG for scrubbed toolbar — magnifying glass / download icon only. */
const ICON_SEARCH_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>`
const ICON_DOWNLOAD_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>`

function setButtonIconOnly(btn: HTMLElement, kind: "search" | "download") {
  if (btn.dataset.sinkduceIcon === kind && btn.querySelector("svg")) {
    // Already iconified — only strip any leftover text
    btn.childNodes.forEach((n) => {
      if (n.nodeType === Node.TEXT_NODE) n.textContent = ""
    })
    return
  }
  btn.dataset.sinkduceIcon = kind
  btn.innerHTML = kind === "search" ? ICON_SEARCH_SVG : ICON_DOWNLOAD_SVG
  btn.setAttribute("aria-label", kind === "search" ? "Search" : "Download")
  btn.setAttribute("title", kind === "search" ? "Search" : "Download")
}

/**
 * Re-home PDF chrome (sidebar/nav removed product-side):
 * - Zoom + rotate stay in `.pdf-toolbar` as left float pill
 * - Page meter / nav toggle hidden via CSS (navigation: false in options)
 * - Ctrl/⌘+wheel and two-finger pinch on the PDF viewport
 */
function layoutPdfChrome(root: HTMLElement) {
  const pdfTb = root.querySelector<HTMLElement>(".pdf-toolbar")
  if (!pdfTb) return

  // If a previous layout parked tools in the web toolbar, put them back
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
  root.querySelectorAll(".sinkduce-pdf-tools-left").forEach((el) => {
    if (!el.querySelector(".pdf-toolbar-group")) el.remove()
  })

  // Mark toolbar for CSS: zoom + rotate float top-left
  pdfTb.dataset.sinkduceBar = "top-left"
  delete pdfTb.dataset.sinkduceEmpty

  // Web bar: hide idle "0/0" search counters (reads like a broken page meter)
  root.querySelectorAll(".file-viewer-web-search-count").forEach((el) => {
    const t = (el.textContent || "").trim().replace(/\s+/g, "")
    if (t === "0/0" || t === "0of0" || t === "-/-") {
      ;(el as HTMLElement).style.display = "none"
    } else {
      ;(el as HTMLElement).style.display = ""
    }
  })

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
    // Display labels from index import (no real extension)
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

/** True when name looks like CJK→ASCII mangling (e.g. Word______ 2.docx). */
function looksAsciiMangled(name: string): boolean {
  return /_{2,}/.test(name) && !/[^\x00-\x7F]/.test(name)
}

/**
 * Best-effort original filename from preview response headers.
 * Prefer RFC 5987 ``filename*`` (UTF-8) — never prefer ASCII-mangled
 * ``X-File-Name`` (e.g. ``Word______ 2.docx``) which is only for extension routing.
 */
function filenameFromResponseHeaders(res: Response): string | null {
  const cd = res.headers.get("content-disposition") || ""
  // RFC 5987 UTF-8 filename* first
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(cd)
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^["']|["']$/g, ""))
    } catch {
      return star[1].trim()
    }
  }
  const plain = /filename\s*=\s*("?)([^";]+)\1/i.exec(cd)
  if (plain?.[2]) {
    const p = plain[2].trim()
    if (p && !looksAsciiMangled(p)) return p
  }
  const xName = (
    res.headers.get("x-file-name") ||
    res.headers.get("X-File-Name") ||
    ""
  ).trim()
  // X-File-Name is ASCII-safe fallback — skip mangled CJK replacements
  if (xName && !looksAsciiMangled(xName)) return xName
  return null
}

/** Prefer a human filename over an ASCII-underscore fallback. */
function preferDisplayFilename(
  ...candidates: Array<string | null | undefined>
): string {
  const list = candidates
    .map((c) => (c || "").trim())
    .filter(Boolean)
  // Prefer any name with non-ASCII (CJK etc.) and an extension
  for (const n of list) {
    if (/[^\x00-\x7F]/.test(n) && n.includes(".")) return n
  }
  // Prefer names without long underscore runs (ASCII mangling of CJK)
  for (const n of list) {
    if (n.includes(".") && !looksAsciiMangled(n)) return n
  }
  return resolveRawFilename(...list)
}

export interface RawFileViewerProps {
  /** Same-origin preview URL, or blob:/data: URL. */
  url: string | null
  filename: string
  className?: string
  downloadUrl?: string | null
  /**
   * Compact select-preview mode: hide web + PDF toolbars, pure document surface.
   * Used by floating / side preview cards (Premium soft float).
   */
  hideChrome?: boolean
  /**
   * Toolbar preset when chrome is shown.
   * - full: search · download · print (+ zoom for non-PDF; PDF has own zoom/rotate)
   * - download-search: only Search + Download (Chat source Preview tab)
   */
  tools?: "full" | "download-search"
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
  hideChrome = false,
  tools = "full",
}: RawFileViewerProps) {
  const downloadSearchOnly = tools === "download-search"
  /** File Viewer routes by File.name / filename — prefer File over bare blob URL. */
  const [viewerFile, setViewerFile] = useState<File | null>(null)
  const [loading, setLoading] = useState(false)
  const [errorTitle, setErrorTitle] = useState<string | null>(null)
  const hostRef = useRef<HTMLDivElement>(null)

  const supported = isRawViewerSupported(filename)
  const safeName = resolveRawFilename(filename)

  // PDF chrome layout: top row search+zoom+rotate (full tools only).
  // download-search: strip residual print/zoom nodes the library may still mount.
  useEffect(() => {
    if (hideChrome || !viewerFile || !hostRef.current) return
    const root = hostRef.current

    const scrubDownloadSearchChrome = () => {
      // Hide PDF float zoom/rotate entirely
      root.querySelectorAll(
        ".pdf-toolbar, .sinkduce-pdf-tools-left, [data-sinkduce-bar]"
      ).forEach((el) => {
        ;(el as HTMLElement).style.display = "none"
      })

      const searchInput = root.querySelector<HTMLInputElement>(
        ".file-viewer-web-search input"
      )
      if (searchInput) {
        // Short placeholder for narrow rail
        if (searchInput.placeholder.length > 8) {
          searchInput.placeholder = "Search"
        }
      }
      const hasQuery = !!(searchInput?.value || "").trim()

      // Web bar: only Search + Download; iconify labels; hide idle nav when empty
      root
        .querySelectorAll<HTMLElement>(
          ".file-viewer-web-toolbar button, .file-viewer-web-toolbar [role='button']"
        )
        .forEach((el) => {
          const label = (
            el.getAttribute("aria-label") ||
            el.getAttribute("title") ||
            el.textContent ||
            ""
          ).toLowerCase()
          const isSearchAction =
            label.includes("search") ||
            label.includes("find") ||
            label.includes("查找") ||
            label.includes("搜索")
          const isNav =
            label.includes("next") ||
            label.includes("prev") ||
            label.includes("previous") ||
            label.includes("clear") ||
            label.includes("下一个") ||
            label.includes("上一个") ||
            label.includes("清除")
          const isDownload =
            label.includes("download") || label.includes("下载")
          if (!isSearchAction && !isNav && !isDownload) {
            el.style.display = "none"
            return
          }
          // Hide prev/next/clear until user has typed a query — saves rail width
          if (isNav && !hasQuery) {
            el.style.display = "none"
            return
          }
          el.style.display = ""
          // Primary actions: force magnifying-glass / download icons (no text)
          if (isDownload) {
            setButtonIconOnly(el, "download")
            return
          }
          if (isSearchAction && !isNav) {
            setButtonIconOnly(el, "search")
            return
          }
          // Nav (prev/next/clear): keep library SVG, strip text labels
          el.childNodes.forEach((n) => {
            if (n.nodeType === Node.TEXT_NODE) {
              n.textContent = ""
            } else if (n.nodeType === Node.ELEMENT_NODE) {
              const child = n as HTMLElement
              if (
                child.tagName !== "SVG" &&
                !child.querySelector("svg") &&
                child.children.length === 0
              ) {
                child.style.display = "none"
              }
            }
          })
        })
      root
        .querySelectorAll(
          ".file-viewer-web-zoom-meter, .file-viewer-web-toolbar [class*='zoom']"
        )
        .forEach((el) => {
          ;(el as HTMLElement).style.display = "none"
        })
    }

    if (downloadSearchOnly) {
      scrubDownloadSearchChrome()
      const mo = new MutationObserver(() => scrubDownloadSearchChrome())
      mo.observe(root, { childList: true, subtree: true })
      const onInput = () => scrubDownloadSearchChrome()
      root.addEventListener("input", onInput, true)
      root.addEventListener("change", onInput, true)
      const t1 = window.setTimeout(scrubDownloadSearchChrome, 50)
      const t2 = window.setTimeout(scrubDownloadSearchChrome, 300)
      const t3 = window.setTimeout(scrubDownloadSearchChrome, 1000)
      return () => {
        mo.disconnect()
        root.removeEventListener("input", onInput, true)
        root.removeEventListener("change", onInput, true)
        window.clearTimeout(t1)
        window.clearTimeout(t2)
        window.clearTimeout(t3)
      }
    }

    layoutPdfChrome(root)
    const mo = new MutationObserver(() => {
      layoutPdfChrome(root)
    })
    mo.observe(root, { childList: true, subtree: true })
    const t1 = window.setTimeout(() => layoutPdfChrome(root), 50)
    const t2 = window.setTimeout(() => layoutPdfChrome(root), 300)
    const t3 = window.setTimeout(() => layoutPdfChrome(root), 1000)
    return () => {
      mo.disconnect()
      window.clearTimeout(t1)
      window.clearTimeout(t2)
      window.clearTimeout(t3)
    }
  }, [viewerFile, hideChrome, downloadSearchOnly])

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
        // Prefer UTF-8 Content-Disposition / client name over ASCII-mangled headers
        // so download keeps names like "Word文件版本测试 2.docx".
        const headerName = filenameFromResponseHeaders(res)
        const finalName = preferDisplayFilename(
          headerName,
          filename,
          safeName
        )
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

  /** PDF owns zoom/rotate on its left float pill — web bar is Search + Download + Print only. */
  const isPdfFile = useMemo(
    () => mimeForName(safeName).includes("pdf") || /\.pdf$/i.test(safeName),
    [safeName]
  )

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
        /*
         * Web toolbar:
         * - full: Search · Download · Print (+ zoom for non-PDF)
         * - download-search: ONLY Search + Download (Chat Preview / former Raw tab)
         * hideChrome: pure document for select-preview soft cards.
         */
        toolbar: hideChrome
          ? false
          : downloadSearchOnly
            ? {
                position: "top" as const,
                search: true,
                download: true,
                print: false,
                zoom: false,
                exportHtml: false,
                theme: false,
                // Typed mutable array (FileViewerToolbarItem[]); plain string[] fails tsc
                order: ["search" as const, "download" as const],
                items: {
                  print: false,
                  "export-html": false,
                  "zoom-in": false,
                  "zoom-out": false,
                  "zoom-reset": false,
                },
                permissions: {
                  print: false,
                  "export-html": false,
                  "zoom-in": false,
                  "zoom-out": false,
                  "zoom-reset": false,
                },
              }
            : {
                position: "top" as const,
                exportHtml: false,
                theme: false,
                print: true,
                download: true,
                zoom: !isPdfFile,
                search: true,
                items: {
                  "zoom-reset": false,
                },
                permissions: {
                  "zoom-reset": false,
                },
              },
        pdf: {
          // Product: no page/outline sidebar — full-bleed preview only
          navigation: false,
          defaultNavigationVisible: false,
          // PDF zoom/rotate float only in full mode
          toolbar: !hideChrome && !downloadSearchOnly,
          thumbnails: false,
        },
      }),
    [isPdfFile, hideChrome, downloadSearchOnly]
  )

  const downloadName =
    viewerFile?.name || preferDisplayFilename(filename, safeName)

  const handleDownloadClick = useCallback(
    async (e: MouseEvent) => {
      e.preventDefault()
      const src = downloadUrl || url
      if (!src) return
      try {
        const res = await fetch(src, { credentials: "same-origin" })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const blob = await res.blob()
        const headerName = filenameFromResponseHeaders(res)
        const name = preferDisplayFilename(
          headerName,
          downloadName,
          filename,
          safeName
        )
        const objectUrl = URL.createObjectURL(blob)
        const a = document.createElement("a")
        a.href = objectUrl
        a.download = name
        document.body.appendChild(a)
        a.click()
        a.remove()
        URL.revokeObjectURL(objectUrl)
      } catch {
        // Fallback: open URL (browser may use Content-Disposition)
        window.open(src, "_blank", "noopener,noreferrer")
      }
    },
    [downloadUrl, url, downloadName, filename, safeName]
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
          <button
            type="button"
            onClick={(e) => void handleDownloadClick(e)}
            className="inline-flex items-center h-7 gap-1 rounded-lg border border-border bg-background px-2.5 text-[0.8rem] hover:bg-muted"
          >
            <Download className="h-3.5 w-3.5" />
            Download
          </button>
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
          <button
            type="button"
            onClick={(e) => void handleDownloadClick(e)}
            className="inline-flex items-center h-7 gap-1 rounded-lg border border-border bg-background px-2.5 text-[0.8rem] hover:bg-muted"
          >
            <Download className="h-3.5 w-3.5" />
            Download
          </button>
        )}
      </div>
    )
  }

  return (
    <div
      ref={hostRef}
      className={cn(
        "sinkduce-file-viewer h-full min-h-0 w-full overflow-hidden bg-white",
        hideChrome && "sinkduce-file-viewer--chrome-off",
        downloadSearchOnly && "sinkduce-file-viewer--tools-ds",
        className
      )}
    >
      <FileViewer
        key={`${viewerFile.name}:${viewerFile.size}:${viewerFile.lastModified}`}
        file={viewerFile}
        filename={downloadName}
        name={downloadName}
        options={viewerOptions}
        style={{ height: "100%", width: "100%", display: "block" }}
      />
    </div>
  )
}
