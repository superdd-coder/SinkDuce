import { useState, useEffect, useLayoutEffect, useCallback, useMemo, useRef } from 'react'
import { GitBranch, MessageSquareText, Plus, RotateCcw, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { Chain, Node, NodeGroup } from '@/types/file-mgmt'
import { listChains,listNodes,reorderNode,listGroups,reopenChain,createNode,updateNode,createChain,deleteChain } from "@/api/file-mgmt"
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  useDroppable,
  DragOverlay,
  type CollisionDetection,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import { SortableContext, arrayMove, horizontalListSortingStrategy, useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { NodeCard, groupSourceForNode } from './node-card'
import { NodeDetailSidebar } from './node-detail-sidebar'
import { AddNodeDialog } from './add-node-dialog'
import { CreateChainDialog } from './create-chain-dialog'
import { EndChainDialog } from './end-chain-dialog'
import { GroupsMenu, type FocusGroupId } from './groups-menu'
import { UNCATEGORIZED_ID } from './group-icons'
import {
  MessageStreamSidebar,
  collectChainSubtree,
  type MessageFocus,
} from './message-stream-sidebar'

/** Droppable id for "append at end of chain" zone. */
const endDropId = (chainId: string) => `__end__:${chainId}`
const parseEndDropId = (id: string): string | null =>
  id.startsWith('__end__:') ? id.slice('__end__:'.length) : null
/** Droppable id for "insert at start of chain" zone (before first node). */
const startDropId = (chainId: string) => `__start__:${chainId}`
const parseStartDropId = (id: string): string | null =>
  id.startsWith('__start__:') ? id.slice('__start__:'.length) : null

/** Visible/sortable nodes on a chain row (branch hides start/end anchors). */
function visibleNodesForChain(chain: Chain, nodes: Node[]): Node[] {
  const sorted = [...nodes].sort((a, b) => a.order - b.order)
  if (chain.is_main) return sorted
  // Branch row only shows event nodes; start/end stay as hidden anchors in DB
  return sorted.filter(n => n.node_type === 'event')
}

/**
 * Given the desired visible id sequence after a drag, compute the order value
 * the moved node should receive for `reorder_node`.
 *
 * Uses the **current order at the target index** (not min+idx), so the backend
 * shift logic lands on the correct visual slot even with sparse order values.
 */
function orderForDesiredIndex(
  chain: Chain,
  allNodes: Node[],
  desiredIds: string[],
  movedId: string,
): number {
  const visible = visibleNodesForChain(chain, allNodes)
  const sorted = [...visible].sort((a, b) => a.order - b.order)
  const byId = new Map(sorted.map(n => [n.node_id, n]))
  const idx = desiredIds.indexOf(movedId)
  if (idx < 0) return byId.get(movedId)?.order ?? 1
  if (sorted.length === 0) return idx + 1

  const targetIdx = Math.max(0, Math.min(sorted.length - 1, idx))
  return sorted[targetIdx].order
}

/**
 * Horizontal insert index from pointer X (works for first/last slots).
 * Returns the index of `activeId` in the list after re-inserting it at the
 * pointer position — i.e. how many *other* items should sit to its left.
 */
function listWithActiveAt(list: string[], activeId: string, insertIndex: number): string[] {
  const others = list.filter(id => id !== activeId)
  const i = Math.max(0, Math.min(others.length, insertIndex))
  return [...others.slice(0, i), activeId, ...others.slice(i)]
}

/**
 * Closed branch anchors live on the main chain: parent_node_id (start) must stay
 * strictly before merge_node_id. Clamp any illegal order after a drag preview.
 *
 * When `activeId` is known, move the dragged node back across the boundary so the
 * other anchor stays put (feels like a hard stop, not both jumping).
 */
function enforceBranchAnchorOrder(
  list: string[],
  chains: Chain[],
  activeId?: string | null,
): string[] {
  let result = [...list]
  for (const ch of chains) {
    // Only branch chains with a closed merge
    if (ch.parent_chain_id == null || !ch.parent_node_id || !ch.merge_node_id) continue
    const startId = ch.parent_node_id
    const mergeId = ch.merge_node_id
    let si = result.indexOf(startId)
    let mi = result.indexOf(mergeId)
    if (si < 0 || mi < 0) continue
    if (si < mi) continue

    if (activeId === mergeId) {
      // User dragged merge before start → park merge just after start
      result = result.filter(id => id !== mergeId)
      si = result.indexOf(startId)
      if (si < 0) result.push(mergeId)
      else result.splice(si + 1, 0, mergeId)
    } else {
      // Default / start dragged past merge → park start just before merge
      result = result.filter(id => id !== startId)
      mi = result.indexOf(mergeId)
      if (mi < 0) result.push(startId)
      else result.splice(mi, 0, startId)
    }
  }
  return result
}

/**
 * Branch start (parent_node_id) and merge (merge_node_id) live on the main
 * chain as geometry anchors — they must never be dropped onto a branch row.
 */
function isMainBranchAnchor(nodeId: string, chains: Chain[]): boolean {
  for (const ch of chains) {
    if (ch.is_main || ch.parent_chain_id == null) continue
    if (ch.parent_node_id === nodeId || ch.merge_node_id === nodeId) return true
  }
  return false
}

/** Nodes that may only reorder on the main chain (anchors + start/end types on main). */
function isMainLockedDrag(
  nodeId: string,
  chains: Chain[],
  nodeById: Map<string, Node>,
  homeChainId: string | null | undefined,
): boolean {
  if (isMainBranchAnchor(nodeId, chains)) return true
  const n = nodeById.get(nodeId)
  if (!n) return false
  if (n.node_type !== 'start' && n.node_type !== 'end') return false
  // Only lock when home is main (branch-local end markers are not main anchors)
  const home = chains.find(c => c.chain_id === homeChainId)
  return !!home?.is_main
}

function isBranchChainId(chainId: string, chains: Chain[]): boolean {
  const ch = chains.find(c => c.chain_id === chainId)
  return !!ch && !ch.is_main
}

function mainChainIdOf(chains: Chain[]): string | undefined {
  return chains.find(c => c.is_main)?.chain_id
}

/**
 * Insert index among `list` (excluding active) from pointer X vs DOM card centers.
 * Used when collision hits a branch row but the node is main-locked.
 */
function insertIndexFromPointerX(
  list: string[],
  activeId: string,
  pointerX: number,
): number {
  const others = list.filter(id => id !== activeId)
  let insertAt = others.length
  for (let i = 0; i < others.length; i++) {
    // node ids are hex; attribute selector is safe without CSS.escape
    const el = document.querySelector<HTMLElement>(
      `[data-sortable-id="${others[i]}"]`,
    )
    if (!el) continue
    const r = el.getBoundingClientRect()
    if (pointerX < r.left + r.width / 2) {
      insertAt = i
      break
    }
  }
  return insertAt
}

function sameIdList(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false
  return true
}

function SNC(p:{
  node:Node
  groupName?:string|null
  groupSource?:ReturnType<typeof groupSourceForNode>
  isSelected:boolean
  isCompleted:boolean
  isGroupFocus?:boolean
  isGroupDim?:boolean
  onClick:()=>void
  onCreateChain?:()=>void
  onMergeBranch?:()=>void
  showDragGrip?:boolean
  isDragging?:boolean
}){
  return <NodeCard {...p} />
}

const MIN_SCALE = 0.25
const MAX_SCALE = 2.5

interface TVP {collectionId:string}
interface CWN {chain:Chain;nodes:Node[]}

/** Horizontal pitch of one main-chain node slot (w-48 card + inter-node connector). */
const SLOT_W = 232
/** Vertical gap between main chain and nearest branch lane. */
const CONN_GAP = 24

interface BranchInfo {
  bc: Chain
  bcd: CWN
  evns: Node[]
  hasEnd: boolean
  done: boolean
  /** Index of parent (start) node on the main chain (0-based). */
  pidx: number
  /** Index of merge node on main chain when done; -1 if open. */
  mergeIdx: number
  /**
   * Inclusive end column of the branch's horizontal footprint (in main-chain slots).
   */
  spanEnd: number
  /**
   * Lane level: 0 = first row below main, -1 = first row above,
   * 1 = second row below, -2 = second row above, ...
   */
  level: number
}

/**
 * Horizontal footprint in **node-index** slots (for alternate lane conflict).
 * Events only — open-branch "+" does not expand main-chain geometry.
 */
function branchSpanSlots(eventCount: number, _done: boolean): number {
  return Math.max(1, eventCount)
}

/** Hard cap so a bad layout never mounts thousands of spacer DOM nodes. */
const MAX_BRANCH_SPAN = 24

/**
 * Layout B — **only** when a closed branch's start→merge gap on the main chain
 * is shorter than the branch event count. Then insert empty visual columns
 * between start and merge.
 *
 * Open branches do **not** push the main chain (keep classic alternate-lane layout).
 */
function computeMainVisualColumns(
  mainCount: number,
  branches: { pidx: number; mergeIdx: number; eventCount: number; done: boolean }[],
): number[] {
  if (mainCount <= 0) return []
  const nodeCol = Array.from({ length: mainCount }, (_, i) => i)
  for (let guard = 0; guard < 32; guard++) {
    let changed = false
    for (const b of branches) {
      // Only closed branches with merge after start
      if (!b.done || b.pidx < 0 || b.mergeIdx <= b.pidx || b.mergeIdx >= mainCount) {
        continue
      }
      const need = Math.min(MAX_BRANCH_SPAN, Math.max(1, b.eventCount))
      // Natural index gap already enough? (mergeIdx - pidx + 1) >= need
      // In visual cols: mergeCol >= startCol + need - 1
      const minMerge = nodeCol[b.pidx] + need - 1
      if (nodeCol[b.mergeIdx] < minMerge) {
        const delta = minMerge - nodeCol[b.mergeIdx]
        for (let j = b.mergeIdx; j < mainCount; j++) nodeCol[j] += delta
        changed = true
      }
    }
    if (!changed) break
  }
  return nodeCol
}

/** Vertical start stub + optional L-shaped return to merge node (closed loop). */
interface ConnectorGeom {
  chainId: string
  /** SVG path in layout-root coordinates */
  d: string
}

/**
 * Assign branch levels so that:
 * - No alternation unless horizontal spans overlap (conflict).
 * - Within an overlapping group, rightmost branch keeps the innermost lane (0 / below).
 * - Left-er branches get progressively outer lanes (above, then further below, ...),
 *   so their long vertical connectors are not occluded by later branches.
 */
function assignBranchLevels(binfo: BranchInfo[]): void {
  binfo.sort((a, b) => a.pidx - b.pidx || a.spanEnd - b.spanEnd)
  const overlaps = (a: BranchInfo, b: BranchInfo) =>
    Math.max(a.pidx, b.pidx) <= Math.min(a.spanEnd, b.spanEnd)

  const grps: BranchInfo[][] = []
  for (const bi of binfo) {
    let placed = false
    for (const g of grps) {
      if (g.some(ex => overlaps(bi, ex))) {
        g.push(bi)
        placed = true
        break
      }
    }
    if (!placed) grps.push([bi])
  }
  // Union pass for transitive overlaps
  for (let i = 0; i < grps.length; i++) {
    for (let j = i + 1; j < grps.length; j++) {
      if (grps[i].some(a => grps[j].some(b => overlaps(a, b)))) {
        grps[i].push(...grps[j])
        grps.splice(j, 1)
        j = i
      }
    }
  }

  for (const g of grps) {
    // Rightmost first → innermost. Left-er → outer.
    // Order: 0 (below), -1 (above), 1 (below+1), -2 (above+1), ...
    g.sort((a, b) => b.pidx - a.pidx || b.spanEnd - a.spanEnd)
    g.forEach((bi, i) => {
      bi.level = i % 2 === 1 ? -Math.ceil(i / 2) : Math.floor(i / 2)
    })
  }
}

export function TimelineView({collectionId}:TVP){
  const [chains,setChains]=useState<Chain[]>([])
  const [chainData,setChainData]=useState<Map<string,CWN>>(new Map())
  const [groups,setGroups]=useState<NodeGroup[]>([])
  const [loading,setLoading]=useState(true)
  const [selId,setSelId]=useState<string|null>(null)
  /** Keeps sidebar content mounted during close slide animation. */
  const [panelNodeId, setPanelNodeId] = useState<string | null>(null)
  /** Visual open state (one frame behind mount so enter slide plays). */
  const [panelAnimOpen, setPanelAnimOpen] = useState(false)
  const panelOpen = selId != null
  // Canvas pan/zoom
  const [scale, setScale] = useState(1)
  const [tx, setTx] = useState(0)
  const [ty, setTy] = useState(0)
  const viewSnapshot = useRef<{ scale: number; tx: number; ty: number } | null>(null)
  const [focusGroupId, setFocusGroupId] = useState<FocusGroupId | null>(null)
  const panDrag = useRef<{ x: number; y: number; tx0: number; ty0: number; moved: boolean } | null>(null)
  const scaleRef = useRef(scale)
  const txRef = useRef(tx)
  const tyRef = useRef(ty)
  scaleRef.current = scale
  txRef.current = tx
  tyRef.current = ty
  const [addOpen,setAddOpen]=useState(false)
  const [addTgt,setAddTgt]=useState<{chainId:string;afterOrder:number}|null>(null)
  const [ccOpen,setCcOpen]=useState(false)
  const [ccTgt,setCcTgt]=useState<{parentChainId:string;parentNodeId:string}|null>(null)
  const [ecOpen,setEcOpen]=useState(false)
  const [ecTgt,setEcTgt]=useState<{chainId:string;nodes:Node[];endNodeId:string}|null>(null)
  /** Empty branch pending delete confirmation (project Dialog, not window.confirm). */
  const [confirmDeleteBranchId, setConfirmDeleteBranchId] = useState<string | null>(null)
  const [drk,setDrk]=useState(0)
  const [dragN,setDragN]=useState<Node|null>(null)
  const [msgMode, setMsgMode] = useState(false)
  const [msgFocus, setMsgFocus] = useState<MessageFocus>({ kind: 'main' })

  /** @param silent — skip full-page Loading (use after drag/reorder so connectors are not unmounted). */
  const fetch=useCallback(async(opts?:{silent?:boolean})=>{
    if(!opts?.silent) setLoading(true)
    try{
      const[cl,gl]=await Promise.all([listChains(collectionId),listGroups(collectionId)])
      setChains(cl);setGroups(gl)
      const m=new Map<string,CWN>()
      for(const c of cl){try{m.set(c.chain_id,{chain:c,nodes:await listNodes(collectionId,c.chain_id)})}catch{m.set(c.chain_id,{chain:c,nodes:[]})}}
      setChainData(m)
    }catch{toast.error('Failed to load')}finally{if(!opts?.silent) setLoading(false)}
  },[collectionId])
  useEffect(()=>{fetch()},[fetch])
  useEffect(()=>{if(drk>0)fetch({silent:true})},[drk,fetch])

  const clk = useCallback((id: string) => {
    if (msgMode) {
      // Message mode: node click focuses message stream only
      let chainId = ''
      for (const [cid, cwn] of chainData) {
        if (cwn.nodes.some((n) => n.node_id === id)) {
          chainId = cid
          break
        }
      }
      setMsgFocus({ kind: 'node', nodeId: id, chainId })
      setSelId(null)
      return
    }
    setPanelNodeId(id)
    setSelId(id)
  }, [msgMode, chainData])
  const closeSidebar = useCallback(() => setSelId(null), [])

  const toggleMsgMode = useCallback(() => {
    setMsgMode((on) => {
      if (!on) {
        // entering: close node detail
        setSelId(null)
        setPanelNodeId(null)
        setMsgFocus({ kind: 'main' })
        return true
      }
      setMsgFocus({ kind: 'main' })
      return false
    })
  }, [])

  const msgScopeNodeIds = useMemo(() => {
    if (!msgMode) return null as Set<string> | null
    if (msgFocus.kind === 'main') return null
    if (msgFocus.kind === 'node') return new Set([msgFocus.nodeId])
    // Branch focus: all nodes on the branch (+ nested) AND main-chain
    // start/merge anchors (parent_node_id / merge_node_id) that close the loop.
    const ids = new Set<string>()
    for (const cid of collectChainSubtree(chains, msgFocus.chainId)) {
      const ch = chains.find(c => c.chain_id === cid)
      if (ch?.parent_node_id) ids.add(ch.parent_node_id)
      if (ch?.merge_node_id) ids.add(ch.merge_node_id)
      for (const n of chainData.get(cid)?.nodes ?? []) {
        ids.add(n.node_id)
      }
    }
    return ids
  }, [msgMode, msgFocus, chains, chainData])

  // Drive enter/exit slide: mount panel, then flip anim flag next frame
  useEffect(() => {
    if (selId) {
      setPanelNodeId(selId)
      const id = requestAnimationFrame(() => setPanelAnimOpen(true))
      return () => cancelAnimationFrame(id)
    }
    setPanelAnimOpen(false)
  }, [selId])
  const ref=useCallback(()=>{fetch({silent:true});setDrk(k=>k+1)},[fetch])
  const ncr=useCallback(()=>{setAddOpen(false);setAddTgt(null);fetch({silent:true})},[fetch])
  const ccr=useCallback(()=>{setCcOpen(false);setCcTgt(null);fetch({silent:true})},[fetch])
  const ecr=useCallback(()=>{setEcOpen(false);setEcTgt(null);fetch({silent:true})},[fetch])
  const addN=useCallback((cid:string,ao:number)=>{setAddTgt({chainId:cid,afterOrder:ao});setAddOpen(true)},[])
  const cc=useCallback(async(pCid:string,pNid:string)=>{try{const mcd2=chainData.get(pCid);const pn=mcd2?.nodes.find(n=>n.node_id===pNid);if(chains.filter(bc=>bc.parent_node_id===pNid&&!bc.has_end_node).length>0){toast.error("Node already has an active branch");return}const title=pn?.title||"Branch";await createChain(collectionId,{parent_chain_id:pCid,parent_node_id:pNid,title});await updateNode(collectionId,pNid,{node_type:"start",version:pn?.version??1});toast.success("Branch "+title+" created");fetch({silent:true})}catch(e){toast.error("Failed: "+String(e))}},[collectionId,chainData,fetch,chains])
  const confirmDeleteBranchTitle = useMemo(() => {
    if (!confirmDeleteBranchId) return 'Empty branch'
    const ch = chains.find(c => c.chain_id === confirmDeleteBranchId)
    return ch?.title?.trim() || 'Empty branch'
  }, [confirmDeleteBranchId, chains])
  /**
   * Merge / end branch:
   * 1) Ensure an end-type node exists (API requires it)
   * 2) Open dialog → end_chain promotes last event onto parent chain
   */
  const mergeBranch = useCallback(async (cid: string) => {
    const cd = chainData.get(cid)
    if (!cd) return
    const events = cd.nodes.filter(n => n.node_type === 'event')
    if (events.length === 0) {
      toast.error('Branch has no event nodes to merge')
      return
    }
    try {
      let endId = cd.nodes.find(n => n.node_type === 'end')?.node_id
      let nodes = cd.nodes
      if (!endId) {
        const maxO = nodes.reduce((m, n) => Math.max(m, n.order), 0)
        const end = await createNode(collectionId, cid, {
          group_id: null,
          node_type: 'end',
          title: null,
          order: maxO + 1,
          event_time: null,
        })
        endId = end.node_id
        // Reload nodes so dialog / subsequent ops see the end node
        const fresh = await listNodes(collectionId, cid)
        nodes = fresh
      }
      if (!endId) {
        toast.error('Failed to create end node for branch')
        return
      }
      setEcTgt({ chainId: cid, nodes, endNodeId: endId })
      setEcOpen(true)
    } catch (e) {
      toast.error('Failed to prepare merge: ' + String(e))
    }
  }, [collectionId, chainData])
  const ro=useCallback(async(cid:string)=>{try{await reopenChain(collectionId,cid);toast.success('Chain reopened');fetch({silent:true})}catch(e){toast.error('Reopen failed: '+String(e))}},[collectionId,fetch])
  const gn=(gid:string|null):string=>{if(!gid)return'No Group';return groups.find(g=>g.group_id===gid)?.name??'Unknown'}

  const allNodes = useMemo(() => {
    const out: Node[] = []
    for (const [, cd] of chainData) out.push(...cd.nodes)
    return out
  }, [chainData])

  const fitAllNodes = useCallback(() => {
    const vp = viewportRef.current
    const layout = layoutRef.current
    if (!vp || !layout) return
    const cards = layout.querySelectorAll<HTMLElement>('[data-node-card]')
    if (cards.length === 0) return
    // Cards are inside transformed world; getBoundingClientRect is screen space.
    // Convert back to world-local by dividing by current scale.
    const worldEl = layout.parentElement
    if (!worldEl) return
    const s = scaleRef.current || 1
    const worldBox = worldEl.getBoundingClientRect()
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    for (const card of cards) {
      const r = card.getBoundingClientRect()
      const lx = (r.left - worldBox.left) / s
      const ly = (r.top - worldBox.top) / s
      const rx = (r.right - worldBox.left) / s
      const by = (r.bottom - worldBox.top) / s
      minX = Math.min(minX, lx)
      minY = Math.min(minY, ly)
      maxX = Math.max(maxX, rx)
      maxY = Math.max(maxY, by)
    }
    if (!Number.isFinite(minX)) return
    const pad = 48
    const bw = Math.max(maxX - minX, 1)
    const bh = Math.max(maxY - minY, 1)
    const vw = vp.clientWidth
    const vh = vp.clientHeight
    // Round scale to 2 decimals — fewer fractional scales = less blurry text
    const nextScale =
      Math.round(
        Math.min(
          MAX_SCALE,
          Math.max(MIN_SCALE, Math.min((vw - pad * 2) / bw, (vh - pad * 2) / bh))
        ) * 100
      ) / 100
    const nextTx = Math.round((vw - bw * nextScale) / 2 - minX * nextScale)
    const nextTy = Math.round((vh - bh * nextScale) / 2 - minY * nextScale)
    setScale(nextScale)
    setTx(nextTx)
    setTy(nextTy)
  }, [])

  const handleFocusGroup = useCallback(
    (id: FocusGroupId | null) => {
      if (id === null) {
        setFocusGroupId(null)
        const snap = viewSnapshot.current
        if (snap) {
          setScale(snap.scale)
          setTx(snap.tx)
          setTy(snap.ty)
          viewSnapshot.current = null
        }
        return
      }
      if (focusGroupId === null) {
        viewSnapshot.current = {
          scale: scaleRef.current,
          tx: txRef.current,
          ty: tyRef.current,
        }
      }
      setFocusGroupId(id)
      requestAnimationFrame(() => {
        requestAnimationFrame(() => fitAllNodes())
      })
    },
    [focusGroupId, fitAllNodes]
  )

  const isNodeInFocus = useCallback(
    (node: Node): boolean => {
      if (!focusGroupId) return false
      if (focusGroupId === UNCATEGORIZED_ID) return !node.group_id
      return node.group_id === focusGroupId
    },
    [focusGroupId]
  )

  // node_id → chain_id from server state
  const ncm = useMemo(() => {
    const m = new Map<string, string>()
    for (const [cid, cd] of chainData) for (const n of cd.nodes) m.set(n.node_id, cid)
    return m
  }, [chainData])

  const nodeById = useMemo(() => {
    const m = new Map<string, Node>()
    for (const [, cd] of chainData) for (const n of cd.nodes) m.set(n.node_id, n)
    return m
  }, [chainData])

  /** Delete an empty (no event nodes) open branch — undo accidental create. */
  const deleteEmptyBranch = useCallback(async (cid: string) => {
    const cd = chainData.get(cid)
    const ch = chains.find(c => c.chain_id === cid)
    if (!cd || !ch) return
    const hasEvents = cd.nodes.some(n => n.node_type === 'event')
    if (hasEvents) {
      toast.error('Only empty branches can be deleted here')
      setConfirmDeleteBranchId(null)
      return
    }
    const parentId = ch.parent_node_id
    try {
      await deleteChain(collectionId, cid)
      // Parent must become a normal event again (backend also reverts; keep UI sure)
      if (parentId) {
        const stillBranched = chains.some(
          c => c.chain_id !== cid && c.parent_node_id === parentId,
        )
        if (!stillBranched) {
          const parent = nodeById.get(parentId)
          if (parent && parent.node_type === 'start') {
            try {
              await updateNode(collectionId, parentId, {
                node_type: 'event',
                version: parent.version ?? 1,
              })
            } catch {
              /* version race — silent fetch heals */
            }
          }
        }
      }
      toast.success('Branch removed')
      setConfirmDeleteBranchId(null)
      fetch({ silent: true })
    } catch (e) {
      toast.error('Failed: ' + String(e))
    }
  }, [chainData, chains, collectionId, fetch, nodeById])

  /**
   * Live sortable id lists during drag (enables placement preview within & across chains).
   * null = not dragging; use server order.
   */
  const [dragItems, setDragItems] = useState<Record<string, string[]> | null>(null)
  /** Always-latest dragItems for dragEnd (avoids missing the last onDragOver update). */
  const dragItemsRef = useRef<Record<string, string[]> | null>(null)
  dragItemsRef.current = dragItems
  /** chain of the active drag at drag start */
  const dragOriginChain = useRef<string | null>(null)
  /** Latest pointer for collision + insert-before/after on cross-chain drops */
  const pointerRef = useRef<{ x: number; y: number } | null>(null)

  /**
   * Freeze layout geometry at drag-start so onDragOver cannot thrash Layout-B /
   * branch lanes (was a common crash: remount SortableContext mid-drag).
   */
  type LayoutFreeze = {
    mainNodeCol: number[]
    gridCols: number
    contentW: number
    branches: Record<
      string,
      { pidx: number; mergeIdx: number; spanEnd: number; level: number; pCol: number }
    >
  }
  const layoutFreezeRef = useRef<LayoutFreeze | null>(null)
  const lastStableLayoutRef = useRef<LayoutFreeze | null>(null)
  /** Batch dragItems setState to one update per frame (avoids over-fire crash). */
  const dragOverRafRef = useRef<number | null>(null)

  const snapshotDragItems = useCallback((): Record<string, string[]> => {
    const snap: Record<string, string[]> = {}
    for (const [cid, cd] of chainData) {
      snap[cid] = visibleNodesForChain(cd.chain, cd.nodes).map(n => n.node_id)
    }
    return snap
  }, [chainData])

  const findDragContainer = useCallback((id: string, items: Record<string, string[]>): string | undefined => {
    const endCid = parseEndDropId(id)
    if (endCid) return endCid
    const startCid = parseStartDropId(id)
    if (startCid) return startCid
    if (items[id]) return id
    return Object.keys(items).find(cid => items[cid].includes(id))
  }, [])

  // distance: avoid fighting with click-to-select; still easy to start a drag
  const ss = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
  )

  /**
   * Collision: prefer droppables on the same horizontal row as the pointer
   * (main vs branch), then pick the closest by distance to rect center.
   * Main-locked anchors (branch start/merge) only collide with main-chain
   * targets — otherwise the branch row under them steals the hit and drag
   * appears to "do nothing".
   */
  const collisionDetection = useCallback<CollisionDetection>((args) => {
    const { pointerCoordinates, droppableContainers, active } = args
    if (!pointerCoordinates) return closestCenter(args)

    pointerRef.current = { x: pointerCoordinates.x, y: pointerCoordinates.y }

    const activeId = String(active.id)
    const homeCid =
      dragOriginChain.current ??
      findDragContainer(activeId, dragItemsRef.current ?? {}) ??
      ncm.get(activeId)
    const mainLocked = isMainLockedDrag(activeId, chains, nodeById, homeCid)
    const mainCid = mainChainIdOf(chains)

    const allowed = mainLocked && mainCid
      ? droppableContainers.filter(c => {
          const id = String(c.id)
          if (parseEndDropId(id) === mainCid || parseStartDropId(id) === mainCid) {
            return true
          }
          const items = dragItemsRef.current?.[mainCid]
          if (items?.includes(id)) return true
          if (ncm.get(id) === mainCid) return true
          return false
        })
      : droppableContainers

    const pool = allowed.length > 0 ? allowed : droppableContainers

    const ROW_TOL = 48
    type Hit = {
      id: (typeof droppableContainers)[number]['id']
      dist: number
      container: (typeof droppableContainers)[number]
    }
    let best: Hit | null = null
    let bestSameRow: Hit | null = null

    for (const c of pool) {
      const rect = c.rect.current
      if (!rect) continue
      const cx = rect.left + rect.width / 2
      const cy = rect.top + rect.height / 2
      const dist = Math.hypot(pointerCoordinates.x - cx, pointerCoordinates.y - cy)
      const hit: Hit = { id: c.id, dist, container: c }
      if (!best || dist < best.dist) best = hit
      if (Math.abs(cy - pointerCoordinates.y) <= ROW_TOL) {
        if (!bestSameRow || dist < bestSameRow.dist) bestSameRow = hit
      }
    }
    const pick = bestSameRow ?? best
    if (!pick) return []
    return [
      {
        id: pick.id,
        data: { droppableContainer: pick.container, value: pick.dist },
      },
    ]
  }, [chains, nodeById, ncm, findDragContainer])

  const ds = useCallback((e: DragStartEvent) => {
    const nid = String(e.active.id)
    const node = nodeById.get(nid)
    // Freeze geometry before any dragOver reorders (prevents layout thrash / crash)
    if (lastStableLayoutRef.current) {
      layoutFreezeRef.current = {
        mainNodeCol: [...lastStableLayoutRef.current.mainNodeCol],
        gridCols: lastStableLayoutRef.current.gridCols,
        contentW: lastStableLayoutRef.current.contentW,
        branches: { ...lastStableLayoutRef.current.branches },
      }
    }
    if (node) setDragN(node)
    const snap = snapshotDragItems()
    dragItemsRef.current = snap
    setDragItems(snap)
    dragOriginChain.current = findDragContainer(nid, snap) ?? ncm.get(nid) ?? null
  }, [nodeById, snapshotDragItems, findDragContainer, ncm])

  /**
   * Controlled multi-list reorder.
   * Prefer stable over-id arrayMove (pointer-X every frame caused layout thrash → crash).
   * Start/end drop zones cover first/last slots. Branch start must stay before merge.
   * Ref updates immediately; React state is rAF-batched to one paint per frame.
   */
  const dOver = useCallback((e: DragOverEvent) => {
    try {
      const { active, over } = e
      if (!over) return
      const activeId = String(active.id)
      const overId = String(over.id)
      if (activeId === overId) return

      const t = active.rect.current.translated
      if (t) {
        pointerRef.current = { x: t.left + t.width / 2, y: t.top + t.height / 2 }
      }
      const pointerX =
        pointerRef.current?.x ??
        (t ? t.left + t.width / 2 : null)

      const prev = dragItemsRef.current
      if (!prev) return
      let srcCid = findDragContainer(activeId, prev)
      let dstCid =
        findDragContainer(overId, prev) ??
        findDragContainer(activeId, prev)
      if (!srcCid || !dstCid) return

      const originCid = dragOriginChain.current ?? srcCid
      const mainCid = mainChainIdOf(chains)
      const mainLocked = isMainLockedDrag(activeId, chains, nodeById, originCid)

      // Main-locked anchors: never leave main. If collision still hit a branch
      // (or any other chain), remap to main-chain reorder by pointer X.
      if (mainLocked && mainCid && prev[mainCid]?.includes(activeId)) {
        if (dstCid !== mainCid || srcCid !== mainCid) {
          srcCid = mainCid
          dstCid = mainCid
          const list = prev[mainCid]
          if (!list) return
          const insertAt =
            pointerX != null
              ? insertIndexFromPointerX(list, activeId, pointerX)
              : list.indexOf(activeId)
          let nextList = listWithActiveAt(list, activeId, insertAt)
          nextList = enforceBranchAnchorOrder(nextList, chains, activeId)
          if (sameIdList(nextList, list)) return
          const next = { ...prev, [mainCid]: nextList }
          dragItemsRef.current = next
          if (dragOverRafRef.current == null) {
            dragOverRafRef.current = requestAnimationFrame(() => {
              dragOverRafRef.current = null
              const latest = dragItemsRef.current
              if (latest) setDragItems(latest)
            })
          }
          return
        }
      } else if (
        srcCid !== dstCid &&
        isBranchChainId(dstCid, chains) &&
        mainLocked
      ) {
        return
      }

      let next: Record<string, string[]> | null = null

      // ── Same chain ──
      if (srcCid === dstCid) {
        const list = prev[srcCid]
        if (!list.includes(activeId)) return

        let nextList: string[]
        if (parseEndDropId(overId)) {
          nextList = listWithActiveAt(list, activeId, list.length - 1)
        } else if (parseStartDropId(overId)) {
          nextList = listWithActiveAt(list, activeId, 0)
        } else {
          const oldIndex = list.indexOf(activeId)
          const newIndex = list.indexOf(overId)
          if (oldIndex < 0 || newIndex < 0 || oldIndex === newIndex) return
          nextList = arrayMove(list, oldIndex, newIndex)
        }
        nextList = enforceBranchAnchorOrder(nextList, chains, activeId)
        if (sameIdList(nextList, list)) return
        next = { ...prev, [srcCid]: nextList }
      } else {
        // ── Cross chain ──
        const srcList = [...prev[srcCid]]
        let dstList = [...prev[dstCid]]
        const fromIdx = srcList.indexOf(activeId)
        if (fromIdx < 0) return

        if (dstList.includes(activeId)) {
          let nextList: string[]
          if (parseEndDropId(overId)) {
            nextList = listWithActiveAt(dstList, activeId, dstList.length - 1)
          } else if (parseStartDropId(overId)) {
            nextList = listWithActiveAt(dstList, activeId, 0)
          } else {
            const oldIndex = dstList.indexOf(activeId)
            const newIndex = dstList.indexOf(overId)
            if (oldIndex < 0 || newIndex < 0 || oldIndex === newIndex) return
            nextList = arrayMove(dstList, oldIndex, newIndex)
          }
          nextList = enforceBranchAnchorOrder(nextList, chains, activeId)
          if (sameIdList(nextList, dstList)) return
          next = { ...prev, [dstCid]: nextList }
        } else {
          srcList.splice(fromIdx, 1)
          let toIdx: number
          if (parseEndDropId(overId)) {
            toIdx = dstList.length
          } else if (parseStartDropId(overId)) {
            toIdx = 0
          } else {
            toIdx = dstList.indexOf(overId)
            if (toIdx < 0) toIdx = dstList.length
          }
          dstList.splice(Math.max(0, Math.min(dstList.length, toIdx)), 0, activeId)
          dstList = enforceBranchAnchorOrder(dstList, chains, activeId)
          next = { ...prev, [srcCid]: srcList, [dstCid]: dstList }
        }
      }

      if (!next) return
      dragItemsRef.current = next
      // Coalesce React state updates to 1/frame
      if (dragOverRafRef.current == null) {
        dragOverRafRef.current = requestAnimationFrame(() => {
          dragOverRafRef.current = null
          const latest = dragItemsRef.current
          if (latest) setDragItems(latest)
        })
      }
    } catch {
      // Never let drag-over throw
    }
  }, [findDragContainer, chains, nodeById])

  const [measureEpoch, setMeasureEpoch] = useState(0)
  const bumpMeasure = useCallback(() => setMeasureEpoch(e => e + 1), [])

  const clearDrag = useCallback(() => {
    if (dragOverRafRef.current != null) {
      cancelAnimationFrame(dragOverRafRef.current)
      dragOverRafRef.current = null
    }
    setDragN(null)
    setDragItems(null)
    dragItemsRef.current = null
    dragOriginChain.current = null
    layoutFreezeRef.current = null
    // Remeasure connectors after layout settles back to server order
    requestAnimationFrame(() => bumpMeasure())
  }, [bumpMeasure])

  const de = useCallback(async (e: DragEndEvent) => {
    const sid = String(e.active.id)
    const originCid = dragOriginChain.current
    // Snapshot before clearing — must not read refs after async gaps
    const items = dragItemsRef.current
      ? Object.fromEntries(
          Object.entries(dragItemsRef.current).map(([k, v]) => [k, [...v]]),
        )
      : null

    // Resolve final order **before** clearing preview
    let destCid: string | undefined
    let finalIndex = -1
    let desiredIds: string[] = []
    const mainCid = mainChainIdOf(chains)
    const mainLocked = isMainLockedDrag(
      sid,
      chains,
      nodeById,
      originCid ?? mainCid,
    )
    if (items) {
      // Main-locked anchors always commit on main (ignore accidental branch list)
      if (mainLocked && mainCid && items[mainCid]?.includes(sid)) {
        destCid = mainCid
        desiredIds = enforceBranchAnchorOrder([...items[mainCid]], chains, sid)
        finalIndex = desiredIds.indexOf(sid)
      } else {
        for (const [cid, ids] of Object.entries(items)) {
          const idx = ids.indexOf(sid)
          if (idx >= 0) {
            destCid = cid
            desiredIds = enforceBranchAnchorOrder([...ids], chains, sid)
            finalIndex = desiredIds.indexOf(sid)
            break
          }
        }
      }
    }

    // Clear preview after measuring
    if (dragOverRafRef.current != null) {
      cancelAnimationFrame(dragOverRafRef.current)
      dragOverRafRef.current = null
    }
    setDragN(null)
    setDragItems(null)
    dragItemsRef.current = null
    dragOriginChain.current = null
    layoutFreezeRef.current = null
    pointerRef.current = null

    const remasureSoon = () => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          try {
            bumpMeasure()
          } catch {
            /* ignore */
          }
        })
      })
    }

    if (!items || !originCid || !destCid || finalIndex < 0) {
      remasureSoon()
      return
    }

    const srcNode = nodeById.get(sid)
    if (!srcNode) {
      remasureSoon()
      return
    }

    const destChain = chains.find(c => c.chain_id === destCid)
    const destCwn = chainData.get(destCid)
    if (!destChain || !destCwn) {
      remasureSoon()
      return
    }

    const originCwn = chainData.get(originCid)
    const originVisible = originCwn
      ? visibleNodesForChain(originCwn.chain, originCwn.nodes).map(n => n.node_id)
      : []
    const originIndex = originVisible.indexOf(sid)
    const sameChain = originCid === destCid
    if (sameChain && originIndex === finalIndex) {
      remasureSoon()
      return
    }

    // Hard reject: branch start/merge (and start/end types) cannot leave main onto a branch
    if (
      !sameChain &&
      isBranchChainId(destCid, chains) &&
      (isMainBranchAnchor(sid, chains) ||
        srcNode.node_type === 'start' ||
        srcNode.node_type === 'end')
    ) {
      toast.error('Branch start/end nodes must stay on the main chain')
      remasureSoon()
      return
    }

    try {
      if (!sameChain) {
        const maxOrder = destCwn.nodes.reduce((m, n) => Math.max(m, n.order), 0)
        const moved = await updateNode(collectionId, sid, {
          chain_id: destCid,
          order: maxOrder + 1,
          version: srcNode.version ?? 1,
        })
        const patched = [
          ...destCwn.nodes.filter(n => n.node_id !== sid),
          { ...moved, chain_id: destCid },
        ]
        const newOrder = orderForDesiredIndex(
          destChain,
          patched,
          desiredIds,
          sid,
        )
        if (newOrder !== moved.order) {
          await reorderNode(collectionId, sid, newOrder)
        }
        toast.success('Node moved to ' + (destChain.title || 'chain'))
      } else {
        const newOrder = orderForDesiredIndex(
          destChain,
          destCwn.nodes,
          desiredIds,
          sid,
        )
        if (newOrder === srcNode.order) {
          remasureSoon()
          return
        }
        await reorderNode(collectionId, sid, newOrder)
        toast.success('Order updated')
      }
      // Optimistic local state so layout/connectors match drop before refetch
      setChainData(prev => {
        const next = new Map(prev)
        const cwn = next.get(destCid!)
        if (!cwn) return prev
        const isMain = !!destChain.is_main
        const startCount = isMain
          ? 0
          : cwn.nodes.filter(n => n.node_type === 'start').length

        // Include the moved node when crossing chains (it is not yet on dest)
        let baseNodes = cwn.nodes.filter(n => n.node_id !== sid)
        if (!sameChain) {
          const movedLocal = {
            ...srcNode,
            chain_id: destCid!,
          }
          baseNodes = [...baseNodes, movedLocal]
        }

        // Hidden start/end on branch (not in desiredIds) keep relative orders
        const hidden = baseNodes.filter(n => !desiredIds.includes(n.node_id))
        const visibleOrdered: Node[] = []
        desiredIds.forEach((id, vi) => {
          const n = baseNodes.find(x => x.node_id === id)
          if (!n) return
          visibleOrdered.push({
            ...n,
            chain_id: destCid!,
            order: startCount + vi + 1,
          })
        })

        next.set(destCid!, {
          chain: cwn.chain,
          nodes: [...hidden, ...visibleOrdered],
        })

        if (!sameChain && originCid) {
          const oc = next.get(originCid)
          if (oc) {
            next.set(originCid, {
              ...oc,
              nodes: oc.nodes.filter(n => n.node_id !== sid),
            })
          }
        }
        return next
      })
      await fetch({ silent: true })
      // Layout may take a few frames after refetch — remeasure connectors firmly
      remasureSoon()
      window.setTimeout(() => bumpMeasure(), 80)
      window.setTimeout(() => bumpMeasure(), 250)
    } catch (err) {
      toast.error('Move failed: ' + String(err))
      try {
        await fetch({ silent: true })
      } catch {
        /* ignore */
      }
      remasureSoon()
    }
  }, [nodeById, chains, chainData, collectionId, fetch, bumpMeasure])
  // note: chains used in enforceBranchAnchorOrder inside de

  const dCancel = useCallback(() => {
    clearDrag()
  }, [clearDrag])

  /** Resolve display nodes for a chain row (honours live drag order). */
  const nodesForRow = useCallback((chainId: string, fallback: Node[]): Node[] => {
    if (!dragItems?.[chainId]) return fallback
    const ids = dragItems[chainId]
    // Never drop ids mid-drag — missing entries crash dnd-kit SortableContext
    const out: Node[] = []
    for (const id of ids) {
      const n = nodeById.get(id)
      if (n) out.push(n)
      else {
        // Incomplete map → fall back to stable order
        return fallback
      }
    }
    return out.length > 0 ? out : fallback
  }, [dragItems, nodeById])

  const mc=chains.find(c=>c.is_main),bcs=chains.filter(c=>!c.is_main)

  // Layout measurement refs (connectors drawn from measured DOM positions)
  const layoutRef = useRef<HTMLDivElement>(null)
  const mainRowRef = useRef<HTMLDivElement>(null)
  const viewportRef = useRef<HTMLDivElement>(null)
  const branchAnchorRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const [connectors, setConnectors] = useState<ConnectorGeom[]>([])
  /** Viewport size — drives canvas margin so nodes can scroll fully off-screen. null until measured. */
  const [vpSize, setVpSize] = useState<{ w: number; h: number } | null>(null)
  const didCenterRef = useRef(false)

  // ── Display order is always live (incl. drag preview) ──
  // Only branch *lane levels* freeze during drag (prevents row remount thrash).
  // Column geometry stays live so cross-chain drag / Layout-B match the final layout.
  const mcd = mc ? chainData.get(mc.chain_id) : undefined
  const mnsServer = [...(mcd?.nodes ?? [])].sort((a, b) => a.order - b.order)
  const mns = mc ? nodesForRow(mc.chain_id, mnsServer) : mnsServer
  const freeze = dragN ? layoutFreezeRef.current : null

  const binfo: BranchInfo[] = []
  if (mc) {
    for (const bc of bcs) {
      const bcd = chainData.get(bc.chain_id)
      if (!bcd) continue
      const done = bc.has_end_node || !!bc.merge_node_id
      const evnsServer = [...bcd.nodes.filter(n => n.node_type === 'event')].sort(
        (a, b) => a.order - b.order,
      )
      // During drag, branch list may include nodes still keyed on other chains in nodeById
      const evns = nodesForRow(bc.chain_id, evnsServer)
      if (evns.length === 0 && done) continue
      const hasEnd = bcd.nodes.some(n => n.node_type === 'end') || !!bc.merge_node_id
      const frozenB = freeze?.branches[bc.chain_id]
      let pidx = mns.findIndex(n => n.node_id === bc.parent_node_id)
      // Keep branch mounted if parent temporarily missing mid-drag
      if (pidx < 0 && frozenB) pidx = Math.min(frozenB.pidx, Math.max(0, mns.length - 1))
      if (pidx < 0) continue
      let mergeIdx = bc.merge_node_id
        ? mns.findIndex(n => n.node_id === bc.merge_node_id)
        : -1
      if (mergeIdx < 0 && frozenB && frozenB.mergeIdx >= 0 && mns.length > 0) {
        mergeIdx = Math.min(frozenB.mergeIdx, mns.length - 1)
      }
      const spanLen = branchSpanSlots(evns.length, done)
      const spanEnd =
        done && mergeIdx >= 0
          ? Math.max(pidx + spanLen - 1, mergeIdx)
          : pidx + spanLen - 1
      binfo.push({
        bc,
        bcd,
        evns,
        hasEnd,
        done,
        pidx,
        mergeIdx,
        spanEnd,
        level: frozenB?.level ?? 0,
      })
    }
    if (!freeze) assignBranchLevels(binfo)
  }

  // Layout B always live — WYSIWYG with final column geometry
  const mainNodeCol = computeMainVisualColumns(
    mns.length,
    binfo.map(bi => ({
      pidx: bi.pidx,
      mergeIdx: bi.mergeIdx,
      eventCount: bi.evns.length,
      done: bi.done,
    })),
  )

  const liveIdToCol = new Map<string, number>()
  mns.forEach((n, i) => {
    liveIdToCol.set(n.node_id, mainNodeCol[i] ?? i)
  })

  const aboveLevels = [...new Set(binfo.filter(b => b.level < 0).map(b => b.level))]
    .sort((a, b) => a - b)
  const belowLevels = [...new Set(binfo.filter(b => b.level >= 0).map(b => b.level))]
    .sort((a, b) => a - b)

  let maxVisualCol = mns.length > 0 ? (mainNodeCol[mns.length - 1] ?? 0) : 0
  for (const bi of binfo) {
    const pCol = mainNodeCol[bi.pidx] ?? bi.pidx
    const spanLen = branchSpanSlots(bi.evns.length, bi.done)
    if (bi.done && bi.mergeIdx >= 0) {
      const mCol = mainNodeCol[bi.mergeIdx] ?? bi.mergeIdx
      maxVisualCol = Math.max(maxVisualCol, mCol, pCol + spanLen - 1)
    } else {
      maxVisualCol = Math.max(maxVisualCol, pCol + spanLen - 1)
    }
  }
  const MAX_GRID_COLS = 64
  const gridCols = Math.min(MAX_GRID_COLS, Math.max(maxVisualCol + 1, 1))
  const contentW = Math.max(gridCols * SLOT_W + 80, SLOT_W)

  // Snapshot lane levels when idle (used only to keep levels stable mid-drag)
  if (!dragN) {
    const branches: LayoutFreeze['branches'] = {}
    for (const bi of binfo) {
      branches[bi.bc.chain_id] = {
        pidx: bi.pidx,
        mergeIdx: bi.mergeIdx,
        spanEnd: bi.spanEnd,
        level: bi.level,
        pCol: mainNodeCol[bi.pidx] ?? bi.pidx,
      }
    }
    lastStableLayoutRef.current = {
      mainNodeCol: [...mainNodeCol],
      gridCols,
      contentW,
      branches,
    }
    layoutFreezeRef.current = null
  }

  // Canvas margin ≈ one viewport so the rightmost node can scroll to the left edge
  // (and vice versa). Recomputed when the viewport resizes (incl. sidebar open/close).
  const canvasPadX = Math.max(vpSize?.w ?? 800, 480)
  const canvasPadY = Math.max(vpSize?.h ?? 600, 320)

  // Track viewport size for infinite-canvas padding (only update when size actually changes)
  useEffect(() => {
    const el = viewportRef.current
    if (!el) return
    const apply = () => {
      const w = el.clientWidth
      const h = el.clientHeight
      setVpSize(prev => (prev && prev.w === w && prev.h === h ? prev : { w, h }))
    }
    apply()
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(apply) : null
    ro?.observe(el)
    return () => ro?.disconnect()
  }, [loading, mc])

  // Reset centering when switching collection
  useEffect(() => {
    didCenterRef.current = false
  }, [collectionId])

  /** Center the node layout at default scale=1 (do NOT fit-zoom on open). */
  const centerAtDefaultScale = useCallback(() => {
    const vp = viewportRef.current
    const layout = layoutRef.current
    if (!vp || !layout) return
    // Reset zoom; pan so layout bbox is centered in the viewport
    const s = 1
    setScale(s)
    // Layout sits inside world with padding (canvasPadX/Y). World origin = padding corner.
    // Content (layout) top-left in world coords ≈ (canvasPadX, canvasPadY)
    const lw = layout.offsetWidth || contentW
    const lh = layout.offsetHeight || 200
    const nextTx = (vp.clientWidth - lw * s) / 2 - canvasPadX * s
    const nextTy = (vp.clientHeight - lh * s) / 2 - canvasPadY * s
    setTx(nextTx)
    setTy(nextTy)
  }, [contentW, canvasPadX, canvasPadY])

  // Center once data is ready — keep scale at 1, only pan to center
  useLayoutEffect(() => {
    if (loading || !mc || !vpSize || didCenterRef.current) return
    if (vpSize.w < 40 || vpSize.h < 40) return
    let raf2 = 0
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => {
        centerAtDefaultScale()
        didCenterRef.current = true
      })
    })
    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
    }
  }, [loading, mc, contentW, canvasPadX, canvasPadY, vpSize, centerAtDefaultScale])

  // When selection changes (sidebar opens / switches node), pan so card stays in view
  useLayoutEffect(() => {
    if (!selId || focusGroupId) return
    let raf2 = 0
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => {
        const vp = viewportRef.current
        const layout = layoutRef.current
        if (!vp || !layout) return
        const card = layout.querySelector<HTMLElement>(`[data-node-id="${selId}"]`)
        if (!card) return
        const margin = 24
        const vpRect = vp.getBoundingClientRect()
        const r = card.getBoundingClientRect()
        let dx = 0
        let dy = 0
        if (r.right > vpRect.right - margin) dx = r.right - (vpRect.right - margin)
        if (r.left < vpRect.left + margin) dx = r.left - (vpRect.left + margin)
        if (r.bottom > vpRect.bottom - margin) dy = r.bottom - (vpRect.bottom - margin)
        if (r.top < vpRect.top + margin) dy = r.top - (vpRect.top + margin)
        if (dx || dy) {
          setTx((v) => v - dx)
          setTy((v) => v - dy)
        }
      })
    })
    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
    }
  }, [selId, vpSize?.w, focusGroupId])

  // Connector remeasure key — while dragging we hide SVG (stale paths look broken);
  // after drop, include full geometry so paths reattach.
  const layoutKey = dragN
    ? `dragging:${dragN.node_id}`
    : binfo
        .map(
          b =>
            `${b.bc.chain_id}:${b.pidx}:${b.mergeIdx}:${b.level}:${b.done}:` +
            b.evns.map(n => n.node_id).join(',') +
            `:m${b.bc.merge_node_id ?? ''}`,
        )
        .join('|') +
      `|m${mns.map(n => n.node_id).join(',')}` +
      `|c${mainNodeCol.join(',')}` +
      `|e${measureEpoch}` +
      `|s${scale.toFixed(2)}`

  // Measure closed-loop connectors: parent → first event; last event → merge (if done).
  useLayoutEffect(() => {
    // Hide connectors during drag — cards move under frozen/wrong paths otherwise
    if (dragN) {
      setConnectors(prev => (prev.length === 0 ? prev : []))
      return
    }

    const measure = () => {
      const root = layoutRef.current
      const mainEl = mainRowRef.current
      if (!root || !mainEl) return
      if (binfo.length === 0) {
        setConnectors(prev => (prev.length === 0 ? prev : []))
        return
      }
      const rootBox = root.getBoundingClientRect()
      // SVG paths live in layout CSS pixels (pre-transform). getBoundingClientRect is
      // post-transform screen space — divide by current scale to convert back.
      const s = scaleRef.current || 1
      const next: ConnectorGeom[] = []
      const rel = (box: DOMRect) => ({
        x: (box.left + box.width / 2 - rootBox.left) / s,
        y: (box.top + box.height / 2 - rootBox.top) / s,
      })
      // Prefer real cards; fall back to sortable slot (placeholder mid-drag shouldn't appear idle)
      const pickAnchor = (el: HTMLElement | null): HTMLElement | null => {
        if (!el) return null
        const card = el.querySelector<HTMLElement>('[data-node-card]')
        if (card && card.getBoundingClientRect().width >= 2 * s) return card
        if (el.getBoundingClientRect().width >= 2 * s) return el
        return card ?? el
      }

      for (const bi of binfo) {
        const branchEl = branchAnchorRefs.current.get(bi.bc.chain_id)
        if (!branchEl) continue
        const slots = [
          ...branchEl.querySelectorAll<HTMLElement>('[data-sortable-id]'),
        ]
        const cards = [...branchEl.querySelectorAll<HTMLElement>('[data-node-card]')]
        const firstEl =
          pickAnchor(cards[0] ?? slots[0] ?? null) ??
          branchEl.querySelector<HTMLElement>('[data-branch-target]')
        const lastEl =
          pickAnchor(cards[cards.length - 1] ?? slots[slots.length - 1] ?? null) ??
          firstEl
        const parentId = bi.bc.parent_node_id
        if (!parentId || !firstEl) continue
        const parentCard = mainEl.querySelector<HTMLElement>(
          `[data-node-id="${parentId}"]`,
        )
        if (!parentCard || parentCard.getBoundingClientRect().width < 2 * s) continue

        const p = rel(parentCard.getBoundingClientRect())
        const f = rel(firstEl.getBoundingClientRect())

        // Start: vertical from parent, then horizontal to first branch node
        next.push({
          chainId: `${bi.bc.chain_id}-start`,
          d: `M ${p.x} ${p.y} L ${p.x} ${f.y} L ${f.x} ${f.y}`,
        })

        // Closed loop: last branch node → under merge → up to merge
        if (bi.done && bi.bc.merge_node_id && lastEl) {
          const mergeCard = mainEl.querySelector<HTMLElement>(
            `[data-node-id="${bi.bc.merge_node_id}"]`,
          )
          if (mergeCard && mergeCard.getBoundingClientRect().width >= 2 * s) {
            const l = rel(lastEl.getBoundingClientRect())
            const m = rel(mergeCard.getBoundingClientRect())
            next.push({
              chainId: `${bi.bc.chain_id}-end`,
              d: `M ${l.x} ${l.y} L ${m.x} ${l.y} L ${m.x} ${m.y}`,
            })
          }
        }
      }
      setConnectors(prev => {
        if (
          prev.length === next.length &&
          prev.every((p, i) => p.chainId === next[i].chainId && p.d === next[i].d)
        ) {
          return prev
        }
        return next
      })
    }

    let raf2 = 0
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => measure())
    })
    const root = layoutRef.current
    let ro: ResizeObserver | undefined
    if (root && typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => measure())
      ro.observe(root)
    }
    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
      ro?.disconnect()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutKey])

  const setBranchRef = useCallback((chainId: string, el: HTMLDivElement | null) => {
    if (el) branchAnchorRefs.current.set(chainId, el)
    else branchAnchorRefs.current.delete(chainId)
  }, [])

  const zoomAt = useCallback((clientX: number, clientY: number, factor: number) => {
    const vp = viewportRef.current
    if (!vp) return
    const rect = vp.getBoundingClientRect()
    const px = clientX - rect.left
    const py = clientY - rect.top
    const s0 = scaleRef.current
    // Snap scale to 2 decimals — fewer fractional scales = less blurry text
    const s1 =
      Math.round(
        Math.min(MAX_SCALE, Math.max(MIN_SCALE, s0 * factor)) * 100
      ) / 100
    if (s1 === s0) return
    const wx = (px - txRef.current) / s0
    const wy = (py - tyRef.current) / s0
    setScale(s1)
    setTx(Math.round(px - wx * s1))
    setTy(Math.round(py - wy * s1))
  }, [])

  // Non-passive wheel — must run unconditionally (before any early return)
  useEffect(() => {
    const el = viewportRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const zoomGesture =
        e.ctrlKey || e.metaKey || e.deltaMode === 1 || e.deltaMode === 2
      if (zoomGesture) {
        const factor = e.deltaY > 0 ? 0.92 : 1.08
        zoomAt(e.clientX, e.clientY, factor)
      } else {
        setTx((v) => v - e.deltaX)
        setTy((v) => v - e.deltaY)
      }
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [zoomAt, loading, mc])

  // Must stay before any early return (hooks order)
  const chainNodesMap = useMemo(() => {
    const m = new Map<string, Node[]>()
    for (const [cid, cwn] of chainData) m.set(cid, cwn.nodes)
    return m
  }, [chainData])

  const isMsgDim = useCallback(
    (nodeId: string) => !!msgScopeNodeIds && !msgScopeNodeIds.has(nodeId),
    [msgScopeNodeIds]
  )
  const isMsgFocus = useCallback(
    (nodeId: string) => !!msgScopeNodeIds && msgScopeNodeIds.has(nodeId),
    [msgScopeNodeIds]
  )

  if (loading) return <div className="flex items-center justify-center h-full text-muted-foreground"><p className="text-sm">Loading...</p></div>
  if (!mc) return <div className="flex items-center justify-center h-full text-muted-foreground"><div className="text-center"><p className="text-sm">No main chain found</p></div></div>

  /** One horizontal lane: all branches at the same level share a grid row (natural height). */
  const renderLane = (level: number) => {
    const laneBranches = binfo.filter(b => b.level === level)
    return (
      <div
        key={`lane-${level}`}
        className="relative z-[1]"
        style={{
          display: 'grid',
          gridTemplateColumns: `repeat(${gridCols}, ${SLOT_W}px)`,
          width: contentW,
          alignItems: 'start',
        }}
      >
        {laneBranches.map(bi => {
          const rowNodes = bi.evns // already live-ordered via nodesForRow in binfo
          const isEmpty = rowNodes.length === 0 && !bi.done
          const parentId = bi.bc.parent_node_id
          const pCol = parentId
            ? (liveIdToCol.get(parentId) ?? mainNodeCol[bi.pidx] ?? bi.pidx)
            : (mainNodeCol[bi.pidx] ?? bi.pidx)
          const mCol =
            bi.done && bi.mergeIdx >= 0
              ? (mainNodeCol[bi.mergeIdx] ?? bi.mergeIdx)
              : pCol
          const eventSlots = Math.max(1, rowNodes.length)
          // Closed: span parent→merge (Layout-B). Open: only event columns —
          // trailing + is tucked next to the last card (not a full empty SLOT_W).
          // Empty open branch: single column under parent.
          const needCols = isEmpty
            ? 1
            : bi.done
              ? Math.max(eventSlots, mCol - pCol + 1)
              : eventSlots
          const spanCols = Math.min(needCols, Math.max(1, gridCols - pCol))
          // Pack events left from parent; relative visual cols 0..n-1
          const branchVisualCols = rowNodes.map((_, i) => i)
          return (
            <div
              key={bi.bc.chain_id}
              ref={el => setBranchRef(bi.bc.chain_id, el)}
              style={{
                gridColumnStart: pCol + 1,
                gridColumnEnd: `span ${spanCols}`,
              }}
              className="py-2 overflow-visible relative z-[1]"
            >
              {isEmpty ? (
                // + centered under parent (SVG + local stem). Delete sits *beside*
                // the stem so it never covers / breaks the vertical connector.
                <div
                  className="relative flex justify-center items-center"
                  style={{ width: SLOT_W, minHeight: 40 }}
                >
                  {/* Continuous stem through the gap into the + (matches connector stroke) */}
                  <div
                    className="absolute left-1/2 w-px -translate-x-1/2 pointer-events-none z-0 bg-muted-foreground/30"
                    style={{
                      top: -CONN_GAP,
                      bottom: 20, // stop at vertical center of the 40px + button
                    }}
                    aria-hidden
                  />
                  {!msgMode && (
                    <button
                      type="button"
                      className={cn(
                        'absolute z-10 -translate-y-1/2',
                        // Mid-gap, to the right of the stem (not on the line)
                        'left-1/2 ml-3',
                        'w-6 h-6 rounded-full border border-dashed border-destructive/30',
                        'bg-background flex items-center justify-center',
                        'text-muted-foreground/40 hover:text-destructive hover:border-destructive/50',
                        'transition-colors shadow-sm',
                      )}
                      style={{ top: -CONN_GAP / 2 }}
                      onClick={() => setConfirmDeleteBranchId(bi.bc.chain_id)}
                      title="Delete empty branch"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  )}
                  <button
                    type="button"
                    data-branch-target
                    className="relative z-[1] w-10 h-10 rounded-md border border-dashed border-muted-foreground/30 bg-background flex items-center justify-center text-muted-foreground/40 hover:text-muted-foreground/70 hover:border-muted-foreground/50 transition-colors shadow-sm"
                    onClick={() => {
                      const all = chainData.get(bi.bc.chain_id)?.nodes ?? []
                      const maxO = all.reduce((m, n) => Math.max(m, n.order), 0)
                      addN(bi.bc.chain_id, maxO)
                    }}
                    title="Add first node to branch"
                  >
                    <Plus className="h-4 w-4" />
                  </button>
                </div>
              ) : (
                <div className="relative min-w-0">
                  {msgMode && (
                    <button
                      type="button"
                      data-branch-msg-focus
                      title="Focus branch messages"
                      className={cn(
                        'absolute -left-6 top-1/2 -translate-y-1/2 z-10 p-1 rounded border bg-background shadow-sm',
                        msgFocus.kind === 'chain' && msgFocus.chainId === bi.bc.chain_id
                          ? 'border-primary text-primary'
                          : 'border-border text-muted-foreground hover:text-foreground'
                      )}
                      onClick={(e) => {
                        e.stopPropagation()
                        setMsgFocus({ kind: 'chain', chainId: bi.bc.chain_id })
                      }}
                    >
                      <GitBranch className="h-3 w-3" />
                    </button>
                  )}
                  <ChainRow
                    chainData={rowNodes}
                    chainId={bi.bc.chain_id}
                    isBranch
                    isCompleted={bi.done}
                    onNodeClick={clk}
                    onAddNode={msgMode ? () => {} : addN}
                    onMergeBranch={msgMode ? undefined : () => mergeBranch(bi.bc.chain_id)}
                    onCreateChain={msgMode ? () => {} : cc}
                    groups={groups}
                    focusGroupId={focusGroupId}
                    isNodeInFocus={isNodeInFocus}
                    selId={selId}
                    messageMode={msgMode}
                    isMsgFocusNode={isMsgFocus}
                    isMsgDimNode={isMsgDim}
                    dragActive={!!dragN}
                    slotWidth={SLOT_W}
                    visualColumns={branchVisualCols}
                    // Use full parent→merge span so baseline can reach under merge
                    gridCols={spanCols}
                    showBaseline
                    // Extend baseline to merge column on closed branches
                    baselineEndCol={
                      bi.done
                        ? Math.max(0, Math.min(spanCols - 1, mCol - pCol))
                        : undefined
                    }
                  />
                </div>
              )}
              {bi.done && !msgMode && (
                <div className="flex items-center gap-2 mt-0.5 ml-2">
                  <button
                    className="text-[9px] text-muted-foreground hover:text-foreground flex items-center gap-0.5"
                    onClick={() => ro(bi.bc.chain_id)}
                  >
                    <RotateCcw className="h-2 w-2" />Reopen
                  </button>
                </div>
              )}
            </div>
          )
        })}
      </div>
    )
  }

  // Sidebar geometry — stays inside parent (page has px-10); keep right inset so border is not clipped.
  const SIDEBAR_W = 300
  const SIDEBAR_GAP_LEFT = 12
  const SIDEBAR_INSET_RIGHT = 4
  const SIDEBAR_INSET_BOTTOM = 12
  const rightPanelOpen = msgMode || panelOpen
  const shellW = rightPanelOpen
    ? SIDEBAR_W + SIDEBAR_GAP_LEFT + SIDEBAR_INSET_RIGHT
    : 0

  return (
    <div className="h-full min-h-0 flex overflow-hidden bg-muted/5">
      {/* Canvas — click empty area closes sidebar / exits group focus */}
      <div
        className="flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden relative"
        onClick={(e) => {
          const t = e.target as HTMLElement
          if (t.closest('[data-node-card]')) return
          if (t.closest('button')) return
          if (t.closest('[data-groups-menu]')) return
          if (t.closest('[data-message-stream-sidebar]')) return
          if (panDrag.current?.moved) return
          if (msgMode) {
            setMsgFocus({ kind: 'main' })
            return
          }
          if (focusGroupId) handleFocusGroup(null)
          if (selId) closeSidebar()
        }}
      >
        {/* Toolbar */}
        <div className="absolute top-2 right-2 z-30 flex items-center gap-2" data-groups-menu>
          <button
            type="button"
            title={msgMode ? 'Exit message stream' : 'Message stream'}
            className={cn(
              'h-8 px-2 rounded-md border text-[11px] flex items-center gap-1.5 transition-colors',
              msgMode
                ? 'border-primary bg-primary/10 text-primary'
                : 'border-border bg-background/90 text-muted-foreground hover:text-foreground'
            )}
            onClick={(e) => {
              e.stopPropagation()
              toggleMsgMode()
            }}
          >
            <MessageSquareText className="h-3.5 w-3.5" />
            Messages
          </button>
          <GroupsMenu
            collectionId={collectionId}
            groups={groups}
            allNodes={allNodes}
            focusGroupId={focusGroupId}
            onFocusGroup={handleFocusGroup}
            onGroupsChanged={() => fetch({ silent: true })}
          />
        </div>

        <DndContext
          sensors={ss}
          collisionDetection={collisionDetection}
          onDragStart={ds}
          onDragOver={dOver}
          onDragEnd={de}
          onDragCancel={dCancel}
        >
          <div
            ref={viewportRef}
            className="flex-1 min-h-0 overflow-hidden cursor-grab active:cursor-grabbing select-none"
            style={{
              backgroundImage: 'radial-gradient(circle,rgba(128,128,128,0.08) 1px,transparent 1px)',
              backgroundSize: '20px 20px',
              backgroundPosition: `${tx}px ${ty}px`,
              WebkitUserSelect: 'none',
              userSelect: 'none',
            }}
            onPointerDown={(e) => {
              if (e.button !== 0) return
              const t = e.target as HTMLElement
              // Never pan when interacting with nodes / chrome / dnd-kit
              if (
                t.closest('[data-node-card]') ||
                t.closest('[data-sw-dragging]') ||
                t.closest('button') ||
                t.closest('[data-groups-menu]') ||
                t.closest('[data-message-stream-sidebar]')
              ) {
                return
              }
              // Prevent browser text selection while panning the canvas
              e.preventDefault()
              window.getSelection()?.removeAllRanges()
              panDrag.current = {
                x: e.clientX,
                y: e.clientY,
                tx0: txRef.current,
                ty0: tyRef.current,
                moved: false,
              }
              ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
            }}
            onPointerMove={(e) => {
              const d = panDrag.current
              if (!d) return
              const dx = e.clientX - d.x
              const dy = e.clientY - d.y
              if (Math.hypot(dx, dy) > 4) d.moved = true
              if (d.moved) {
                e.preventDefault()
                window.getSelection()?.removeAllRanges()
                setTx(Math.round(d.tx0 + dx))
                setTy(Math.round(d.ty0 + dy))
              }
            }}
            onPointerUp={() => {
              panDrag.current = null
            }}
            onPointerCancel={() => {
              panDrag.current = null
            }}
          >
            {/* World layer: pan + zoom */}
            <div
              style={{
                // translate3d + rounded values reduce subpixel blur on scaled text
                transform: `translate3d(${Math.round(tx)}px, ${Math.round(ty)}px, 0) scale(${scale})`,
                transformOrigin: '0 0',
                width: contentW + canvasPadX * 2,
                minHeight: Math.max((vpSize?.h ?? 600) + canvasPadY, 1),
                paddingLeft: canvasPadX,
                paddingRight: canvasPadX,
                paddingTop: canvasPadY,
                paddingBottom: canvasPadY,
                boxSizing: 'border-box',
                // Avoid permanent will-change layer which often blurs text under scale
                backfaceVisibility: 'hidden',
                WebkitFontSmoothing: 'antialiased',
              }}
            >
              <div
                ref={layoutRef}
                className="relative"
                style={{ width: contentW, minWidth: contentW }}
              >
                {/* Closed-loop branch connectors (behind cards) */}
                <svg
                  className="absolute inset-0 pointer-events-none z-0 overflow-visible"
                  width="100%"
                  height="100%"
                  aria-hidden
                >
                  {connectors.map(c => (
                    <path
                      key={c.chainId}
                      d={c.d}
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={1}
                      className="text-muted-foreground/30"
                    />
                  ))}
                </svg>

                {/* Above lanes (outer → inner) + gap toward main */}
                {aboveLevels.length > 0 && (
                  <div className="flex flex-col" style={{ gap: 0 }}>
                    {aboveLevels.map(lvl => renderLane(lvl))}
                    <div style={{ height: CONN_GAP }} />
                  </div>
                )}

                {/* Main chain — equal-width columns shared with branch lanes (fixes spacer misalignment) */}
                <div ref={mainRowRef} className="relative z-[1]">
                  <ChainRow
                    chainData={mns}
                    chainId={mc.chain_id}
                    isBranch={false}
                    onNodeClick={clk}
                    onAddNode={msgMode ? () => {} : addN}
                    onCreateChain={msgMode ? () => {} : cc}
                    groups={groups}
                    focusGroupId={focusGroupId}
                    isNodeInFocus={isNodeInFocus}
                    selId={selId}
                    messageMode={msgMode}
                    isMsgFocusNode={isMsgFocus}
                    isMsgDimNode={isMsgDim}
                    visualColumns={mainNodeCol}
                    slotWidth={SLOT_W}
                    gridCols={gridCols}
                    dragActive={!!dragN}
                  />
                </div>

                {/* Below lanes (inner → outer) + gap from main */}
                {belowLevels.length > 0 && (
                  <div className="flex flex-col" style={{ gap: 0 }}>
                    <div style={{ height: CONN_GAP }} />
                    {belowLevels.map(lvl => renderLane(lvl))}
                  </div>
                )}
              </div>
            </div>
          </div>
          <DragOverlay dropAnimation={null} style={{ cursor: 'grabbing' }}>
            {dragN ? (
              <div className="opacity-95 shadow-xl rounded-md ring-2 ring-primary/30">
                <NodeCard
                  node={dragN}
                  groupSource={groupSourceForNode(groups, dragN.group_id)}
                  isSelected
                  isCompleted={false}
                  isDragging
                  showDragGrip
                  onClick={() => {}}
                />
              </div>
            ) : null}
          </DragOverlay>
        </DndContext>
      </div>

      {/*
        Right dock shell: always the rightmost flex child.
        Width animates open/closed; inner panel slides.
        paddingLeft = gap from canvas, paddingRight = inset from page edge, paddingBottom = bottom margin.
      */}
      <div
        className="h-full min-h-0 shrink-0 overflow-hidden transition-[width] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]"
        style={{ width: shellW }}
        onTransitionEnd={(e) => {
          if (e.target !== e.currentTarget) return
          if (e.propertyName === 'width' && !panelOpen) {
            setPanelNodeId(null)
          }
        }}
      >
        {(msgMode || panelNodeId) && (
          <div
            className="h-full box-border flex justify-end"
            style={{
              width: SIDEBAR_W + SIDEBAR_GAP_LEFT + SIDEBAR_INSET_RIGHT,
              paddingLeft: SIDEBAR_GAP_LEFT,
              paddingRight: SIDEBAR_INSET_RIGHT,
              paddingBottom: SIDEBAR_INSET_BOTTOM,
            }}
          >
            <div
              className={cn(
                'h-full shrink-0 transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]',
                msgMode || panelAnimOpen ? 'translate-x-0' : 'translate-x-[110%]',
              )}
              style={{ width: SIDEBAR_W }}
              onClick={(e) => e.stopPropagation()}
            >
              {msgMode ? (
                <MessageStreamSidebar
                  collectionId={collectionId}
                  chains={chains}
                  chainNodes={chainNodesMap}
                  focus={msgFocus}
                  onClose={() => setMsgMode(false)}
                  onFocusChange={setMsgFocus}
                />
              ) : (
                <NodeDetailSidebar
                  collectionId={collectionId}
                  nodeId={panelNodeId}
                  onClose={closeSidebar}
                  onNodeUpdated={ref}
                  getGroupName={gn}
                  groups={groups}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {/* Portaled dialogs (outside flex flow) */}
      <AddNodeDialog
        collectionId={collectionId}
        chainId={addTgt?.chainId ?? ''}
        afterOrder={addTgt?.afterOrder ?? -1}
        open={addOpen}
        onOpenChange={setAddOpen}
        onCreated={ncr}
        groups={groups}
        onGroupsChanged={() => fetch({ silent: true })}
      />
      <CreateChainDialog collectionId={collectionId} parentChainId={ccTgt?.parentChainId ?? ''} parentNodeId={ccTgt?.parentNodeId ?? ''} open={ccOpen} onOpenChange={setCcOpen} onCreated={ccr} />
      <EndChainDialog
        collectionId={collectionId}
        chainNodeId={ecTgt?.endNodeId ?? ''}
        nodes={ecTgt?.nodes ?? []}
        open={ecOpen}
        onOpenChange={setEcOpen}
        onComplete={ecr}
        groups={groups}
      />

      <Dialog
        open={!!confirmDeleteBranchId}
        onOpenChange={(v) => {
          if (!v) setConfirmDeleteBranchId(null)
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-sm">
              Delete branch “{confirmDeleteBranchTitle}”?
            </DialogTitle>
          </DialogHeader>
          <p className="text-xs text-muted-foreground">
            This empty branch will be removed. You can create a new branch from
            the same node later.
          </p>
          <DialogFooter className="gap-2 sm:justify-end">
            <Button
              variant="outline"
              size="xs"
              onClick={() => setConfirmDeleteBranchId(null)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              size="xs"
              onClick={() => {
                if (confirmDeleteBranchId) {
                  void deleteEmptyBranch(confirmDeleteBranchId)
                }
              }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

interface CRP {
  /** Nodes in **display order** (parent applies server sort or live drag order). Do not re-sort. */
  chainData: Node[]
  chainId: string
  isBranch: boolean
  isCompleted?: boolean
  onNodeClick: (id: string) => void
  onAddNode: (cid: string, after: number) => void
  /** Last branch node hover: merge / end chain */
  onMergeBranch?: () => void
  onCreateChain: (pCid: string, pNid: string) => void
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
        'flex items-center justify-center self-center w-4 h-14 rounded transition-colors shrink-0 -mr-1',
        isOver ? 'bg-primary/15 ring-1 ring-primary/40' : 'bg-transparent',
      )}
      title="Drop at start of chain"
    >
      <div className={cn('w-0.5 h-8 rounded-full', isOver ? 'bg-primary/50' : 'bg-transparent')} />
    </div>
  )
}

function ChainEndDrop({ chainId }: { chainId: string }) {
  const { setNodeRef, isOver } = useDroppable({ id: endDropId(chainId) })
  return (
    <div
      ref={setNodeRef}
      className={cn(
        'flex items-center justify-center self-center mx-0.5 w-6 h-14 rounded transition-colors shrink-0',
        isOver ? 'bg-primary/15 ring-1 ring-primary/40' : 'bg-transparent',
      )}
      title="Drop at end of chain"
    >
      {/* Only show a drop hint when hovering — idle vertical bar looked like a broken connector */}
      <div
        className={cn(
          'w-0.5 h-8 rounded-full transition-colors',
          isOver ? 'bg-primary/50' : 'bg-transparent',
        )}
      />
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
}: {
  chainId: string
  onAdd: () => void
}) {
  const { setNodeRef, isOver } = useDroppable({ id: endDropId(chainId) })
  return (
    <button
      ref={setNodeRef}
      type="button"
      data-branch-add
      className={cn(
        'w-10 h-10 shrink-0 rounded-md border border-dashed',
        'bg-background flex items-center justify-center transition-colors shadow-sm',
        isOver
          ? 'border-primary/50 text-primary ring-2 ring-primary/20'
          : 'border-muted-foreground/30 text-muted-foreground/50 hover:text-muted-foreground/80 hover:border-muted-foreground/50',
      )}
      onClick={onAdd}
      title="Add node"
    >
      <Plus className="h-4 w-4" />
    </button>
  )
}

function ChainRow({
  chainData,
  chainId,
  isBranch,
  isCompleted = false,
  onNodeClick,
  onAddNode,
  onMergeBranch,
  onCreateChain,
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
}: CRP) {
  // Display order is owned by parent (live drag preview). Sortable ids MUST match render order.
  const ids = chainData.map(n => n.node_id)
  const cols =
    gridColsProp ??
    Math.max(
      1,
      ...(visualColumns ?? []).map(c => c + 1),
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
          <button
            type="button"
            data-branch-target
            className="w-10 h-10 rounded-md border border-dashed border-muted-foreground/30 bg-background flex items-center justify-center text-muted-foreground/40 hover:text-muted-foreground/70 hover:border-muted-foreground/50 transition-colors shadow-sm"
            onClick={() => onAddNode(chainId, 0)}
            title="Add first node to branch"
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>
      )
    }
    return (
      <div className="flex items-center py-2">
        <ChainEndDrop chainId={chainId} />
        <button
          type="button"
          data-branch-target
          data-empty-chain-placeholder
          className={cn(
            'w-48 h-12 shrink-0 rounded-md border-2 border-dashed',
            'border-muted-foreground/30 bg-background/60',
            'flex items-center justify-center gap-2',
            'text-muted-foreground/55 hover:text-muted-foreground',
            'hover:border-muted-foreground/50 hover:bg-muted/30',
            'transition-colors shadow-sm',
          )}
          onClick={() => onAddNode(chainId, 0)}
          title="Add first node to timeline"
        >
          <Plus className="h-4 w-4 shrink-0" />
          <span className="text-xs font-medium truncate">Add first node</span>
        </button>
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
                display: 'grid',
                gridTemplateColumns: `repeat(${cols}, ${slotWidth}px)`,
                width: rowWidth,
                alignItems: 'center',
              }
            : undefined
        }
      >
        {/* Continuous baseline through visual columns (spans Layout-B gaps) */}
        {drawBaseline && (
          <div
            className="absolute top-1/2 h-px bg-muted-foreground/15 -translate-y-1/2 pointer-events-none z-0"
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
                  <div className="w-5 h-5 rounded-full border border-dashed border-muted-foreground/40 flex items-center justify-center text-muted-foreground/60 bg-background hover:border-primary/50 hover:text-primary shadow-sm">
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
                    isCompleted={isCompleted || node.node_type === 'end'}
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
                      node.node_type !== 'end'
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
                      isCompleted={isCompleted || node.node_type === 'end'}
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
                        node.node_type !== 'end'
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
            // Dashed square +, center on baseline (short connector from last card)
            <div
              className="absolute z-10 top-1/2 -translate-x-1/2 -translate-y-1/2"
              style={{ left: plusCenterX }}
            >
              <ChainEndAddButton
                chainId={chainId}
                onAdd={() => {
                  const l = chainData[chainData.length - 1]
                  onAddNode(chainId, l ? l.order : 0)
                }}
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
              />
            </div>
          ) : (
            <>
              <ChainEndDrop chainId={chainId} />
              <div className="flex flex-row items-center gap-0 shrink-0 relative z-10">
                <div className="flex items-center justify-center self-center mx-1">
                  <div className="w-8 h-px bg-muted-foreground/15" />
                </div>
                <button
                  type="button"
                  data-branch-add
                  className="w-10 h-10 shrink-0 rounded-md border border-dashed border-muted-foreground/30 bg-background flex items-center justify-center text-muted-foreground/50 hover:text-muted-foreground/80 hover:border-muted-foreground/50 transition-colors shadow-sm"
                  onClick={() => {
                    const l = chainData[chainData.length - 1]
                    onAddNode(chainId, l ? l.order : 0)
                  }}
                  title="Add node"
                >
                  <Plus className="h-4 w-4" />
                </button>
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
  children: React.ReactNode | ((isDragging: boolean) => React.ReactNode)
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
    onPointerDown?: (e: React.PointerEvent) => void
    [key: string]: unknown
  }

  // List order drives layout; kill strategy transforms while any drag is active.
  const styleTransform =
    dragActive || isDragging ? undefined : CSS.Transform.toString(transform)

  return (
    <div
      ref={setNodeRef}
      data-sw-dragging={isDragging ? 'true' : 'false'}
      data-sortable-id={node.node_id}
      style={{
        transform: styleTransform,
        transition: dragActive
          ? 'none'
          : transition,
        touchAction: disableDrag ? undefined : 'none',
      }}
      className={cn(
        'flex flex-row items-center gap-0',
        !disableDrag && 'touch-none',
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
          <div className="w-8 h-px bg-muted-foreground/15" />
          {showInsertBtn && onInsert && (
            <button
              className="absolute inset-0 flex items-center justify-center opacity-0 group-hover/conn:opacity-100 transition-opacity"
              onClick={e => {
                e.stopPropagation()
                onInsert()
              }}
              title="Insert node after"
            >
              <div className="w-4 h-4 rounded-full border border-dashed border-muted-foreground/30 flex items-center justify-center text-muted-foreground/50 bg-background hover:border-primary/50 hover:text-primary">
                <Plus className="h-2.5 w-2.5" />
              </div>
            </button>
          )}
        </div>
      )}
      {/* Placeholder keeps the slot (covers baseline); DragOverlay is the floating card */}
      {isDragging ? (
        <div
          className="w-48 h-12 rounded-md border-2 border-dashed border-primary/40 bg-background shrink-0"
          aria-hidden
        />
      ) : typeof children === 'function' ? (
        children(false)
      ) : (
        children
      )}
    </div>
  )
}
