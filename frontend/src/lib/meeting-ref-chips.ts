/**
 * Group meeting sentence citations into Summary-style chips.
 *
 * LLM output uses [ref:N], [ref:67,70], or ranges [ref:67-70].
 * parseMeetingRefGroups takes the inner text after the ref: prefix.
 * Summary expands ranges on persist; Quick Chat expands here so
 * [ref:1-5] is one chip, not buttons 1 and 5.
 */

export type MeetingRefChip = {
  label: string
  ids: string[]
}

/** Same cap as backend `_clean_refs` (`e - s > 50`). */
const MAX_RANGE_SPAN = 50

function toStt(n: number): string {
  return `stt_${String(n).padStart(4, "0")}`
}

function parseRange(token: string): [number, number] | null {
  const m = token
    .trim()
    .match(/^(?:stt_)?(\d+)\s*[-–—]\s*(?:stt_)?(\d+)$/i)
  if (!m) return null
  let a = parseInt(m[1], 10)
  let b = parseInt(m[2], 10)
  if (Number.isNaN(a) || Number.isNaN(b)) return null
  if (b < a) [a, b] = [b, a]
  return [a, b]
}

function parseNum(token: string): number | null {
  const m = token.trim().match(/^(?:stt_)?(\d+)$/i)
  if (!m) return null
  const n = parseInt(m[1], 10)
  return Number.isNaN(n) ? null : n
}

function groupConsecutive(nums: number[]): MeetingRefChip[] {
  if (nums.length === 0) return []
  const uniq: number[] = []
  for (const n of [...nums].sort((a, b) => a - b)) {
    if (uniq.length === 0 || uniq[uniq.length - 1] !== n) uniq.push(n)
  }
  const groups: MeetingRefChip[] = []
  let i = 0
  while (i < uniq.length) {
    let j = i
    while (j + 1 < uniq.length && uniq[j + 1] === uniq[j] + 1) j++
    const start = uniq[i]
    const end = uniq[j]
    groups.push({
      label: start === end ? String(start) : `${start}-${end}`,
      ids: uniq.slice(i, j + 1).map(toStt),
    })
    i = j + 1
  }
  return groups
}

/** Parse citation inner text (`1-5`, `47, 78-86`, `stt_0001-stt_0005`). */
export function parseMeetingRefGroups(raw: string): MeetingRefChip[] {
  const tokens = (raw || "")
    .split(/[,，、;；]+/)
    .map((t) => t.trim())
    .filter(Boolean)

  const chips: MeetingRefChip[] = []
  let pending: number[] = []

  const flush = () => {
    if (pending.length === 0) return
    chips.push(...groupConsecutive(pending))
    pending = []
  }

  for (const token of tokens) {
    const range = parseRange(token)
    if (range) {
      const [a, b] = range
      if (b - a > MAX_RANGE_SPAN) {
        flush()
        chips.push({ label: `${a}-${b}`, ids: [toStt(a), toStt(b)] })
        continue
      }
      for (let n = a; n <= b; n++) pending.push(n)
      continue
    }
    const n = parseNum(token)
    if (n != null) pending.push(n)
  }
  flush()
  return chips
}
