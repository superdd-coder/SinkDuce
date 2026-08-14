/** Shared helpers for the file detail dialog (no React UI). */

import type { FileVersion, Message } from "@/types/file-mgmt"

// ── summary generation markers (module-level, same pattern as legacy dialog) ──

export const _generating = new Map<string, number>()

export function _genKey(collection: string, source: string) {
  return `${collection}::${source}`
}

export function _markGenerating(key: string) {
  const now = Date.now()
  _generating.set(key, now)
  try {
    localStorage.setItem(`wk:gen:${key}`, String(now))
  } catch {
    /* ignore */
  }
}

export function _unmarkGenerating(key: string) {
  _generating.delete(key)
  try {
    localStorage.removeItem(`wk:gen:${key}`)
  } catch {
    /* ignore */
  }
}

export function _isMarked(key: string): boolean {
  if (_generating.has(key)) return true
  try {
    const raw = localStorage.getItem(`wk:gen:${key}`)
    if (raw) {
      const ts = Number(raw)
      if (Date.now() - ts < 300_000) {
        _generating.set(key, ts)
        return true
      }
      localStorage.removeItem(`wk:gen:${key}`)
    }
  } catch {
    /* ignore */
  }
  return false
}

export function fileSource(fileId: string) {
  return `__file__:${fileId}`
}

/** Extract file_id from document source when it is a managed file. */
export function parseFileIdFromSource(
  source: string | null | undefined
): string | null {
  if (!source) return null
  const s = source.trim()
  if (!s) return null
  if (s.startsWith("__file__:")) {
    const id = s.slice("__file__:".length).trim()
    return id || null
  }
  // Bare 32-char hex UUID (Info panel sometimes passes file_id without prefix)
  if (/^[a-f0-9]{32}$/i.test(s)) return s.toLowerCase()
  return null
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

// ── timeline merge ──

export function isVersionUpdateMessage(m: Message): boolean {
  return (m.owner_type || "").toLowerCase() === "system_version"
}

export type TimelineItem =
  | {
      /** Legacy version row with no linked system_version message */
      kind: "version"
      id: string
      created_at: string
      version: FileVersion
    }
  | {
      kind: "message"
      id: string
      created_at: string
      message: Message
      /** True for file version notes (owner_type=system_version) */
      isVersionUpdate: boolean
      version?: FileVersion
    }

export function buildTimeline(
  versions: FileVersion[],
  messages: Message[],
  filter: "all" | "versions"
): TimelineItem[] {
  const versionMsgs = messages.filter(isVersionUpdateMessage)
  const userMsgs = messages.filter((m) => !isVersionUpdateMessage(m))

  // Pair system_version messages with file_versions:
  // 1) same created_at (upload writes both with one timestamp)
  // 2) same commit_message / body
  // 3) chronological index fallback
  const versAsc = [...versions].sort((a, b) => a.version_no - b.version_no)
  const msgsAsc = [...versionMsgs].sort((a, b) => {
    const ta = new Date(a.created_at).getTime() || 0
    const tb = new Date(b.created_at).getTime() || 0
    return ta - tb
  })
  const usedVersionIds = new Set<string>()

  const pickVersionForMessage = (m: Message, index: number): FileVersion | undefined => {
    const byTime = versAsc.find(
      (v) =>
        !usedVersionIds.has(v.version_id) &&
        v.created_at &&
        m.created_at &&
        v.created_at === m.created_at
    )
    if (byTime) return byTime
    const body = (m.body || "").trim()
    if (body) {
      const byMsg = versAsc.find(
        (v) =>
          !usedVersionIds.has(v.version_id) &&
          (v.commit_message || "").trim() === body
      )
      if (byMsg) return byMsg
    }
    const byIndex = versAsc[index]
    if (byIndex && !usedVersionIds.has(byIndex.version_id)) return byIndex
    return versAsc.find((v) => !usedVersionIds.has(v.version_id))
  }

  const versionUpdateItems: TimelineItem[] = msgsAsc.map((m, i) => {
    const v = pickVersionForMessage(m, i)
    if (v) usedVersionIds.add(v.version_id)
    return {
      kind: "message" as const,
      id: `msg-${m.message_id}`,
      created_at: m.created_at,
      message: m,
      isVersionUpdate: true,
      version: v,
    }
  })

  // Versions without a system_version message (older data) — display-only
  const orphanVersions: TimelineItem[] = versAsc
    .filter((v) => !usedVersionIds.has(v.version_id))
    .map((v) => ({
      kind: "version" as const,
      id: `ver-${v.version_id}`,
      created_at: v.created_at,
      version: v,
    }))

  const userItems: TimelineItem[] = userMsgs.map((m) => ({
    kind: "message" as const,
    id: `msg-${m.message_id}`,
    created_at: m.created_at,
    message: m,
    isVersionUpdate: false,
  }))

  const all =
    filter === "versions"
      ? [...versionUpdateItems, ...orphanVersions]
      : [...versionUpdateItems, ...orphanVersions, ...userItems]

  return all.sort((a, b) => {
    const ta = new Date(a.created_at).getTime() || 0
    const tb = new Date(b.created_at).getTime() || 0
    return tb - ta
  })
}

export function versionUpdateBody(body: string | null | undefined): string {
  const t = (body || "").trim()
  return t || "version update"
}
