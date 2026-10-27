/**
 * Promise-based gate for todo-delete HITL during SSE streams.
 *
 * Reuses the web-search composer anchor (on-screen Chat / Quick Chat session)
 * so switching surfaces hides the card without a second host.
 */

import {
  getWebSearchConfirmAnchorSnapshot,
  subscribeWebSearchConfirmAnchor,
} from "./web-search-confirm.ts"

export type TodoDeleteConfirmState = {
  open: boolean
  title: string
  collectionName: string
  confirmId: string
  sessionId: string
}

type Resolver = (approved: boolean) => void

const CLOSED_STATE: TodoDeleteConfirmState = {
  open: false,
  title: "",
  collectionName: "",
  confirmId: "",
  sessionId: "",
}

let pending: {
  title: string
  collectionName: string
  confirmId: string
  sessionId: string
  resolve: Resolver
} | null = null
let snapshot: TodoDeleteConfirmState = CLOSED_STATE
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
        collectionName: pending.collectionName,
        confirmId: pending.confirmId,
        sessionId: pending.sessionId,
      }
    : CLOSED_STATE
  listeners.forEach((l) => l())
}

export function getTodoDeleteConfirmState(): TodoDeleteConfirmState {
  return snapshot
}

export function subscribeTodoDeleteConfirm(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function shouldShowTodoDeleteConfirm(): boolean {
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

export function promptTodoDeleteConfirm(
  confirmId: string,
  title: string,
  sessionId?: string | null,
  collectionName?: string | null,
): Promise<boolean> {
  const sid = (sessionId || "").trim()

  if (pending) {
    const prev = pending
    pending = null
    clearPendingTimer()
    notify()
    prev.resolve(false)
  }

  return new Promise<boolean>((resolve) => {
    pending = {
      title: title || "",
      collectionName: (collectionName || "").trim(),
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
          "[todo-delete-confirm] timed out waiting for user — auto-decline",
          confirmId,
        )
        answerTodoDeleteConfirm(false)
      }
    }, 120_000)
  })
}

export function answerTodoDeleteConfirm(approved: boolean) {
  if (!pending) return
  const { resolve } = pending
  pending = null
  clearPendingTimer()
  notify()
  resolve(approved)
}
