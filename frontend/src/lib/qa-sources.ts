import type { QaSource } from "../api/qa.ts"

export interface QaSourceGroup {
  key: string
  source: QaSource
  sources: QaSource[]
}

export function groupQaSources(sources: QaSource[]): QaSourceGroup[] {
  const groups = new Map<string, { sources: QaSource[]; seen: Set<string>; filename: string }>()

  for (const source of sources) {
    const key = JSON.stringify([source.kind, source.file_id, source.version_id])
    let group = groups.get(key)
    if (!group) {
      group = { sources: [], seen: new Set(), filename: "" }
      groups.set(key, group)
    }
    if (!group.filename && source.filename.trim()) group.filename = source.filename
    const passage = JSON.stringify([source.chunk_id || "", source.page_number ?? null, source.excerpt])
    if (!group.seen.has(passage)) {
      group.seen.add(passage)
      group.sources.push(source)
    }
  }

  return Array.from(groups, ([key, group]) => {
    const representative = group.sources.find(source => source.excerpt.trim()) || group.sources[0]
    const filename = representative.filename.trim()
      ? representative.filename
      : group.filename
    return { key, source: { ...representative, filename }, sources: group.sources }
  })
}
