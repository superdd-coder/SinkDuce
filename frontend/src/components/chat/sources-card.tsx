import { useState } from "react"
import { ChevronDown, ChevronUp, ExternalLink, Globe } from "lucide-react"
import { useAppStore, type Source } from "@/stores/app-store"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"

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
  const t = useT()
  const [expanded, setExpanded] = useState(false)
  const collections = useAppStore((s) => s.collections)

  const getCollectionName = (id: string) => {
    const col = collections.find((c) => c.id === id)
    return col?.name || id
  }

  /**
   * Dedupe by chunk id / url (agentic RAG can return the same point twice).
   * Keep highest score so React list keys stay unique.
   */
  const raw = Array.isArray(sources) ? sources : []
  const byKey = new Map<string, Source>()
  raw.forEach((s, idx) => {
    const meta =
      s?.metadata && typeof s.metadata === "object" ? s.metadata : {}
    const chunkId = String((meta as { id?: string }).id || "")
    const url = String((meta as { url?: string }).url || "")
    const k = chunkId || url || `__i${idx}`
    const prev = byKey.get(k)
    if (!prev || (Number(s?.score) || 0) >= (Number(prev?.score) || 0)) {
      byKey.set(k, s)
    }
  })
  const list = [...byKey.values()].sort(
    (a, b) => (Number(b?.score) || 0) - (Number(a?.score) || 0),
  )
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
          <span>{t("chat.sourcesCount", { n: list.length })}</span>
          {webCount > 0 && (
            <span className="pm-chat-web-badge" title={t("chat.publicSources")}>
              <Globe aria-hidden />
              {t("chat.nWeb", { n: webCount })}
            </span>
          )}
          {kbCount > 0 && (
            <span className="pm-chat-kb-badge" title={t("chat.kbSources")}>
              {t("chat.nKb", { n: kbCount })}
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
          {list.map((s, i) => {
              const meta = (s?.metadata && typeof s.metadata === "object") ? s.metadata : {}
              const sourceName = (meta.source_label as string) || (meta.source as string) || ""
              const collection = (meta.collection as string) || ""
              const chunkId = (meta.id as string) || ""
              const isWeb = isWebSource(s)
              const url = (meta.url as string) || ""
              const isSelected = selectedSourceId === chunkId

              return (
                <button
                  key={chunkId || url ? `${chunkId || url}` : `src-${i}`}
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
                  title={isWeb && url ? t("chat.externalLink") : undefined}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5 min-w-0">
                      {isWeb ? (
                        <span className="pm-chat-web-badge">
                          <Globe aria-hidden />
                          {t("chat.web")}
                        </span>
                      ) : (
                        <span className="pm-chat-kb-badge" title={t("chat.knowledgeBase")}>
                          kb
                        </span>
                      )}
                      <div className="pm-chat-source-name">
                        {sourceName || t("chat.sourceN", { n: i + 1 })}
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
                      <span className="pm-chat-source-ext" aria-hidden title={t("chat.externalLink")}>
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
