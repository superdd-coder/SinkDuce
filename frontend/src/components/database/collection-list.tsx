import { Pencil, Trash2 } from "lucide-react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import type { CollectionItem } from "@/stores/app-store"

interface CollectionListProps {
  collections: CollectionItem[]
  activeCollection: string
  onSelect: (id: string) => void
  onCreate: () => void
  onDelete: (id: string) => void
  onRename: (id: string) => void
}

export function CollectionList({ collections, activeCollection, onSelect, onCreate, onDelete, onRename }: CollectionListProps) {
  return (
    <div
      className="w-64 border-r flex flex-col shrink-0 border-border bg-background"
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-4 h-12 border-b border-border"
      >
        <span
          className="uppercase t-body-family"
          style={{
            fontSize: "14px",
            fontWeight: 300,
            letterSpacing: "-0.015em",
            color: "var(--muted-foreground)",
          }}
        >
          Collections
        </span>
        <button
          type="button"
          onClick={onCreate}
          className="text-[10px] font-medium uppercase tracking-[0.1em] px-2 py-0.5 cursor-pointer transition-opacity hover:opacity-85 bg-primary text-primary-foreground border-none t-sans-family"
          style={{ borderRadius: "2px" }}
        >
          + New
        </button>
      </div>

      {/* List */}
      <ScrollArea className="flex-1">
        <div className="px-5 py-3">
          {collections.map((col) => {
            const isActive = activeCollection === col.id
            return (
              <div
                key={col.id}
                className={cn(
                  "group relative py-2.5 cursor-pointer flex items-center justify-between border-b transition-colors border-b border-dashed border-border",
                  isActive ? "text-foreground" : "text-foreground/80",
                )}
                onClick={() => onSelect(col.id)}
              >
                {/* Active indicator */}
                {isActive && (
                  <span
                    className="absolute left-[-20px] top-0 bottom-0 w-[1.5px] bg-primary"
                  />
                )}

                <span className={cn("text-xs truncate flex-1", isActive ? "font-medium" : "font-normal")}>
                  {col.name}
                </span>

                <div className="flex items-center gap-1.5 shrink-0">
                  {col.points_count > 0 && (
                    <span
                      className="text-[10px] font-medium text-muted-foreground"
                    >
                      {col.points_count}
                    </span>
                  )}

                  {/* Hover actions */}
                  <div className="hidden group-hover:flex items-center gap-1 ml-1">
                    <button
                      type="button"
                      className="p-0.5 cursor-pointer text-muted-foreground"
                      style={{ background: "none", border: "none" }}
                      onClick={(e) => { e.stopPropagation(); onRename(col.id) }}
                      title="Rename"
                    >
                      <Pencil className="h-3 w-3" />
                    </button>
                    <button
                      type="button"
                      className="p-0.5 cursor-pointer text-muted-foreground"
                      style={{ background: "none", border: "none" }}
                      onClick={(e) => { e.stopPropagation(); onDelete(col.id) }}
                      title="Delete"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </ScrollArea>
    </div>
  )
}
