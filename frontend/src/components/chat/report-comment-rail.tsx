import { Children, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from 'react'
import { ChevronDown, ChevronUp, MessageSquareText } from 'lucide-react'
import { useT } from '@/i18n/use-t'
import { passageRange, reviewPassageTarget } from '@/lib/report-review-dom'
import { layoutCommentRail } from '@/lib/report-comment-layout'
import type { CommentAnchor } from '@/lib/report-review'

export interface CommentRailEntry {
  id: string
  anchor: CommentAnchor
  text: string
  index: number
  draft?: boolean
  history?: boolean
  stale?: boolean
  /** Exact edited-location hint for a previous-version comment in tracking mode. */
  changeId?: string
  targetLine?: number
  groupId?: string
}
interface Geometry { anchors: Record<string, number>; full: Record<string, number>; heights: Record<string, number>; documentHeight: number }
const DRAFT_HEIGHT = 224
const COMPACT_HEIGHT = 44
const sameNumbers = (a: Record<string, number>, b: Record<string, number>) =>
  Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(key => Math.abs(a[key] - b[key]) < 0.5)

/** Shares the document scroll: every card keeps a source anchor and avoids earlier cards. */
export function ReportCommentRail({ entries, activeId, focusNonce=0, documentRef, railRef, draftRange, open, tracking,
  onActivate, children }: {
  entries: CommentRailEntry[]
  activeId: string
  focusNonce?: number
  documentRef: RefObject<HTMLDivElement | null>
  railRef: RefObject<HTMLElement | null>
  draftRange: RefObject<Range | null>
  open: boolean
  tracking: boolean
  onActivate: (id: string) => void
  children: ReactNode
}) {
  const t = useT()
  const [cardExpansion, setCardExpansion] = useState<Record<string, {open:boolean;focus:string}>>({})
  const focusKey=`${activeId}/${focusNonce}`
  const [geometry, setGeometry] = useState<Geometry>({ anchors: {}, full: {}, heights: {}, documentHeight: 0 })
  const observedCards = useRef(new Map<string, HTMLElement>())
  const canonicalHeights = useRef<Record<string, number>>({})
  const focusDraft = useRef(false)
  const pendingFrame = useRef<number | null>(null)
  const nodes = Children.toArray(children)
  const raw = useMemo(() => entries.map(entry => ({
    id: entry.id,
    anchorTop: geometry.anchors[entry.id] ?? 0,
    fullHeight: geometry.full[entry.id] ?? (entry.draft ? DRAFT_HEIGHT : 176),
    height: geometry.full[entry.id] ?? (entry.draft ? DRAFT_HEIGHT : 176),
  })), [entries, geometry.anchors, geometry.full])
  const clusters = useMemo(() => layoutCommentRail(raw).groups, [raw])
  const expanded = useMemo(() => {
    const ids = new Set<string>()
    for (const group of clusters) {
      const active = group.ids.includes(activeId) ? activeId : group.ids[0]
      for (const id of group.ids) {
        const choice=cardExpansion[id]
        const focused=id===activeId&&choice?.focus!==focusKey
        if (entries.find(entry => entry.id === id)?.draft || focused || (choice?.open ?? (group.ids.length === 1 || id === active))) ids.add(id)
      }
    }
    return ids
  }, [clusters, entries, activeId, cardExpansion,focusKey])
  const layout = useMemo(() => layoutCommentRail(raw.map(item => ({ ...item,
    height: expanded.has(item.id) ? geometry.heights[item.id] ?? item.fullHeight : COMPACT_HEIGHT,
  }))), [raw, expanded, geometry.heights])

  useLayoutEffect(() => {
    const source = documentRef.current, rail = railRef.current
    if (!open || !source || !rail) return
    let alive = true
    const measure = () => {
      if (!alive) return
      const railTop = rail.getBoundingClientRect().top
      const anchors: Record<string, number> = {}, heights: Record<string, number> = {}
      for (const entry of entries) {
        const block = reviewPassageTarget(source,entry) || (entry.changeId?source.querySelector<HTMLElement>(`[data-review-change="${entry.changeId}"]`):null)
        let rect = block?.getBoundingClientRect()
        if (entry.draft && draftRange.current && source.contains(draftRange.current.startContainer)) {
          const rangeRect = draftRange.current.getClientRects?.()[0] ?? draftRange.current.getBoundingClientRect()
          if (rangeRect.height || rangeRect.width) rect = rangeRect
        } else if (block && entry.targetLine===undefined && !entry.changeId && entry.anchor.kind !== 'block') {
          const range = passageRange(source, entry.anchor)
          const rangeRect = range?.getClientRects?.()[0]
          if (rangeRect) rect = rangeRect
        }
        // A removed or ambiguous old selection stays with its documented change,
        // or at the rail's start; its line is never guessed in a different revision.
        anchors[entry.id] = Math.max(0, (rect?.top ?? railTop) - railTop)
        const card = observedCards.current.get(entry.id)
        if (card?.dataset.expanded === 'true') {
          const height = card.getBoundingClientRect().height
          if (height > 0) { canonicalHeights.current[entry.id] = height; heights[entry.id] = height }
        }
      }
      const full = Object.fromEntries(entries.map(entry => [entry.id, canonicalHeights.current[entry.id] ?? (entry.draft ? DRAFT_HEIGHT : 176)]))
      const documentHeight = source.getBoundingClientRect().height
      setGeometry(current => sameNumbers(current.anchors, anchors) && sameNumbers(current.full, full) &&
        sameNumbers(current.heights, heights) && Math.abs(current.documentHeight - documentHeight) < 0.5
        ? current : { anchors, full, heights, documentHeight })
    }
    const schedule = () => {
      if (pendingFrame.current !== null) return
      pendingFrame.current = requestAnimationFrame(() => { pendingFrame.current = null; measure() })
    }
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule)
    observer?.observe(source)
    for (const node of source.querySelectorAll<HTMLElement>('[data-report-line]')) observer?.observe(node)
    for (const node of observedCards.current.values()) observer?.observe(node)
    window.addEventListener('resize', schedule)
    void document.fonts?.ready.then(() => { if (alive) schedule() })
    return () => {
      alive = false
      observer?.disconnect()
      window.removeEventListener('resize', schedule)
      if (pendingFrame.current !== null) cancelAnimationFrame(pendingFrame.current)
      pendingFrame.current = null
    }
  }, [entries, open, expanded, documentRef, railRef, draftRange, tracking])

  useLayoutEffect(() => {
    if (!entries.some(entry => entry.draft)) { focusDraft.current = false; return }
    if (!open || focusDraft.current || !railRef.current || !('draft' in geometry.anchors)) return
    focusDraft.current = true
    railRef.current.querySelector<HTMLTextAreaElement>('[data-review-card="draft"] textarea')?.focus({ preventScroll: true })
  }, [entries, open, geometry.anchors, railRef])

  return <aside ref={railRef} className="pm-review-rail" aria-label={t('chat.review.comments')} inert={!open}
    style={{ height: open ? Math.max(geometry.documentHeight, layout.height + 40, entries.length ? 0 : 100) : 0 }}>
    {layout.cards.map(card => {
      const index = entries.findIndex(entry => entry.id === card.id)
      const entry = entries[index]
      const isExpanded = expanded.has(entry.id)
      return <div key={entry.id} className="pm-review-positioned-card" data-review-card={entry.id}
        data-expanded={isExpanded} style={{ top: card.top }} ref={node => {
          if (node) observedCards.current.set(entry.id, node)
          else observedCards.current.delete(entry.id)
        }}>
        <div className="pm-review-card-content" hidden={!isExpanded}>{nodes[index]}</div>
        {!isExpanded && <button type="button" className={`pm-review-compact${entry.history ? ' is-history' : ''}`}
          aria-expanded={false} onClick={() => {setCardExpansion(previous=>({...previous,[entry.id]:{open:true,focus:focusKey}}));onActivate(entry.id)}}>
          <span className="pm-review-number">{entry.index + 1}</span><span>{entry.text}</span><ChevronDown className="size-3" />
        </button>}
      </div>
    })}
    {layout.groups.filter(group => group.ids.length > 1).map(group => <button type="button" key={group.id}
      className="pm-review-group-toggle" style={{ top: group.bottom + 6 }} aria-expanded={group.ids.every(id=>expanded.has(id))}
      onClick={() => setCardExpansion(previous => ({...previous,...Object.fromEntries(group.ids.map(id=>[id,{open:!group.ids.every(id=>expanded.has(id)),focus:focusKey}]))}))}>
      {group.ids.every(id=>expanded.has(id)) ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
      {t(group.ids.every(id=>expanded.has(id)) ? 'chat.review.foldGroup' : 'chat.review.expandGroup', { n: group.ids.length })}
    </button>)}
    {!entries.length && <div className="pm-review-empty"><MessageSquareText className="size-4" /><p>{t('chat.review.empty')}</p></div>}
  </aside>
}
