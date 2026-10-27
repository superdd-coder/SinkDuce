/**
 * Follow-up polling for a peer's in-flight ingest task.
 *
 * The Ingesting lock is derived from server task state, so it must be re-read
 * until the task settles — but this device never started the task, so nothing
 * else would ever re-check it (the existing fast poll only runs for the client
 * that began the work). Without this, a lock drawn from a peer's upload would
 * stay on screen forever after ingestion actually finished.
 *
 * Design: development_doc/2026-09-16-multi-device-state-sync-design.md §11.
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { test } from "node:test"

import { createTaskFollower } from "../src/api/task-follow.ts"
import { projectIngestLocks } from "../src/api/ingest-lock.ts"

function makeClock() {
  const timers: Array<{ fn: () => void; ms: number }> = []
  return {
    timers,
    setTimer: (fn: () => void, ms: number) => {
      timers.push({ fn, ms })
      return timers.length - 1
    },
    clearTimer: () => {},
    /** Run all currently armed timers once. */
    tick: () => {
      const batch = timers.splice(0, timers.length)
      for (const t of batch) t.fn()
    },
  }
}

test("notifies immediately when a task is busy", () => {
  const clock = makeClock()
  const calls: string[] = []
  const follower = createTaskFollower({
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    onPoll: (id) => calls.push(id),
    intervalMs: 1500,
  })
  follower.follow("c1", true)
  assert.deepEqual(calls, ["c1"])
})

test("keeps polling while busy and stops when the task settles", () => {
  const clock = makeClock()
  const calls: string[] = []
  let busy = true
  const follower = createTaskFollower({
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    onPoll: (id) => {
      calls.push(id)
    },
    intervalMs: 1500,
  })
  follower.follow("c1", busy)
  clock.tick() // second poll
  clock.tick() // third poll
  assert.equal(calls.length, 3)
  follower.stop("c1")
  clock.tick()
  assert.equal(calls.length, 3, "no polling after stop")
})

test("re-following the same collection does not stack loops", () => {
  // Two events for one collection must not create two concurrent pollers,
  // which would double the request rate for that collection.
  const clock = makeClock()
  const calls: string[] = []
  const follower = createTaskFollower({
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    onPoll: (id) => calls.push(id),
    intervalMs: 1500,
  })
  follower.follow("c1", true)
  follower.follow("c1", true)
  // If the second follow had stacked a loop, one tick would poll twice (4 total).
  clock.tick()
  assert.deepEqual(calls, ["c1", "c1"], "one immediate poll plus one per tick")
  assert.equal(follower.isFollowing("c1"), true)
})

test("followers are independent per collection", () => {
  const clock = makeClock()
  const calls: string[] = []
  const follower = createTaskFollower({
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    onPoll: (id) => calls.push(id),
    intervalMs: 1500,
  })
  follower.follow("c1", true)
  follower.follow("c2", true)
  clock.tick()
  assert.deepEqual(calls.sort(), ["c1", "c1", "c2", "c2"])
})

test("stopAll clears everything (dispose path)", () => {
  const clock = makeClock()
  const calls: string[] = []
  const follower = createTaskFollower({
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    onPoll: (id) => calls.push(id),
    intervalMs: 1500,
  })
  follower.follow("c1", true)
  follower.follow("c2", true)
  follower.stopAll()
  clock.tick()
  assert.deepEqual(calls, ["c1", "c2"], "only the immediate polls ran")
})

test("stop on an unknown collection is a no-op", () => {
  const clock = makeClock()
  const follower = createTaskFollower({
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    onPoll: () => {},
    intervalMs: 1500,
  })
  follower.stop("never-followed")
  clock.tick()
  assert.equal(clock.timers.length, 0)
})

test("polling stops after a maximum number of attempts", () => {
  // Safety net: a task stuck in a busy state must not poll forever.
  const clock = makeClock()
  let polls = 0
  const follower = createTaskFollower({
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    onPoll: () => {
      polls += 1
    },
    intervalMs: 1500,
    maxPolls: 3,
  })
  follower.follow("c1", true)
  for (let i = 0; i < 10; i++) clock.tick()
  assert.equal(polls, 3, "must give up after maxPolls")
})

// ── End-to-end: the peer lock lifecycle (audit fix A8) ──────────

test("a peer's lock appears from task state and clears on completion", () => {
  // The lock is derived from server task state and the follower trails it, so a
  // peer's upload shows a spinner here AND the spinner goes away when ingestion
  // actually finishes — the two halves that the audit found broken separately.
  let tasks: Array<{
    id: string
    status: string
    progress: number
    message: string
    file_id: string
  }> = [
    {
      id: "t1",
      status: "processing",
      progress: 40,
      message: "embedding",
      file_id: "f1",
    },
  ]
  let locks: Record<string, { taskId: string }> = {}

  const clock = makeClock()
  const follower = createTaskFollower({
    intervalMs: 1000,
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    onPoll: (cid) => {
      locks = projectIngestLocks(cid, tasks as never, locks as never)
      const busy = tasks.some(
        (t) => t.status === "pending" || t.status === "processing",
      )
      follower.follow(cid, busy)
    },
  })

  follower.follow("c1", true)
  assert.deepEqual(Object.keys(locks), ["f1"], "lock appears from peer task state")

  tasks = [
    { id: "t1", status: "completed", progress: 100, message: "done", file_id: "f1" },
  ]
  clock.tick()
  assert.deepEqual(locks, {}, "lock clears once the task completes")
  assert.equal(follower.isFollowing("c1"), false, "polling stops when quiet")
  clock.tick()
  assert.equal(follower.isFollowing("c1"), false, "no residual polling")
})
