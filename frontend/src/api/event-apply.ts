/**
 * Pure planning of the refetch actions a change event requires.
 *
 * Split into plan (pure, returns descriptors) and run (invokes store callbacks)
 * so a batch of events can be deduplicated before anything executes: two events
 * on different domains often require the *same* refetch (a file change and a
 * timeline change both refresh one collection's surfaces), and executing both
 * would double the work for no benefit.
 *
 * Design: development_doc/2026-09-16-multi-device-state-sync-design.md (§10).
 */

/** A refetch to perform. Every one maps onto an existing store action. */
export type Action =
  | { kind: "refreshCollection"; collectionId: string }
  | { kind: "refreshTimeline" }
  | { kind: "refreshCollections" }
  | { kind: "refreshSessions" }
  | { kind: "invalidateSession"; sessionId: string }
  | { kind: "syncIngestLocks"; collectionId: string }
  | { kind: "refreshMeetings" }
  | { kind: "refreshMeeting"; meetingId: string }
  | { kind: "refreshNotes"; collectionId: string }
  | { kind: "refreshHotWords" }
  | { kind: "refreshPeople" }
  | { kind: "refreshConfig" }

/**
 * The store callbacks an action may need. Supplied by the hook so this module
 * stays free of React and store imports.
 */
export interface ScopeActions {
  refreshCollection: (collectionId: string) => void
  refreshTimeline: () => void
  refreshCollections: () => void
  refreshSessions: () => void
  invalidateSession: (sessionId: string) => void
  syncIngestLocks: (collectionId: string) => void
  refreshMeetings: () => void
  refreshMeeting: (meetingId: string) => void
  refreshNotes: (collectionId: string) => void
  refreshHotWords: () => void
  refreshPeople: () => void
  refreshConfig: () => void
}

/**
 * Decide what a single event requires.
 *
 * A missing scope id narrows the plan rather than widening it: an
 * unidentifiable change must not trigger a global refetch. Domains not yet wired
 * (tasks, meetings, notes, hot_words, people, config) plan nothing rather than
 * guess.
 */
export function planActions(
  domain: string,
  scope: Record<string, string | null | undefined>,
): Action[] {
  const collectionId = scope.collection_id ?? ""

  switch (domain) {
    case "files":
      return collectionId ? [{ kind: "refreshCollection", collectionId }] : []

    case "timeline":
      return collectionId
        ? [{ kind: "refreshCollection", collectionId }]
        : [{ kind: "refreshTimeline" }]

    case "collections":
      return [{ kind: "refreshCollections" }]

    case "sessions": {
      const sessionId = scope.session_id ?? ""
      const actions: Action[] = [{ kind: "refreshSessions" }]
      if (sessionId) actions.push({ kind: "invalidateSession", sessionId })
      return actions
    }

    case "tasks":
      // A peer queued or changed an ingest task: refetch that collection's task
      // list and rebuild the locks from it, so their upload shows a spinner here.
      return collectionId ? [{ kind: "syncIngestLocks", collectionId }] : []

    case "meetings": {
      // Refresh the list, and the detail too when it is the open meeting. Covers
      // CRUD and the "work started" signals: re-reading the detail is what lets a
      // peer trail background generation to completion (see design §11).
      const meetingId = scope.meeting_id ?? ""
      const actions: Action[] = [{ kind: "refreshMeetings" }]
      if (meetingId) actions.push({ kind: "refreshMeeting", meetingId })
      return actions
    }

    case "notes":
      // note.created/updated/deleted and the note.*_started signals all resolve
      // to "re-read this collection's notes".
      return collectionId ? [{ kind: "refreshNotes", collectionId }] : []

    case "hot_words":
      return [{ kind: "refreshHotWords" }]

    case "people":
      return [{ kind: "refreshPeople" }]

    case "config":
      // Provider/model defaults changed on another device.
      return [{ kind: "refreshConfig" }]

    default:
      return []
  }
}

/**
 * Actions for a full reconcile after a gap or overflow.
 *
 * Deliberately broad: replay was impossible, so re-reading every visible surface
 * is cheaper and safer than guessing which one changed. `activeCollectionId` is
 * the client's own notion of what is on screen.
 */
export function planResync(activeCollectionId: string | null): Action[] {
  const actions: Action[] = [
    { kind: "refreshCollections" },
    { kind: "refreshTimeline" },
    { kind: "refreshMeetings" },
    { kind: "refreshSessions" },
    { kind: "refreshHotWords" },
    { kind: "refreshPeople" },
  ]
  if (activeCollectionId) {
    actions.push(
      { kind: "refreshCollection", collectionId: activeCollectionId },
      // The Ingesting lock is derived from server task state, so a resync must
      // rebuild it as well; otherwise a lock predating the gap would never be
      // re-established (or cleared once its task finished during the gap).
      { kind: "syncIngestLocks", collectionId: activeCollectionId },
    )
  }
  return actions
}

/** Stable identity for an action, used to deduplicate a batch. */
export function actionKey(action: Action): string {
  switch (action.kind) {
    case "refreshCollection":
      return `refreshCollection:${action.collectionId}`
    case "invalidateSession":
      return `invalidateSession:${action.sessionId}`
    case "syncIngestLocks":
      return `syncIngestLocks:${action.collectionId}`
    case "refreshMeeting":
      return `refreshMeeting:${action.meetingId}`
    case "refreshNotes":
      return `refreshNotes:${action.collectionId}`
    default:
      return action.kind
  }
}

/** Collapse a batch to its distinct actions, preserving first-seen order. */
export function dedupeActions(actions: Action[]): Action[] {
  const seen = new Set<string>()
  const out: Action[] = []
  for (const action of actions) {
    const key = actionKey(action)
    if (seen.has(key)) continue
    seen.add(key)
    out.push(action)
  }
  return out
}

/** Execute planned actions against the real store callbacks. */
export function runActions(actions: Action[], io: ScopeActions): void {
  for (const action of actions) {
    switch (action.kind) {
      case "refreshCollection":
        io.refreshCollection(action.collectionId)
        break
      case "refreshTimeline":
        io.refreshTimeline()
        break
      case "refreshCollections":
        io.refreshCollections()
        break
      case "refreshSessions":
        io.refreshSessions()
        break
      case "invalidateSession":
        io.invalidateSession(action.sessionId)
        break
      case "syncIngestLocks":
        io.syncIngestLocks(action.collectionId)
        break
      case "refreshMeetings":
        io.refreshMeetings()
        break
      case "refreshMeeting":
        io.refreshMeeting(action.meetingId)
        break
      case "refreshNotes":
        io.refreshNotes(action.collectionId)
        break
      case "refreshHotWords":
        io.refreshHotWords()
        break
      case "refreshPeople":
        io.refreshPeople()
        break
      case "refreshConfig":
        io.refreshConfig()
        break
    }
  }
}
