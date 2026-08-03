/**
 * Shared Meeting Summary markdown renderer — same visual as the Meeting page:
 * speaker names resolved for display only, [stt_…] as ref chips, [priority:…] badges.
 * Does NOT mutate source markdown on disk.
 */
import type { ReactNode } from "react"

/** Fix Tiptap-style markdown quirks: extra spaces inside bold/italic syntax. */
export function normalizeMd(md: string): string {
  return md
    .replace(/\*\*\s+([^*]+?)\s*\*\*/g, "**$1**")
    .replace(/(?<!\*)\*(?!\*)\s+([^*]+?)\s*(?<!\*)\*(?!\*)/g, "*$1*")
}

/** Render inline markdown: **bold**, *italic*, `code`, [stt_XXXX] refs, [priority: X] badges */
export function renderSummaryInline(
  text: string,
  onRefClick: (id: string) => void,
): ReactNode[] {
  const parts: ReactNode[] = []
  const regex =
    /(\*\*(.+?)\*\*)|(\*(.+?)\*)|(`(.+?)`)|(\[(?:ref:)?\s*(stt_\d+(?:\s*,\s*stt_\d+)*)\s*\])|(【(?:ref:)?\s*(stt_\d+(?:\s*,\s*stt_\d+)*)\s*】)|(\[priority:\s*(high|medium|low)\s*\])|(【priority:\s*(high|medium|low)\s*】)/gi
  let lastIdx = 0
  let match
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIdx) {
      parts.push(<span key={`t${lastIdx}`}>{text.slice(lastIdx, match.index)}</span>)
    }
    if (match[1]) {
      parts.push(<strong key={`b${lastIdx}`}>{match[2]}</strong>)
    } else if (match[3]) {
      parts.push(<em key={`i${lastIdx}`}>{match[4]}</em>)
    } else if (match[5]) {
      parts.push(
        <code
          key={`c${lastIdx}`}
          className="bg-muted px-1 rounded text-xs t-mono-family"
        >
          {match[6]}
        </code>,
      )
    } else if (match[8] || match[10]) {
      const raw = (match[8] || match[10])!
      const ids = raw
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
      const parsed = ids
        .map((id) => ({
          id,
          num: parseInt(id.replace(/^stt_0*/, "") || "0", 10),
        }))
        .sort((a, b) => a.num - b.num)

      let ri = 0
      while (ri < parsed.length) {
        const start = parsed[ri]
        let end = start
        let rj = ri + 1
        while (rj < parsed.length && parsed[rj].num === end.num + 1) {
          end = parsed[rj]
          rj++
        }
        const sl = start.id.replace(/^stt_0*/, "") || "0"
        const el = end.id.replace(/^stt_0*/, "") || "0"
        const label = start.id === end.id ? sl : `${sl}-${el}`
        const allInRange = parsed.slice(ri, rj).map((p) => p.id)
        parts.push(
          <button
            key={`r${lastIdx}${ri}`}
            type="button"
            className="inline-flex items-center px-1 py-0 text-[10px] rounded bg-[rgba(61,175,115,0.12)] text-[#2D8A5E] hover:bg-[rgba(61,175,115,0.20)] t-mono-family align-baseline cursor-pointer mr-1"
            onClick={(e) => {
              e.stopPropagation()
              onRefClick(start.id)
            }}
            title={`Sources: ${allInRange.join(", ")}`}
          >
            {label}
          </button>,
        )
        ri = rj
      }
    } else if (match[12] || match[14]) {
      const level = (match[12] || match[14])!.toLowerCase()
      const colors: Record<string, { bg: string; fg: string }> = {
        high: { bg: "rgba(140,46,46,0.12)", fg: "#C06060" },
        medium: { bg: "rgba(138,101,0,0.10)", fg: "#B09030" },
        low: { bg: "rgba(26,94,61,0.10)", fg: "#5A9070" },
      }
      const c = colors[level] ?? colors.medium
      parts.push(
        <span
          key={`p${lastIdx}`}
          className="inline-flex items-center px-1 py-0 text-[9px] rounded font-medium tracking-wider align-baseline select-none"
          style={{ backgroundColor: c.bg, color: c.fg }}
        >
          {level.toUpperCase()}
        </span>,
      )
    }
    lastIdx = match.index + match[0].length
  }
  if (lastIdx < text.length) {
    parts.push(<span key={`t${lastIdx}`}>{text.slice(lastIdx)}</span>)
  }
  return parts
}

export interface SummaryMarkdownViewerProps {
  md: string
  speakerNames?: Record<string, string> | null
  onRefClick?: (id: string) => void
  className?: string
}

/**
 * Display-only Meeting Summary viewer.
 * Speaker mapping is display-only — source string is never written back.
 */
export function SummaryMarkdownViewer({
  md,
  speakerNames,
  onRefClick,
  className,
}: SummaryMarkdownViewerProps) {
  if (!md) {
    return (
      <p className="text-muted-foreground text-sm py-8 text-center">
        No content yet.
      </p>
    )
  }

  const handleRef = onRefClick ?? (() => {})

  // Display-only resolve: never persisted
  let resolved = normalizeMd(md)
  const names = speakerNames || {}
  if (Object.keys(names).length > 0) {
    resolved = resolved.replace(
      /\[spk:(\d+)\]/g,
      (_, id: string) => names[id] ?? `Speaker ${id}`,
    )
    for (const [id, name] of Object.entries(names)) {
      if (!name) continue
      resolved = resolved.replace(
        new RegExp(
          `\\bSpeaker ${id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`,
          "g",
        ),
        name,
      )
    }
  }

  return (
    <div className={className ?? "prose prose-sm dark:prose-invert max-w-none"}>
      {resolved.split("\n").map((line, i) => {
        if (!line.trim()) return <div key={i} className="h-3" />
        if (line.startsWith("### "))
          return (
            <h3 key={i} className="text-base font-light mb-1.5 mt-2">
              {renderSummaryInline(line.slice(4), handleRef)}
            </h3>
          )
        if (line.startsWith("## "))
          return (
            <h2 key={i} className="text-lg font-light mb-2 mt-3">
              {renderSummaryInline(line.slice(3), handleRef)}
            </h2>
          )
        if (line.startsWith("# "))
          return (
            <h1 key={i} className="text-xl font-light mb-3 mt-4">
              {renderSummaryInline(line.slice(2), handleRef)}
            </h1>
          )
        if (/^\s*[-*+]\s/.test(line)) {
          return (
            <li key={i} className="text-sm leading-relaxed ml-4">
              {renderSummaryInline(line.replace(/^\s*[-*+]\s/, ""), handleRef)}
            </li>
          )
        }
        return (
          <p key={i} className="text-sm leading-relaxed mb-1">
            {renderSummaryInline(line, handleRef)}
          </p>
        )
      })}
    </div>
  )
}
