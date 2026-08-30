/**
 * Pre-meeting brief section parsing (pure, no React).
 *
 * The synthesis prompt emits fixed English H2 tokens (## Recap / ## To chase /
 * ## Undecided / ## Attendees) so the frontend can localize section titles and
 * icons itself. Localized or unexpected headings degrade gracefully: unknown
 * headings render as their own section with the original title.
 */

export const BRIEF_SECTION_ORDER = ["recap", "chase", "undecided", "attendees"] as const
export type BriefSectionKind = (typeof BRIEF_SECTION_ORDER)[number] | "other"

export interface BriefSection {
  kind: BriefSectionKind
  /** Raw heading text as output ("Recap", or the original unknown heading). */
  token: string
  /** Trimmed body lines under the heading (bullets, "### Name" blocks kept). */
  body: string
}

const TOKEN_MAP: Record<string, BriefSectionKind> = {
  recap: "recap",
  "to chase": "chase",
  "to chase (todos)": "chase",
  undecided: "undecided",
  attendees: "attendees",
  // zh aliases in case the model localizes the heading anyway
  上集回顾: "recap",
  这次可以追的: "chase",
  可以追的: "chase",
  还没定的: "undecided",
  未决事项: "undecided",
  参会人: "attendees",
}

function normalizeHeading(raw: string): string {
  return raw
    .replace(/^#+\s*/, "")
    .replace(/^\*+|\*+$/g, "")
    .replace(/[:：]\s*$/, "")
    .trim()
}

export function parseBriefSections(md: string): BriefSection[] {
  const text = (md || "").replace(/\r\n/g, "\n").trim()
  if (!text) return []
  const lines = text.split("\n")
  const sections: BriefSection[] = []
  let current: BriefSection | null = null
  let sawHeading = false

  for (const line of lines) {
    // Section headings: exactly "## ..." or a bold-only line ("**Attendees**").
    // "### ..." stays in the body (attendee name blocks).
    const isH2 = /^##\s/.test(line)
    const isBoldOnly = /^\*\*[^*]+\*\*\s*$/.test(line)
    if (isH2 || isBoldOnly) {
      sawHeading = true
      if (current) sections.push(current)
      const heading = normalizeHeading(isH2 ? line.replace(/^##\s*/, "") : line)
      const key = heading.toLowerCase()
      current = {
        kind: TOKEN_MAP[key] ?? TOKEN_MAP[heading] ?? "other",
        token: heading || "—",
        body: "",
      }
      continue
    }
    if (current) current.body += (current.body ? "\n" : "") + line
  }
  if (current) sections.push(current)

  if (!sawHeading) {
    return [{ kind: "other", token: "", body: text }]
  }
  for (const section of sections) section.body = section.body.trim()
  return sections.filter((s) => s.body.length > 0)
}

export function orderBriefSections(sections: BriefSection[]): BriefSection[] {
  const rank = (kind: BriefSectionKind) => {
    const idx = (BRIEF_SECTION_ORDER as readonly string[]).indexOf(kind)
    return idx === -1 ? BRIEF_SECTION_ORDER.length : idx
  }
  return sections
    .map((section, index) => ({ section, index }))
    .sort((a, b) => rank(a.section.kind) - rank(b.section.kind) || a.index - b.index)
    .map((row) => row.section)
}

export interface InlineSegment {
  text: string
  bold: boolean
  italic: boolean
}

/** Parse inline markdown emphasis: **bold** and *italic* runs. */
export function parseInlineBold(text: string): InlineSegment[] {
  const out: InlineSegment[] = []
  const rest = text ?? ""
  const pattern = /\*\*([^*]+)\*\*|\*([^*]+)\*/g
  let last = 0
  let match: RegExpExecArray | null
  while ((match = pattern.exec(rest)) !== null) {
    if (match.index > last) {
      out.push({ text: rest.slice(last, match.index), bold: false, italic: false })
    }
    if (match[1] !== undefined) {
      out.push({ text: match[1], bold: true, italic: false })
    } else if (match[2] !== undefined) {
      out.push({ text: match[2], bold: false, italic: true })
    }
    last = match.index + match[0].length
  }
  if (last < rest.length) {
    out.push({ text: rest.slice(last), bold: false, italic: false })
  }
  return out.length ? out : [{ text: "", bold: false, italic: false }]
}
