/**
 * In-memory cleaning for meeting → note **distillation only**.
 * Mirrors backend ``prepare_meeting_summary_for_note``.
 *
 * NEVER write the result back to meeting section .md files.
 * Note UI display uses SummaryMarkdownViewer (Meeting-style render) instead.
 */
export function prepareMeetingSummaryForNote(
  content: string,
  speakerNames?: Record<string, string> | null,
): string {
  let md = content || ""

  // 1a. Unescape markdown-escaped brackets
  md = md.replace(/\\\[/g, "[").replace(/\\\]/g, "]")

  // 1b. CJK / fullwidth brackets
  md = md
    .replace(/【/g, "[")
    .replace(/】/g, "]")
    .replace(/〔/g, "[")
    .replace(/〕/g, "]")
    .replace(/［/g, "[")
    .replace(/］/g, "]")

  // 2. Speakers
  const names = speakerNames || {}
  for (const [spkId, name] of Object.entries(names)) {
    if (!name) continue
    const escaped = spkId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    md = md.replaceAll(`[spk:${spkId}]`, name)
    md = md.replace(new RegExp(`\\bSpeaker\\s+${escaped}\\b`, "gi"), name)
  }
  md = md.replace(/\[spk:([^\]]+)\]/g, "Speaker $1")
  // Artifacts: \Speaker 4\  \\Speaker 2\\
  md = md.replace(/\\+[Ss]peaker\s+(\d+)\\*/g, "Speaker $1")
  for (const [spkId, name] of Object.entries(names)) {
    if (!name) continue
    const escaped = spkId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    md = md.replace(new RegExp(`\\bSpeaker\\s+${escaped}\\b`, "gi"), name)
  }

  // 3. Whole citation bracket groups (never leave [,,,])
  // Tokens: full sentence id | stt_N | bare number
  const citeToken = "(?:[A-Za-z0-9]{6,}_stt_\\d+|stt_\\d+|\\d+)"
  const citeSep = "[\\s,，、;；\\-–—]+"
  md = md.replace(
    new RegExp(
      `\\[(?:ref\\s*:)?\\s*${citeToken}(?:${citeSep}${citeToken})*\\s*\\]`,
      "gi",
    ),
    "",
  )
  md = md.replace(/\b[A-Za-z0-9]{6,}_stt_\d+\b/gi, "")
  md = md.replace(/\bstt_\d+\b/gi, "")

  // 4. priority
  md = md.replace(/\[\s*priority\s*:\s*(?:high|medium|low)\s*\]/gi, "")

  // 5. Leftover empty / comma-only brackets e.g. [,,,,,,,,,,,]
  md = md.replace(/\[\s*(?:[,，、;；\-–—\s])*\s*\]/g, "")

  // 6. whitespace
  md = md.replace(/[ \t]+\n/g, "\n")
  md = md.replace(/ +([.,;:!?])/g, "$1")
  md = md.replace(/ {2,}/g, " ")
  md = md.replace(/\n{3,}/g, "\n\n")
  return md.trim()
}
