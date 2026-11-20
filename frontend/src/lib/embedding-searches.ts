/** A query vector that was actually submitted to a collection's index. */
export interface EmbeddingSearch {
  collection: string
  model: string
  dimensions: number
  mode: "dense" | "hybrid"
  provider_id: string
  provider_name: string
}

export function embeddingSearches(value: unknown): EmbeddingSearch[] {
  const searches = new Map<string, EmbeddingSearch>()
  if (!Array.isArray(value)) return []
  for (const row of value) {
    if (!row || (row.kind && row.kind !== "retrieval" && row.kind !== "retrieval_fallback")
        || typeof row.collection !== "string" || !row.collection || typeof row.model !== "string" || !row.model
        || !Number.isInteger(row.dimensions) || row.dimensions <= 0
        || (row.mode !== "dense" && row.mode !== "hybrid")) continue
    const { collection, model, dimensions, mode } = row
    const provider_id = typeof row.provider_id === "string" ? row.provider_id : ""
    const provider_name = typeof row.provider_name === "string" ? row.provider_name : ""
    searches.set(JSON.stringify([collection, provider_id, model, dimensions, mode]),
      { collection, model, dimensions, mode, provider_id, provider_name })
  }
  return [...searches.values()]
}
