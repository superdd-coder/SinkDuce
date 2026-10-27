/**
 * Event dispatch rules: echo suppression and burst debouncing.
 *
 * Design: development_doc/2026-09-16-multi-device-state-sync-design.md (§8, §10).
 * Kept as plain functions rather than React internals so the decisions that
 * actually matter are directly testable.
 */

import type { ChangeEvent } from "./events.ts"

/** Domains whose own-origin events still apply to the initiating client. */
const ECHO_EXEMPT_DOMAINS = new Set(["tasks"])

/**
 * Whether this client should act on an event.
 *
 * A client skips the echo of its own action, because it already updated
 * optimistically. `tasks` is exempt: the device that queued a task no longer
 * runs a fast poll, so it needs the same task signals every other device needs.
 * Events with no origin (server-side completions) always apply.
 */
export function shouldApply(event: ChangeEvent, selfClientId: string | null): boolean {
  const origin = event.origin?.client_id
  if (!origin || !selfClientId) return true
  if (origin !== selfClientId) return true
  return ECHO_EXEMPT_DOMAINS.has(event.domain)
}

interface DebouncerOptions {
  delayMs: number
  onFlush: () => void
  /** Injected so tests can drive time without real timers. */
  setTimer?: (fn: () => void, ms: number) => unknown
  clearTimer?: (handle: unknown) => void
  /** Upper bound on how long a flush may be deferred under a continuous stream. */
  maxWaitMs?: number
}

export interface Debouncer {
  /** Note that there is work to flush. */
  schedule: () => void
  dispose: () => void
}

/**
 * Trailing debounce with a hard cap.
 *
 * A burst collapses into one flush. Without the cap, a steady stream of events
 * (dense writes during transcription or ingestion) could defer the flush
 * indefinitely, so the first `schedule()` of a batch also arms a cap timer.
 *
 * Deliberately not keyed per surface: the batch accumulates deduplicated actions
 * rather than the latest event, so batching everything together cannot lose an
 * update the way a "latest event wins" map would.
 */
export function createDebouncer(options: DebouncerOptions): Debouncer {
  const delayMs = options.delayMs
  const maxWaitMs = options.maxWaitMs ?? delayMs * 5
  const setTimer =
    options.setTimer ?? ((fn: () => void, ms: number) => setTimeout(fn, ms))
  const clearTimer =
    options.clearTimer ??
    ((h: unknown) => clearTimeout(h as ReturnType<typeof setTimeout>))

  let flushTimer: unknown = null
  let capTimer: unknown = null
  let disposed = false
  let armed = false

  const flush = () => {
    // Guarded by `armed` rather than relying on clearTimeout alone: a timer that
    // already fired (or a stale handle that clearTimer could not cancel) must not
    // cause a second flush of the same batch.
    if (!armed) return
    armed = false
    if (flushTimer !== null) {
      clearTimer(flushTimer)
      flushTimer = null
    }
    if (capTimer !== null) {
      clearTimer(capTimer)
      capTimer = null
    }
    if (disposed) return
    options.onFlush()
  }

  return {
    schedule() {
      if (disposed) return
      armed = true
      if (capTimer === null) capTimer = setTimer(flush, maxWaitMs)
      if (flushTimer !== null) clearTimer(flushTimer)
      flushTimer = setTimer(flush, delayMs)
    },
    dispose() {
      disposed = true
      if (flushTimer !== null) clearTimer(flushTimer)
      if (capTimer !== null) clearTimer(capTimer)
      flushTimer = null
      capTimer = null
      armed = false
    },
  }
}
