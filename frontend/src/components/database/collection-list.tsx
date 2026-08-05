import { useCallback, useLayoutEffect, useRef, useState } from "react"
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

type IndicatorBox = { top: number; height: number }

export function CollectionList({
  collections,
  activeCollection,
  onSelect,
  onCreate,
  onDelete,
  onRename,
}: CollectionListProps) {
  const listRef = useRef<HTMLDivElement>(null)
  const rowRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const [indicator, setIndicator] = useState<IndicatorBox | null>(null)
  /** When false, indicator jumps with no transition (enter Library / first paint). */
  const [indicatorReady, setIndicatorReady] = useState(false)
  /**
   * true → next measure places hard (no slide).
   * Set on first paint + whenever the list becomes visible again (view switch).
   * Cleared after a hard place so collection clicks still animate.
   */
  const hardPlaceRef = useRef(true)
  const visibleRef = useRef(false)

  const measureIndicator = useCallback(
    (opts?: { hard?: boolean }) => {
      const list = listRef.current
      const row = activeCollection
        ? rowRefs.current.get(activeCollection)
        : null
      if (!list || !row) {
        setIndicator(null)
        return
      }
      // Hidden view → zero box; don’t arm transitions from bogus coords
      if (list.getBoundingClientRect().height < 4) {
        hardPlaceRef.current = true
        setIndicatorReady(false)
        return
      }

      const next = {
        top: row.offsetTop,
        height: row.offsetHeight,
      }
      const hard = opts?.hard ?? hardPlaceRef.current

      if (hard) {
        // Paint at final position with transition disabled, then arm slides
        setIndicatorReady(false)
        setIndicator(next)
        hardPlaceRef.current = false
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            setIndicatorReady(true)
          })
        })
      } else {
        setIndicator(next)
        setIndicatorReady(true)
      }
    },
    [activeCollection]
  )

  useLayoutEffect(() => {
    measureIndicator()
  }, [measureIndicator, collections])

  useLayoutEffect(() => {
    const list = listRef.current
    if (!list) return

    // Entering Library after Chat/etc.: list goes from hidden → visible
    const io = new IntersectionObserver(
      ([entry]) => {
        const now = entry.isIntersecting && entry.intersectionRatio > 0
        if (now && !visibleRef.current) {
          hardPlaceRef.current = true
          measureIndicator({ hard: true })
        }
        visibleRef.current = now
      },
      { threshold: [0, 0.01, 0.1] }
    )
    io.observe(list)

    const ro = new ResizeObserver(() => {
      // Layout settle after show — hard place if we still owe one
      if (hardPlaceRef.current) measureIndicator({ hard: true })
      else measureIndicator()
    })
    ro.observe(list)

    const onResize = () => measureIndicator()
    window.addEventListener("resize", onResize)
    return () => {
      io.disconnect()
      ro.disconnect()
      window.removeEventListener("resize", onResize)
    }
  }, [measureIndicator])

  const setRowRef = useCallback((id: string, el: HTMLDivElement | null) => {
    if (el) rowRefs.current.set(id, el)
    else rowRefs.current.delete(id)
  }, [])

  return (
    <div className="pm-shell-collections">
      <div className="pm-shell-collections-surface pm-float-surface">
        <div className="pm-shell-collections-head">
          <span className="pm-shell-collections-title">Collections</span>
          <button type="button" onClick={onCreate} className="pm-shell-collections-new">
            New
          </button>
        </div>

        <ScrollArea className="flex-1 min-h-0">
          <div ref={listRef} className="pm-shell-col-list relative pb-3 pt-1">
            {indicator && (
              <div
                className={cn(
                  "pm-shell-col-indicator",
                  indicatorReady && "is-ready"
                )}
                style={{
                  transform: `translateY(${indicator.top}px)`,
                  height: indicator.height,
                }}
                aria-hidden
              />
            )}

            {collections.map((col) => {
              const isActive = activeCollection === col.id
              return (
                <div
                  key={col.id}
                  ref={(el) => setRowRef(col.id, el)}
                  role="button"
                  tabIndex={0}
                  className={cn("pm-shell-col-row group", isActive && "is-active")}
                  onClick={() => onSelect(col.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      onSelect(col.id)
                    }
                  }}
                >
                  <span className="pm-shell-col-name truncate flex-1 min-w-0">
                    {col.name}
                  </span>

                  <div className="pm-shell-col-meta">
                    <span
                      className={cn(
                        "pm-shell-col-count",
                        col.points_count <= 0 && "opacity-0"
                      )}
                    >
                      {col.points_count > 0 ? col.points_count : "0"}
                    </span>
                    <div className="pm-shell-col-actions">
                      <button
                        type="button"
                        className="pm-shell-col-action"
                        onClick={(e) => {
                          e.stopPropagation()
                          onRename(col.id)
                        }}
                        title="Rename"
                      >
                        <Pencil className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        className="pm-shell-col-action pm-shell-col-action--danger"
                        onClick={(e) => {
                          e.stopPropagation()
                          onDelete(col.id)
                        }}
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
    </div>
  )
}
