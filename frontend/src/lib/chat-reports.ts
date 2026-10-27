import { normalizeEvidence, type Evidence } from "./chat-citations.ts"

export interface ReportReference {
  id: string
  session_id: string
  title: string
  revision: number
  updated_at: string
}

export interface ReportBlock {
  id: string
  kind: 'heading' | 'paragraph' | 'list' | 'quote' | 'table' | 'code' | 'rule'
  hash: string
  start: number
  end: number
  start_line: number
  end_line: number
  parent_ids?: string[]
}

export function selectedConversationReport(selected: ReportReference | null, sessionId: string | null,
  messages: { reportChanges?: ReportReference[] }[]): ReportReference | null {
  if (!selected || selected.session_id !== sessionId) return null
  return messages.flatMap((m) => m.reportChanges || []).reduce((latest, report) =>
    report.session_id === sessionId && report.id === latest.id && report.revision > latest.revision ? report : latest, selected)
}

export function mergeReportChanges(existing: ReportReference[] = [], incoming: unknown): ReportReference[] {
  const merged = new Map(existing.map((report) => [report.id, report]))
  if (Array.isArray(incoming)) for (const report of incoming) {
    if (!report || typeof report !== "object" || typeof report.id !== "string" || typeof report.session_id !== "string" ||
      typeof report.title !== "string" || !Number.isInteger(report.revision) || report.revision < 1) continue
    const previous = merged.get(report.id)
    if (!previous || previous.revision <= report.revision) merged.set(report.id, report as ReportReference)
  }
  return [...merged.values()]
}

export function applyChatArtifacts<T extends { citations?: Evidence[]; reportChanges?: ReportReference[]; reportReviews?: import("./report-review").ReviewBatch[] }>(message: T, data: Record<string, unknown>): T {
  return { ...message,
    ...(Array.isArray(data.report_reviews) ? {reportReviews: data.report_reviews as import("./report-review").ReviewBatch[]} : {}),
    ...(Array.isArray(data.citations) ? { citations: normalizeEvidence(data.citations) } : {}),
    ...(Array.isArray(data.report_changes) ? { reportChanges: mergeReportChanges(message.reportChanges, data.report_changes) } : {}),
  }
}

export function reportSections(markdown: string, omitOpeningTitle = false): { title: string; line: number }[] {
  if (markdown.length < 6000) return []
  const headings: { title: string; line: number }[] = []
  let fence = ""
  markdown.split("\n").forEach((line, index) => {
    const marker = line.match(/^ {0,3}(`{3,}|~{3,})/)
    if (marker) { if (!fence) fence = marker[1][0]; else if (fence === marker[1][0]) fence = ""; return }
    if (fence) return
    const match = line.match(/^#{1,2}\s+(.+?)\s*#*$/)
    if (match && !(omitOpeningTitle && /^#\s/.test(line) && !markdown.split("\n").slice(0, index).join("").trim())) headings.push({ title: match[1].replace(/[*_`]/g, ""), line: index + 1 })
  })
  return headings.length >= 4 && !headings.slice(0, 2).some((h) => /^(目录|contents|table of contents)$/i.test(h.title)) ? headings.slice(0, 32) : []
}
