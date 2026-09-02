import { useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { cn } from "@/lib/utils"
import {
  Calendar,
  GitBranchPlus,
  GitMerge,
  GripVertical,
  ListTodo,
  Star,
} from "lucide-react"
import type { Node as NodeType, NodeGroup } from "@/types/file-mgmt"
import { GroupIconView, groupFromList, type GroupIconSource } from "./group-icons"
import { useT } from "@/i18n/use-t"
import { systemFolderDisplayName } from "@/i18n/system-folder"

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
  /** Meeting anchor: create todos from attached section summaries. */
  onCreateMeetingTodos?: () => void
  /** Visual only — drag sensors are on the sortable wrapper (see timeline SW). */
  showDragGrip?: boolean
  isDragging?: boolean
}

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
  onCreateMeetingTodos,
  showDragGrip = false,
  isDragging = false,
}: NodeCardProps) {
  const t = useT()
  const isMeetingAnchor = !!(node.external_ref || "").startsWith("meeting:")
  const source: GroupIconSource =
    groupSource ?? (groupName ? { name: groupName } : { name: t("common.uncategorized") })
  const groupLabel = systemFolderDisplayName(
    (source?.name && String(source.name).trim()) ||
      (groupName && groupName.trim()) ||
      t("common.uncategorized"),
    t,
  )
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
          "pm-timeline-node group/card",
          isSelected && !isGroupFocus && "is-selected",
          isCompleted && "is-completed",
          node.has_definitive_file && !isGroupFocus && "is-definitive",
          isDragging && "is-dragging",
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
            <GripVertical className="h-3.5 w-3.5 text-[var(--pm-faint)]" />
          </div>
        )}

        {node.has_definitive_file && (
          <div
            className="absolute inset-0 rounded-[var(--pm-r-sm)] pointer-events-none bg-[color-mix(in_srgb,var(--pm-green)_10%,#ffffff)]"
            aria-hidden
          />
        )}

        {/* Group icon (name lives in debounced card tooltip) */}
        <span
          className="relative inline-flex shrink-0 items-center justify-center w-5 h-5"
          aria-hidden
        >
          <GroupIconView source={source} className="h-3.5 w-3.5" />
        </span>

        <div className="relative flex-1 min-w-0 overflow-hidden">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="pm-timeline-node-title">{nodeTitle}</span>
            {node.has_definitive_file && (
              <Star className="h-3 w-3 shrink-0 text-[var(--pm-green)] fill-[var(--pm-green)]" />
            )}
          </div>
          {node.event_time && (
            <div className="pm-timeline-node-time">
              <Calendar className="h-2.5 w-2.5 shrink-0" />
              <span className="truncate">{node.event_time}</span>
            </div>
          )}
        </div>

        {/* Hover-only actions: absolutely positioned so they take no layout
            room; the opaque (inherited) surface covers the title while shown. */}
        {(isMeetingAnchor && onCreateMeetingTodos) || onCreateChain || onMergeBranch ? (
          <div className="pm-timeline-node-actions">
            {isMeetingAnchor && onCreateMeetingTodos && (
              <button
                type="button"
                className="pm-timeline-node-action"
                onClick={(e) => {
                  e.stopPropagation()
                  onCreateMeetingTodos()
                }}
                title={t("fileMgmt.createTodosSummary")}
              >
                <ListTodo className="h-3.5 w-3.5" />
              </button>
            )}

            {onCreateChain && (
              <button
                type="button"
                className="pm-timeline-node-action"
                onClick={(e) => {
                  e.stopPropagation()
                  onCreateChain()
                }}
                title={t("fileMgmt.startBranch")}
              >
                <GitBranchPlus className="h-3.5 w-3.5" />
              </button>
            )}

            {onMergeBranch && (
              <button
                type="button"
                className="pm-timeline-node-action is-merge"
                onClick={(e) => {
                  e.stopPropagation()
                  onMergeBranch()
                }}
                title={t("fileMgmt.mergeBranch")}
              >
                <GitMerge className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        ) : null}
      </div>

      {tipOpen &&
        !isDragging &&
        createPortal(
          <div
            role="tooltip"
            className={cn("pm-timeline-tip", tipAbove && "is-above")}
            style={{
              top: tipPos.top,
              left: tipPos.left,
            }}
            onPointerEnter={cancelClose}
            onPointerLeave={scheduleClose}
          >
            <div className="flex items-center gap-1.5 min-w-0">
              <GroupIconView source={source} className="h-3 w-3 shrink-0" />
              <span className="pm-timeline-tip-title truncate">{groupLabel}</span>
            </div>
            {groupDescription ? (
              <div className="pm-timeline-tip-desc">{groupDescription}</div>
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
