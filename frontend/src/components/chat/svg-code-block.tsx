import { useCallback, useEffect, useRef, useState, type ReactNode } from "react"
import { useT } from "@/i18n/use-t"
import { createSvgPreview, getSvgPreviewStatus } from "@/lib/svg-preview"
import { SvgPreviewCanvas, SvgPreviewDialog } from "@/components/chat/svg-preview-dialog"
import { useSvgPreviewHost } from "@/components/shared/svg-preview-context"
import { Check, Code2, Copy, Eye, Maximize2 } from "lucide-react"

export function SvgCodeBlock({ source, complete, sourceEditor, report = false }: {
  source: string
  complete: boolean
  /** Keep a Tiptap content DOM mounted while switching between preview and source. */
  sourceEditor?: ReactNode
  report?: boolean
}) {
  const t = useT()
  const previewHost = useSvgPreviewHost()
  const status = getSvgPreviewStatus(source, complete)
  const [sourceSelected, setSourceSelected] = useState(false)
  const [failedSource, setFailedSource] = useState<string | null>(null)
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle")
  const [expandedSource, setExpandedSource] = useState<string | null>(null)
  const expandRef = useRef<HTMLButtonElement>(null)
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const failed = failedSource === source
  const canPreview = (status === "ready" || status === "recovered") && !failed
  const showSource = sourceSelected || (status !== "pending" && !canPreview)
  const handlePreviewError = useCallback(() => setFailedSource(source), [source])
  const expanded = (expandedSource === source || previewHost?.preview?.triggerRef === expandRef) && canPreview

  useEffect(() => () => {
    if (copyTimer.current !== null) clearTimeout(copyTimer.current)
  }, [])

  // Callback-ref cleanup releases the URL on source changes, tab switches and
  // unmount. The browser receives an image URL only; SVG never enters page DOM.
  const attachImage = useCallback((node: HTMLImageElement | null) => {
    if (!node) return
    try {
      const preview = createSvgPreview(source, complete)
      if (!preview) return
      node.src = preview.url
      return preview.dispose
    } catch {
      setFailedSource(source)
    }
  }, [source, complete])

  const copySource = async () => {
    if (copyTimer.current !== null) clearTimeout(copyTimer.current)
    try {
      await navigator.clipboard.writeText(source)
      setCopyState("copied")
    } catch {
      setCopyState("failed")
      setSourceSelected(true)
    }
    copyTimer.current = setTimeout(() => setCopyState("idle"), 2000)
  }

  const statusKey = status === "pending"
    ? "chat.svgGenerating"
    : status === "too-large"
      ? "chat.svgTooLarge"
      : !canPreview
        ? "chat.svgInvalid"
        : status === "recovered"
          ? "chat.svgRecovered"
          : null

  return (
    <section className={`chat-md-visual${report ? " chat-md-visual--report" : ""}`} aria-label={t("chat.svgBlock")}>
      <div
        className="chat-md-visual-toolbar" contentEditable={sourceEditor ? false : undefined}
        data-svg-preview-controls={sourceEditor ? "" : undefined}
        onMouseDown={sourceEditor ? (event) => { event.preventDefault(); event.stopPropagation() } : undefined}
        onClick={sourceEditor ? (event) => event.stopPropagation() : undefined}
      >
        {!report && <span>SVG</span>}
        {report ? <button type="button" aria-pressed={showSource} aria-label={t(showSource ? "common.preview" : "chat.svgSource")}
          title={t(showSource ? "common.preview" : "chat.svgSource")} onClick={() => setSourceSelected((value) => !value)}>
          {showSource ? <Eye size={15} /> : <Code2 size={15} />}
        </button> : <>
        <button
          type="button"
          aria-pressed={!showSource}
          disabled={!canPreview}
          onClick={() => setSourceSelected(false)}
        >
          {t("common.preview")}
        </button>
        <button
          type="button"
          aria-pressed={showSource}
          onClick={() => setSourceSelected(true)}
        >
          {t("chat.svgSource")}
        </button>
        </>}
        <button type="button" onClick={() => void copySource()} disabled={!source} aria-live="polite"
          aria-label={t(copyState === "copied" ? "common.copied" : "common.copy")} title={t(copyState === "copied" ? "common.copied" : "common.copy")}>
          {report ? copyState === "copied" ? <Check size={15} /> : <Copy size={15} /> : t(copyState === "copied" ? "common.copied" : "common.copy")}
        </button>
        <button
          ref={expandRef} type="button" className="chat-svg-expand-button"
          aria-haspopup="dialog" aria-expanded={expanded}
          aria-label={t("chat.svgExpand")} title={t("chat.svgExpand")}
          disabled={!canPreview} onClick={() => {
            if (sourceEditor && previewHost) previewHost.open(source, expandRef)
            else setExpandedSource(source)
          }}
        >
          {report ? <Maximize2 size={15} /> : <span aria-hidden="true" className="chat-svg-expand-icon">⤢</span>}
        </button>
      </div>
      {showSource && statusKey && <p className="chat-md-visual-status" role="status" contentEditable={false}>{t(statusKey)}</p>}
      {copyState === "failed" && (
        <p className="chat-md-visual-status" role="status">{t("chat.svgCopyFailed")}</p>
      )}
      {(showSource || sourceEditor) && (
        <pre hidden={!showSource} className="chat-md-visual-source" role="region" aria-label={t("chat.svgSource")} tabIndex={0}>
          {sourceEditor ?? <code className="language-svg">{source}</code>}
        </pre>
      )}
      {!showSource && (
        <div
          className={`chat-md-visual-preview${sourceEditor ? " chat-md-visual-preview--interactive" : ""}`}
          role="region" aria-label={t("chat.svgImage")} tabIndex={0} contentEditable={sourceEditor ? false : undefined}
          data-svg-preview-controls={sourceEditor ? "" : undefined}
          onClick={sourceEditor ? (event) => event.stopPropagation() : undefined}
        >
          {statusKey && <p className="chat-md-visual-status" role="status">{t(statusKey)}</p>}
          {canPreview && (
            sourceEditor ? (
              <SvgPreviewCanvas key={source} source={source} inline onPreviewError={handlePreviewError} />
            ) : <img
              ref={attachImage}
              className="chat-md-visual-image"
              alt={t("chat.svgImage")}
              onError={handlePreviewError}
            />
          )}
        </div>
      )}
      {expandedSource === source && canPreview && (
        <SvgPreviewDialog source={source} triggerRef={expandRef} onClose={() => setExpandedSource(null)} />
      )}
    </section>
  )
}
