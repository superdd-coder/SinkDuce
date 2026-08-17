import type { PointerEvent, ReactNode } from "react"
import { Plus } from "lucide-react"
import { cn } from "@/lib/utils"
import type { Node, NodeGroup, TodoSuggestionItem } from "@/types/file-mgmt"
import { useDroppable } from "@dnd-kit/core"
import { SortableContext, horizontalListSortingStrategy, useSortable } from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"
import { NodeCard, groupSourceForNode } from "./node-card"
import { AddNodeTodoSplit } from "./add-node-todo-split"
import { TodoSuggestBubble } from "./todo-suggest-bubble"
import type { FocusGroupId } from "./groups-menu"

/** Droppable id for "append at end of chain" zone. */
export const endDropId = (chainId: string) => `__end__:${chainId}`
export const parseEndDropId = (id: string): string | null =>
  id.startsWith("__end__:") ? id.slice("__end__:".length) : null
/** Droppable id for "insert at start of chain" zone (before first node). */
export const startDropId = (chainId: string) => `__start__:${chainId}`
export const parseStartDropId = (id: string): string | null =>
  id.startsWith("__start__:") ? id.slice("__start__:".length) : null

function SNC(p: {
  node: Node
  groupName?: string | null
  groupSource?: ReturnType<typeof groupSourceForNode>
  isSelected: boolean
  isCompleted: boolean
  isGroupFocus?: boolean
  isGroupDim?: boolean
  onClick: () => void
  onCreateChain?: () => void
  onMergeBranch?: () => void
  onCreateMeetingTodos?: () => void
  showDragGrip?: boolean
  isDragging?: boolean
}) {
  return <NodeCard {...p} />
}

export interface ChainRowProps {
  /** Nodes in **display order** (parent applies server sort or live drag order). Do not re-sort. */
  chainData: Node[]
  chainId: string
  isBranch: boolean
  isCompleted?: boolean
  onNodeClick: (id: string) => void
  onAddNode: (cid: string, after: number) => void
  /** Add todo linked to this chain (timeline hover split). */
  onAddTodo?: (cid: string) => void
  /** Smart suggestion click → prefill create-todo. */
  onPickSuggestion?: (cid: string, item: TodoSuggestionItem) => void
  collectionId?: string
  suggestRefreshKey?: number
  /** Last branch node hover: merge / end chain */
  onMergeBranch?: () => void
  onCreateChain: (pCid: string, pNid: string) => void
  /** Meeting anchor hover: create todos from section candidates */
  onCreateMeetingTodos?: (node: Node) => void
  groups: NodeGroup[]
  focusGroupId: FocusGroupId | null
  isNodeInFocus: (n: Node) => boolean
  selId: string | null
  messageMode?: boolean
  isMsgFocusNode?: (nodeId: string) => boolean
  isMsgDimNode?: (nodeId: string) => boolean
  /** Main-chain layout B: visual column per node index (insert empty slots). */
  visualColumns?: number[]
  slotWidth?: number
  /** Total equal-width columns (shared with branch lanes for alignment). */
  gridCols?: number
  /** When true, suppress sortable CSS transforms (list order is the source of truth). */
  dragActive?: boolean
  /** Draw continuous horizontal baseline through equal-grid columns (main + branch). */
  showBaseline?: boolean
  /**
   * Optional end column (0-based) for baseline — used by closed branches to stretch
   * the line under the merge node even when events are left-packed.
   */
  baselineEndCol?: number
}

function ChainStartDrop({ chainId }: { chainId: string }) {
  const { setNodeRef, isOver } = useDroppable({ id: startDropId(chainId) })
  return (
    <div
      ref={setNodeRef}
      className={cn(
        "pm-timeline-drop-hint flex items-center justify-center self-center w-4 h-14 rounded transition-colors shrink-0 -mr-1",
        isOver && "is-over",
      )}
      title="Drop at start of chain"
    >
      <div className="pm-timeline-drop-bar" />
    </div>
  )
}

function ChainEndDrop({ chainId }: { chainId: string }) {
  const { setNodeRef, isOver } = useDroppable({ id: endDropId(chainId) })
  return (
    <div
      ref={setNodeRef}
      className={cn(
        "pm-timeline-drop-hint flex items-center justify-center self-center mx-0.5 w-6 h-14 rounded transition-colors shrink-0",
        isOver && "is-over",
      )}
      title="Drop at end of chain"
    >
      {/* Only show a drop hint when hovering — idle vertical bar looked like a broken connector */}
      <div className="pm-timeline-drop-bar" />
    </div>
  )
}

