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

export interface BilingualTailRow {
  key: string
  text: string
  kind: "source" | "translation"
  partial: boolean
}

/** Bilingual tail rows in BLOCK layout: the newest sources on top (≤2
 * lines), the newest translations below (≤2 lines), oldest first within
 * each block. Units are a completed segment or the in-flight partial —
 * each contributes at most one line per kind, so late translations can
 * never crowd source lines out. When nothing has a translation the tail
 * widens to 4 source lines instead. */
export function tailBilingualRows(
  segments: TranscriptSegment[],
  tailFromT: number,
  partial: string,
  partialTranslation: string | undefined,
): BilingualTailRow[] {
  const units: { key: string; source: string; translation?: string; partial: boolean }[] = []
  tailSegments(segments, tailFromT, 5).forEach((s, i) => {
    units.push({
      key: `${s.start}-${i}`,
      source: s.text,
      translation: s.translation || undefined,
      partial: false,
    })
  })
  if (partial) {
    units.push({
      key: "partial",
      source: partial,
      translation: partialTranslation || undefined,
      partial: true,
    })
  }
  const hasTranslation = units.some((u) => !!u.translation)
  const keep = hasTranslation ? 2 : 4
  const kept = units.slice(Math.max(0, units.length - keep))
  const sourceRows: BilingualTailRow[] = []
  const translationRows: BilingualTailRow[] = []
  for (const u of kept) {
    sourceRows.push({ key: `${u.key}-s`, text: u.source, kind: "source", partial: u.partial })
    if (u.translation) {
      translationRows.push({
        key: `${u.key}-t`,
        text: u.translation,
        kind: "translation",
        partial: u.partial,
      })
    }
  }
  return [...sourceRows, ...translationRows]
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
