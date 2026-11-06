import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react"
import { ChevronDown, ChevronRight, GripVertical, Plus, Pencil } from "lucide-react"
import type { QaListDetail, QaOutlineItem, QaQuestion } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { QaActionMenu, QaMenuItem } from "./qa-controls"
import { useT } from "@/i18n/use-t"

import { DndContext, DragOverlay, KeyboardSensor, PointerSensor, closestCenter, pointerWithin, useDraggable, useDroppable, useSensor, useSensors, type DragMoveEvent, type KeyboardCoordinateGetter } from "@dnd-kit/core"
import { applyQaMove, qaDropPlan, qaMovingIds, type QaMove as Move, type QaDrop } from "@/lib/qa-outline-drag"

const keyboardCoordinates = (items: QaOutlineItem[], event: Parameters<KeyboardCoordinateGetter>[0], { context }: Parameters<KeyboardCoordinateGetter>[1]) => {
  if (!["ArrowUp", "ArrowDown"].includes(event.code)) return
  event.preventDefault()
  const targets = context.droppableContainers.getEnabled().filter(row => !!qaDropPlan(items, String(context.active?.id), String(row.id), "before"))
    .map(row => ({ id: row.id, rect: context.droppableRects.get(row.id) })).filter(row => row.rect)
    .sort((a, b) => a.rect!.top - b.rect!.top)
  const current = context.over?.id || context.active?.id
  const activeRect = context.collisionRect
  const at = targets.findIndex(row => row.id === current)
  const target = at >= 0 ? targets[at + (event.code === "ArrowDown" ? 1 : -1)] :
    event.code === "ArrowDown" ? targets.find(row => row.rect!.top > (activeRect?.top || 0)) : targets.findLast(row => row.rect!.top < (activeRect?.top || 0))
  if (target?.rect) return { x: target.rect.left, y: target.rect.top }
}

function QaDragHandle({ id, disabled, onClick }: { id: string; disabled: boolean; onClick: () => void }) {
  const t = useT()
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id, disabled })
  return <Button ref={setNodeRef} variant="ghost" size="icon-xs" className="qa-drag-handle" disabled={disabled}
    {...attributes} {...listeners} aria-label={t("qa.moveItem")} onClick={() => { if (!isDragging) onClick() }}><GripVertical className="size-3" strokeWidth={1.5} /></Button>
}
function QaDropRow({ id, disabled, className, children, style, depth }: { id: string; disabled: boolean; className: string; children: ReactNode; style?: CSSProperties; depth?: number }) {
  const { setNodeRef } = useDroppable({ id, disabled })
  return <div ref={setNodeRef} data-outline-id={id} data-depth={depth} className={className} style={style}>{children}</div>
}

