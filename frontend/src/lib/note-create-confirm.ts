/**
 * Promise-based gate for note-create HITL during SSE streams.
 *
 * Reuses the web-search composer anchor (on-screen Chat / Quick Chat session)
 * so switching surfaces hides the card without a second host.
 */

import {
  getWebSearchConfirmAnchorSnapshot,
  subscribeWebSearchConfirmAnchor,
} from "./web-search-confirm.ts"

export type NoteCreateConfirmState = {
  open: boolean
  title: string
  collectionId: string
  collectionName: string
  preview: string
  confirmId: string
  sessionId: string
}

/** Decision: `[approved, overrideCollection?]` — override set only when the
 *  user re-picked a target on the card's dropdown. */
export type NoteCreateDecision = [approved: boolean, overrideCollection?: string]

type Resolver = (decision: NoteCreateDecision) => void

const CLOSED_STATE: NoteCreateConfirmState = {
  open: false,
  title: "",
  collectionId: "",
  collectionName: "",
  preview: "",
  confirmId: "",
  sessionId: "",
}

let pending: {
  title: string
  collectionId: string
  collectionName: string
  preview: string
  confirmId: string
  sessionId: string
  resolve: Resolver
} | null = null
let snapshot: NoteCreateConfirmState = CLOSED_STATE
const listeners = new Set<() => void>()
let pendingTimer: ReturnType<typeof setTimeout> | 0 = 0

function clearPendingTimer() {
  if (pendingTimer) {
    clearTimeout(pendingTimer)
    pendingTimer = 0
  }
}

function notify() {
  snapshot = pending
    ? {
        open: true,
        title: pending.title,
        collectionId: pending.collectionId,
        collectionName: pending.collectionName,
        preview: pending.preview,
        confirmId: pending.confirmId,
        sessionId: pending.sessionId,
      }
    : CLOSED_STATE
  listeners.forEach((l) => l())
}

export function getNoteCreateConfirmState(): NoteCreateConfirmState {
  return snapshot
}

export function subscribeNoteCreateConfirm(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function shouldShowNoteCreateConfirm(): boolean {
  if (!snapshot.open) return false
  const onScreenSid = getWebSearchConfirmAnchorSnapshot().sessionId
  const pendingSid = snapshot.sessionId
  // Hide only when another Chat / Quick Chat session is clearly on screen.
  // If the composer anchor is missing or session id is empty, still show —
  // otherwise the stream waits on HITL with no card (looks frozen).
  if (pendingSid && onScreenSid && pendingSid !== onScreenSid) return false
  return true
}

export { subscribeWebSearchConfirmAnchor }

export function promptNoteCreateConfirm(
  confirmId: string,
  title: string,
  sessionId?: string | null,
  collectionName?: string | null,
  preview?: string | null,
  collectionId?: string | null,
): Promise<NoteCreateDecision> {
  const sid = (sessionId || "").trim()

  if (pending) {
    const prev = pending
    pending = null
    clearPendingTimer()
    notify()
    prev.resolve([false])
  }

  return new Promise<NoteCreateDecision>((resolve) => {
    pending = {
      title: title || "",
      collectionId: (collectionId || "").trim(),
      collectionName: (collectionName || "").trim(),
      preview: (preview || "").trim(),
      confirmId,
      sessionId: sid,
      resolve,
    }
    notify()
    clearPendingTimer()
    pendingTimer = setTimeout(() => {
      pendingTimer = 0
      if (pending?.confirmId === confirmId) {
        console.warn(
          "[note-create-confirm] timed out waiting for user — auto-decline",
          confirmId,
        )
        answerNoteCreateConfirm(false)
      }
    }, 120_000)
  })
}

export function answerNoteCreateConfirm(
  approved: boolean,
  overrideCollection?: string,
) {
  if (!pending) return
  const { resolve } = pending
  pending = null
  clearPendingTimer()
  notify()
  resolve([approved, overrideCollection])
}
