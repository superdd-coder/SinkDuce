export interface CommentRailItem {
  id: string
  anchorTop: number
  fullHeight: number
  height: number
}
export interface CommentRailGroup { id: string; ids: string[]; top: number; bottom: number }
export interface PositionedComment extends CommentRailItem { top: number; bottom: number; group: string }

/** Groups follow nearby source anchors, independently of editors or expanded history. */
export function layoutCommentRail(items: readonly CommentRailItem[], gap = 12) {
  const ordered = items.map((item, index) => ({ ...item, index,
    anchorTop: Math.max(0, Number.isFinite(item.anchorTop) ? item.anchorTop : 0),
    fullHeight: Math.max(40, Number.isFinite(item.fullHeight) ? item.fullHeight : 180),
    height: Math.max(40, Number.isFinite(item.height) ? item.height : 180),
  })).sort((a, b) => a.anchorTop - b.anchorTop || a.index - b.index)
  const clusters: { id: string; items: typeof ordered; end: number }[] = []
  for (const item of ordered) {
    const cluster = clusters.at(-1)
    if (cluster && item.anchorTop < cluster.end + gap) {
      cluster.items.push(item)
      cluster.end = item.anchorTop + 176
    } else clusters.push({ id: item.id, items: [item], end: item.anchorTop + 176 })
  }
  const cards: PositionedComment[] = [], groups: CommentRailGroup[] = []
  let previousBottom = -gap
  for (const cluster of clusters) {
    const start = Math.max(cluster.items[0].anchorTop, previousBottom + gap)
    for (const item of cluster.items) {
      const top = Math.max(item.anchorTop, previousBottom + gap)
      const bottom = top + item.height
      cards.push({ ...item, top, bottom, group: cluster.id })
      previousBottom = bottom
    }
    const bottom = previousBottom
    groups.push({ id: cluster.id, ids: cluster.items.map(item => item.id), top: start, bottom })
    // A small group control has its own place and never covers a following card.
    if (cluster.items.length > 1) previousBottom += 32
  }
  return { cards, groups, height: Math.max(0, previousBottom) }
}