export function QaOutlineList({ data, visible, busy, filtered, renderQuestion, onMove, onEdit, onCreate, onAddQuestion }: {
  data: QaListDetail; visible: QaQuestion[]; busy: boolean; filtered: boolean;
  renderQuestion: (question: QaQuestion) => ReactNode;
  onMove: (id: string, payload: Move) => Promise<boolean>;
  onEdit: (id: string, payload: Record<string, unknown>) => Promise<boolean>;
  onCreate: (kind: "group" | "note", text: string, group: string) => Promise<boolean>;
  onAddQuestion: (group: string, parent?: string) => void;
}) {
  const t = useT()
  const [optimistic, setOptimistic] = useState<QaOutlineItem[] | null>(null)
  const items: QaOutlineItem[] = optimistic || data.outline || data.questions.map(q => ({ id: q.id, kind: "question" }))
  const groups = items.filter(i => i.kind === "group")
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [dragging, setDragging] = useState<string | null>(null)
  const [drop, setDrop] = useState<QaDrop | null>(null)
  const root = useRef<HTMLDivElement>(null)
  const pointer = useRef<{ x: number; y: number } | null>(null)
  const positions = useRef(new Map<string, number>())
  const keyboardEdge = useRef<"before" | "after">("before")
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor, { coordinateGetter: (event, args) => {
    keyboardEdge.current = event.code === "ArrowDown" ? "after" : "before"
    return keyboardCoordinates(items, event, args)
  } }))
  useLayoutEffect(() => {
    const next = new Map<string, number>()
    root.current?.querySelectorAll<HTMLElement>("[data-outline-id]").forEach(element => {
      const id = element.dataset.outlineId!
      const top = element.offsetTop
      next.set(id, top)
      const previous = positions.current.get(id)
      if (previous !== undefined && previous !== top && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        element.animate([{ transform: `translateY(${previous - top}px)` }, { transform: "translateY(0)" }], { duration: 240, easing: "cubic-bezier(.2,.8,.2,1)" })
      }
    })
    positions.current = next
  }, [items])
  const [editing, setEditing] = useState<{ id?: string; kind: "group" | "note"; group: string; text: string } | null>(null)
  const [moving, setMoving] = useState<string | null>(null)
  const allowed = new Set(visible.map(q => q.id))
  const byId = new Map(items.map(i => [i.id, i]))
  for (const q of visible) {
    let parent = byId.get(q.id)?.parent_id
    const seen = new Set<string>()
    while (parent && !seen.has(parent)) { seen.add(parent); allowed.add(parent); parent = byId.get(parent)?.parent_id }
  }
  const groupOptions = [{ value: "", label: t("qa.ungrouped") }, ...groups.map(g => ({ value: g.id, label: g.text || "" }))]
  const editable = data.can_edit && !busy
  function toggle(id: string) { setCollapsed(previous => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next }) }
  function depth(item: QaOutlineItem) { let n = 0, parent = item.parent_id; const seen = new Set<string>(); while (parent && !seen.has(parent)) { seen.add(parent); n++; parent = byId.get(parent)?.parent_id } return n }
  const movable = editable && !filtered && !optimistic
  function updateDrop(event: DragMoveEvent) {
    if (!event.over) { setDrop(null); return }
    const edge = pointer.current ? (pointer.current.y > event.over.rect.top + event.over.rect.height / 2 ? "after" : "before") : keyboardEdge.current
    setDrop(qaDropPlan(items, String(event.active.id), String(event.over.id), edge))
  }
  function targetClass(id: string) {
    const anchor = drop?.edge === "after" ? items.slice(0, items.findIndex(row => row.id === drop.anchor) + 1).findLast(row =>
      row.kind === "group" || (!collapsed.has(row.group_id || "") && (row.kind === "note" ? !filtered : allowed.has(row.id))))?.id : drop?.anchor
    return `${dragging && qaMovingIds(items, dragging).has(id) ? "is-moving" : ""} ${drop && anchor === id ? `qa-drop-${drop.edge}` : ""}`
  }
  function grip(item: QaOutlineItem) { return data.can_edit && <QaDragHandle id={item.id} disabled={!movable}
    onClick={() => setMoving(moving === item.id ? null : item.id)} /> }
  async function saveEdit() {
    if (!editing || !editing.text.trim()) return
    const ok = editing.id ? await onEdit(editing.id, { text: editing.text }) : await onCreate(editing.kind, editing.text, editing.group)
    if (ok) setEditing(null)
  }
  function editor() { return editing && <form className="qa-outline-editor" onSubmit={e => { e.preventDefault(); void saveEdit() }}>
    {editing.kind === "group" ? <Input autoFocus aria-label={t("qa.groupName")} value={editing.text} maxLength={200} onChange={e => setEditing({ ...editing, text: e.target.value })} /> : <Textarea autoFocus aria-label={t("qa.note")} value={editing.text} maxLength={32767} onChange={e => setEditing({ ...editing, text: e.target.value })} />}
    <div className="qa-actions"><Button size="sm" type="submit" disabled={busy || !editing.text.trim()}>{t("qa.save")}</Button><Button size="sm" variant="ghost" type="button" onClick={() => setEditing(null)}>{t("qa.cancel")}</Button></div>
  </form> }
  function moveControls(item: QaOutlineItem) { return moving === item.id && <div className="qa-outline-move">
    {item.kind === "group" ? <DropdownSelect aria-label={t("qa.moveBefore")} value="" options={[{ value: "", label: t("qa.moveBefore") }, ...groups.filter(g => g.id !== item.id).map(g => ({ value: g.id, label: g.text || "" }))]} onChange={before_id => { if (before_id) void onMove(item.id, { before_id }).then(ok => { if (ok) setMoving(null) }) }} /> : <>
      <DropdownSelect aria-label={t("qa.group")} value={item.group_id || ""} options={groupOptions} onChange={group_id => { void onMove(item.id, { group_id, parent_id: "" }).then(ok => { if (ok) setMoving(null) }) }} />
      {item.kind === "question" && <DropdownSelect aria-label={t("qa.parentQuestion")} value={item.parent_id || ""} options={[{ value: "", label: t("qa.independentQuestion") }, ...data.questions.filter(q => q.included && !qaMovingIds(items, item.id).has(q.id) && (q.group_id || "") === (item.group_id || "")).map(q => ({ value: q.id, label: q.summary || q.text }))]} onChange={parent_id => { void onMove(item.id, { parent_id }).then(ok => { if (ok) setMoving(null) }) }} />}
      <DropdownSelect aria-label={t("qa.moveBefore")} value="" options={[{ value: "", label: t("qa.moveBefore") }, ...items.filter(i => i.id !== item.id && i.kind !== "group" && (i.group_id || "") === (item.group_id || "") && (i.parent_id || "") === (item.parent_id || "")).map(i => ({ value: i.id, label: i.text || data.questions.find(q => q.id === i.id)?.text || "" }))]} onChange={before_id => { if (before_id) void onMove(item.id, { before_id }).then(ok => { if (ok) setMoving(null) }) }} />
    </>}
    <Button size="sm" variant="ghost" onClick={() => setMoving(null)}>{t("qa.cancel")}</Button>
  </div> }

  const hasUngrouped = items.some(item => item.kind !== "group" && !item.group_id && (item.kind === "note" ? !filtered : allowed.has(item.id)))
  const dragged = items.find(item => item.id === dragging)
  const dragLabel = dragged?.text || data.questions.find(q => q.id === dragging)?.summary || data.questions.find(q => q.id === dragging)?.text || ""
  return <DndContext sensors={sensors} autoScroll={{ threshold: { x: .08, y: .18 }, acceleration: 8 }}
    accessibility={{ screenReaderInstructions: { draggable: t("qa.dragInstructions") }, announcements: {
      onDragStart: () => t("qa.dragStarted"), onDragOver: ({ over }) => over ? t("qa.dragPosition") : undefined,
      onDragEnd: () => t("qa.dragEnded"), onDragCancel: () => t("qa.dragCancelled"),
    } }}
    collisionDetection={args => {
      pointer.current = args.pointerCoordinates
      const candidates = args.droppableContainers.filter(row => !!qaDropPlan(items, String(args.active.id), String(row.id), "before"))
      return (args.pointerCoordinates ? pointerWithin : closestCenter)({ ...args, droppableContainers: candidates })
    }}
    onDragStart={({ active }) => { setDragging(String(active.id)); setDrop(null); setMoving(null) }} onDragMove={updateDrop} onDragOver={updateDrop}
    onDragCancel={() => { setDragging(null); setDrop(null) }}
    onDragEnd={({ active }) => {
      setDragging(null); setDrop(null)
      if (!drop || !movable) return
      const next = applyQaMove(items, String(active.id), drop.payload)
      if (JSON.stringify(next) === JSON.stringify(items)) return
      setOptimistic(next)
      void onMove(String(active.id), drop.payload).catch(() => false).finally(() => setOptimistic(null))
    }}>
    <div ref={root} className={`qa-outline ${dragging ? "is-dragging" : ""}`}>
    {groups.length > 0 && <QaDropRow id="__ungrouped" disabled={!movable} className={`qa-outline-ungrouped ${hasUngrouped ? "" : "is-empty"} ${targetClass("__ungrouped")}`}>{t("qa.ungrouped")}</QaDropRow>}
    {items.map(item => {
      if (item.kind === "group") {
        if (filtered && !items.some(i => i.group_id === item.id && allowed.has(i.id))) return null
        return <QaDropRow key={item.id} id={item.id} disabled={!movable} className={`qa-outline-group ${targetClass(item.id)}`}><div className="qa-group-heading">
            {grip(item)}<button className="qa-group-toggle" aria-expanded={!collapsed.has(item.id)} onClick={() => toggle(item.id)}>{collapsed.has(item.id) ? <ChevronRight /> : <ChevronDown />}{item.display_number && <span className="qa-display-number">{item.display_number}</span>}<span>{item.text}</span><span className="qa-meta">{data.questions.filter(q => q.included && q.group_id === item.id).length}</span></button>
            {editable && <div className="qa-outline-tools"><Button size="icon-sm" variant="ghost" aria-label={t("qa.addQuestion")} title={t("qa.addQuestion")} onClick={() => onAddQuestion(item.id)}><Plus /></Button><QaActionMenu label={t("qa.more")}>
              <QaMenuItem onClick={() => setEditing({ id: item.id, kind: "group", group: "", text: item.text || "" })}>{t("qa.rename")}</QaMenuItem>
              <QaMenuItem onClick={() => setEditing({ kind: "note", group: item.id, text: "" })}>{t("qa.addNote")}</QaMenuItem>
              <QaMenuItem onClick={() => { void onEdit(item.id, { remove: true, replacement_id: "" }) }}>{t("qa.removeGroupToUngrouped")}</QaMenuItem>
            </QaActionMenu></div>}
          </div>
          {moveControls(item)}{editing?.id === item.id && editor()}
        </QaDropRow>
      }
      if (collapsed.has(item.group_id || "")) return null
      if (item.kind === "note") {
        if (filtered) return null
        return <QaDropRow key={item.id} id={item.id} disabled={!movable} className={`qa-outline-note ${targetClass(item.id)}`}>
          {grip(item)}{editing?.id === item.id ? editor() : <p>{item.text}</p>}
          {editable && <div className="qa-outline-tools"><QaActionMenu label={t("qa.more")}><QaMenuItem onClick={() => setEditing({ id: item.id, kind: "note", group: item.group_id || "", text: item.text || "" })}>{t("qa.edit")}</QaMenuItem><QaMenuItem onClick={() => { void onEdit(item.id, { remove: true }) }}>{t("qa.removeNote")}</QaMenuItem></QaActionMenu></div>}{moveControls(item)}
        </QaDropRow>
      }
      const question = data.questions.find(q => q.id === item.id)
      if (!question || !allowed.has(item.id)) return null
      const level = depth(item)
      return <QaDropRow key={item.id} id={item.id} disabled={!movable} depth={level} style={{ "--qa-depth": Math.min(level, 5) } as CSSProperties} className={`qa-outline-question ${targetClass(item.id)}`}>
        {grip(item)}{renderQuestion(question)}
        {editable && <div className="qa-outline-tools"><QaActionMenu label={t("qa.more")}><QaMenuItem onClick={() => onAddQuestion(item.group_id || "", item.id)}>{t("qa.addChild")}</QaMenuItem><QaMenuItem onClick={() => setMoving(item.id)}>{t("qa.moveItem")}</QaMenuItem></QaActionMenu></div>}
        {moveControls(item)}
      </QaDropRow>
    })}
    {editing && !editing.id && editor()}
    {editable && <div className="qa-outline-add"><Button size="sm" variant="ghost" onClick={() => setEditing({ kind: "group", group: "", text: "" })}><Plus />{t("qa.addGroup")}</Button><Button size="sm" variant="ghost" onClick={() => setEditing({ kind: "note", group: groups.at(-1)?.id || "", text: "" })}><Pencil />{t("qa.addNote")}</Button></div>}
    </div>
    <DragOverlay dropAnimation={null}>{dragging && <div className="qa-drag-preview"><GripVertical className="size-4" /><span>{dragLabel}</span></div>}</DragOverlay>
  </DndContext>
}
