/**
 * Cross-view refresh signal for domains whose data lives in component-local state
 * rather than a store (meetings, notes, hot words, people).
 *
 * A monotonic counter per domain is enough: the app-wide event stream bumps it,
 * and a view's fetch effect depends on it — the same idiom the timeline already
 * uses with `timelineRefreshEpoch`. This keeps the event layer from reaching into
 * view internals.
 */

type Listener = () => void

const listeners = new Map<string, Set<Listener>>()
const epochs = new Map<string, number>()

/** Current epoch for a domain (0 if never bumped). */
export function domainEpoch(domain: string): number {
  return epochs.get(domain) ?? 0
}

/** Bump a domain's epoch, notifying every subscriber. */
export function bumpDomain(domain: string): void {
  epochs.set(domain, domainEpoch(domain) + 1)
  const subs = listeners.get(domain)
  if (!subs) return
  // Iterate a snapshot: a listener may unsubscribe during the pass.
  for (const fn of [...subs]) fn()
}

/** Subscribe to a domain's changes; returns an unsubscribe function. */
export function subscribeDomain(domain: string, fn: Listener): () => void {
  let subs = listeners.get(domain)
  if (!subs) {
    subs = new Set()
    listeners.set(domain, subs)
  }
  subs.add(fn)
  return () => {
    subs?.delete(fn)
  }
}
