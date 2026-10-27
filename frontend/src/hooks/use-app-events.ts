/**
 * Mounts the app-wide change stream and applies incoming events to the stores.
 *
 * Mounted once from `app-layout.tsx`. All decisions live in testable pure modules
 * (`event-map.ts` for echo/debounce, `event-apply.ts` for planning/execution);
 * this file only supplies the store callbacks and the React lifecycle. Design:
 * development_doc/2026-09-16-multi-device-state-sync-design.md (§10).
 */

import { useEffect } from "react"

import { getClientId } from "@/api/client-id"
import { openEventStream, type Frame } from "@/api/events"
import {
  dedupeActions,
  planActions,
  planResync,
  runActions,
  type Action,
  type ScopeActions,
} from "@/api/event-apply"
import { createDebouncer, shouldApply } from "@/api/event-map"
import { createTaskFollower } from "@/api/task-follow"
import { getTasks } from "@/api/collections"
import { bumpDomain } from "@/lib/domain-refresh"
import { useAppStore } from "@/stores/app-store"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"

/** Burst window: one user action can emit several events for one surface. */
const COALESCE_MS = 200

/**
 * How often to re-check a peer's in-flight ingest task.
 *
 * Slower than the initiating client's 1.5s poll on purpose: this only has to
 * clear a lock eventually, and a peer's tasks must not add meaningful load.
 */
const PEER_TASK_POLL_MS = 5000

export function useAppEvents(): void {
  useEffect(() => {
    const selfId = getClientId()

    // Trails a peer's in-flight ingest task so its lock clears on completion.
    // The existing fast poll only runs for the client that started the work, so
    // nothing else would ever re-check a peer's task (design §11).
    const follower = createTaskFollower({
      intervalMs: PEER_TASK_POLL_MS,
      onPoll: (collectionId) => {
        void (async () => {
          try {
            const res = await getTasks(collectionId)
            useFileMgmtStore.getState().syncIngestLocks(collectionId, res.tasks)
            const busy = res.tasks.some(
              (t) => t.status === "pending" || t.status === "processing"
            )
            // Ends the loop once the peer's task settles.
            follower.follow(collectionId, busy)
          } catch {
            // Transient failure: leave the loop running so it can recover.
            follower.follow(collectionId, true)
          }
        })()
      },
    })

    const io: ScopeActions = {
      refreshCollection: (collectionId) => {
        void useFileMgmtStore.getState().refreshLibrarySurfaces(collectionId)
      },
      refreshTimeline: () => {
        useFileMgmtStore.getState().bumpTimelineRefresh()
      },
      refreshCollections: () => {
        void useAppStore.getState().fetchCollections()
      },
      refreshSessions: () => {
        void useAppStore.getState().refreshSessionList()
      },
      invalidateSession: (sessionId) => {
        useAppStore.getState().invalidateSessionCache(sessionId)
      },
      syncIngestLocks: (collectionId) => {
        // The lock map is a projection of the server task list, so a peer's
        // task signal becomes a refetch plus a rebuild (see api/ingest-lock).
        follower.follow(collectionId, true)
      },
      refreshMeetings: () => {
        bumpDomain("meetings")
      },
      refreshMeeting: (meetingId) => {
        // Scoped to the meeting so a view can ignore peers' other meetings.
        bumpDomain(`meeting:${meetingId}`)
      },
      refreshNotes: (collectionId) => {
        bumpDomain(`notes:${collectionId}`)
      },
      refreshHotWords: () => {
        bumpDomain("hot_words")
      },
      refreshPeople: () => {
        bumpDomain("people")
      },
      refreshConfig: () => {
        // Another device changed provider/model defaults; views that cache them
        // subscribe to this domain and re-read.
        bumpDomain("config")
      },
    }

    // Accumulate planned actions for the batch, then dedupe at flush. Accumulating
    // (rather than keeping the latest event per key) is what keeps a burst with
    // two different target sessions from silently dropping one.
    let batch: Action[] = []

    const debouncer = createDebouncer({
      delayMs: COALESCE_MS,
      onFlush: () => {
        const actions = batch
        batch = []
        runActions(dedupeActions(actions), io)
      },
    })

    const handle = (frame: Frame) => {
      if (frame.kind === "resync") {
        // Replay was impossible (gap or overflow): reconcile everything visible.
        resyncAll(io)
        return
      }
      if (frame.kind !== "change") return
      if (!shouldApply(frame.event, selfId)) return
      batch.push(...planActions(frame.event.domain, frame.event.scope))
      debouncer.schedule()
    }

    const dispose = openEventStream({ onFrame: handle })
    return () => {
      dispose()
      debouncer.dispose()
      follower.stopAll()
      batch = []
    }
  }, [])
}

/**
 * Full reconcile after a gap or overflow. The plan is computed by the tested
 * pure `planResync`; this only supplies the client's current view of what is open.
 */
function resyncAll(io: ScopeActions): void {
  runActions(planResync(useAppStore.getState().activeCollection || null), io)
}
