/**
 * Stable client id for event-stream echo suppression.
 *
 * Scope matters: the id must be **per browsing context** (per tab), not per
 * origin. Echo suppression means "I already applied this change locally", which
 * is true for the tab that made it and false for another tab of the same browser
 * — and localStorage is shared across tabs, so storing the id there made two
 * tabs of one browser invisible to each other (a second window is exactly the
 * two-client case this whole feature is for).
 *
 * sessionStorage is per tab and survives reloads, which is the behaviour wanted.
 * An id is kept in memory for the session too, so a blocked sessionStorage still
 * yields a stable id rather than a new one per request.
 */

import { newId } from "../lib/uuid.ts"

const STORAGE_KEY = "sinkduce.clientId"

/** Per-tab fallback when sessionStorage is unavailable (e.g. blocked). */
let sessionFallback: string | null = null

function generateId(): string {
  return newId()
}

/** sessionStorage when present, else undefined (never localStorage — see above). */
function store(): Storage | undefined {
  try {
    return globalThis.sessionStorage ?? undefined
  } catch {
    return undefined
  }
}

function readStored(): string | null {
  try {
    const v = store()?.getItem(STORAGE_KEY)
    return v && v.trim() ? v : null
  } catch {
    return null
  }
}

function persist(id: string): void {
  try {
    store()?.setItem(STORAGE_KEY, id)
  } catch {
    // Best-effort only: private mode / quota must not break the app.
  }
}

export function getClientId(): string {
  const stored = readStored()
  if (stored) return stored
  if (sessionFallback) return sessionFallback
  const id = generateId()
  persist(id)
  // If the write did not take effect, keep the id for this tab so the client
  // does not present a new identity on every request.
  if (!readStored()) sessionFallback = id
  return id
}

