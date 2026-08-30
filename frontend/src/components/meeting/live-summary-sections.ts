import type { LiveSummaryEntry, TranscriptSegment } from "@/api/client"

/** Panel section order: unresolved questions first (steering aid), durable
 * facts last — the panel is scanned top-down during a live meeting. */
export const SECTION_ORDER = ["question", "decision", "action", "point"] as const
export type SectionKind = (typeof SECTION_ORDER)[number]

export interface GroupedEntries {
  point: LiveSummaryEntry[]
  decision: LiveSummaryEntry[]
  question: LiveSummaryEntry[]
  action: LiveSummaryEntry[]
}

function entryIdNum(id: string): number {
  const n = Number(id.replace(/^e/, ""))
  return Number.isFinite(n) ? n : 0
}

/** Bucket summary entries into the four panel sections, newest first within
 * each section (the panel is scanned live — the freshest items belong on
 * top; same-timestamp ties from one round fall back to the newer id). */
export function groupEntriesByKind(entries: LiveSummaryEntry[]): GroupedEntries {
  const g: GroupedEntries = { point: [], decision: [], question: [], action: [] }
  for (const e of entries) {
    if (e.kind in g) g[e.kind as keyof GroupedEntries].push(e)
  }
  for (const key of Object.keys(g) as (keyof GroupedEntries)[]) {
    g[key].sort((a, b) => b.t - a.t || entryIdNum(b.id) - entryIdNum(a.id))
  }
  return g
}

/** Transcript lines the summary has NOT absorbed yet (end > tail_from_t),
 * capped to the last `limit` lines — the "live tail" strip under the panel. */
export function tailSegments(
  segments: TranscriptSegment[],
  tailFromT: number,
  limit = 3,
): TranscriptSegment[] {
  const tail = segments.filter((s) => s.end > tailFromT)
  return tail.slice(Math.max(0, tail.length - limit))
}

export type RelativeAgeKey = "justNow" | "minutesAgo" | "hoursAgo"

/** Coarse age for the "updated" meta line; never throws on bad input. */
export function relativeAge(updatedAtIso: string, nowMs: number): {
  key: RelativeAgeKey
  n: number
} {
  const t = Date.parse(updatedAtIso)
  if (!Number.isFinite(t)) return { key: "justNow", n: 0 }
  const seconds = Math.max(0, (nowMs - t) / 1000)
  if (seconds < 60) return { key: "justNow", n: 0 }
  if (seconds < 3600) return { key: "minutesAgo", n: Math.floor(seconds / 60) }
  return { key: "hoursAgo", n: Math.floor(seconds / 3600) }
}

/** Ids added or text-amended between two snapshots — drives the highlight
 * animations so users can see what just changed. */
export function diffEntryStatus(
  prev: LiveSummaryEntry[],
  next: LiveSummaryEntry[],
): { added: Set<string>; amended: Set<string> } {
  const prevMap = new Map(prev.map((e) => [e.id, e.text]))
  const added = new Set<string>()
  const amended = new Set<string>()
  for (const e of next) {
    const before = prevMap.get(e.id)
    if (before === undefined) added.add(e.id)
    else if (before !== e.text) amended.add(e.id)
  }
  return { added, amended }
}
