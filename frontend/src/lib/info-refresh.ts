/**
 * Lightweight pub/sub so INFO panel (and Notes card) can hot-refresh after
 * note ingest, definitive toggle, consolidate, etc. — without full remounts.
 */

export const INFO_REFRESH_EVENT = "sinkduce:info-refresh"

export type InfoRefreshDetail = {
  /** Limit refresh to one collection; omit = any open Info panel. */
  collectionId?: string
  /** Why the refresh fired (for logs / selective reload). */
  reason?:
    | "note-ingest"
    | "note-uningest"
    | "consolidate"
    | "definitive"
    | "upload"
    | "manual"
    | "poll"
}

export function triggerInfoRefresh(detail: InfoRefreshDetail = {}) {
  if (typeof window === "undefined") return
  window.dispatchEvent(new CustomEvent(INFO_REFRESH_EVENT, { detail }))
}

export function onInfoRefresh(
  handler: (detail: InfoRefreshDetail) => void
): () => void {
  if (typeof window === "undefined") return () => {}
  const listener = (e: Event) => {
    const ce = e as CustomEvent<InfoRefreshDetail>
    handler(ce.detail ?? {})
  }
  window.addEventListener(INFO_REFRESH_EVENT, listener)
  return () => window.removeEventListener(INFO_REFRESH_EVENT, listener)
}
