/** Group chat chips → spoken sentences.

Chips show 1, 2, 3 in appearance order. Tokens still carry roster n
and optional sentence k: `[1]`, `[1:53]`, `[1:stt_0053]`, `([1])`.
A k may also be a sentence span — `[1:41-45]` (models compress
consecutive excerpt refs) — and resolves to the span's first sentence.
*/

import { MEETING_CITE_RE_SOURCE, parseMeetingRefGroups } from "./meeting-ref-chips.ts"

/** Hover debounce before the meeting-name tooltip opens. */
export const GROUP_CITE_HOVER_DELAY_MS = 400

export type GroupCite = {
  n: number
  meeting_id?: string
  sentence_id?: string
  ref_n?: number
  title?: string
  date?: string
}

export type DisplayedGroupCite = GroupCite & {
  displayIndex: number
  occurrence: number
  tokenRefN?: number
}

export type CiteMeetingLookup = {
  id: string
  title: string
  created_at?: string
}

/** Optional wrapping parens / extra brackets around `[n]`, `[n:k]` or `[n:k-l]`.
The span suffix stays non-capturing: consumers read capture 2 as the start k. */
export const GROUP_CITE_RE_SOURCE =
  String.raw`(?:\(|（|\[)?\[(\d+)(?::(?:stt_)?(\d+)(?:\s*[-–]\s*(?:stt_)?\d+)?)?\](?:\)|）|\])?`

export function parseGroupCiteToken(
  raw: string,
): { n: number; refN?: number } | null {
  const m = (raw || "").trim().match(new RegExp(`^${GROUP_CITE_RE_SOURCE}$`))
  if (!m) return null
  const n = parseInt(m[1], 10)
  if (!Number.isFinite(n) || n <= 0) return null
  const refN = m[2] != null && m[2] !== "" ? parseInt(m[2], 10) : undefined
  if (refN != null && (!Number.isFinite(refN) || refN <= 0)) return { n }
  return { n, refN }
}

export function paddedSentenceId(refN: number): string {
  return `stt_${String(refN).padStart(4, "0")}`
}

export function resolveGroupCite(
  cites: GroupCite[],
  n: number,
  opts?: { refN?: number; occurrence?: number },
): GroupCite | undefined {
  const list = cites.filter((c) => c.n === n)
  if (opts?.refN != null && Number.isFinite(opts.refN)) {
    const padded = paddedSentenceId(opts.refN)
    const hit = list.find(
      (c) =>
        c.ref_n === opts.refN ||
        c.sentence_id === padded ||
        c.sentence_id?.endsWith(padded),
    )
    if (hit) return hit
    return {
      n,
      meeting_id: list[0]?.meeting_id,
      sentence_id: padded,
      ref_n: opts.refN,
    }
  }
  return pickGroupCite(citesByGroupN(list), n, opts?.occurrence ?? 0)
}

export function resolveGroupCiteByRefN(
  cites: GroupCite[],
  refN: number,
): GroupCite | undefined {
  const padded = paddedSentenceId(refN)
  return cites.find(
    (c) =>
      c.ref_n === refN ||
      c.sentence_id === padded ||
      c.sentence_id?.endsWith(padded),
  )
}

export function parseGroupCites(raw: unknown): GroupCite[] {
  if (!Array.isArray(raw)) return []
  const out: GroupCite[] = []
  for (const row of raw) {
    if (!row || typeof row !== "object") continue
    const rec = row as Record<string, unknown>
    const n = Number(rec.n)
    if (!Number.isFinite(n) || n <= 0) continue
    const sid = String(rec.sentence_id || "").trim()
    const mid = String(rec.meeting_id || "").trim()
    const refN = Number(rec.ref_n)
    const title = String(rec.title || "").trim()
    const date = isoDateOnly(String(rec.date || rec.created_at || ""))
    out.push({
      n,
      meeting_id: mid || undefined,
      sentence_id: sid || undefined,
      ref_n: Number.isFinite(refN) && refN > 0 ? refN : undefined,
      title: title || undefined,
      date: date || undefined,
    })
  }
  return out
}

export function citesByGroupN(cites: GroupCite[]): Map<number, GroupCite[]> {
  const m = new Map<number, GroupCite[]>()
  for (const c of cites) {
    const list = m.get(c.n) || []
    list.push(c)
    m.set(c.n, list)
  }
  return m
}

export function pickGroupCite(
  byN: Map<number, GroupCite[]>,
  n: number,
  occurrence: number,
): GroupCite | undefined {
  const list = byN.get(n) || []
  if (list.length === 0) return undefined
  return list[occurrence] ?? list[list.length - 1]
}

/** Stamp chip index while rendering. Must not run on click. */
export function nextGroupCiteOccurrence(occ: Map<number, number>, n: number): number {
  const i = occ.get(n) ?? 0
  occ.set(n, i + 1)
  return i
}

