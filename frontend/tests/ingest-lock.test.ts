/**
 * The Ingesting lock, derived from server task state.
 *
 * Previously the lock was purely a local optimistic map written only by the
 * device that started an upload, so a peer could never draw it. It is now a
 * projection of the backend task list (development_doc/2026-09-16-multi-device-state-sync-design.md §9).
 *
 * Projection must be scoped per collection: the task list for one collection is
 * authoritative only for that collection, and blindly replacing the map would
 * wipe the lock of a file ingesting in another collection.
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { test } from "node:test"

import { projectIngestLocks, type IngestLock } from "../src/api/ingest-lock.ts"
import type { TaskInfo } from "../src/api/collections.ts"

function task(partial: Partial<TaskInfo> & { id: string }): TaskInfo {
  return {
    filename: "f.txt",
    status: "processing",
    progress: 0,
    message: "",
    created_at: "2026-01-01T00:00:00Z",
    ...partial,
  }
}

test("an active task with a file_id becomes a lock entry", () => {
  const locks = projectIngestLocks(
    "c1",
    [task({ id: "t1", status: "processing", progress: 40, message: "parsing", file_id: "f1" })],
    {},
  )
  assert.deepEqual(Object.keys(locks), ["f1"])
  assert.equal(locks["f1"].taskId, "t1")
  assert.equal(locks["f1"].progress, 40)
  assert.equal(locks["f1"].message, "parsing")
  assert.equal(locks["f1"].collectionId, "c1")
})

test("a pending task also locks", () => {
  const locks = projectIngestLocks(
    "c1",
    [task({ id: "t1", status: "pending", file_id: "f1" })],
    {},
  )
  assert.deepEqual(Object.keys(locks), ["f1"])
})

test("a completed or failed task does not lock", () => {
  const locks = projectIngestLocks(
    "c1",
    [
      task({ id: "t1", status: "completed", file_id: "f1" }),
      task({ id: "t2", status: "failed", file_id: "f2" }),
    ],
    {},
  )
  assert.deepEqual(locks, {})
})

test("a task without a file_id is skipped", () => {
  // Non-upload tasks carry no file_id and cannot be shown as a file lock.
  const locks = projectIngestLocks(
    "c1",
    [task({ id: "t1", status: "processing", file_id: null })],
    {},
  )
  assert.deepEqual(locks, {})
})

test("a terminal task clears the previous lock for that file", () => {
  const prev: Record<string, IngestLock> = {
    f1: { taskId: "t1", progress: 90, message: "embedding", collectionId: "c1" },
  }
  const locks = projectIngestLocks(
    "c1",
    [task({ id: "t1", status: "completed", file_id: "f1" })],
    prev,
  )
  assert.deepEqual(locks, {})
})

test("a vanished task clears the previous lock for that file", () => {
  const prev: Record<string, IngestLock> = {
    f1: { taskId: "t1", progress: 90, message: "embedding", collectionId: "c1" },
  }
  const locks = projectIngestLocks("c1", [], prev)
  assert.deepEqual(locks, {})
})

test("locks in another collection are preserved", () => {
  // The fetched list is authoritative only for its own collection.
  const prev: Record<string, IngestLock> = {
    f9: { taskId: "t9", progress: 10, message: "queued", collectionId: "c2" },
  }
  const locks = projectIngestLocks(
    "c1",
    [task({ id: "t1", status: "processing", file_id: "f1" })],
    prev,
  )
  assert.deepEqual(Object.keys(locks).sort(), ["f1", "f9"])
  assert.equal(locks["f9"].collectionId, "c2")
})

test("progress updates replace the entry for the same file", () => {
  const prev: Record<string, IngestLock> = {
    f1: { taskId: "t1", progress: 10, message: "queued", collectionId: "c1" },
  }
  const locks = projectIngestLocks(
    "c1",
    [task({ id: "t1", status: "processing", progress: 60, message: "embedding", file_id: "f1" })],
    prev,
  )
  assert.equal(locks["f1"].progress, 60)
  assert.equal(locks["f1"].message, "embedding")
})

test("an empty task list clears this collection's locks but keeps others", () => {
  const prev: Record<string, IngestLock> = {
    f1: { taskId: "t1", progress: 10, message: "", collectionId: "c1" },
    f9: { taskId: "t9", progress: 10, message: "", collectionId: "c2" },
  }
  const locks = projectIngestLocks("c1", [], prev)
  assert.deepEqual(Object.keys(locks), ["f9"])
})

test("multiple files in one collection all lock", () => {
  const locks = projectIngestLocks(
    "c1",
    [
      task({ id: "t1", status: "processing", file_id: "f1" }),
      task({ id: "t2", status: "pending", file_id: "f2" }),
    ],
    {},
  )
  assert.deepEqual(Object.keys(locks).sort(), ["f1", "f2"])
})