/**
 * End-of-chain add: dashed square (w-10), same style as empty-branch + /
 * main trailing add — not the inter-node insert circle.
 * Droppable for append-at-end; centered on the horizontal baseline.
 */
function ChainEndAddButton({
  chainId,
  onAdd,
  onAddTodo,
  collectionId,
  suggestRefreshKey,
  onPickSuggestion,
}: {
  chainId: string
  onAdd: () => void
  onAddTodo?: () => void
  collectionId?: string
  suggestRefreshKey?: number
  onPickSuggestion?: (item: TodoSuggestionItem) => void
}) {
  const { setNodeRef, isOver } = useDroppable({ id: endDropId(chainId) })
  return (
    <div
      ref={setNodeRef}
      className={cn(
        "flex flex-row items-center overflow-visible rounded-[var(--pm-r-sm)]",
        isOver && "ring-2 ring-[color-mix(in_srgb,var(--pm-green)_22%,transparent)]",
      )}
    >
      <AddNodeTodoSplit
        onAddNode={onAdd}
        onAddTodo={onAddTodo ?? onAdd}
      />
      {collectionId && onPickSuggestion ? (
        <TodoSuggestBubble
          collectionId={collectionId}
          chainId={chainId}
          refreshKey={suggestRefreshKey}
          onPick={onPickSuggestion}
        />
      ) : null}
    </div>
  )
}

