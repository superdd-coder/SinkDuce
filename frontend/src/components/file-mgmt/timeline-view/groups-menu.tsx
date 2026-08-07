import { useMemo, useState } from "react"
import { ChevronDown, Pencil, Plus } from "lucide-react"
import type { Node, NodeGroup } from "@/types/file-mgmt"
import { cn } from "@/lib/utils"
import {
  GroupIconView,
  UNCATEGORIZED_ID,
  isSystemGroup,
} from "./group-icons"
import { GroupFormDialog } from "./group-form-dialog"

export type FocusGroupId = string | typeof UNCATEGORIZED_ID

interface GroupsMenuProps {
  collectionId: string
  groups: NodeGroup[]
  allNodes: Node[]
  focusGroupId: FocusGroupId | null
  onFocusGroup: (id: FocusGroupId | null) => void
  onGroupsChanged: () => void
}

export function GroupsMenu({
  collectionId,
  groups,
  allNodes,
  focusGroupId,
  onFocusGroup,
  onGroupsChanged,
}: GroupsMenuProps) {
  const [open, setOpen] = useState(false)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<NodeGroup | null>(null)

  const uncategorizedCount = useMemo(
    () => allNodes.filter((n) => !n.group_id && n.node_type === "event").length,
    [allNodes]
  )

  const boundFolderIds = useMemo(() => {
    const s = new Set<string>()
    for (const g of groups) {
      if (g.folder_id) s.add(g.folder_id)
    }
    return s
  }, [groups])

  // Meeting / Notes are always system (by flag or name)
  const systemGroups = groups.filter((g) => isSystemGroup(g))
  const userGroups = groups.filter((g) => !isSystemGroup(g))

  const handleSelect = (id: FocusGroupId) => {
    if (focusGroupId === id) {
      onFocusGroup(null)
    } else {
      onFocusGroup(id)
    }
    setOpen(false)
  }

  const openEdit = (g: NodeGroup) => {
    if (isSystemGroup(g)) return
    setEditing(g)
    setFormOpen(true)
    setOpen(false)
  }

  return (
    <div className="relative">
      <button
        type="button"
        className={cn("pm-timeline-tb-btn", focusGroupId && "is-on")}
        onClick={() => setOpen((v) => !v)}
      >
        Groups
        <ChevronDown className="h-3 w-3 opacity-60" />
      </button>

      {open && (
        <>
          <div
            className="pm-timeline-menu-scrim"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <div className="pm-timeline-menu" role="menu">
            {systemGroups.map((g) => (
              <GroupRow
                key={g.group_id}
                group={g}
                active={focusGroupId === g.group_id}
                onSelect={() => handleSelect(g.group_id)}
                readOnly
              />
            ))}
            {userGroups.map((g) => (
              <GroupRow
                key={g.group_id}
                group={g}
                active={focusGroupId === g.group_id}
                onSelect={() => handleSelect(g.group_id)}
                onEdit={() => openEdit(g)}
              />
            ))}
            <div className="pm-timeline-menu-sep" role="separator" />
            <button
              type="button"
              className={cn(
                "pm-timeline-menu-item",
                focusGroupId === UNCATEGORIZED_ID && "is-active"
              )}
              onClick={() => handleSelect(UNCATEGORIZED_ID)}
            >
              <GroupIconView source={{ name: "Uncategorized" }} />
              <span className="pm-timeline-menu-name" title="Uncategorized">
                Uncategorized
              </span>
              <span className="pm-timeline-menu-trail" aria-hidden>
                <span className="pm-timeline-menu-count">
                  {uncategorizedCount}
                </span>
              </span>
            </button>
            <button
              type="button"
              className="pm-timeline-menu-item text-[var(--pm-green)]"
              onClick={() => {
                setEditing(null)
                setFormOpen(true)
                setOpen(false)
              }}
            >
              <Plus className="h-3.5 w-3.5 shrink-0" />
              <span className="pm-timeline-menu-name">Create group</span>
            </button>
          </div>
        </>
      )}

      <GroupFormDialog
        collectionId={collectionId}
        open={formOpen}
        onOpenChange={(v) => {
          setFormOpen(v)
          if (!v) setEditing(null)
        }}
        editing={editing}
        boundFolderIds={boundFolderIds}
        onSaved={onGroupsChanged}
        onDeleted={() => {
          if (editing && focusGroupId === editing.group_id) {
            onFocusGroup(null)
          }
          onGroupsChanged()
        }}
      />
    </div>
  )
}

/**
 * Row: [icon] [name trunc…] [count]
 * Hover (user groups): edit icon replaces count in the same trail slot.
 * Delete lives in Edit Group dialog — not in this menu.
 */
function GroupRow({
  group,
  active,
  onSelect,
  onEdit,
  readOnly,
}: {
  group: NodeGroup
  active: boolean
  onSelect: () => void
  onEdit?: () => void
  readOnly?: boolean
}) {
  const count = group.node_count ?? 0
  const editable = !readOnly && !!onEdit

  return (
    <div
      className={cn(
        "pm-timeline-menu-row",
        active && "is-active",
        editable && "is-editable"
      )}
    >
      <button
        type="button"
        role="menuitem"
        className={cn("pm-timeline-menu-item", active && "is-active")}
        onClick={onSelect}
        title={group.name}
      >
        <GroupIconView source={group} />
        <span className="pm-timeline-menu-name">{group.name}</span>
        <span className="pm-timeline-menu-trail">
          <span className="pm-timeline-menu-count">{count}</span>
        </span>
      </button>
      {editable && (
        <button
          type="button"
          className="pm-timeline-menu-edit"
          title="Edit group"
          aria-label={`Edit group ${group.name}`}
          onClick={(e) => {
            e.stopPropagation()
            onEdit?.()
          }}
        >
          <Pencil className="h-3 w-3" strokeWidth={1.75} />
        </button>
      )}
    </div>
  )
}
