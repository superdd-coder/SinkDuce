/**
 * Integration of the modules the hook composes, in the order the hook uses them:
 * shouldApply → (accumulate) → debounce → plan/dedupe → run.
 *
 * The hook itself cannot run under `node --test` (it imports React and the
 * `@/`-aliased stores), so this pins the composition that a mismatch between the
 * modules would break.
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { test } from "node:test"

import {
  dedupeActions,
  planActions,
  runActions,
  type Action,
  type ScopeActions,
} from "../src/api/event-apply.ts"
import { createDebouncer, shouldApply } from "../src/api/event-map.ts"
import type { ChangeEvent } from "../src/api/events.ts"

/** Driver mirroring the hook's event handling. */
function makeDriver(selfId: string) {
  const calls: string[] = []
  const io: ScopeActions = {
    refreshCollection: (id) => calls.push(`refreshCollection:${id}`),
    refreshTimeline: () => calls.push("refreshTimeline"),
    refreshCollections: () => calls.push("refreshCollections"),
    refreshSessions: () => calls.push("refreshSessions"),
    invalidateSession: (id) => calls.push(`invalidateSession:${id}`),
  }
  // Batch accumulates planned actions, so a burst cannot lose an update.
  let batch: Action[] = []
  const timers: Array<() => void> = []
  const debouncer = createDebouncer({
    delayMs: 200,
    setTimer: (fn) => {
      timers.push(fn)
      return timers.length - 1
    },
    clearTimer: () => {},
    onFlush: () => {
      runActions(dedupeActions(batch), io)
      batch = []
    },
  })
  return {
    calls,
    feed(partial: Partial<ChangeEvent>) {
      const event: ChangeEvent = {
        seq: 1,
        ts: 0,
        type: "file.updated",
        domain: "files",
        scope: {},
        origin: {},
        payload: {},
        ...partial,
      }
      if (!shouldApply(event, selfId)) return
      batch.push(...planActions(event.domain, event.scope))
      debouncer.schedule()
    },
    flush: () => timers.forEach((fn) => fn()),
  }
}

test("a peer's burst across files and timeline on one collection refetches once", () => {
  // Both domains refresh the same collection surface; dedup must collapse them.
  const d = makeDriver("me")
  d.feed({ domain: "files", scope: { collection_id: "c1" }, origin: { client_id: "peer" } })
  d.feed({ domain: "timeline", scope: { collection_id: "c1" }, origin: { client_id: "peer" } })
  d.feed({ domain: "files", scope: { collection_id: "c1" }, origin: { client_id: "peer" } })
  d.flush()
  assert.deepEqual(d.calls, ["refreshCollection:c1"])
})

test("two different sessions in one burst both get invalidated", () => {
  // Under a latest-event-wins cache the first session would be silently dropped.
  const d = makeDriver("me")
  d.feed({ domain: "sessions", scope: { session_id: "s1" }, origin: { client_id: "peer" } })
  d.feed({ domain: "sessions", scope: { session_id: "s2" }, origin: { client_id: "peer" } })
  d.flush()
  assert.deepEqual(d.calls, [
    "refreshSessions",
    "invalidateSession:s1",
    "invalidateSession:s2",
  ])
})

test("changes in two collections both refetch", () => {
  const d = makeDriver("me")
  d.feed({ domain: "files", scope: { collection_id: "c1" }, origin: { client_id: "peer" } })
  d.feed({ domain: "files", scope: { collection_id: "c2" }, origin: { client_id: "peer" } })
  d.flush()
  assert.deepEqual(d.calls, ["refreshCollection:c1", "refreshCollection:c2"])
})

test("our own file change is suppressed end to end", () => {
  const d = makeDriver("me")
  d.feed({ domain: "files", scope: { collection_id: "c1" }, origin: { client_id: "me" } })
  d.flush()
  assert.deepEqual(d.calls, [])
})

test("a mixed burst of peers and self applies only the peers' parts", () => {
  const d = makeDriver("me")
  d.feed({ domain: "collections", scope: {}, origin: { client_id: "me" } })
  d.feed({ domain: "files", scope: { collection_id: "c1" }, origin: { client_id: "me" } })
  d.feed({ domain: "collections", scope: {}, origin: { client_id: "peer" } })
  d.flush()
  assert.deepEqual(d.calls, ["refreshCollections"])
})

test("a background completion (no origin) reaches us", () => {
  const d = makeDriver("me")
  d.feed({ domain: "files", scope: { collection_id: "c1" }, origin: {} })
  d.flush()
  assert.deepEqual(d.calls, ["refreshCollection:c1"])
})

test("nothing flushes when every event was our own echo", () => {
  const d = makeDriver("me")
  d.feed({ domain: "files", scope: { collection_id: "c1" }, origin: { client_id: "me" } })
  d.feed({ domain: "sessions", scope: { session_id: "s1" }, origin: { client_id: "me" } })
  d.flush()
  assert.deepEqual(d.calls, [])
})
