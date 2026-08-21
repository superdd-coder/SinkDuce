import { Badge } from "@/components/ui/badge"
import { useAppStore } from "@/stores/app-store"
import type { RecallResult } from "@/api/client"
import { cn } from "@/lib/utils"
import { ChunkMd } from "@/components/shared/chunk-md"
import { useT } from "@/i18n/use-t"

function getCollectionName(id: string) {
  const collections = useAppStore.getState().collections
  return collections.find((c) => c.id === id)?.name || id
}

/** Technical source keys must never be shown as filenames. */
function isOpaqueSourceKey(value: string | undefined | null): boolean {
  const s = (value || "").trim()
  if (!s) return true
  if (
    s.startsWith("__file__:") ||
    s.startsWith("__meeting__:") ||
    s.startsWith("__note__:") ||
    s.startsWith("file:")
  ) {
    return true
  }
  // bare 32-char hex id
  if (s.length === 32 && /^[0-9a-f]+$/i.test(s)) return true
  return false
}

/** Placeholder names from failed resolve — prefer filesMap over these. */
function isGenericDisplayName(value: string | undefined | null): boolean {
  const s = (value || "").trim()
  if (!s || isOpaqueSourceKey(s)) return true
  const low = s.toLowerCase()
  if (["document", "meeting", "note", "unknown", "untitled", "file"].includes(low)) {
    return true
  }
  if (low.startsWith("document (") && low.endsWith(")")) return true
  return false
}

function lookupFilesMap(
  source: string,
  filesMap: Record<string, string>,
): string | undefined {
  if (!source) return undefined
  const direct = filesMap[source]
  if (direct && !isGenericDisplayName(direct)) return direct
  // Aliases: file_id bare / __file__:id
  if (source.startsWith("__file__:")) {
    const fid = source.slice("__file__:".length)
    const byId = filesMap[fid] || filesMap[`file:${fid}`]
    if (byId && !isGenericDisplayName(byId)) return byId
  } else if (source.startsWith("file:")) {
    const fid = source.slice("file:".length)
    const byId = filesMap[fid] || filesMap[`__file__:${fid}`]
    if (byId && !isGenericDisplayName(byId)) return byId
  } else if (/^[0-9a-f]{32}$/i.test(source)) {
    const byId = filesMap[`__file__:${source}`] || filesMap[`file:${source}`]
    if (byId && !isGenericDisplayName(byId)) return byId
  }
  return undefined
}

function resolveSource(
  result: {
    source?: string
    display_name?: string
    source_label?: string
    collection?: string
  },
  filesMap: Record<string, string>,
  t: (key: string, vars?: Record<string, string | number>) => string,
): string {
  const source = result.source || ""
  // 1) filesMap first when server only has a weak/generic name
  const mapped = lookupFilesMap(source, filesMap)
  const serverName = result.display_name?.trim()
  const serverOk = !!(serverName && !isGenericDisplayName(serverName))

  // Prefer real server name; if missing/generic, use client map
  if (serverOk && mapped) {
    // Prefer longer/more specific of the two when both exist
    return serverName!.length >= mapped.length ? serverName! : mapped
  }
  if (serverOk) return serverName!
  if (mapped) return mapped

  const label = result.source_label?.trim()
  if (label && !isGenericDisplayName(label)) {
    // Strip "Note: " / "Meeting: " chrome
    if (label.startsWith("Note: ")) return label.slice(6).trim() || label
    if (label.startsWith("Meeting: ")) return label.slice(9).trim() || label
    return label
  }

  // Path-style legacy sources
  if (source && !isOpaqueSourceKey(source)) {
    const last = source.split("/").pop() || source
    if (!isGenericDisplayName(last)) return last
  }

  // Last resort — short type tag (better than raw UUID key)
  if (source.startsWith("__meeting__:")) return t("nav.meeting")
  if (source.startsWith("__note__:")) return t("common.note")
  if (source.startsWith("__file__:") || source.startsWith("file:")) return t("common.file")
  return t("common.file")
}

