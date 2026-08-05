/**
 * Per-session Web-toggle preference (Chat / Quick Chat).
 * Stored in localStorage as { [sessionId]: boolean }. Default OFF.
 */

const STORAGE_KEY = "chat_web_search_by_session"
/** Preference while no session is selected (e.g. after delete, before first send). */
export const WEB_SEARCH_DRAFT_KEY = "__draft__"

function loadMap(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, boolean>
    }
  } catch {
    /* ignore */
  }
  return {}
}

function saveMap(map: Record<string, boolean>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map))
  } catch {
    /* ignore */
  }
}

/** Read Web toggle for a session (or draft). Missing → false. */
export function getSessionWebSearch(sessionId: string | null | undefined): boolean {
  const key = sessionId || WEB_SEARCH_DRAFT_KEY
  const map = loadMap()
  return map[key] === true
}

/** Persist Web toggle for a session (or draft when sessionId is null). */
export function setSessionWebSearch(
  sessionId: string | null | undefined,
  enabled: boolean,
): void {
  const key = sessionId || WEB_SEARCH_DRAFT_KEY
  const map = loadMap()
  map[key] = !!enabled
  saveMap(map)
}

/**
 * Load preference when switching sessions.
 * If the new session has no entry yet but a draft exists, migrate draft → session.
 */
export function loadWebSearchForSession(sessionId: string | null | undefined): boolean {
  const map = loadMap()
  if (sessionId) {
    if (Object.prototype.hasOwnProperty.call(map, sessionId)) {
      return map[sessionId] === true
    }
    // New session: adopt draft if user toggled Web before first message
    if (Object.prototype.hasOwnProperty.call(map, WEB_SEARCH_DRAFT_KEY)) {
      const draft = map[WEB_SEARCH_DRAFT_KEY] === true
      map[sessionId] = draft
      delete map[WEB_SEARCH_DRAFT_KEY]
      saveMap(map)
      return draft
    }
    return false
  }
  return map[WEB_SEARCH_DRAFT_KEY] === true
}
