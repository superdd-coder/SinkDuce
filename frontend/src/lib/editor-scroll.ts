/** Viewport / caret box in client coordinates. */
export type ClientRange = { top: number; bottom: number }

const DEFAULT_MARGIN = 8

/**
 * How far to move a scroll parent so the caret stays in view (nearest).
 *
 * Empty-heading input rules (`## `) often report a caret box as tall as the
 * editor pad. ProseMirror then pins that box's top to the scroller top.
 * A box taller than half the viewport is treated as a single line at its top.
 */
export function computeCaretScrollDelta(
  caret: ClientRange,
  container: ClientRange,
  margin = DEFAULT_MARGIN,
): number {
  const viewHeight = container.bottom - container.top
  if (viewHeight <= 0) return 0

  const caretHeight = caret.bottom - caret.top
  // Empty heading / min-height pad: Safari and Chrome both invent a box as
  // tall as the editor. Any use of that top/bottom pins the line to the start.
  if (caretHeight <= 0 || caretHeight >= viewHeight * 0.5) return 0

  if (caret.top < container.top + margin) {
    return caret.top - (container.top + margin)
  }
  if (caret.bottom > container.bottom - margin) {
    return caret.bottom - (container.bottom - margin)
  }
  return 0
}

function isScrollableY(el: HTMLElement): boolean {
  const { overflowY } = getComputedStyle(el)
  if (overflowY !== "auto" && overflowY !== "scroll" && overflowY !== "overlay") {
    return false
  }
  return el.scrollHeight > el.clientHeight + 1
}

/** Nearest overflow-y parent. Skips the document so we do not move the window. */
export function findScrollParent(el: HTMLElement | null): HTMLElement | null {
  let cur = el
  while (cur && cur !== document.documentElement && cur !== document.body) {
    if (isScrollableY(cur)) return cur
    cur = cur.parentElement
  }
  return null
}

type CaretView = {
  coordsAtPos: (pos: number, side?: number) => ClientRange
  nodeDOM?: (pos: number) => Node | null
  state: { selection: { head: number; $head?: { depth: number; before: (d: number) => number } } }
  dom: HTMLElement
}

function blockRange(view: CaretView): ClientRange | null {
  const $head = view.state.selection.$head
  if (!$head || !view.nodeDOM) return null
  try {
    const node = view.nodeDOM($head.before($head.depth))
    if (!(node instanceof HTMLElement)) return null
    const r = node.getBoundingClientRect()
    return { top: r.top, bottom: r.bottom }
  } catch {
    return null
  }
}

/**
 * Keep the caret visible inside the editor scroller only.
 * Always returns true so ProseMirror's default (window + pin-to-top) is skipped.
 */
export function scrollCaretIntoNearestView(view: CaretView): boolean {
  let caret: ClientRange
  try {
    caret = view.coordsAtPos(view.state.selection.head)
  } catch {
    return true
  }
  const block = blockRange(view)
  if (block) {
    const caretH = caret.bottom - caret.top
    const blockH = block.bottom - block.top
    if (blockH > 0 && blockH < caretH) caret = block
  }
  const parent = findScrollParent(view.dom.parentElement) ?? findScrollParent(view.dom)
  if (!parent) return true
  const dy = computeCaretScrollDelta(caret, parent.getBoundingClientRect())
  if (dy) parent.scrollTop += dy
  return true
}
