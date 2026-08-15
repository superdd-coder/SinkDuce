import { API_BASE, request } from "./http"

// ── Hot Words ──

export interface HotWordItem {
  text: string
  weight: number
  lang?: string
}

export interface HotWordsLibrary {
  id: string
  name: string
  description: string
  words: HotWordItem[]
  is_default?: boolean
  created_at: string
  updated_at: string
}

export interface HotWordsLibrarySummary {
  id: string
  name: string
  description: string
  word_count: number
  is_default?: boolean
  created_at: string
  updated_at: string
}

export const getHotWordsLibraries = () =>
  request<HotWordsLibrarySummary[]>("/hot-words")

export const getHotWordsLibrary = (id: string) =>
  request<HotWordsLibrary>(`/hot-words/${id}`)

export const createHotWordsLibrary = (data: { name: string; description?: string }) =>
  request<HotWordsLibrary>("/hot-words", {
    method: "POST",
    body: JSON.stringify(data),
  })

export const updateHotWordsLibrary = (id: string, data: Partial<HotWordsLibrary>) =>
  request<HotWordsLibrary>(`/hot-words/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  })

export const deleteHotWordsLibrary = (id: string) =>
  request<{ message?: string; error?: string }>(`/hot-words/${id}`, {
    method: "DELETE",
  })

/** Set or clear the default hot-words library (auto-selected on new meetings). */
export const setDefaultHotWordsLibrary = (libraryId: string | null) =>
  request<{ default_library_id: string | null; error?: string }>("/hot-words/default", {
    method: "PUT",
    body: JSON.stringify({ library_id: libraryId }),
  })

/** Download CSV / Excel hot-words import template (attachment). */
export function downloadHotWordsTemplate(format: "csv" | "xlsx" = "csv") {
  const a = document.createElement("a")
  a.href = `${API_BASE}/hot-words/template.${format}`
  a.download = `hot-words-template.${format}`
  a.rel = "noopener"
  document.body.appendChild(a)
  a.click()
  a.remove()
}

/** Export a library as Excel (.xlsx). */
export function exportHotWordsLibrary(id: string, nameHint?: string) {
  const a = document.createElement("a")
  a.href = `${API_BASE}/hot-words/${encodeURIComponent(id)}/export.xlsx`
  a.download = `${(nameHint || "hot-words").replace(/[^\w\-]+/g, "_").slice(0, 60)}.xlsx`
  a.rel = "noopener"
  document.body.appendChild(a)
  a.click()
  a.remove()
}

/** Import a CSV or Excel file as a new hot-words library. */
export async function importHotWordsLibrary(
  file: File,
  opts?: { name?: string; description?: string },
): Promise<HotWordsLibrary> {
  const fd = new FormData()
  fd.append("file", file)
  if (opts?.name?.trim()) fd.append("name", opts.name.trim())
  if (opts?.description?.trim()) fd.append("description", opts.description.trim())
  const res = await fetch(`${API_BASE}/hot-words/import`, {
    method: "POST",
    body: fd,
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(
      typeof body?.error === "string"
        ? body.error
        : `API ${res.status}: import failed`,
    )
  }
  if (body?.error) throw new Error(String(body.error))
  return body as HotWordsLibrary
}
