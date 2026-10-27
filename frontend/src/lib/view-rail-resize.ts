import { MAX_VIEW_RAIL_WIDTH, MIN_VIEW_RAIL_WIDTH, normalizeViewRailWidth } from "./view-rail-width.ts"

type InlineStyle = Pick<CSSStyleDeclaration,
  "getPropertyValue" | "getPropertyPriority" | "setProperty" | "removeProperty">
type PointerCaptureTarget = Pick<HTMLElement,
  "setPointerCapture" | "hasPointerCapture" | "releasePointerCapture">

interface DragStart {
  pointerId: number
  clientX: number
  button: number
  isPrimary: boolean
  width: number
  target: PointerCaptureTarget
  host: { style: InlineStyle }
  documentStyle: InlineStyle
}

function rememberProperty(style: InlineStyle, name: string) {
  const value = style.getPropertyValue(name)
  const priority = style.getPropertyPriority(name)
  return () => {
    if (value) style.setProperty(name, value, priority)
    else style.removeProperty(name)
  }
}

/** Keeps live DOM previews separate from the persisted width until release. */
export function createViewRailResizeController(callbacks: {
  onPreview: (width: number | null) => void
  onCommit: (width: number) => void
}, options: { propertyName?: string; normalizeWidth?: (width: number) => number } = {}) {
  const propertyName = options.propertyName ?? "--pm-view-rail-w"
  const normalizeWidth = options.normalizeWidth ?? normalizeViewRailWidth
  let drag: (DragStart & {
    initialWidth: number
    currentWidth: number
    restoreWidth: () => void
    restoreCursor: () => void
    restoreSelection: () => void
  }) | null = null

  function cleanup(restoreWidth: boolean) {
    const previous = drag
    if (!previous) return
    // releasePointerCapture can dispatch lostpointercapture synchronously.
    drag = null
    if (restoreWidth) previous.restoreWidth()
    previous.restoreCursor()
    previous.restoreSelection()
    callbacks.onPreview(null)
    try {
      if (previous.target.hasPointerCapture(previous.pointerId)) {
        previous.target.releasePointerCapture(previous.pointerId)
      }
    } catch { /* A detached handle may already have lost its capture. */ }
  }

  function move(pointerId: number, clientX: number) {
    if (!drag || pointerId !== drag.pointerId || !Number.isFinite(clientX)) return
    drag.currentWidth = normalizeWidth(drag.initialWidth + clientX - drag.clientX)
    drag.host.style.setProperty(propertyName, `${drag.currentWidth}px`)
    callbacks.onPreview(drag.currentWidth)
  }

  return {
    start(input: DragStart): boolean {
      if (drag || input.button !== 0 || !input.isPrimary || !Number.isFinite(input.clientX)) return false
      try { input.target.setPointerCapture(input.pointerId) } catch { return false }
      const width = normalizeWidth(input.width)
      drag = {
        ...input, initialWidth: width, currentWidth: width,
        restoreWidth: rememberProperty(input.host.style, propertyName),
        restoreCursor: rememberProperty(input.documentStyle, "cursor"),
        restoreSelection: rememberProperty(input.documentStyle, "user-select"),
      }
      input.documentStyle.setProperty("cursor", "col-resize", "important")
      input.documentStyle.setProperty("user-select", "none", "important")
      callbacks.onPreview(width)
      return true
    },
    move,
    finish(pointerId: number, clientX: number) {
      if (!drag || pointerId !== drag.pointerId) return
      move(pointerId, clientX)
      const width = drag.currentWidth
      const changed = width !== drag.initialWidth
      cleanup(!changed)
      // Stationary clicks (including the two clicks before double-click reset)
      // do not cause redundant localStorage writes.
      if (changed) callbacks.onCommit(width)
    },
    cancel(pointerId?: number) {
      if (!drag || (pointerId !== undefined && pointerId !== drag.pointerId)) return
      cleanup(true)
    },
    isDragging() { return drag !== null },
  }
}

export function viewRailKeyboardWidth(width: number, key: string, shift: boolean): number | undefined {
  const step = shift ? 40 : 10
  if (key === "Home") return MIN_VIEW_RAIL_WIDTH
  if (key === "End") return MAX_VIEW_RAIL_WIDTH
  if (key === "ArrowLeft") return normalizeViewRailWidth(width - step)
  if (key === "ArrowRight") return normalizeViewRailWidth(width + step)
  return undefined
}
