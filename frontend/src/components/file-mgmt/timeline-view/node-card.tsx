import { useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { cn } from "@/lib/utils"
import {
  Calendar,
  GitBranchPlus,
  GitMerge,
  GripVertical,
  Star,
} from "lucide-react"
import type { Node as NodeType, NodeGroup } from "@/types/file-mgmt"
import { GroupIconView, groupFromList, type GroupIconSource } from "./group-icons"

const TIP_OPEN_MS = 450
const TIP_CLOSE_MS = 120

interface NodeCardProps {
  node: NodeType
  /** @deprecated prefer groupSource */
  groupName?: string | null
  /** Full group icon source (from groups list). */
  groupSource?: GroupIconSource
  isSelected: boolean
  isCompleted: boolean
  /** Green breathing glow when group-focus targets this node. */
  isGroupFocus?: boolean
  /** Dim when another group is focused. */
  isGroupDim?: boolean
  onClick: () => void
  onCreateChain?: () => void
  onMergeBranch?: () => void
  /** Visual only — drag sensors are on the sortable wrapper (see timeline SW). */
  showDragGrip?: boolean
  isDragging?: boolean
}

const CARD_H = "h-12"

export function NodeCard({
  node,
  groupName = null,
  groupSource,
  isSelected,
  isCompleted,
  isGroupFocus = false,
  isGroupDim = false,
  onClick,
  onCreateChain,
  onMergeBranch,
  showDragGrip = false,
  isDragging = false,
}: NodeCardProps) {
  const source: GroupIconSource =
    groupSource ?? (groupName ? { name: groupName } : { name: "未分类" })
  const groupLabel =
    (source?.name && String(source.name).trim()) ||
    (groupName && groupName.trim()) ||
    "Uncategorized"
  const groupDescription =
    groupSource &&
    typeof groupSource === "object" &&
    "description" in groupSource &&
    typeof (groupSource as NodeGroup).description === "string"
      ? (groupSource as NodeGroup).description?.trim() || null
      : null
  const nodeTitle = node.title || "Untitled Node"

  const cardRef = useRef<HTMLDivElement>(null)
  const [tipOpen, setTipOpen] = useState(false)
  const [tipPos, setTipPos] = useState({ top: 0, left: 0 })
  const openT = useRef<ReturnType<typeof setTimeout> | null>(null)
  const closeT = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clearTimers = () => {
    if (openT.current) {
      clearTimeout(openT.current)
      openT.current = null
    }
    if (closeT.current) {
      clearTimeout(closeT.current)
      closeT.current = null
    }
  }

  const placeTip = () => {
    const el = cardRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    // Prefer above the card; if near top of viewport, flip below
    const above = r.top >= 48
    setTipPos({
      top: above ? r.top - 8 : r.bottom + 8,
      left: r.left + r.width / 2,
    })
    return above
  }

  const [tipAbove, setTipAbove] = useState(true)

  const scheduleOpen = () => {
    if (isDragging) return
    clearTimers()
    openT.current = setTimeout(() => {
      const above = placeTip()
      setTipAbove(above !== false)
      setTipOpen(true)
    }, TIP_OPEN_MS)
  }

  const scheduleClose = () => {
    clearTimers()
    closeT.current = setTimeout(() => setTipOpen(false), TIP_CLOSE_MS)
  }

  const cancelClose = () => {
    if (closeT.current) {
      clearTimeout(closeT.current)
      closeT.current = null
    }
  }

  useEffect(() => {
    if (!tipOpen) return
    const onMove = () => {
      const above = placeTip()
      setTipAbove(above !== false)
    }
    window.addEventListener("scroll", onMove, true)
    window.addEventListener("resize", onMove)
    return () => {
      window.removeEventListener("scroll", onMove, true)
      window.removeEventListener("resize", onMove)
    }
  }, [tipOpen])

  useEffect(() => {
    if (isDragging) {
      clearTimers()
      setTipOpen(false)
    }
  }, [isDragging])

  useEffect(() => () => clearTimers(), [])

  return (
    <>
      <div
        ref={cardRef}
        data-node-card
        data-node-id={node.node_id}
        data-dragging={isDragging ? "true" : "false"}
        className={cn(
          "relative z-[1] flex items-center gap-2 px-3 rounded-md border cursor-pointer transition-all group/card bg-background w-48 shrink-0 overflow-visible",
          CARD_H,
          isSelected && !isGroupFocus
            ? "border-primary/50 ring-1 ring-primary/20"
            : "border-border hover:border-muted-foreground/30",
          isCompleted && "bg-muted text-muted-foreground border-muted-foreground/20",
          node.has_definitive_file &&
            !isGroupFocus &&
            "shadow-[0_0_8px_rgba(34,197,94,0.3)] border-emerald-500/40",
          isDragging && "opacity-60 shadow-lg z-50",
          isGroupFocus && "node-group-focus",
          isGroupDim && "node-group-dim",
          showDragGrip && "cursor-grab active:cursor-grabbing"
        )}
        onClick={onClick}
        onPointerEnter={scheduleOpen}
        onPointerLeave={scheduleClose}
      >
        {showDragGrip && (
          <div
            className="shrink-0 opacity-30 group-hover/card:opacity-100 transition-opacity touch-none cursor-grab"
            aria-hidden
          >
            <GripVertical className="h-3.5 w-3.5 text-muted-foreground" />
          </div>
        )}

        {node.has_definitive_file && (
          <div className="absolute inset-0 rounded-md pointer-events-none animate-pulse bg-emerald-500/5" />
        )}

        {/* Group icon (name lives in debounced card tooltip) */}
        <span
          className="inline-flex shrink-0 items-center justify-center w-5 h-5"
          aria-hidden
        >
          <GroupIconView source={source} className="h-3.5 w-3.5" />
        </span>

        <div className="flex-1 min-w-0 overflow-hidden">
          <div className="flex items-center gap-1.5 min-w-0">
            <span
              className={cn(
                "text-xs font-medium truncate block min-w-0",
                isCompleted ? "text-muted-foreground" : "text-foreground"
              )}
            >
              {nodeTitle}
            </span>
            {node.has_definitive_file && (
              <Star className="h-3 w-3 shrink-0 text-emerald-500" />
            )}
          </div>
          {node.event_time && (
            <div className="flex items-center gap-0.5 mt-0.5 min-w-0 text-[10px] text-muted-foreground/50">
              <Calendar className="h-2.5 w-2.5 shrink-0" />
              <span className="truncate">{node.event_time}</span>
            </div>
          )}
        </div>

        {onCreateChain && (
          <button
            className="opacity-0 group-hover/card:opacity-100 transition-opacity p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted/50 shrink-0"
            onClick={(e) => {
              e.stopPropagation()
              onCreateChain()
            }}
            title="Start branch chain from here"
          >
            <GitBranchPlus className="h-3.5 w-3.5" />
          </button>
        )}

        {onMergeBranch && (
          <button
            className="opacity-0 group-hover/card:opacity-100 transition-opacity p-1 rounded text-muted-foreground hover:text-emerald-600 hover:bg-emerald-500/10 shrink-0"
            onClick={(e) => {
              e.stopPropagation()
              onMergeBranch()
            }}
            title="Merge branch back to main"
          >
            <GitMerge className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {tipOpen &&
        !isDragging &&
        createPortal(
          <div
            role="tooltip"
            className={cn(
              "pointer-events-auto fixed z-[99999] max-w-[240px] -translate-x-1/2 rounded-md border border-border/40",
              "bg-[#0A120E] text-[#FAFAF7] px-2.5 py-1.5 text-[11px] shadow-xl",
              tipAbove ? "-translate-y-full" : ""
            )}
            style={{
              top: tipPos.top,
              left: tipPos.left,
            }}
            onPointerEnter={cancelClose}
            onPointerLeave={scheduleClose}
          >
            <div className="flex items-center gap-1.5 min-w-0">
              <GroupIconView source={source} className="h-3 w-3 shrink-0" />
              <span className="font-medium truncate">{groupLabel}</span>
            </div>
            {groupDescription ? (
              <div className="mt-0.5 text-[10px] text-[#FAFAF7]/80 leading-snug">
                {groupDescription}
              </div>
            ) : null}
          </div>,
          document.body
        )}
    </>
  )
}

/** Helper for callers that have groups list. */
export function groupSourceForNode(
  groups: NodeGroup[],
  groupId: string | null
): GroupIconSource {
  return groupFromList(groups, groupId)
}
