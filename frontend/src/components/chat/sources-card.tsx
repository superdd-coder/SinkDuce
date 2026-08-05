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
  // Default collapsed — user expands manually
  const [expanded, setExpanded] = useState(false)
  const collections = useAppStore((s) => s.collections)

  const getCollectionName = (id: string) => {
    const col = collections.find((c) => c.id === id)
    return col?.name || id
  }

  const list = Array.isArray(sources) ? sources : []
  const webCount = list.filter(
    (s) =>
      s?.metadata?.source_type === "web" ||
      s?.metadata?.provider === "tavily",
  ).length
  const kbCount = list.length - webCount

  if (!list.length) return null

  return (
    <div
      className={cn(
        "mt-5 pt-3.5 border-t border-dashed border-border",
        webCount > 0 && "border-amber-500/30",
      )}
    >
      {/* Toggle header */}
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex items-center justify-between w-full mb-3 cursor-pointer"
      >
        <span
          className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80 flex items-center gap-1.5"
        >
          Sources · {list.length}
          {webCount > 0 && (
            <span className="text-[9px] font-bold tracking-wider px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-700 dark:text-amber-400 border border-amber-500/40">
              {webCount} WEB
            </span>
          )}
          {kbCount > 0 && webCount > 0 ? (
            <span className="text-muted-foreground/60">· {kbCount} kb</span>
          ) : null}
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
            {[...list]
              .sort((a, b) => (Number(b?.score) || 0) - (Number(a?.score) || 0))
              .map((s, i) => {
              const meta = (s?.metadata && typeof s.metadata === "object") ? s.metadata : {}
              const sourceName = (meta.source_label as string) || (meta.source as string) || ""
              const collection = (meta.collection as string) || ""
              const chunkId = (meta.id as string) || ""
              const isWeb =
                meta.source_type === "web" || meta.provider === "tavily"
              const url = (meta.url as string) || ""
              const isSelected = selectedSourceId === chunkId

              return (
                <button
                  key={chunkId || url || i}
                  type="button"
                  onClick={() => {
                    if (isWeb && url) {
                      window.open(url, "_blank", "noopener,noreferrer")
                      return
                    }
                    onSelectSource?.(s)
                  }}
                  className={cn(
                    "w-full text-left flex justify-between items-baseline py-2.5 border-b cursor-pointer transition-colors border-dashed border-border overflow-hidden",
                    isWeb && "bg-amber-500/[0.04]",
                  )}
                  style={isSelected && !isWeb ? { color: "var(--color-primary)" } : undefined}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5 min-w-0">
                      {isWeb && (
                        <span className="shrink-0 text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-700 dark:text-amber-400 border border-amber-500/40">
                          WEB
                        </span>
                      )}
                      <div className={cn("text-xs truncate", isSelected ? "text-primary" : "text-foreground")}>
                        {sourceName || `Source ${i + 1}`}
                      </div>
                    </div>
                    {s.text && (
                      <div
                        className="text-[11px] mt-0.5 line-clamp-2 leading-relaxed text-muted-foreground"
                      >
                        {s.text}
                      </div>
                    )}
                    {isWeb && url && (
                      <div className="text-[10px] mt-0.5 truncate text-amber-700/80 dark:text-amber-400/80">
                        {url}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-2.5 shrink-0 ml-3">
                    {!isWeb && collection && (
                      <span className="text-[10px] text-muted-foreground">
                        {getCollectionName(collection)}
                      </span>
                    )}
                    {isWeb ? (
                      <span className="text-[10px] font-semibold text-amber-700 dark:text-amber-400">
                        WEB
                      </span>
                    ) : (
                      <span
                        className="text-[10px] font-semibold text-primary"
                      >
                        {((Number(s?.score) || 0) * 100).toFixed(1)}%
                      </span>
                    )}
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
