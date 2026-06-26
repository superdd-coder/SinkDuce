import { useState } from "react"
import { ChevronDown, ChevronUp } from "lucide-react"
import { useAppStore, type Source } from "@/stores/app-store"
import { cn } from "@/lib/utils"

interface SourcesCardProps {
  sources: Source[]
  onSelectSource?: (source: Source) => void
  selectedSourceId?: string | null
}

export function SourcesCard({ sources, onSelectSource, selectedSourceId }: SourcesCardProps) {
  const [expanded, setExpanded] = useState(false)
  const collections = useAppStore((s) => s.collections)

  const getCollectionName = (id: string) => {
    const col = collections.find((c) => c.id === id)
    return col?.name || id
  }

  if (!sources.length) return null

  return (
    <div
      className="mt-5 pt-3.5 border-t border-t border-dashed border-border"
    >
      {/* Toggle header */}
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex items-center justify-between w-full mb-3 cursor-pointer"
      >
        <span
          className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80"
        >
          Sources · {sources.length}
        </span>
        {expanded ? (
          <ChevronUp className="h-3 w-3 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-3 w-3 text-muted-foreground" />
        )}
      </button>

      <div className={`grid transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${expanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}>
        <div className="overflow-hidden">
          <div>
            {[...sources].sort((a, b) => b.score - a.score).map((s, i) => {
              const sourceName = (s.metadata?.source_label as string) || (s.metadata?.source as string) || ""
              const collection = (s.metadata?.collection as string) || ""
              const chunkId = (s.metadata?.id as string) || ""
              const isSelected = selectedSourceId === chunkId

              return (
                <button
                  key={chunkId || i}
                  type="button"
                  onClick={() => onSelectSource?.(s)}
                  className="w-full text-left flex justify-between items-baseline py-2.5 border-b cursor-pointer transition-colors border-b border-dashed border-border overflow-hidden"
                  style={isSelected ? { color: "var(--color-primary)" } : undefined}
                >
                  <div className="min-w-0 flex-1">
                    <div className={cn("text-xs truncate", isSelected ? "text-primary" : "text-foreground")}>
                      {sourceName || `Source ${i + 1}`}
                    </div>
                    {s.text && (
                      <div
                        className="text-[11px] mt-0.5 line-clamp-2 leading-relaxed text-muted-foreground"
                      >
                        {s.text}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-2.5 shrink-0 ml-3">
                    {collection && (
                      <span className="text-[10px] text-muted-foreground">
                        {getCollectionName(collection)}
                      </span>
                    )}
                    <span
                      className="text-[10px] font-semibold text-primary"
                    >
                      {(s.score * 100).toFixed(1)}%
                    </span>
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
