import { useState, useCallback } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
  TabsIndicator,
} from "@/components/ui/tabs"
import { Link2, ArrowUpRight } from "lucide-react"
import { cn } from "@/lib/utils"
import { type NoteReference } from "@/api/client"

interface NoteSidebarRightProps {
  references: NoteReference[]
  injectedInto: string[]
  injectedIntoTitles: Map<string, string>
  activeBlockId: string | null
  onSelectBlock: (blockId: string) => void
  onNavigateToNote: (noteId: string) => void
}

export function NoteSidebarRight({
  references,
  injectedInto,
  injectedIntoTitles,
  activeBlockId,
  onSelectBlock,
  onNavigateToNote,
}: NoteSidebarRightProps) {
  const hasReferences = references.length > 0
  const hasInjectedInto = injectedInto.length > 0

  const [blockIndices, setBlockIndices] = useState<Map<string, number>>(new Map())

  const handleSourceClick = useCallback(
    (sourceId: string, blockIds: string[]) => {
      if (blockIds.length === 0) return
      const currentIndex = blockIndices.get(sourceId) || 0
      const nextIndex = (currentIndex + 1) % blockIds.length
      setBlockIndices((prev) => new Map(prev).set(sourceId, nextIndex))
      onSelectBlock(blockIds[nextIndex])
    },
    [blockIndices, onSelectBlock]
  )

  if (!hasReferences && !hasInjectedInto) return null

  const showTabs = hasReferences && hasInjectedInto
  const defaultTab = hasReferences ? "in" : "out"

  const sourcesContent = (
    <div className="p-1.5 space-y-0">
      {references.length === 0 ? (
        <p className="pm-ws-empty">No distill blocks from other notes</p>
      ) : (
        (() => {
          const sourceMap = new Map<
            string,
            { title: string; blockIds: string[] }
          >()
          for (const ref of references) {
            const existing = sourceMap.get(ref.source_note_id)
            if (existing) {
              if (!existing.blockIds.includes(ref.block_id)) {
                existing.blockIds.push(ref.block_id)
              }
            } else {
              sourceMap.set(ref.source_note_id, {
                title: ref.source_title || ref.source_note_id,
                blockIds: [ref.block_id],
              })
            }
          }
          return Array.from(sourceMap.entries()).map(
            ([sourceId, { title, blockIds }]) => {
              const currentIndex = blockIndices.get(sourceId) || 0
              const isActive = blockIds.some((id) => id === activeBlockId)
              const count = blockIds.length

              return (
                <button
                  key={sourceId}
                  type="button"
                  className={cn("pm-ws-item", isActive && "is-focus")}
                  onClick={() => handleSourceClick(sourceId, blockIds)}
                  title={
                    count > 1
                      ? `Click to cycle through ${count} blocks`
                      : undefined
                  }
                >
                  <Link2 className="pm-ws-item-icon h-3 w-3" />
                  <span className="truncate flex-1">{title}</span>
                  {count > 1 && (
                    <span className="pm-ws-badge is-live tabular-nums">
                      {currentIndex + 1}/{count}
                    </span>
                  )}
                </button>
              )
            }
          )
        })()
      )}
    </div>
  )

  const injectedContent = (
    <div className="p-1.5 space-y-0">
      {injectedInto.length === 0 ? (
        <p className="pm-ws-empty">Not distilled into any notes</p>
      ) : (
        injectedInto.map((targetId) => (
          <button
            key={targetId}
            type="button"
            className="pm-ws-item"
            onClick={() => onNavigateToNote(targetId)}
          >
            <ArrowUpRight className="pm-ws-item-icon h-3 w-3" />
            <span className="truncate">
              {injectedIntoTitles.get(targetId) || targetId}
            </span>
          </button>
        ))
      )}
    </div>
  )

  if (!showTabs) {
    return (
      <div className="pm-ws-rail pm-ws-rail-r">
        <div className="pm-ws-seg !justify-start">
          <span className="pm-label px-1">
            {hasReferences ? "Distill In" : "Distill Out"}
          </span>
        </div>
        <ScrollArea className="flex-1">
          {hasReferences ? sourcesContent : injectedContent}
        </ScrollArea>
      </div>
    )
  }

  return (
    <div className="pm-ws-rail pm-ws-rail-r">
      <Tabs defaultValue={defaultTab} className="flex flex-col flex-1 min-h-0">
        <div className="pm-ws-seg">
          <TabsList variant="pill" className="w-full !h-auto">
            <TabsIndicator renderBeforeHydration className="pm-tabs-indicator" />
            <TabsTrigger value="in" className="flex-1">
              In ({new Set(references.map((r) => r.source_note_id)).size})
            </TabsTrigger>
            <TabsTrigger value="out" className="flex-1">
              Out ({injectedInto.length})
            </TabsTrigger>
          </TabsList>
        </div>
        <ScrollArea className="flex-1">
          <TabsContent value="in" className="mt-0">
            {sourcesContent}
          </TabsContent>
          <TabsContent value="out" className="mt-0">
            {injectedContent}
          </TabsContent>
        </ScrollArea>
      </Tabs>
    </div>
  )
}
