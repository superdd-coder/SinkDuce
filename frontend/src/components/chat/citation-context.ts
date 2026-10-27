import { createContext, useContext } from "react"
import type { Evidence } from "@/api/client"

export const CitationContext = createContext<{ records: Evidence[]; numbers: Map<string, number>; open: (record: Evidence) => void }>({
  records: [], numbers: new Map(), open() {},
})
export const useCitations = () => useContext(CitationContext)
