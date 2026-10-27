/**
 * Follow-up polling for a peer's in-flight ingest task.
 *
 * The Ingesting lock is derived from server task state, so a peer's upload must
 * be re-read until the task settles. This device never started that task, so the
 * existing fast poll (which only runs for the initiating client) never covers it
 * — without this the lock would linger after ingestion actually finished.
 *
 * Ownership model: the follower OWNS the loop. Starting it polls immediately and
 * then repeatedly on the interval; the caller ends it by calling
 * `follow(id, false)` once its own poll reports nothing busy, or `stop(id)`.
 * The caller must not re-arm anything, which is what keeps a burst of events for
 * one collection from stacking multiple loops.
 *
 * Design: development_doc/2026-09-16-multi-device-state-sync-design.md §11.
 */

export interface TaskFollower {
  /** Start following a collection while busy; stop it when not. Idempotent. */
  follow: (collectionId: string, busy: boolean) => void
  /** Stop following one collection. */
  stop: (collectionId: string) => void
  /** Stop all followers (unmount / dispose). */
  stopAll: () => void
  /** Whether a collection is currently being followed (diagnostics/tests). */
  isFollowing: (collectionId: string) => boolean
}

interface Options {
  /** Poll callback; the caller refetches and calls follow() with the new state. */
  onPoll: (collectionId: string) => void
  intervalMs: number
  /** Give up after this many polls, so a stuck task cannot poll forever. */
  maxPolls?: number
  setTimer?: (fn: () => void, ms: number) => unknown
  clearTimer?: (handle: unknown) => void
}

export function createTaskFollower(options: Options): TaskFollower {
  const intervalMs = options.intervalMs
  const maxPolls = options.maxPolls ?? 200
  const setTimer =
    options.setTimer ?? ((fn: () => void, ms: number) => setTimeout(fn, ms))
  const clearTimer =
    options.clearTimer ??
    ((h: unknown) => clearTimeout(h as ReturnType<typeof setTimeout>))

  /** Armed timer handle per collection. Presence means "currently following". */
  const timers = new Map<string, unknown>()
  const polls = new Map<string, number>()

  const stop = (collectionId: string) => {
    const handle = timers.get(collectionId)
    if (handle !== undefined) clearTimer(handle)
    timers.delete(collectionId)
    polls.delete(collectionId)
  }

  const tick = (collectionId: string) => {
    // Checked here, not in follow(), so a stop() during onPoll is respected.
    if (!timers.has(collectionId)) return
    const n = (polls.get(collectionId) ?? 0) + 1
    polls.set(collectionId, n)
    if (n > maxPolls) {
      stop(collectionId)
      return
    }
    options.onPoll(collectionId)
    // The loop re-arms itself. It stays alive until the caller calls
    // follow(id, false) or stop(id); a caller must never re-arm.
    if (!timers.has(collectionId)) return
    timers.set(
      collectionId,
      setTimer(() => tick(collectionId), intervalMs),
    )
  }

  return {
    follow: (collectionId, busy) => {
      if (!busy) {
        stop(collectionId)
        return
      }
      // Already following: nothing to do. Starting again would create a second
      // loop and double the request rate for this collection.
      if (timers.has(collectionId)) return
      timers.set(collectionId, null)
      polls.set(collectionId, 1)
      options.onPoll(collectionId)
      if (!timers.has(collectionId)) return
      timers.set(
        collectionId,
        setTimer(() => tick(collectionId), intervalMs),
      )
    },
    stop,
    stopAll: () => {
      for (const id of [...timers.keys()]) stop(id)
    },
    isFollowing: (collectionId) => timers.has(collectionId),
  }
}
