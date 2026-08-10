import { useState } from "react"
import { ChevronDown, ChevronUp, ExternalLink, Globe } from "lucide-react"
import { useAppStore, type Source } from "@/stores/app-store"
import { cn } from "@/lib/utils"

interface SourcesCardProps {
  sources: Source[]
  onSelectSource?: (source: Source) => void
  selectedSourceId?: string | null
}

function isWebSource(s: Source | null | undefined): boolean {
  const meta = s?.metadata
  if (!meta || typeof meta !== "object") return false
  return (
    meta.source_type === "web" ||
    meta.provider === "tavily" ||
    meta.provider === "web"
  )
}

/**
 * Sources list:
 * - KB (database): soft green rail + wash — primary, owned knowledge
 * - Web: neutral row, silver WEB tag only (no green rail / wash)
 */
export function SourcesCard({ sources, onSelectSource, selectedSourceId }: SourcesCardProps) {
  const [expanded, setExpanded] = useState(false)
  const collections = useAppStore((s) => s.collections)

  const getCollectionName = (id: string) => {
    const col = collections.find((c) => c.id === id)
    return col?.name || id
  }

  const list = Array.isArray(sources) ? sources : []
  const webCount = list.filter(isWebSource).length
  const kbCount = list.length - webCount

  if (!list.length) return null

  return (
    <div className={cn("pm-chat-sources", webCount > 0 && "has-web")}>
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="pm-chat-sources-toggle"
      >
        <span className="inline-flex items-center gap-1.5 min-w-0 flex-wrap">
          <span>Sources · {list.length}</span>
          {webCount > 0 && (
            <span className="pm-chat-web-badge" title="Public internet sources">
              <Globe aria-hidden />
              {webCount} web
            </span>
          )}
          {kbCount > 0 && (
            <span className="pm-chat-kb-badge" title="Knowledge base sources">
              {kbCount} kb
            </span>
          )}
        </span>
        {expanded ? (
          <ChevronUp className="size-3 shrink-0 text-[var(--pm-faint)]" />
        ) : (
          <ChevronDown className="size-3 shrink-0 text-[var(--pm-faint)]" />
        )}
      </button>

      <div
        className={cn("pm-chat-sources-list", expanded && "is-open")}
        aria-hidden={!expanded}
        // inert when collapsed — blocks focus/click even if a subpixel leaks
        {...(!expanded ? { inert: true } : {})}
      >
        <div>
          {[...list]
            .sort((a, b) => {
              // Web after KB within same score band is fine; keep score primary
              return (Number(b?.score) || 0) - (Number(a?.score) || 0)
            })
            .map((s, i) => {
              const meta = (s?.metadata && typeof s.metadata === "object") ? s.metadata : {}
              const sourceName = (meta.source_label as string) || (meta.source as string) || ""
              const collection = (meta.collection as string) || ""
              const chunkId = (meta.id as string) || ""
              const isWeb = isWebSource(s)
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
                    "pm-chat-source-row",
                    isWeb ? "is-web" : "is-kb",
                    isSelected && !isWeb && "is-selected",
                  )}
                  title={isWeb && url ? `Open external: ${url}` : undefined}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5 min-w-0">
                      {isWeb ? (
                        <span className="pm-chat-web-badge">
                          <Globe aria-hidden />
                          web
                        </span>
                      ) : (
                        <span className="pm-chat-kb-badge" title="Knowledge base">
                          kb
                        </span>
                      )}
                      <div className="pm-chat-source-name">
                        {sourceName || `Source ${i + 1}`}
                      </div>
                    </div>
                    {s.text && (
                      <div className="pm-chat-source-snippet">{s.text}</div>
                    )}
                    {isWeb && url && (
                      <div className="pm-chat-source-url" title={url}>
                        {url}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0 ml-2">
                    {!isWeb && collection && (
                      <span className="pm-meta hidden sm:inline max-w-[7rem] truncate">
                        {getCollectionName(collection)}
                      </span>
                    )}
                    {isWeb ? (
                      <span className="pm-chat-source-ext" aria-hidden title="External link">
                        <ExternalLink className="size-3" strokeWidth={1.75} />
                      </span>
                    ) : (
                      <span className="pm-chat-source-score">
                        {((Number(s?.score) || 0) * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                </button>
              )
            })}
        </div>
      </div>
    </div>
  )
}
