/**
 * The Ingesting lock, projected from server task state.
 *
 * Previously the lock was a purely local optimistic map written only by the
 * device that started an upload, so a peer could never draw it. It is now a
 * projection of the backend task list: every device derives the same locks from
 * the same source. Design:
 * development_doc/2026-09-16-multi-device-state-sync-design.md (§9).
 */

import type { TaskInfo } from "./collections.ts"

export interface IngestLock {
  taskId: string
  progress: number
  message: string
  /** Which collection this lock belongs to, so a scoped refresh never wipes others. */
  collectionId: string
}

const ACTIVE_STATUSES = new Set(["pending", "processing"])

/**
 * Rebuild the lock map for one collection from that collection's task list.
 *
 * `prev` entries for other collections are preserved: a scoped task list is
 * authoritative only for its own collection, so replacing the whole map would
 * drop the lock of a file ingesting elsewhere.
 *
 * A task that is terminal or absent from the list removes any previous lock for
 * its file, which is what clears the spinner when ingestion finishes.
 */
export function projectIngestLocks(
  collectionId: string,
  tasks: TaskInfo[],
  prev: Record<string, IngestLock>,
): Record<string, IngestLock> {
  const next: Record<string, IngestLock> = {}
  // Keep other collections' locks untouched.
  for (const [fileId, lock] of Object.entries(prev)) {
    if (lock.collectionId !== collectionId) next[fileId] = lock
  }
  for (const task of tasks) {
    const fileId = task.file_id
    if (!fileId) continue
    if (!ACTIVE_STATUSES.has(task.status)) continue
    next[fileId] = {
      taskId: task.id,
      progress: task.progress ?? 0,
      message: task.message || "",
      collectionId,
    }
  }
  return next
}