export function mergeGroupCites(
  prev: GroupCite[] | undefined,
  extra: GroupCite[],
): GroupCite[] {
  const out = [...(prev || [])]
  const seen = new Set(
    out.filter((c) => c.sentence_id).map((c) => `${c.n}:${c.sentence_id}`),
  )
  for (const c of extra) {
    const key = c.sentence_id ? `${c.n}:${c.sentence_id}` : ""
    if (key && seen.has(key)) continue
    if (key) seen.add(key)
    out.push(c)
  }
  return out
}

export function groupCitesFromToolTrace(
  meta: Record<string, unknown> | null | undefined,
): GroupCite[] {
  const trace = meta?.tool_trace
  if (!Array.isArray(trace)) return []
  let out: GroupCite[] = []
  for (const row of trace) {
    if (!row || typeof row !== "object") continue
    const cites = parseGroupCites((row as { cites?: unknown }).cites)
    if (cites.length) out = mergeGroupCites(out, cites)
  }
  return out
}

export function parseCitesFromToolBody(content: string | undefined | null): GroupCite[] {
  const raw = (content || "").trim()
  if (!raw.startsWith("{")) return []
  try {
    return parseGroupCites(JSON.parse(raw)?.cites)
  } catch {
    return []
  }
}

function isoDateOnly(raw?: string): string {
  const s = (raw || "").trim()
  if (!s) return ""
  const m = s.match(/^(\d{4}-\d{2}-\d{2})/)
  if (m) return m[1]
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return ""
  return d.toISOString().slice(0, 10)
}

export function formatGroupCiteDate(date?: string): string {
  const raw = isoDateOnly(date)
  if (!raw) return ""
  const d = new Date(`${raw}T00:00:00`)
  if (Number.isNaN(d.getTime())) return raw
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
}

export function enrichGroupCites(
  cites: GroupCite[],
  meetings: CiteMeetingLookup[] | undefined,
): GroupCite[] {
  if (!meetings?.length) return cites
  const byId = new Map(meetings.map((row) => [row.id, row]))
  return cites.map((c) => {
    if (!c.meeting_id) return c
    const row = byId.get(c.meeting_id)
    if (!row) return c
    return {
      ...c,
      title: c.title || row.title,
      date: c.date || isoDateOnly(row.created_at),
    }
  })
}

type CiteTokenHit = {
  start: number
  end: number
  kind: "group" | "ref"
  n?: number
  refN?: number
  inner?: string
}

function collectCiteTokens(content: string): CiteTokenHit[] {
  const hits: CiteTokenHit[] = []
  const groupRe = new RegExp(GROUP_CITE_RE_SOURCE, "g")
  let m: RegExpExecArray | null
  while ((m = groupRe.exec(content)) !== null) {
    hits.push({
      start: m.index,
      end: m.index + m[0].length,
      kind: "group",
      n: parseInt(m[1], 10),
      refN: m[2] ? parseInt(m[2], 10) : undefined,
    })
  }
  const refRe = new RegExp(MEETING_CITE_RE_SOURCE, "gi")
  while ((m = refRe.exec(content)) !== null) {
    hits.push({
      start: m.index,
      end: m.index + m[0].length,
      kind: "ref",
      inner: m[1],
    })
  }
  hits.sort((a, b) => a.start - b.start || a.end - b.end)
  const out: CiteTokenHit[] = []
  let lastEnd = -1
  for (const hit of hits) {
    if (hit.start < lastEnd) continue
    out.push(hit)
    lastEnd = hit.end
  }
  return out
}

/** Live chips in document order, numbered 1…N for display. */
export function displayedGroupCites(
  content: string,
  cites: GroupCite[],
): DisplayedGroupCite[] {
  const occ = new Map<number, number>()
  const out: DisplayedGroupCite[] = []
  for (const tok of collectCiteTokens(content || "")) {
    if (tok.kind === "ref") {
      for (const g of parseMeetingRefGroups(tok.inner || "")) {
        const refN = /^\d+$/.test(g.label.trim())
          ? parseInt(g.label.trim(), 10)
          : NaN
        if (!Number.isFinite(refN)) continue
        const mapped = resolveGroupCiteByRefN(cites, refN)
        if (!mapped?.sentence_id || mapped.n == null) continue
        out.push({
          ...mapped,
          displayIndex: out.length + 1,
          occurrence: 0,
          tokenRefN: mapped.ref_n ?? refN,
        })
      }
      continue
    }
    const n = tok.n
    if (n == null || !Number.isFinite(n) || n <= 0) continue
    const refN = tok.refN != null && Number.isFinite(tok.refN) ? tok.refN : undefined
    const occurrence = refN ? 0 : nextGroupCiteOccurrence(occ, n)
    const live = resolveGroupCite(cites, n, { refN, occurrence })
    if (!live?.sentence_id) continue
    out.push({
      ...live,
      displayIndex: out.length + 1,
      occurrence,
      tokenRefN: refN,
    })
  }
  return out
}