export function ChainRow({
  chainData,
  chainId,
  isBranch,
  isCompleted = false,
  onNodeClick,
  onAddNode,
  onAddTodo,
  onPickSuggestion,
  collectionId,
  suggestRefreshKey = 0,
  onMergeBranch,
  onCreateChain,
  onCreateMeetingTodos,
  groups,
  focusGroupId,
  isNodeInFocus,
  selId,
  messageMode = false,
  isMsgFocusNode,
  isMsgDimNode,
  visualColumns,
  slotWidth = 232,
  gridCols: gridColsProp,
  dragActive = false,
  showBaseline,
  baselineEndCol,
}: ChainRowProps) {
  // Display order is owned by parent (live drag preview). Sortable ids MUST match render order.
  const ids = chainData.map((n) => n.node_id)
  const cols =
    gridColsProp ??
    Math.max(
      1,
      ...(visualColumns ?? []).map((c) => c + 1),
      chainData.length,
    )

  // Empty chain
  if (chainData.length === 0) {
    if (isCompleted || messageMode) {
      return <div className="w-48 h-12 py-2" aria-hidden />
    }
    // Branch empty is rendered by parent as compact + under parent node.
    // Main empty: dashed node-sized card inviting first timeline node.
    if (isBranch) {
      return (
        <div
          className="flex justify-center items-center py-2"
          style={{ width: slotWidth }}
        >
          <AddNodeTodoSplit
            onAddNode={() => onAddNode(chainId, 0)}
            onAddTodo={() => onAddTodo?.(chainId)}
            titleNode="Add first node to branch"
          />
        </div>
      )
    }
    return (
      <div className="flex items-center py-2 gap-2">
        <ChainEndDrop chainId={chainId} />
        <button
          type="button"
          data-branch-target
          data-empty-chain-placeholder
          className={cn(
            "pm-timeline-add-slot w-48 h-12 shrink-0 gap-2 px-3",
          )}
          onClick={() => onAddNode(chainId, 0)}
          title="Add first node to timeline"
        >
          <Plus className="h-4 w-4 shrink-0" />
          <span className="pm-label truncate normal-case tracking-normal">
            Add first node
          </span>
        </button>
        <AddNodeTodoSplit
          onAddNode={() => onAddNode(chainId, 0)}
          onAddTodo={() => onAddTodo?.(chainId)}
        />
      </div>
    )
  }

  // Equal-width column layout (main + branch share SLOT_W) — no flex spacer drift
  const useEqualGrid = !!visualColumns && visualColumns.length === chainData.length
  // Main always gets baseline; branch needs explicit showBaseline (equal-grid path sets isFirst)
  const drawBaseline = useEqualGrid && (showBaseline || !isBranch)

  // Card is w-48 (192px), centered in SLOT_W.
  // Trailing add: dashed square + (not circle), short connector, center-aligned on baseline.
  const CARD_W = 192
  const END_PLUS_SIZE = 40 // w-10 dashed square
  /** Gap between last card's right edge and the + button's left edge. */
  const END_PLUS_GAP = 16
  /** Distance from last card center → + button center. */
  const END_PLUS_CENTER_OFFSET = CARD_W / 2 + END_PLUS_GAP + END_PLUS_SIZE / 2

  // Baseline must follow **visual columns** (Layout-B spacers), not just node count.
  let baselineLeft = slotWidth / 2
  let baselineWidth = Math.max(0, (Math.max(1, chainData.length) - 1) * slotWidth)
  // Absolute x of trailing + **center**, for open chains
  let plusCenterX: number | null = null
  if (useEqualGrid && visualColumns && visualColumns.length > 0) {
    const firstCol = Math.min(...visualColumns)
    const lastNodeCol = Math.max(...visualColumns)
    let endX = lastNodeCol * slotWidth + slotWidth / 2 // center of last node
    if (baselineEndCol != null) {
      endX = Math.max(endX, baselineEndCol * slotWidth + slotWidth / 2)
    }
    // Open chain: short baseline ending at dashed-square + center
    if (!isCompleted && !messageMode) {
      plusCenterX = lastNodeCol * slotWidth + slotWidth / 2 + END_PLUS_CENTER_OFFSET
      endX = Math.max(endX, plusCenterX)
    }
    baselineLeft = firstCol * slotWidth + slotWidth / 2
    baselineWidth = Math.max(0, endX - baselineLeft)
  }

  // Grid width: node columns only; open + overflows a bit to the right
  const rowWidth =
    useEqualGrid && plusCenterX != null
      ? Math.max(cols * slotWidth, plusCenterX + END_PLUS_SIZE / 2 + 8)
      : useEqualGrid
        ? cols * slotWidth
        : undefined

  return (
    <SortableContext items={ids} strategy={horizontalListSortingStrategy} id={chainId}>
      <div
        className="relative min-w-max overflow-visible"
        style={
          useEqualGrid
            ? {
                display: "grid",
                gridTemplateColumns: `repeat(${cols}, ${slotWidth}px)`,
                width: rowWidth,
                alignItems: "center",
              }
            : undefined
        }
      >
        {/* Continuous baseline through visual columns (spans Layout-B gaps) */}
        {drawBaseline && (
          <div
            className="absolute top-1/2 h-px -translate-y-1/2 pointer-events-none z-0 bg-[color-mix(in_srgb,var(--pm-ink)_12%,transparent)]"
            style={{
              left: baselineLeft,
              width: baselineWidth,
            }}
            aria-hidden
          />
        )}

        {/* Equal-grid: insert-on-connector hotspots between consecutive nodes
            (SW left-connector is disabled so cards stay centered in columns). */}
        {useEqualGrid &&
          !messageMode &&
          !isCompleted &&
          visualColumns &&
          chainData.map((node, idx) => {
            if (idx >= chainData.length - 1) return null
            const colA = visualColumns[idx] ?? idx
            const colB = visualColumns[idx + 1] ?? idx + 1
            // Midpoint between the two card centers
            const left =
              ((colA + colB) / 2) * slotWidth + slotWidth / 2
            // Hit zone spans most of the gap between card edges (card ≈ 192px in 232 slot)
            const gapPx = Math.max(24, (colB - colA) * slotWidth - 192)
            return (
              <div
                key={`insert-between-${node.node_id}`}
                className="absolute top-1/2 z-20 -translate-x-1/2 -translate-y-1/2 flex items-center justify-center h-12 group/conn pointer-events-auto"
                style={{ left, width: Math.min(gapPx, slotWidth) }}
              >
                <button
                  type="button"
                  className="flex items-center justify-center w-full h-full opacity-0 group-hover/conn:opacity-100 focus-visible:opacity-100 transition-opacity"
                  onClick={(e) => {
                    e.stopPropagation()
                    onAddNode(chainId, node.order)
                  }}
                  title="Insert node after"
                >
                  <div className="pm-timeline-add-slot w-5 h-5 rounded-full">
                    <Plus className="h-3 w-3" />
                  </div>
                </button>
              </div>
            )
          })}

        {/* Drop zones only while chain is still editable */}
        {!messageMode && !isCompleted && !useEqualGrid && (
          <ChainStartDrop chainId={chainId} />
        )}
        {!messageMode && !isCompleted && useEqualGrid && (
          <div
            className="relative z-[1] flex justify-start"
            style={{ gridColumnStart: 1, gridRowStart: 1 }}
          >
            <ChainStartDrop chainId={chainId} />
          </div>
        )}

        {chainData.map((node, idx) => {
          const isLast = idx === chainData.length - 1
          const msgFocus = !!isMsgFocusNode?.(node.node_id)
          const msgDim = !!isMsgDimNode?.(node.node_id)
          const col = useEqualGrid ? (visualColumns![idx] ?? idx) : idx

          const card = (
            <SW
              node={node}
              isFirst={useEqualGrid ? true : idx === 0}
              showInsertBtn={
                !messageMode &&
                !isCompleted &&
                !useEqualGrid &&
                idx < chainData.length - 1
              }
              onInsert={() => onAddNode(chainId, node.order)}
              disableDrag={messageMode}
              dragActive={dragActive}
            >
              {(dragging) => (
                <div className="flex flex-col items-center gap-0">
                  <SNC
                    node={node}
                    groupSource={groupSourceForNode(groups, node.group_id)}
                    isSelected={selId === node.node_id || (messageMode && msgFocus)}
                    isCompleted={isCompleted || node.node_type === "end"}
                    isGroupFocus={
                      (!!focusGroupId && isNodeInFocus(node)) ||
                      (messageMode && msgFocus)
                    }
                    isGroupDim={
                      (!!focusGroupId && !isNodeInFocus(node)) ||
                      (messageMode && msgDim)
                    }
                    onClick={() => onNodeClick(node.node_id)}
                    onCreateChain={
                      !messageMode &&
                      !isBranch &&
                      !isCompleted &&
                      node.node_type !== "end"
                        ? () => onCreateChain(chainId, node.node_id)
                        : undefined
                    }
                    onMergeBranch={
                      !messageMode &&
                      isBranch &&
                      !isCompleted &&
                      isLast &&
                      onMergeBranch
                        ? onMergeBranch
                        : undefined
                    }
                    onCreateMeetingTodos={
                      !messageMode && onCreateMeetingTodos
                        ? () => onCreateMeetingTodos(node)
                        : undefined
                    }
                    showDragGrip={!messageMode}
                    isDragging={dragging}
                  />
                </div>
              )}
            </SW>
          )

          if (useEqualGrid) {
            return (
              <div
                key={node.node_id}
                className="relative z-[1] flex items-center justify-center"
                style={{ gridColumnStart: col + 1, gridRowStart: 1 }}
              >
                {card}
              </div>
            )
          }

          return (
            <div key={node.node_id} className="flex flex-row items-center gap-0">
              <SW
                node={node}
                isFirst={idx === 0}
                showInsertBtn={
                  !messageMode && !isCompleted && idx < chainData.length - 1
                }
                onInsert={() => onAddNode(chainId, node.order)}
                disableDrag={messageMode}
                dragActive={dragActive}
              >
                {(dragging) => (
                  <div className="flex flex-col items-center gap-0">
                    <SNC
                      node={node}
                      groupSource={groupSourceForNode(groups, node.group_id)}
                      isSelected={
                        selId === node.node_id || (messageMode && msgFocus)
                      }
                      isCompleted={isCompleted || node.node_type === "end"}
                      isGroupFocus={
                        (!!focusGroupId && isNodeInFocus(node)) ||
                        (messageMode && msgFocus)
                      }
                      isGroupDim={
                        (!!focusGroupId && !isNodeInFocus(node)) ||
                        (messageMode && msgDim)
                      }
                      onClick={() => onNodeClick(node.node_id)}
                      onCreateChain={
                        !messageMode &&
                        !isBranch &&
                        !isCompleted &&
                        node.node_type !== "end"
                          ? () => onCreateChain(chainId, node.node_id)
                          : undefined
                      }
                      onCreateMeetingTodos={
                        !messageMode && onCreateMeetingTodos
                          ? () => onCreateMeetingTodos(node)
                          : undefined
                      }
                      onMergeBranch={
                        !messageMode &&
                        isBranch &&
                        !isCompleted &&
                        isLast &&
                        onMergeBranch
                          ? onMergeBranch
                          : undefined
                      }
                      showDragGrip={!messageMode}
                      isDragging={dragging}
                    />
                  </div>
                )}
              </SW>
            </div>
          )
        })}

        {/* End drop + add only while chain is open */}
        {!messageMode && !isCompleted && (
          useEqualGrid && plusCenterX != null ? (
            // Anchor so the 40px Node + center sits on baseline; Todo expands right
            // without shifting the + (no translate-x-1/2 on the whole group).
            <div
              className="absolute z-10 top-1/2 -translate-y-1/2 overflow-visible"
              style={{ left: plusCenterX - 20 }}
            >
              <ChainEndAddButton
                chainId={chainId}
                onAdd={() => {
                  const l = chainData[chainData.length - 1]
                  onAddNode(chainId, l ? l.order : 0)
                }}
                onAddTodo={() => onAddTodo?.(chainId)}
                collectionId={collectionId}
                suggestRefreshKey={suggestRefreshKey}
                onPickSuggestion={
                  onPickSuggestion
                    ? (item) => onPickSuggestion(chainId, item)
                    : undefined
                }
              />
            </div>
          ) : useEqualGrid ? (
            <div
              className="relative z-10 flex items-center justify-center"
              style={{
                gridColumnStart:
                  (visualColumns![chainData.length - 1] ?? chainData.length - 1) + 2,
                gridRowStart: 1,
              }}
            >
              <ChainEndAddButton
                chainId={chainId}
                onAdd={() => {
                  const l = chainData[chainData.length - 1]
                  onAddNode(chainId, l ? l.order : 0)
                }}
                onAddTodo={() => onAddTodo?.(chainId)}
                collectionId={collectionId}
                suggestRefreshKey={suggestRefreshKey}
                onPickSuggestion={
                  onPickSuggestion
                    ? (item) => onPickSuggestion(chainId, item)
                    : undefined
                }
              />
            </div>
          ) : (
            <>
              <ChainEndDrop chainId={chainId} />
              <div className="flex flex-row items-center gap-0 shrink-0 relative z-10">
                <div className="flex items-center justify-center self-center mx-1">
                  <div className="w-8 h-px bg-[color-mix(in_srgb,var(--pm-ink)_12%,transparent)]" />
                </div>
                <AddNodeTodoSplit
                  onAddNode={() => {
                    const l = chainData[chainData.length - 1]
                    onAddNode(chainId, l ? l.order : 0)
                  }}
                  onAddTodo={() => onAddTodo?.(chainId)}
                />
                {collectionId && onPickSuggestion ? (
                  <TodoSuggestBubble
                    collectionId={collectionId}
                    chainId={chainId}
                    refreshKey={suggestRefreshKey}
                    onPick={(item) => onPickSuggestion(chainId, item)}
                  />
                ) : null}
              </div>
            </>
          )
        )}
      </div>
    </SortableContext>
  )
}

