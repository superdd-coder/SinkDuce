// The browser plans an insertion; the server remains authoritative for the hierarchy.
export interface OutlineRow { id: string; kind: string; group_id?: string; parent_id?: string }
export type QaMove = { group_id?: string; parent_id?: string; before_id?: string }
export type QaDrop = { anchor: string; edge: "before" | "after" | "inside"; payload: QaMove }

export function qaMovingIds(items: readonly OutlineRow[], id: string): Set<string> {
  const ids = new Set([id])
  const group = items.find(row => row.id === id)?.kind === "group"
  let changed = true
  while (changed) {
    changed = false
    for (const row of items) {
      if (!ids.has(row.id) && ((group && row.group_id === id) || (row.parent_id && ids.has(row.parent_id)))) {
        ids.add(row.id); changed = true
      }
    }
  }
  return ids
}

export function qaDropPlan(items: readonly OutlineRow[], id: string, targetId: string, edge: "before" | "after"): QaDrop | null {
  const source = items.find(row => row.id === id)
  const members = qaMovingIds(items, id)
  if (!source || members.has(targetId)) return null
  const remaining = items.filter(row => !members.has(row.id))
  if (targetId === "__ungrouped") return source.kind === "group" ? null : { anchor: targetId, edge: "inside", payload: { group_id: "", parent_id: "" } }
  let target = remaining.find(row => row.id === targetId)
  if (!target) return null
  if (source.kind !== "group" && target.kind === "group") {
    return { anchor: target.id, edge: "inside", payload: { group_id: target.id, parent_id: "" } }
  }
  if (source.kind === "group" && target.kind !== "group") return null
  // Reordering never assigns a new parent implicitly. Root rows land around a whole family.
  while (target.parent_id && target.parent_id !== (source.parent_id || "")) {
    const parent = remaining.find(row => row.id === target!.parent_id)
    if (!parent) return null
    target = parent
  }
  if ((target.parent_id || "") !== (source.parent_id || "")) return null
  const family = qaMovingIds(remaining, target.id)
  const siblings = remaining.filter(row => source.kind === "group" ? row.kind === "group" :
    row.kind !== "group" && (row.group_id || "") === (target!.group_id || "") && (row.parent_id || "") === (target!.parent_id || ""))
  const before = edge === "before" ? target.id : siblings[siblings.findIndex(row => row.id === target!.id) + 1]?.id
  const payload: QaMove = source.kind === "group" ? {} : { group_id: target.group_id || "", parent_id: target.parent_id || "" }
  if (before) payload.before_id = before
  return { anchor: edge === "before" ? target.id : remaining.filter(row => family.has(row.id)).at(-1)!.id, edge, payload }
}

export function applyQaMove<T extends OutlineRow>(items: readonly T[], id: string, payload: QaMove): T[] {
  const members = qaMovingIds(items, id)
  const block = items.filter(row => members.has(row.id)).map(row => ({ ...row }))
  const remaining = items.filter(row => !members.has(row.id))
  const item = block.find(row => row.id === id)
  if (!item) return [...items]
  if (item.kind !== "group") {
    const parent = payload.parent_id ?? item.parent_id ?? ""
    const group = payload.group_id ?? remaining.find(row => row.id === parent)?.group_id ?? item.group_id ?? ""
    item.parent_id = parent
    block.forEach(row => { row.group_id = group })
  }
  let index = payload.before_id ? remaining.findIndex(row => row.id === payload.before_id) : -1
  if (index < 0) {
    if (item.kind === "group") index = remaining.length
    else {
      const family = item.parent_id ? qaMovingIds(remaining, item.parent_id) : null
      index = remaining.reduce((last, row, position) => (family ? family.has(row.id) :
        row.id === item.group_id || (row.kind !== "group" && (row.group_id || "") === (item.group_id || ""))) ? position + 1 : last, 0)
    }
  }
  return [...remaining.slice(0, index), ...block, ...remaining.slice(index)]
}
