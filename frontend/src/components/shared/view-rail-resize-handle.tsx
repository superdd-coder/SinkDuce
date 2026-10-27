import { useEffect, useMemo, useState } from "react"
import { useT } from "@/i18n/use-t"
import { useAppStore } from "@/stores/app-store"
import { createViewRailResizeController, viewRailKeyboardWidth } from "@/lib/view-rail-resize"
import { DEFAULT_VIEW_RAIL_WIDTH, MAX_VIEW_RAIL_WIDTH, MIN_VIEW_RAIL_WIDTH } from "@/lib/view-rail-width"

export function ViewRailResizeHandle() {
  const t = useT()
  const width = useAppStore((state) => state.viewRailWidth)
  const setWidth = useAppStore((state) => state.setViewRailWidth)
  const sidebarView = useAppStore((state) => state.sidebarView)
  const [previewWidth, setPreviewWidth] = useState<number | null>(null)
  const resize = useMemo(() => createViewRailResizeController({
    onPreview: setPreviewWidth,
    onCommit: setWidth,
  }), [setWidth])

  // Shell views stay mounted when hidden, so view changes must also cancel.
  useEffect(() => () => resize.cancel(), [resize, sidebarView])

  return (
    <div
      className="pm-view-rail-resize-handle"
      role="separator"
      aria-orientation="vertical"
      aria-label={t("shell.resizeSidebar")}
      aria-valuemin={MIN_VIEW_RAIL_WIDTH}
      aria-valuemax={MAX_VIEW_RAIL_WIDTH}
      aria-valuenow={previewWidth ?? width}
      title={t("shell.resizeSidebarHint")}
      tabIndex={0}
      data-resizing={previewWidth !== null ? "true" : undefined}
      onPointerDown={(event) => {
        const target = event.currentTarget
        const host = target.closest<HTMLElement>(".pm-shell-view-host")
        if (!host) return
        if (resize.start({
          pointerId: event.pointerId, clientX: event.clientX,
          button: event.button, isPrimary: event.isPrimary,
          width, target, host, documentStyle: target.ownerDocument.documentElement.style,
        })) {
          event.preventDefault()
          target.focus({ preventScroll: true })
        }
      }}
      onPointerMove={(event) => resize.move(event.pointerId, event.clientX)}
      onPointerUp={(event) => resize.finish(event.pointerId, event.clientX)}
      onPointerCancel={(event) => resize.cancel(event.pointerId)}
      onLostPointerCapture={(event) => resize.cancel(event.pointerId)}
      onBlur={() => resize.cancel()}
      onKeyDown={(event) => {
        if (event.key === "Escape" && resize.isDragging()) {
          event.preventDefault()
          event.stopPropagation()
          resize.cancel()
          return
        }
        if (resize.isDragging() || event.altKey || event.ctrlKey || event.metaKey) return
        const next = viewRailKeyboardWidth(width, event.key, event.shiftKey)
        if (next === undefined) return
        event.preventDefault()
        if (next !== width) setWidth(next)
      }}
      onDoubleClick={(event) => {
        event.preventDefault()
        resize.cancel()
        if (width !== DEFAULT_VIEW_RAIL_WIDTH) setWidth(DEFAULT_VIEW_RAIL_WIDTH)
      }}
    />
  )
}
