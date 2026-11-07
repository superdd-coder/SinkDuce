import { request } from "./http"

export const SEARCH_CATEGORIES = ["projects", "files", "meetings", "minutes", "notes"] as const
export type SearchCategory = typeof SEARCH_CATEGORIES[number]
export type WorkspaceSearchHit = {
  category: SearchCategory
  id: string
  title: string
  context: string
  snippet: string
  collection_id?: string
  file_id?: string
  meeting_id?: string
  section_id?: string
  note_id?: string
}
export type SearchGroup = { category: SearchCategory; total: number; items: WorkspaceSearchHit[]; has_more: boolean; next_offset: number }
export type WorkspaceSearchResponse = { query: string; groups: SearchGroup[]; unavailable_categories: SearchCategory[] }

export function searchWorkspace(query: string, signal: AbortSignal, category: SearchCategory | "all" = "all", offset = 0) {
  const params = new URLSearchParams({ q: query, category, offset: String(offset), limit: "8" })
  return request<WorkspaceSearchResponse>(`/search/workspace?${params}`, { signal })
}
