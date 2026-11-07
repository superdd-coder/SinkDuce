import type { SearchGroup, WorkspaceSearchHit } from "../api/workspace-search"

export function searchHitKey(hit: WorkspaceSearchHit) { return `${hit.category}:${hit.id}` }

/** Keep pagination stable even if a resource changes between page requests. */
export function appendSearchPage(group: SearchGroup, page: SearchGroup): SearchGroup {
  const items = new Map(group.items.map(hit => [searchHitKey(hit), hit]))
  page.items.forEach(hit => items.set(searchHitKey(hit), hit))
  return { ...page, items: [...items.values()] }
}

export function highlightSearchText(text: string, query: string): { text: string; match: boolean }[] {
  const terms = [...new Set(query.trim().split(/\s+/u).filter(Boolean))].sort((a, b) => b.length - a.length)
  if (!terms.length) return [{ text, match: false }]
  const pattern = terms.map(term => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")
  const regex = new RegExp(`(${pattern})`, "giu")
  return text.split(regex).filter(Boolean).map(part => ({ text: part, match: terms.some(term => term.toLocaleLowerCase() === part.toLocaleLowerCase()) }))
}
