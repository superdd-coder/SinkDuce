import { useEffect, useMemo, useState } from "react"
import { useAppStore } from "@/stores/app-store"
import { useT } from "@/i18n/use-t"
import { DEFAULT_REPORT_SPLIT, MIN_REPORT_SPLIT, MAX_REPORT_SPLIT, createReportResizeController, reportSplitKeyboardRatio, reportResizeStartingWidth } from "@/lib/report-split"

/** Live pane resizing uses DOM previews; only release persists a browser preference. */
export function ReportResizeHandle() {
  const t = useT()
  const ratio = useAppStore((state) => state.reportChatRatio)
  const setRatio = useAppStore((state) => state.setReportChatRatio)
  const sidebarView = useAppStore((state) => state.sidebarView)
  const [preview, setPreview] = useState<number | null>(null)
  const resize = useMemo(() => createReportResizeController({ onPreview: setPreview, onCommit: setRatio }), [setRatio])
  useEffect(() => () => resize.cancel(), [resize, sidebarView])
  return <div className="pm-report-resize-handle" role="separator" aria-orientation="vertical" tabIndex={0}
    aria-label={t("chat.reports.resize")} title={t("chat.reports.resizeHint")}
    aria-valuemin={MIN_REPORT_SPLIT * 100} aria-valuemax={MAX_REPORT_SPLIT * 100} aria-valuenow={Math.round((preview ?? ratio) * 100)}
    data-resizing={preview !== null ? "true" : undefined}
    onPointerDown={(event) => {
      const target = event.currentTarget
      const host = target.parentElement
      const left = host?.querySelector<HTMLElement>(":scope > .pm-chat, :scope > .pm-report-workspace-chat")
      if (!host || !left || !target.getClientRects().length) return
      const style = getComputedStyle(host)
      const hostWidth = host.clientWidth - parseFloat(style.paddingLeft || "0") - parseFloat(style.paddingRight || "0")
      if (hostWidth < 692) return
      resize.setHostWidth(hostWidth)
      if (resize.start({ pointerId: event.pointerId, clientX: event.clientX, button: event.button, isPrimary: event.isPrimary,
        width: reportResizeStartingWidth(left.getBoundingClientRect().width, parseFloat(style.getPropertyValue("--pm-report-review-shift")) || 0), host, target, documentStyle: target.ownerDocument.documentElement.style })) {
        event.preventDefault(); target.focus({ preventScroll: true })
      }
    }}
    onPointerMove={(event) => resize.move(event.pointerId, event.clientX)}
    onPointerUp={(event) => resize.finish(event.pointerId, event.clientX)}
    onPointerCancel={(event) => resize.cancel(event.pointerId)}
    onLostPointerCapture={(event) => resize.cancel(event.pointerId)}
    onBlur={() => resize.cancel()}
    onDoubleClick={(event) => { event.preventDefault(); resize.cancel(); setRatio(DEFAULT_REPORT_SPLIT) }}
    onKeyDown={(event) => {
      if (event.key === "Escape" && resize.isDragging()) { event.preventDefault(); event.stopPropagation(); resize.cancel(); return }
      if (resize.isDragging() || event.altKey || event.ctrlKey || event.metaKey) return
      const next = reportSplitKeyboardRatio(ratio, event.key, event.shiftKey)
      if (next !== undefined) { event.preventDefault(); setRatio(next) }
    }} />
}
