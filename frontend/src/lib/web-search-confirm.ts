/**
 * Promise-based gate for web-search HITL during SSE streams.
 *
 * getSnapshot must return a stable reference when nothing changed —
 * returning a fresh `{}` each call causes React useSyncExternalStore
 * infinite re-renders and freezes the UI.
 */

export type WebSearchConfirmState = {
  open: boolean
  query: string
  confirmId: string
  /** Chat / Quick session id for always-allow scoping */
  sessionId: string
}

type Resolver = (approved: boolean) => void

const CLOSED_STATE: WebSearchConfirmState = {
  open: false,
  query: "",
  confirmId: "",
  sessionId: "",
}

/** Per chat-session always-allow map in sessionStorage */
const ALWAYS_ALLOW_MAP_KEY = "web_search_always_allow_by_session"
/** Legacy tab-wide flag (cleared so old tests don't skip the dialog) */
const LEGACY_ALWAYS_KEY = "web_search_always_allow_session"

let pending: {
  query: string
  confirmId: string
  sessionId: string
  resolve: Resolver
} | null = null
/** Cached snapshot for subscribers (stable until notify). */
let snapshot: WebSearchConfirmState = CLOSED_STATE
const listeners = new Set<() => void>()

/** DOM node of the active chat composer — dialog anchors just above it. */
let confirmAnchor: HTMLElement | null = null
const anchorListeners = new Set<() => void>()

function notify() {
  snapshot = pending
    ? {
        open: true,
        query: pending.query,
        confirmId: pending.confirmId,
        sessionId: pending.sessionId,
      }
    : CLOSED_STATE
  listeners.forEach((l) => l())
}

export function getWebSearchConfirmState(): WebSearchConfirmState {
  return snapshot
}

export function subscribeWebSearchConfirm(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** Register the composer root so the confirm card can sit flush above it. */
export function setWebSearchConfirmAnchor(el: HTMLElement | null) {
  if (confirmAnchor === el) return
  confirmAnchor = el
  anchorListeners.forEach((l) => l())
}

export function getWebSearchConfirmAnchor(): HTMLElement | null {
  return confirmAnchor
}

export function subscribeWebSearchConfirmAnchor(listener: () => void): () => void {
  anchorListeners.add(listener)
  return () => {
    anchorListeners.delete(listener)
  }
}

function loadAlwaysMap(): Record<string, boolean> {
  try {
    const raw = sessionStorage.getItem(ALWAYS_ALLOW_MAP_KEY)
    if (raw) {
      const p = JSON.parse(raw)
      if (p && typeof p === "object") return p as Record<string, boolean>
    }
  } catch {
    /* ignore */
  }
  return {}
}

function saveAlwaysMap(map: Record<string, boolean>) {
  try {
    sessionStorage.setItem(ALWAYS_ALLOW_MAP_KEY, JSON.stringify(map))
  } catch {
    /* ignore */
  }
}

export function isWebSearchAlwaysAllow(sessionId?: string | null): boolean {
  if (!sessionId) return false
  const map = loadAlwaysMap()
  return map[sessionId] === true
}

/** Persist "always allow" for one chat session id (until tab closes). */
export function setWebSearchAlwaysAllow(sessionId: string | null | undefined, enabled: boolean) {
  if (!sessionId) return
  const map = loadAlwaysMap()
  if (enabled) map[sessionId] = true
  else delete map[sessionId]
  saveAlwaysMap(map)
  try {
    sessionStorage.removeItem(LEGACY_ALWAYS_KEY)
  } catch {
    /* ignore */
  }
}

/** Clear always-allow for a session (e.g. when Web toggle is turned off). */
export function clearWebSearchAlwaysAllow(sessionId?: string | null) {
  if (sessionId) setWebSearchAlwaysAllow(sessionId, false)
  try {
    sessionStorage.removeItem(LEGACY_ALWAYS_KEY)
  } catch {
    /* ignore */
  }
}

/**
 * Show confirm UI; resolves when user clicks Allow / Deny / Always.
 * @param sessionId — used for per-session always-allow
 */
export function promptWebSearchConfirm(
  confirmId: string,
  query: string,
  sessionId?: string | null,
): Promise<boolean> {
  const sid = (sessionId || "").trim()

  // Per-session auto-approve (no dialog)
  if (sid && isWebSearchAlwaysAllow(sid)) {
    return Promise.resolve(true)
  }

  // Clear legacy silent-approve so the dialog can show again
  try {
    sessionStorage.removeItem(LEGACY_ALWAYS_KEY)
  } catch {
    /* ignore */
  }

  // If a previous dialog is still open, deny it so we don't deadlock
  if (pending) {
    const prev = pending
    pending = null
    notify()
    prev.resolve(false)
  }

  return new Promise<boolean>((resolve) => {
    pending = { query, confirmId, sessionId: sid, resolve }
    notify()
    // Safety: never leave stream hanging if UI fails to render (120s matches backend)
    window.setTimeout(() => {
      if (pending?.confirmId === confirmId) {
        console.warn(
          "[web-search-confirm] timed out waiting for user — auto-decline",
          confirmId,
        )
        answerWebSearchConfirm(false)
      }
    }, 120_000)
  })
}

export function answerWebSearchConfirm(
  approved: boolean,
  opts?: { always?: boolean },
) {
  if (opts?.always && approved && pending?.sessionId) {
    setWebSearchAlwaysAllow(pending.sessionId, true)
  }
  if (!pending) return
  const { resolve } = pending
  pending = null
  notify()
  resolve(approved)
}
