/**
 * Domain → action planning. This decides which existing store refresh calls an
 * incoming change event requires. Planning is pure (returns action descriptors)
 * so it is directly testable and so a batch of events can be deduplicated before
 * anything is executed.
 *
 * Design: development_doc/2026-09-16-multi-device-state-sync-design.md (§10).
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { test } from "node:test"

import {
  actionKey,
  planActions,
  planResync,
  runActions,
  type Action,
  type ScopeActions,
} from "../src/api/event-apply.ts"

function makeActions() {
  const calls: string[] = []
  const actions: ScopeActions = {
    refreshCollection: (id) => calls.push(`refreshCollection:${id}`),
    refreshTimeline: () => calls.push("refreshTimeline"),
    refreshCollections: () => calls.push("refreshCollections"),
    refreshSessions: () => calls.push("refreshSessions"),
    invalidateSession: (id) => calls.push(`invalidateSession:${id}`),
    syncIngestLocks: (id) => calls.push(`syncIngestLocks:${id}`),
    refreshMeetings: () => calls.push("refreshMeetings"),
    refreshMeeting: (id) => calls.push(`refreshMeeting:${id}`),
    refreshNotes: (id) => calls.push(`refreshNotes:${id}`),
    refreshHotWords: () => calls.push("refreshHotWords"),
    refreshPeople: () => calls.push("refreshPeople"),
    refreshConfig: () => calls.push("refreshConfig"),
  }
  return { actions, calls }
}

// ── Planning ────────────────────────────────────────────────────

test("a file change plans a refresh of that collection", () => {
  assert.deepEqual(planActions("files", { collection_id: "c1" }), [
    { kind: "refreshCollection", collectionId: "c1" },
  ])
})

test("a file change with no collection plans nothing, not a global refetch", () => {
  assert.deepEqual(planActions("files", {}), [])
})

test("a timeline change plans a refresh of its collection", () => {
  assert.deepEqual(planActions("timeline", { collection_id: "c1" }), [
    { kind: "refreshCollection", collectionId: "c1" },
  ])
})

test("a timeline change with no collection plans a timeline nudge", () => {
  assert.deepEqual(planActions("timeline", {}), [{ kind: "refreshTimeline" }])
})

test("a collection change plans a list refresh", () => {
  assert.deepEqual(planActions("collections", {}), [{ kind: "refreshCollections" }])
})

test("a session change plans a list refresh and a cache invalidation", () => {
  assert.deepEqual(planActions("sessions", { session_id: "s1" }), [
    { kind: "refreshSessions" },
    { kind: "invalidateSession", sessionId: "s1" },
  ])
})

test("a session change without an id plans only the list refresh", () => {
  assert.deepEqual(planActions("sessions", { session_id: null }), [
    { kind: "refreshSessions" },
  ])
})

test("an unmapped domain plans nothing yet", () => {
  for (const domain of ["unknown", "nonsense"]) {
    assert.deepEqual(planActions(domain, { collection_id: "c1" }), [])
  }
})

test("a config change plans a config refresh", () => {
  assert.deepEqual(planActions("config", {}), [{ kind: "refreshConfig" }])
})

test("a task change plans a lock sync for its collection", () => {
  assert.deepEqual(planActions("tasks", { collection_id: "c1" }), [
    { kind: "syncIngestLocks", collectionId: "c1" },
  ])
})

test("a task change with no collection plans nothing", () => {
  assert.deepEqual(planActions("tasks", {}), [])
})

test("a meeting change plans a list refresh and a detail refresh", () => {
  assert.deepEqual(planActions("meetings", { meeting_id: "m1" }), [
    { kind: "refreshMeetings" },
    { kind: "refreshMeeting", meetingId: "m1" },
  ])
})

test("a meeting change with no id still refreshes the list", () => {
  assert.deepEqual(planActions("meetings", {}), [{ kind: "refreshMeetings" }])
})

test("a note change plans a notes refresh for its collection", () => {
  assert.deepEqual(planActions("notes", { collection_id: "c1", note_id: "n1" }), [
    { kind: "refreshNotes", collectionId: "c1" },
  ])
})

test("a note change with no collection plans nothing", () => {
  assert.deepEqual(planActions("notes", { note_id: "n1" }), [])
})

test("a hot-words change plans a library refresh", () => {
  assert.deepEqual(planActions("hot_words", {}), [{ kind: "refreshHotWords" }])
})

test("a people change plans a people refresh", () => {
  assert.deepEqual(planActions("people", { person_id: "p1" }), [
    { kind: "refreshPeople" },
  ])
})

// ── Action identity (for deduplication) ─────────────────────────

test("identical actions share a key", () => {
  const a: Action = { kind: "refreshCollection", collectionId: "c1" }
  const b: Action = { kind: "refreshCollection", collectionId: "c1" }
  assert.equal(actionKey(a), actionKey(b))
})

test("actions differing by target or kind have different keys", () => {
  const keys = [
    actionKey({ kind: "refreshCollection", collectionId: "c1" }),
    actionKey({ kind: "refreshCollection", collectionId: "c2" }),
    actionKey({ kind: "refreshTimeline" }),
    actionKey({ kind: "refreshSessions" }),
    actionKey({ kind: "invalidateSession", sessionId: "s1" }),
  ]
  assert.equal(new Set(keys).size, keys.length)
})

// ── Execution ───────────────────────────────────────────────────

test("runActions invokes the matching store callbacks in order", () => {
  const { actions, calls } = makeActions()
  runActions(
    [
      { kind: "refreshCollection", collectionId: "c1" },
      { kind: "refreshSessions" },
    ],
    actions,
  )
  assert.deepEqual(calls, ["refreshCollection:c1", "refreshSessions"])
})

test("two events needing the same refetch dedupe to one call", () => {
  // files and timeline both refresh the same collection surface; a burst
  // containing both must not refetch twice.
  const { actions, calls } = makeActions()
  const planned = [
    ...planActions("files", { collection_id: "c1" }),
    ...planActions("timeline", { collection_id: "c1" }),
  ]
  const seen = new Set<string>()
  const deduped = planned.filter((a) => {
    const k = actionKey(a)
    if (seen.has(k)) return false
    seen.add(k)
    return true
  })
  runActions(deduped, actions)
  assert.deepEqual(calls, ["refreshCollection:c1"])
})

test("distinct surfaces in one batch both run", () => {
  const { actions, calls } = makeActions()
  runActions(
    [
      ...planActions("files", { collection_id: "c1" }),
      ...planActions("files", { collection_id: "c2" }),
    ],
    actions,
  )
  assert.deepEqual(calls, ["refreshCollection:c1", "refreshCollection:c2"])
})

test("two different session invalidations in one batch both run", () => {
  const { actions, calls } = makeActions()
  runActions(
    [
      ...planActions("sessions", { session_id: "s1" }),
      ...planActions("sessions", { session_id: "s2" }),
    ],
    actions,
  )
  assert.deepEqual(calls, [
    "refreshSessions",
    "invalidateSession:s1",
    "refreshSessions",
    "invalidateSession:s2",
  ])
})

// ── Full reconcile after a gap/overflow ─────────────────────────

test("a resync plans a refresh of every visible surface", () => {
  const actions = planResync("c1")
  assert.deepEqual(actions, [
    { kind: "refreshCollections" },
    { kind: "refreshTimeline" },
    { kind: "refreshMeetings" },
    { kind: "refreshSessions" },
    { kind: "refreshHotWords" },
    { kind: "refreshPeople" },
    { kind: "refreshCollection", collectionId: "c1" },
    // The Ingesting lock is derived state too, so a resync must rebuild it —
    // otherwise a lock from before the gap is never re-established or cleared.
    { kind: "syncIngestLocks", collectionId: "c1" },
  ])
})

test("a resync with no active collection skips the collection-scoped refreshes", () => {
  const actions = planResync(null)
  assert.ok(!actions.some((a) => a.kind === "refreshCollection"))
  assert.ok(!actions.some((a) => a.kind === "syncIngestLocks"))
  assert.ok(actions.some((a) => a.kind === "refreshCollections"))
})

test("a resync is deliberately broad rather than targeted", () => {
  // Replay was impossible, so guessing what changed is worse than re-reading.
  assert.ok(planResync("c1").length >= 6)
})
