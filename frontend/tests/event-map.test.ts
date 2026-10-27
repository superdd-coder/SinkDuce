/**
 * Echo suppression and burst debouncing.
 *
 * Design: development_doc/2026-09-16-multi-device-state-sync-design.md (§8, §10).
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { test } from "node:test"

import { createDebouncer, shouldApply } from "../src/api/event-map.ts"
import type { ChangeEvent } from "../src/api/events.ts"

function ev(partial: Partial<ChangeEvent>): ChangeEvent {
  return {
    seq: 1,
    ts: 0,
    type: "file.updated",
    domain: "files",
    scope: {},
    origin: {},
    payload: {},
    ...partial,
  }
}

// ── Echo suppression ────────────────────────────────────────────

test("applies an event from another client", () => {
  assert.equal(shouldApply(ev({ origin: { client_id: "other" } }), "me"), true)
})

test("skips an event this client caused", () => {
  assert.equal(shouldApply(ev({ origin: { client_id: "me" } }), "me"), false)
})

test("applies a background event with no client id", () => {
  // Server-side completions have no origin and must reach everyone.
  assert.equal(shouldApply(ev({ origin: {} }), "me"), true)
  assert.equal(shouldApply(ev({ origin: { client_id: null } }), "me"), true)
})

test("applies everything when this client has no id", () => {
  assert.equal(shouldApply(ev({ origin: { client_id: "me" } }), null), true)
})

test("applies our own task events despite the id matching", () => {
  // The tasks exception: the initiating device must also consume these, because
  // it no longer runs a fast poll of its own.
  assert.equal(
    shouldApply(ev({ domain: "tasks", origin: { client_id: "me" } }), "me"),
    true,
  )
})

// ── Debouncing ──────────────────────────────────────────────────

function makeDebouncer() {
  const flushes: number[] = []
  const timers: Array<{ fn: () => void; ms: number }> = []
  const d = createDebouncer({
    delayMs: 200,
    maxWaitMs: 1000,
    setTimer: (fn, ms) => {
      timers.push({ fn, ms })
      return timers.length - 1
    },
    clearTimer: () => {},
    onFlush: () => flushes.push(flushes.length),
  })
  return {
    d,
    flushes,
    timers,
    fireDelayTimers: () => timers.filter((t) => t.ms === 200).forEach((t) => t.fn()),
    fireCapTimer: () => timers.filter((t) => t.ms === 1000).forEach((t) => t.fn()),
  }
}

test("a burst debounces to a single flush", () => {
  const h = makeDebouncer()
  h.d.schedule()
  h.d.schedule()
  h.d.schedule()
  assert.deepEqual(h.flushes, []) // nothing yet
  h.fireDelayTimers()
  assert.equal(h.flushes.length, 1)
})

test("re-arms after flushing so a later burst flushes again", () => {
  const h = makeDebouncer()
  h.d.schedule()
  h.fireDelayTimers()
  h.d.schedule()
  h.fireDelayTimers()
  assert.equal(h.flushes.length, 2)
})

test("a continuous stream cannot defer the flush past maxWait", () => {
  // Dense writes during ingestion/transcription keep re-arming the trailing
  // timer; without the cap the flush would never happen.
  const h = makeDebouncer()
  h.d.schedule()
  for (let i = 0; i < 5; i++) h.d.schedule()
  h.fireCapTimer()
  assert.equal(h.flushes.length, 1)
})

test("the cap timer is armed with maxWaitMs on first schedule", () => {
  const h = makeDebouncer()
  h.d.schedule()
  assert.ok(
    h.timers.some((t) => t.ms === 1000),
    "expected a max-wait timer to be armed",
  )
})

test("dispose prevents a pending flush", () => {
  const h = makeDebouncer()
  h.d.schedule()
  h.d.dispose()
  h.fireDelayTimers()
  h.fireCapTimer()
  assert.equal(h.flushes.length, 0)
})

test("scheduling after dispose is ignored", () => {
  const h = makeDebouncer()
  h.d.dispose()
  h.d.schedule()
  h.fireDelayTimers()
  assert.equal(h.flushes.length, 0)
})
