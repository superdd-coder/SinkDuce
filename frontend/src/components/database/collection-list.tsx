import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react"
import { Archive, ArchiveRestore, ChevronRight, Pencil, Trash2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"
import { useScrollEdgeFade } from "@/hooks/use-scroll-edge-fade"
import { useAppStore } from "@/stores/app-store"
import { canCreateCollection } from "@/lib/collection-perms"
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip"
import type { CollectionItem } from "@/stores/app-store"
import { ViewRailResizeHandle } from "@/components/shared/view-rail-resize-handle"

/**
 * Row name — takes the full row width; hovering shows the complete name in a
 * tooltip, but only when the name is actually truncated (no tooltip noise for
 * short names). Re-measured on every mouseenter so resize stays correct.
 */
function CollectionRowName({ name, archived }: { name: string; archived: boolean }) {
  const [truncated, setTruncated] = useState(false)
  return (
    <Tooltip>
      <TooltipTrigger
        delay={350}
        render={
          <span
            className={cn("pm-shell-col-name truncate flex-1 min-w-0", archived && "is-archived")}
            onMouseEnter={(e) => {
              setTruncated(e.currentTarget.scrollWidth > e.currentTarget.clientWidth + 1)
            }}
          />
        }
      >
        {name}
      </TooltipTrigger>
      {truncated && (
        <TooltipContent side="right" className="w-64 break-words">
          {name}
        </TooltipContent>
      )}
    </Tooltip>
  )
}

interface CollectionListProps {
  collections: CollectionItem[]
  activeCollection: string
  onSelect: (id: string) => void
  onCreate: () => void
  onDelete: (id: string) => void
  onRename: (id: string) => void
  onArchive: (id: string, archived: boolean) => void
}

type IndicatorBox = { top: number; height: number }

export function CollectionList({
  collections,
  activeCollection,
  onSelect,
  onCreate,
  onDelete,
  onRename,
  onArchive,
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
  const [archiveOpen, setArchiveOpen] = useState(false)

  const { activeCols, archivedCols } = useMemo(() => {
    const active: CollectionItem[] = []
    const archived: CollectionItem[] = []
    for (const col of collections) {
      ;(col.archived ? archived : active).push(col)
    }
    return { activeCols: active, archivedCols: archived }
  }, [collections])

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

  const edgeFade = useScrollEdgeFade(listRef, activeCols.length + archivedCols.length)
  const t = useT()
  // 没有盒子级 collection.create 能力的人不画"新建"按钮（点了也注定 403）。
  const canCreate = canCreateCollection(useAppStore((s) => s.identity))

  const renderRow = (col: CollectionItem) => {
    const isActive = activeCollection === col.id
    const isArchived = !!col.archived
    return (
      <div
        key={col.id}
        ref={(el) => setRowRef(col.id, el)}
        role="button"
        tabIndex={0}
        className={cn("pm-shell-col-row group", isActive && "is-active", isArchived && "is-archived")}
        onClick={() => onSelect(col.id)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault()
            onSelect(col.id)
          }
        }}
      >
        <CollectionRowName name={col.name} archived={isArchived} />
        {isArchived && (
          <span className="pm-shell-col-archived-tag">{t("library.archivedTag")}</span>
        )}

        <div className="pm-shell-col-actions">
          <button
            type="button"
            className="pm-shell-col-action"
            onClick={(e) => {
              e.stopPropagation()
              onArchive(col.id, !isArchived)
            }}
            title={isArchived ? t("library.unarchiveCollection") : t("library.archiveCollection")}
          >
            {isArchived
              ? <ArchiveRestore className="size-3" />
              : <Archive className="size-3" />}
          </button>
          <button
            type="button"
            className="pm-shell-col-action"
            onClick={(e) => {
              e.stopPropagation()
              onRename(col.id)
            }}
            title={t("common.rename")}
          >
            <Pencil className="size-3" />
          </button>
          <button
            type="button"
            className="pm-shell-col-action pm-shell-col-action--danger"
            onClick={(e) => {
              e.stopPropagation()
              onDelete(col.id)
            }}
            title={t("common.delete")}
          >
            <Trash2 className="size-3" />
          </button>
        </div>
      </div>
    )
  }

  return (
    <aside className="pm-shell-collections" aria-label={t("library.collections")}>
      <ViewRailResizeHandle />
      <div className="pm-shell-collections-surface">
        <div className="pm-shell-collections-head pm-rail-head">
          <h2 className="pm-shell-collections-title pm-rail-title">{t("library.collections")}</h2>
          {canCreate && (
            <button
              type="button"
              onClick={onCreate}
              className="pm-shell-collections-new pm-rail-new"
              title={t("library.newCollection")}
            >
              {t("common.new")}
            </button>
          )}
        </div>

        {/* Native scroll + edge fades — same path as Chat Sessions / Meeting */}
        <div className="pm-rail-list-shell">
          <div ref={listRef} className="pm-shell-col-list">
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

            {activeCols.map((col) => renderRow(col))}

            {archivedCols.length > 0 && (
              <div className="pm-rail-archive">
                <div
                  role="button"
                  tabIndex={0}
                  aria-expanded={archiveOpen}
                  className="pm-rail-archive-head"
                  onClick={() => setArchiveOpen((v) => !v)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      setArchiveOpen((v) => !v)
                    }
                  }}
                >
                  <ChevronRight
                    className={cn("pm-rail-archive-chevron", archiveOpen && "is-open")}
                    aria-hidden
                  />
                  <span>{t("library.archivedSection")}</span>
                  <span className="pm-rail-archive-count">{archivedCols.length}</span>
                </div>
                {archiveOpen && archivedCols.map((col) => renderRow(col))}
              </div>
            )}
          </div>
          <div
            className={cn(
              "pm-rail-edge-fade pm-rail-edge-fade--top",
              edgeFade.top && "is-visible",
            )}
            aria-hidden
          />
          <div
            className={cn(
              "pm-rail-edge-fade pm-rail-edge-fade--bottom",
              edgeFade.bottom && "is-visible",
            )}
            aria-hidden
          />
        </div>
      </div>
    </aside>
  )
}