function SW({
  node,
  isFirst,
  showInsertBtn,
  onInsert,
  children,
  disableDrag = false,
  dragActive = false,
}: {
  node: Node
  isFirst: boolean
  showInsertBtn?: boolean
  onInsert?: () => void
  children: ReactNode | ((isDragging: boolean) => ReactNode)
  disableDrag?: boolean
  /** Any drag in progress — kill CSS transforms so list order alone drives layout. */
  dragActive?: boolean
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: node.node_id,
    disabled: disableDrag,
    animateLayoutChanges: () => false,
  })

  // Strip onPointerDown from listeners and compose once (avoids double-fire / post-drag crash)
  const {
    onPointerDown: dndPointerDown,
    ...restListeners
  } = (listeners ?? {}) as {
    onPointerDown?: (e: PointerEvent) => void
    [key: string]: unknown
  }

  // List order drives layout; kill strategy transforms while any drag is active.
  const styleTransform =
    dragActive || isDragging ? undefined : CSS.Transform.toString(transform)

  return (
    <div
      ref={setNodeRef}
      data-sw-dragging={isDragging ? "true" : "false"}
      data-sortable-id={node.node_id}
      style={{
        transform: styleTransform,
        transition: dragActive
          ? "none"
          : transition,
        touchAction: disableDrag ? undefined : "none",
      }}
      className={cn(
        "flex flex-row items-center gap-0",
        !disableDrag && "touch-none",
      )}
      {...(disableDrag ? {} : attributes)}
      {...(disableDrag ? {} : restListeners)}
      onPointerDown={(e) => {
        e.stopPropagation()
        if (!disableDrag) dndPointerDown?.(e)
      }}
    >
      {!isFirst && (
        <div className="flex items-center justify-center self-center mx-1 w-8 h-8 relative group/conn">
          <div className="w-8 h-px bg-[color-mix(in_srgb,var(--pm-ink)_12%,transparent)]" />
          {showInsertBtn && onInsert && (
            <button
              type="button"
              className="absolute inset-0 flex items-center justify-center opacity-0 group-hover/conn:opacity-100 transition-opacity"
              onClick={(e) => {
                e.stopPropagation()
                onInsert()
              }}
              title="Insert node after"
            >
              <div className="pm-timeline-add-slot w-4 h-4 rounded-full">
                <Plus className="h-2.5 w-2.5" />
              </div>
            </button>
          )}
        </div>
      )}
      {/* Placeholder keeps the slot (covers baseline); DragOverlay is the floating card */}
      {isDragging ? (
        <div
          className="pm-timeline-add-slot w-48 h-12 shrink-0 border-[color-mix(in_srgb,var(--pm-green)_40%,transparent)] bg-[var(--pm-green-wash)]"
          aria-hidden
        />
      ) : typeof children === "function" ? (
        children(false)
      ) : (
        children
      )}
    </div>
  )
}
