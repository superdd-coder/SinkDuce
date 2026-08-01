import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react"
import { Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import type { Chain, Node, NodeGroup } from "@/types/file-mgmt"
import {
  getNodeDetail,
  listChains,
  listGroups,
  listNodes,
} from "@/api/file-mgmt"
import { GroupIconView, groupFromList } from "./timeline-view/group-icons"

/** Compact slot pitch — same geometry ratios as Timeline, smaller scale. */
const SLOT_W = 100
const CARD_W = 88
const LANE_H = 36
const CONN_GAP = 22
const PAD_X = 8
const PAD_Y = 10

type Bundle = { chain: Chain; nodes: Node[] }

type BranchInfo = {
  chain: Chain
  events: Node[]
  pidx: number
  mergeIdx: number
  done: boolean
  level: number
  spanEnd: number
}

/**
 * Same algorithm as TimelineView.assignBranchLevels:
 * rightmost overlapping branch stays innermost (level 0 = below main).
 */
function assignBranchLevels(list: BranchInfo[]): void {
  list.sort((a, b) => a.pidx - b.pidx || a.spanEnd - b.spanEnd)
  const overlaps = (a: BranchInfo, b: BranchInfo) =>
    Math.max(a.pidx, b.pidx) <= Math.min(a.spanEnd, b.spanEnd)

  const grps: BranchInfo[][] = []
  for (const bi of list) {
    let placed = false
    for (const g of grps) {
      if (g.some((ex) => overlaps(bi, ex))) {
        g.push(bi)
        placed = true
        break
      }
    }
    if (!placed) grps.push([bi])
  }
  for (let i = 0; i < grps.length; i++) {
    for (let j = i + 1; j < grps.length; j++) {
      if (grps[i].some((a) => grps[j].some((b) => overlaps(a, b)))) {
        grps[i].push(...grps[j])
        grps.splice(j, 1)
        j = i
      }
    }
  }

  for (const g of grps) {
    // Rightmost first → innermost
    g.sort((a, b) => b.pidx - a.pidx || b.spanEnd - a.spanEnd)
    g.forEach((bi, i) => {
      bi.level = i % 2 === 1 ? -Math.ceil(i / 2) : Math.floor(i / 2)
    })
  }
}

/** Layout-B: expand main columns when a closed branch needs more room. */
function computeMainVisualColumns(
  mainCount: number,
  branches: {
    pidx: number
    mergeIdx: number
    eventCount: number
    done: boolean
  }[]
): number[] {
  if (mainCount <= 0) return []
  const col = Array.from({ length: mainCount }, (_, i) => i)
  for (let g = 0; g < 32; g++) {
    let changed = false
    for (const b of branches) {
      if (!b.done || b.pidx < 0 || b.mergeIdx <= b.pidx || b.mergeIdx >= mainCount)
        continue
      const need = Math.max(1, Math.min(24, b.eventCount))
      const minMerge = col[b.pidx] + need - 1
      if (col[b.mergeIdx] < minMerge) {
        const d = minMerge - col[b.mergeIdx]
        for (let j = b.mergeIdx; j < mainCount; j++) col[j] += d
        changed = true
      }
    }
    if (!changed) break
  }
  return col
}

function nodeLabel(node: Node): string {
  const t = node.title?.trim()
  if (t) return t
  // Match NodeCard fallback for empty titles
  if (node.node_type === "start") return "Start"
  if (node.node_type === "end") return "End"
  return "Untitled"
}

function NodeChip({
  node,
  groups,
  active,
  dimmed,
  onClick,
  style,
}: {
  node: Node
  groups: NodeGroup[]
  active: boolean
  /** Non-focused nodes are visually weakened */
  dimmed: boolean
  onClick: () => void
  style?: CSSProperties
}) {
  const label = nodeLabel(node)
  const src = groupFromList(groups, node.group_id)

  return (
    <button
      type="button"
      title={label}
      onClick={onClick}
      style={style}
      className={cn(
        // Solid fill so connector lines never show through the chip
        "absolute flex items-center gap-1 rounded-md border px-1.5 py-1 text-left transition-all bg-background shadow-sm",
        active
          ? "border-[var(--ze-green,#1A5E3D)] bg-[var(--ze-green,#1A5E3D)]/15 ring-2 ring-[var(--ze-green,#1A5E3D)]/50 z-[3] opacity-100"
          : dimmed
            ? "border-border/50 text-muted-foreground/70 opacity-[0.38] z-[1] hover:opacity-70 hover:bg-muted/30"
            : "border-border/70 hover:bg-muted/40 z-[1]"
      )}
    >
      <GroupIconView
        source={src}
        className={cn("h-3 w-3 shrink-0", dimmed && !active && "opacity-70")}
      />
      <span className="text-[10px] truncate font-medium leading-tight">
        {label}
      </span>
    </button>
  )
}

/**
 * Timeline-faithful mini graph: main spine + branches forking under parent nodes.
 * Absolute layout + SVG connectors so the compact dialog view stays aligned.
 */
export function MiniChainGraph({
  collectionId,
  nodeId,
  onNodeClick,
  className,
}: {
  collectionId: string
  nodeId: string
  onNodeClick?: (nodeId: string, chainId: string) => void
  className?: string
}) {
  const [bundles, setBundles] = useState<Bundle[]>([])
  const [groups, setGroups] = useState<NodeGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const scrollerRef = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    ;(async () => {
      try {
        await getNodeDetail(collectionId, nodeId)
        if (cancelled) return
        const [chains, gs] = await Promise.all([
          listChains(collectionId),
          listGroups(collectionId),
        ])
        if (cancelled) return
        const loaded = await Promise.all(
          chains.map(async (c) => {
            try {
              const ns = await listNodes(collectionId, c.chain_id)
              return {
                chain: c,
                nodes: [...ns].sort((a, b) => a.order - b.order),
              }
            } catch {
              return { chain: c, nodes: [] as Node[] }
            }
          })
        )
        if (!cancelled) {
          setBundles(loaded)
          setGroups(gs)
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
          setBundles([])
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [collectionId, nodeId])

  const layout = useMemo(() => {
    const mainB = bundles.find((b) => b.chain.is_main)
    if (!mainB) return null

    // Main chain: full ordered list (same as Timeline)
    const mns = mainB.nodes
    if (mns.length === 0) return null

    const branches: BranchInfo[] = []
    for (const b of bundles) {
      if (b.chain.is_main) continue
      // Branch row: event nodes only
      const events = b.nodes.filter((n) => n.node_type === "event")
      if (events.length === 0) continue

      const pidx = mns.findIndex((n) => n.node_id === b.chain.parent_node_id)
      if (pidx < 0) continue
      const mergeIdx = b.chain.merge_node_id
        ? mns.findIndex((n) => n.node_id === b.chain.merge_node_id)
        : -1
      const done = !!(b.chain.has_end_node || b.chain.merge_node_id)
      const spanLen = Math.max(1, events.length)
      const spanEnd =
        done && mergeIdx >= 0
          ? Math.max(pidx + spanLen - 1, mergeIdx)
          : pidx + spanLen - 1
      branches.push({
        chain: b.chain,
        events,
        pidx,
        mergeIdx,
        done,
        level: 0,
        spanEnd,
      })
    }
    assignBranchLevels(branches)

    const mainNodeCol = computeMainVisualColumns(
      mns.length,
      branches.map((bi) => ({
        pidx: bi.pidx,
        mergeIdx: bi.mergeIdx,
        eventCount: bi.events.length,
        done: bi.done,
      }))
    )

    let maxCol = mns.length > 0 ? (mainNodeCol[mns.length - 1] ?? 0) : 0
    for (const bi of branches) {
      const pCol = mainNodeCol[bi.pidx] ?? bi.pidx
      maxCol = Math.max(maxCol, pCol + Math.max(1, bi.events.length) - 1)
      if (bi.done && bi.mergeIdx >= 0) {
        maxCol = Math.max(maxCol, mainNodeCol[bi.mergeIdx] ?? bi.mergeIdx)
      }
    }
    const gridCols = maxCol + 1

    const aboveLevels = [
      ...new Set(branches.map((b) => b.level).filter((l) => l < 0)),
    ].sort((a, b) => a - b) // more negative first (outer)
    const belowLevels = [
      ...new Set(branches.map((b) => b.level).filter((l) => l >= 0)),
    ].sort((a, b) => a - b) // 0 first (inner)

    // Pixel geometry — single coordinate space for nodes + SVG connectors
    const laneH = LANE_H + CONN_GAP
    const aboveH = aboveLevels.length * laneH
    const mainY = PAD_Y + aboveH
    // Main row center Y
    const mainCenterY = mainY + LANE_H / 2

    const levelY = (level: number): number => {
      if (level < 0) {
        const idx = aboveLevels.indexOf(level)
        // aboveLevels sorted ascending (e.g. -2, -1) → outer first at top
        return PAD_Y + idx * laneH + LANE_H / 2
      }
      const idx = belowLevels.indexOf(level)
      return mainY + LANE_H + CONN_GAP + idx * laneH + LANE_H / 2
    }

    const colCenterX = (col: number) => PAD_X + col * SLOT_W + SLOT_W / 2

    type Placed = {
      node: Node
      chainId: string
      x: number // left of chip
      y: number // top of chip
      cx: number
      cy: number
    }
    const placed: Placed[] = []

    for (let i = 0; i < mns.length; i++) {
      const col = mainNodeCol[i] ?? i
      const cx = colCenterX(col)
      const cy = mainCenterY
      placed.push({
        node: mns[i],
        chainId: mainB.chain.chain_id,
        x: cx - CARD_W / 2,
        y: cy - LANE_H / 2 + 2,
        cx,
        cy,
      })
    }

    for (const bi of branches) {
      const pCol = mainNodeCol[bi.pidx] ?? bi.pidx
      const cy = levelY(bi.level)
      bi.events.forEach((ev, i) => {
        const col = pCol + i
        const cx = colCenterX(col)
        placed.push({
          node: ev,
          chainId: bi.chain.chain_id,
          x: cx - CARD_W / 2,
          y: cy - LANE_H / 2 + 2,
          cx,
          cy,
        })
      })
    }

    // SVG paths: connectors stop at card edges so they never cross through chips
    const paths: string[] = []
    const halfCard = CARD_W / 2
    const halfLane = LANE_H / 2 - 2 // chip half-height

    // Main baseline: only gaps between adjacent main chips (not through card bodies)
    for (let i = 0; i < mns.length - 1; i++) {
      const c0 = mainNodeCol[i] ?? i
      const c1 = mainNodeCol[i + 1] ?? i + 1
      const xL = colCenterX(c0) + halfCard
      const xR = colCenterX(c1) - halfCard
      if (xR > xL) {
        paths.push(`M ${xL} ${mainCenterY} L ${xR} ${mainCenterY}`)
      }
    }

    for (const bi of branches) {
      const pCol = mainNodeCol[bi.pidx] ?? bi.pidx
      const pCx = colCenterX(pCol)
      const bCy = levelY(bi.level)
      const n = bi.events.length
      const below = bi.level >= 0

      // Vertical stem: parent card edge → first branch card edge (gap only)
      const parentEdgeY = below
        ? mainCenterY + halfLane
        : mainCenterY - halfLane
      const branchEdgeY = below ? bCy - halfLane : bCy + halfLane
      paths.push(`M ${pCx} ${parentEdgeY} L ${pCx} ${branchEdgeY}`)

      // Branch horizontal baseline: gaps between consecutive branch chips
      for (let i = 0; i < n - 1; i++) {
        const xL = colCenterX(pCol + i) + halfCard
        const xR = colCenterX(pCol + i + 1) - halfCard
        if (xR > xL) {
          paths.push(`M ${xL} ${bCy} L ${xR} ${bCy}`)
        }
      }

      // Closed: L-return in the connector gap — horizontal at mid-gap, then down/up to merge
      // (never runs a long line through the main-row focused node)
      if (bi.done && bi.mergeIdx >= 0) {
        const mCol = mainNodeCol[bi.mergeIdx] ?? bi.mergeIdx
        const mCx = colCenterX(mCol)
        const lastEvCol = pCol + Math.max(0, n - 1)
        const lastCx = colCenterX(lastEvCol)
        // Mid-gap Y between main spine and branch lane
        const gapY = below
          ? (mainCenterY + halfLane + bCy - halfLane) / 2
          : (mainCenterY - halfLane + bCy + halfLane) / 2

        // Drop/rise from last event to gap corridor
        paths.push(
          `M ${lastCx} ${branchEdgeY} L ${lastCx} ${gapY}`
        )
        // Corridor along gap to merge x
        if (Math.abs(mCx - lastCx) > 0.5) {
          paths.push(`M ${lastCx} ${gapY} L ${mCx} ${gapY}`)
        }
        // Attach to merge card edge
        const mergeEdgeY = below
          ? mainCenterY + halfLane
          : mainCenterY - halfLane
        paths.push(`M ${mCx} ${gapY} L ${mCx} ${mergeEdgeY}`)
      }
    }

    const contentW = PAD_X * 2 + gridCols * SLOT_W
    const contentH =
      PAD_Y * 2 +
      aboveLevels.length * laneH +
      LANE_H +
      (belowLevels.length > 0 ? belowLevels.length * laneH : 0) +
      // if only above, still need a little bottom pad after main
      (belowLevels.length === 0 && aboveLevels.length > 0 ? 4 : 0)

    return {
      mainChainId: mainB.chain.chain_id,
      placed,
      paths,
      contentW,
      contentH,
    }
  }, [bundles])

  useEffect(() => {
    const scroller = scrollerRef.current
    if (!scroller || !layout) return
    const fit = () => {
      const availW = scroller.clientWidth - 8
      const availH = scroller.clientHeight - 8
      if (availW <= 0 || availH <= 0 || layout.contentW <= 0 || layout.contentH <= 0) {
        setScale(1)
        return
      }
      setScale(
        Math.min(1, (availW / layout.contentW) * 0.98, (availH / layout.contentH) * 0.98)
      )
    }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(scroller)
    return () => ro.disconnect()
  }, [layout])

  if (loading) {
    return (
      <div
        className={cn(
          "flex items-center justify-center h-full text-muted-foreground",
          className
        )}
      >
        <Loader2 className="h-4 w-4 animate-spin" />
      </div>
    )
  }
  if (error || !layout) {
    return (
      <div
        className={cn(
          "flex items-center justify-center h-full text-xs text-muted-foreground/60",
          className
        )}
      >
        {error || "No timeline"}
      </div>
    )
  }

  const { placed, paths, contentW, contentH } = layout

  return (
    <div
      ref={scrollerRef}
      className={cn(
        "h-full w-full overflow-hidden flex items-center justify-center",
        className
      )}
    >
      <div
        style={{
          width: contentW * scale,
          height: contentH * scale,
          position: "relative",
          flexShrink: 0,
        }}
      >
        <div
          style={{
            width: contentW,
            height: contentH,
            transform: `scale(${scale})`,
            transformOrigin: "top left",
            position: "relative",
          }}
        >
          {/* Connectors always under chips (z-0); chips opaque so lines never cross faces */}
          <svg
            width={contentW}
            height={contentH}
            className="absolute inset-0 pointer-events-none z-0 text-muted-foreground/25"
            aria-hidden
          >
            {paths.map((d, i) => (
              <path
                key={i}
                d={d}
                fill="none"
                stroke="currentColor"
                strokeWidth={1}
              />
            ))}
          </svg>

          {placed.map((p) => {
            const active = p.node.node_id === nodeId
            return (
              <NodeChip
                key={p.node.node_id}
                node={p.node}
                groups={groups}
                active={active}
                dimmed={!active}
                onClick={() => onNodeClick?.(p.node.node_id, p.chainId)}
                style={{
                  left: p.x,
                  top: p.y,
                  width: CARD_W,
                  height: LANE_H - 4,
                }}
              />
            )
          })}
        </div>
      </div>
    </div>
  )
}
