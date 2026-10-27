import { useEffect, useState } from "react"
import { fetchProviderTypes, type ProviderTypesResponse } from "../api/client"

const EMPTY: ProviderTypesResponse = {
  embedding: [],
  reranker: [],
  llm: [],
  file_transcription: [],
  realtime_transcription: [],
}

let cached: ProviderTypesResponse | null = null
let inflight: Promise<ProviderTypesResponse> | null = null

function load(): Promise<ProviderTypesResponse> {
  if (cached) return Promise.resolve(cached)
  if (inflight) return inflight
  inflight = fetchProviderTypes()
    .then((data) => {
      cached = data
      return data
    })
    .catch((err) => {
      console.warn("Failed to load provider types:", err)
      return EMPTY
    })
    .finally(() => {
      inflight = null
    })
  return inflight
}

/** Returns the list of registered provider types for embedding / reranker / llm. */
export function useProviderTypes(): ProviderTypesResponse {
  const [data, setData] = useState<ProviderTypesResponse>(cached ?? EMPTY)

  useEffect(() => {
    let cancelled = false
    load().then((result) => {
      if (!cancelled) setData(result)
    })
    return () => {
      cancelled = true
    }
  }, [])

  return data
}
