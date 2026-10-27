import { useCallback, useEffect, useRef, useState } from "react"
import type { KeyboardEvent, PointerEvent, RefObject } from "react"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { useT } from "@/i18n/use-t"
import { createSvgPreview, getSvgPreviewSize } from "@/lib/svg-preview"
import { constrainSvgViewport, createSvgViewport, fitSvgSize, inlineSvgAspectRatio, zoomSvgAt } from "@/lib/svg-viewport"
import type { Size, SvgViewport } from "@/lib/svg-viewport"

export function SvgPreviewCanvas({ source, inline = false, onPreviewError }: {
  source: string
  inline?: boolean
  onPreviewError?: () => void
}) {
  const t = useT()
  const canvasRef = useRef<HTMLDivElement>(null)
  const imageSize = useRef<Size>({ width: 0, height: 0 })
  const [fitted, setFitted] = useState<Size>({ width: 0, height: 0 })
  const [view, setView] = useState<SvgViewport>({ scale: 1, x: 0, y: 0 })
  const [failed, setFailed] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [aspectRatio, setAspectRatio] = useState(1.6)
  const drag = useRef<{ id: number; x: number; y: number; view: SvgViewport } | null>(null)
  const failPreview = useCallback(() => {
    setFailed(true)
    onPreviewError?.()
  }, [onPreviewError])

  const fit = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas || !canvas.clientWidth || !canvas.clientHeight || !imageSize.current.width || !imageSize.current.height) return
    const size = { width: canvas.clientWidth, height: canvas.clientHeight }
    setFitted(fitSvgSize(imageSize.current, size))
    setView(createSvgViewport(imageSize.current, size))
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const observer = new ResizeObserver(fit)
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [fit])

  const zoom = useCallback((factor: number, anchor?: { x: number; y: number }) => {
    const canvas = canvasRef.current
    if (!canvas || !imageSize.current.width) return
    const point = anchor ?? { x: canvas.clientWidth / 2, y: canvas.clientHeight / 2 }
    setView((previous) => constrainSvgViewport(
      zoomSvgAt(previous, previous.scale * factor, point), fitted,
      { width: canvas.clientWidth, height: canvas.clientHeight },
    ))
  }, [fitted])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const wheel = (event: WheelEvent) => {
      // Inline diagrams are part of a scrollable Note. Only an explicit
      // modified gesture takes ownership of the wheel in that context.
      const modified = event.ctrlKey || event.metaKey
      if (inline ? !modified : modified) return
      event.stopPropagation()
      event.preventDefault()
      if (drag.current) return
      const rect = canvas.getBoundingClientRect()
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? canvas.clientHeight : 1)
      zoom(Math.exp(-Math.max(-100, Math.min(100, delta)) * 0.002), {
        x: event.clientX - rect.left, y: event.clientY - rect.top,
      })
    }
    canvas.addEventListener("wheel", wheel, { passive: false })
    return () => canvas.removeEventListener("wheel", wheel)
  }, [zoom, inline])

  const attachImage = useCallback((node: HTMLImageElement | null) => {
    if (!node) return
    try {
      const preview = createSvgPreview(source, true)
      if (!preview) { failPreview(); return }
      node.src = preview.url
      return preview.dispose
    } catch {
      failPreview()
    }
  }, [source, failPreview])

  const pointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (inline && event.pointerType === "touch") return
    event.stopPropagation()
    if (event.button !== 0 || drag.current || !imageSize.current.width) return
    event.preventDefault()
    event.currentTarget.focus({ preventScroll: true })
    event.currentTarget.setPointerCapture(event.pointerId)
    drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, view }
    setDragging(true)
  }
  const pointerMove = (event: PointerEvent<HTMLDivElement>) => {
    event.stopPropagation()
    const start = drag.current
    if (!start || start.id !== event.pointerId) return
    setView(constrainSvgViewport(
      { ...start.view, x: start.view.x + event.clientX - start.x, y: start.view.y + event.clientY - start.y }, fitted,
      { width: event.currentTarget.clientWidth, height: event.currentTarget.clientHeight },
    ))
  }
  const stopDrag = (event: PointerEvent<HTMLDivElement>) => {
    event.stopPropagation()
    if (drag.current?.id !== event.pointerId) return
    drag.current = null
    setDragging(false)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
  }
  const keyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return
    if (event.key === "+" || event.key === "=") { event.preventDefault(); event.stopPropagation(); zoom(1.25) }
    else if (event.key === "-" || event.key === "_") { event.preventDefault(); event.stopPropagation(); zoom(0.8) }
    else if (event.key === "0") { event.preventDefault(); event.stopPropagation(); fit() }
    else {
      const offset: Record<string, [number, number]> = { ArrowLeft: [40, 0], ArrowRight: [-40, 0], ArrowUp: [0, 40], ArrowDown: [0, -40] }
      const delta = offset[event.key]
      if (!delta) return
      event.preventDefault()
      event.stopPropagation()
      const size = { width: event.currentTarget.clientWidth, height: event.currentTarget.clientHeight }
      setView((previous) => constrainSvgViewport({ ...previous, x: previous.x + delta[0], y: previous.y + delta[1] }, fitted, size))
    }
  }

  return (
    <>
      <div className="chat-svg-zoom-toolbar">
        <button type="button" aria-label={t("chat.svgZoomOut")} title={t("chat.svgZoomOut")} disabled={failed || view.scale <= 0.25} onClick={() => zoom(0.8)}>−</button>
        <output aria-live="polite" aria-label={t("chat.svgZoomLevel")} title={t("chat.svgZoomLevel")}>{Math.round(view.scale * 100)}%</output>
        <button type="button" aria-label={t("chat.svgZoomIn")} title={t("chat.svgZoomIn")} disabled={failed || view.scale >= 8} onClick={() => zoom(1.25)}>+</button>
        <button type="button" aria-label={t("chat.svgFit")} disabled={failed} onClick={fit}>{t("chat.svgFit")}</button>
      </div>
      <div
        ref={canvasRef}
        className={`chat-svg-canvas${dragging ? " is-dragging" : ""}`}
        data-inline={inline || undefined}
        style={inline ? { aspectRatio } : undefined}
        role="region" aria-label={t("chat.svgCanvas")} tabIndex={0}
        onPointerDown={pointerDown} onPointerMove={pointerMove}
        onPointerUp={stopDrag} onPointerCancel={stopDrag} onLostPointerCapture={stopDrag}
        onKeyDown={keyDown}
      >
        {failed ? <p role="alert">{t("chat.svgPreviewFailed")}</p> : (
          <img
            ref={attachImage}
            className="chat-svg-canvas-image" alt={t("chat.svgImage")} draggable={false}
            style={{ width: fitted.width * view.scale, height: fitted.height * view.scale, transform: `translate3d(${view.x}px, ${view.y}px, 0)` }}
            onLoad={(event) => {
              imageSize.current = getSvgPreviewSize(source, {
                width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight,
              })
              if (inline) setAspectRatio(inlineSvgAspectRatio(imageSize.current))
              fit()
            }}
            onError={failPreview}
          />
        )}
      </div>
      <p className="chat-svg-viewer-hint">{t(inline ? "chat.svgInlineViewerHint" : "chat.svgViewerHint")}</p>
    </>
  )
}

export function SvgPreviewDialog({ source, triggerRef, onClose }: {
  source: string
  triggerRef: RefObject<HTMLButtonElement | null>
  onClose: () => void
}) {
  const t = useT()
  const [open, setOpen] = useState(true)
  return (
    <Dialog open={open} onOpenChange={setOpen} onOpenChangeComplete={(opened) => { if (!opened) onClose() }}>
      <DialogContent
        className="chat-svg-dialog" finalFocus={triggerRef} forceOverlay
        onMouseDown={(event) => event.stopPropagation()}
        onClick={(event) => event.stopPropagation()}
      >
        <DialogHeader>
          <DialogTitle>{t("chat.svgDialogTitle")}</DialogTitle>
          <DialogDescription className="sr-only">{t("chat.svgViewerHint")}</DialogDescription>
        </DialogHeader>
        <SvgPreviewCanvas source={source} />
      </DialogContent>
    </Dialog>
  )
}
