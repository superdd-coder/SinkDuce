/**
 * Coalesce the file-viewer web toolbar's per-event DOM rebuilds.
 *
 * Root cause of the toolbar flicker while scrolling a preview: the
 * @file-viewer controller re-renders the whole toolbar on EVERY viewer event
 * (notifyState → renderToolbar → toolbarEl.replaceChildren() + re-append),
 * and its PDF renderer emits a view-state-change on every scroll frame
 * (renderer-pdf: container scroll → rAF → emitViewStateChange("scroll")).
 * Scrolling a PDF therefore tears the toolbar down 60–120×/s → the pill
 * visibly flickers (worst in WKWebView).
 *
 * Strategy: rebuilds spaced ≥ REBUILD_GAP_MS apart apply live (clicks,
 * keystrokes, load state changes). Rebuilds inside an active burst are
 * recorded instead of applied; when the burst goes quiet, the last recorded
 * rebuild is replayed exactly once — identical end state, one rebuild per
 * burst instead of hundreds. A hard cap bounds staleness under continuous
 * scroll, and focus inside the toolbar (typing in Search) always applies
 * live.
 */

export interface ToolbarRebuildTarget {
  replaceChildren(...nodes: never[]): void
  appendChild(node: never): never
  contains(el: unknown): boolean
}

export interface CoalescerClock {
  now: () => number
  schedule: (delayMs: number, fn: () => void) => unknown
  cancel: (handle: unknown) => void
}

type RecordedOp = { kind: "replace"; args: unknown[] } | { kind: "append"; node: unknown }

/** Rebuilds closer than this belong to the same burst. */
const REBUILD_GAP_MS = 40
/** A burst ends after this much silence since its latest rebuild. */
const QUIET_MS = 160
/** Never hold a burst longer than this — periodic flush under endless scroll. */
const MAX_FREEZE_MS = 1200

const defaultClock = (): CoalescerClock => ({
  now: () => performance.now(),
  schedule: (delayMs, fn) => setTimeout(fn, delayMs),
  cancel: (handle) => clearTimeout(handle as number),
})

export function installToolbarRebuildCoalescer(
  toolbar: ToolbarRebuildTarget,
  clock: CoalescerClock = defaultClock(),
  /**
   * Focus guard — when the user is interacting inside the toolbar (typing in
   * Search) every rebuild must apply live. Injectable for tests.
   */
  isInteracting: () => boolean = () =>
    typeof document !== "undefined" &&
    document.activeElement != null &&
    toolbar.contains(document.activeElement)
): { dispose: (flush?: boolean) => void } {
  const prevReplace = toolbar.replaceChildren
  const prevAppend = toolbar.appendChild
  const applyReplace = (...args: unknown[]) => prevReplace.apply(toolbar, args as never[])
  const applyAppend = (node: unknown) => prevAppend.call(toolbar, node as never)

  let disposed = false
  let frozen = false
  let ops: RecordedOp[] = []
  let lastRebuildAt = -Infinity
  let frozenStart = 0
  let frozenDeadline = 0
  let unfreezeHandle: unknown = null

  const cancelUnfreeze = () => {
    if (unfreezeHandle != null) {
      clock.cancel(unfreezeHandle)
      unfreezeHandle = null
    }
  }

  const replay = () => {
    const pending = ops
    ops = []
    for (const op of pending) {
      if (op.kind === "replace") applyReplace(...op.args)
      else applyAppend(op.node)
    }
  }

  const unfreeze = (flush: boolean) => {
    cancelUnfreeze()
    if (!frozen) return
    frozen = false
    if (flush) replay()
    else ops = []
  }

  /**
   * A rebuild is atomic: replaceChildren() followed by its appends in the
   * same synchronous task. Burst detection therefore happens on
   * replaceChildren only — a live replaceChildren lets its appends through,
   * a frozen one records them.
   */
  const interceptRebuild = () => {
    const now = clock.now()
    if (disposed) return false
    if (isInteracting()) {
      if (frozen) unfreeze(true)
      lastRebuildAt = now
      return false
    }
    const burst = frozen || now - lastRebuildAt < REBUILD_GAP_MS
    lastRebuildAt = now
    if (!burst) return false
    if (frozen) {
      // Slide the quiet deadline, bounded by the hard cap from freeze start.
      frozenDeadline = Math.min(frozenStart + MAX_FREEZE_MS, now + QUIET_MS)
    } else {
      frozen = true
      ops = []
      frozenStart = now
      frozenDeadline = now + QUIET_MS
    }
    cancelUnfreeze()
    unfreezeHandle = clock.schedule(Math.max(frozenDeadline - now, 0), () => {
      unfreezeHandle = null
      unfreeze(true)
      // A still-running burst re-freezes on its next rebuild; the live
      // rebuild here bounds staleness to one toolbar generation per cap.
      lastRebuildAt = -Infinity
    })
    return true
  }

  toolbar.replaceChildren = (...args: never[]) => {
    if (interceptRebuild()) {
      // A fresh replace supersedes everything recorded earlier in the burst.
      ops = [{ kind: "replace", args }]
      return
    }
    applyReplace(...args)
  }

  toolbar.appendChild = (node: never) => {
    if (frozen && !disposed) {
      ops.push({ kind: "append", node })
      return node
    }
    return applyAppend(node)
  }

  return {
    dispose: (flush = true) => {
      disposed = true
      unfreeze(flush)
      toolbar.replaceChildren = prevReplace
      toolbar.appendChild = prevAppend
    },
  }
}