interface ResultListProps {
  results: RecallResult[]
  filesMap?: Record<string, string>
}

function ResultCard({
  result,
  rank,
  filesMap,
}: {
  result: RecallResult
  rank: number
  filesMap: Record<string, string>
}) {
  const t = useT()
  const scorePct = Math.max(0, Math.min(100, result.score * 100))
  return (
    <div className="pm-recall-result">
      <div className="pm-recall-result-rail" aria-hidden>
        <span className="pm-recall-rank">{rank}</span>
        <span className="pm-recall-score">{scorePct.toFixed(0)}%</span>
        <span className="pm-recall-score-track">
          <span
            className="pm-recall-score-fill"
            style={{ width: `${scorePct}%` }}
          />
        </span>
      </div>
      <div className="pm-recall-result-body">
        <div className="pm-recall-result-meta">
          {result.collection && (
            <Badge variant="outline">
              {getCollectionName(result.collection)}
            </Badge>
          )}
          {result.chunk_type && result.chunk_type !== "normal" && (
            <Badge variant="secondary">{result.chunk_type}</Badge>
          )}
          {(result.source || result.display_name) && (
            <span className="pm-recall-result-source">
              {resolveSource(result, filesMap, t)}
            </span>
          )}
        </div>
        <div className="pm-recall-result-text pm-recall-md">
          <ChunkMd
            text={result.text}
            collection={result.collection}
            source={result.source}
          />
        </div>
        {result.context && (
          <p className="pm-recall-callout">{result.context}</p>
        )}
      </div>
    </div>
  )
}

function ChildCard({
  child,
  index,
  filesMap,
}: {
  child: RecallResult
  index: number
  filesMap: Record<string, string>
}) {
  const t = useT()
  const scorePct = Math.max(0, Math.min(100, child.score * 100))
  const sourceLabel = resolveSource(child, filesMap, t)
  return (
    <article className="pm-recall-child-card">
      <header className="pm-recall-child-head">
        <span className="pm-recall-child-index" aria-hidden>
          {index}
        </span>
        <div className="pm-recall-child-head-main">
          <div className="pm-recall-child-badges">
            <Badge variant="secondary">{t("recall.child")}</Badge>
            <Badge variant="default">{scorePct.toFixed(0)}%</Badge>
          </div>
          {sourceLabel && sourceLabel !== t("common.file") && (
            <span className="pm-recall-result-source" title={sourceLabel}>
              {sourceLabel}
            </span>
          )}
        </div>
      </header>
      <div className="pm-recall-child-body pm-recall-md">
        <ChunkMd
          text={child.text}
          collection={child.collection}
          source={child.source}
        />
      </div>
      {child.context && (
        <p className="pm-recall-callout pm-recall-child-context">
          {child.context}
        </p>
      )}
    </article>
  )
}

export function ResultList({ results, filesMap }: ResultListProps) {
  const t = useT()
  const map = filesMap || {}
  return (
    <div className="pm-recall-list">
      {results.map((result, i) => (
        <div key={result.id || i} className={cn("pm-recall-result-card")}>
          <ResultCard result={result} rank={i + 1} filesMap={map} />
          {result.children && result.children.length > 0 && (
            <section className="pm-recall-children" aria-label={t("recall.matchedChildren")}>
              <div className="pm-recall-children-head">
                <span className="pm-recall-children-title">
                  {t("recall.matchedChildren")}
                </span>
                <span className="pm-recall-children-count">
                  {result.children.length}
                </span>
              </div>
              <div className="pm-recall-children-stack">
                {result.children.map((child, j) => (
                  <ChildCard
                    key={child.id || j}
                    child={child}
                    index={j + 1}
                    filesMap={map}
                  />
                ))}
              </div>
            </section>
          )}
        </div>
      ))}
    </div>
  )
}
