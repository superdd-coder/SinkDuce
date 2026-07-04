import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { useAppStore } from "@/stores/app-store"
import type { RecallResult } from "@/api/client"

function getCollectionName(id: string) {
  const collections = useAppStore.getState().collections
  return collections.find(c => c.id === id)?.name || id
}

function resolveSource(source: string, filesMap: Record<string, string>): string {
  if (filesMap[source]) return filesMap[source]
  // Fallback: last segment of path
  const last = source.split("/").pop()
  return last || source
}

interface ResultListProps {
  results: RecallResult[]
  filesMap?: Record<string, string>
}

function ResultCard({ result, rank, filesMap }: { result: RecallResult; rank: number; filesMap: Record<string, string> }) {
  return (
    <div className="py-3">
      <div className="flex items-start gap-3">
        <div className="flex items-center gap-2 shrink-0">
          <Badge variant="outline" className="text-xs t-mono-family">#{rank}</Badge>
          <Badge variant="secondary" className="text-xs">
            {(result.score * 100).toFixed(1)}%
          </Badge>
        </div>
        <div className="flex-1 space-y-2">
          <div className="flex items-center gap-2">
            {result.collection && (
              <Badge variant="outline" className="text-[10px]">{getCollectionName(result.collection)}</Badge>
            )}
            {result.source && (
              <span className="text-xs text-muted-foreground truncate">{resolveSource(result.source, filesMap)}</span>
            )}
            {result.chunk_type && result.chunk_type !== "normal" && (
              <Badge variant="secondary" className="text-[10px]">{result.chunk_type}</Badge>
            )}
          </div>
          <p className="text-sm leading-relaxed whitespace-pre-wrap">
            {result.text}
          </p>
          {result.context && (
            <p className="text-xs text-muted-foreground italic border-l-2 pl-2">
              {result.context}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function ChildCard({ child, filesMap }: { child: RecallResult; filesMap: Record<string, string> }) {
  return (
    <div className="ml-8 border-l-2 border-primary/20 pl-3">
      <div className="flex items-center gap-2 mb-1">
        <Badge variant="secondary" className="text-[10px]">child</Badge>
        <Badge variant="outline" className="text-[10px]">
          {(child.score * 100).toFixed(1)}%
        </Badge>
        {child.source && (
          <span className="text-[10px] text-muted-foreground truncate">{resolveSource(child.source, filesMap)}</span>
        )}
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground whitespace-pre-wrap">
        {child.text}
      </p>
      {child.context && (
        <p className="text-[10px] text-muted-foreground italic border-l-2 pl-2 mt-1">
          {child.context}
        </p>
      )}
    </div>
  )
}

export function ResultList({ results, filesMap }: ResultListProps) {
  const map = filesMap || {}
  return (
    <div>
      {results.map((result, i) => (
        <div key={result.id || i}>
          {i > 0 && <Separator className="my-2" />}
          <div className="space-y-2">
            <ResultCard result={result} rank={i + 1} filesMap={map} />
            {result.children && result.children.length > 0 && (
              <div className="space-y-2 py-1">
                <div className="text-[10px] text-muted-foreground font-medium ml-8">
                  Matched child chunks ({result.children.length}):
                </div>
                {result.children.map((child, j) => (
                  <ChildCard key={child.id || j} child={child} filesMap={map} />
                ))}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
