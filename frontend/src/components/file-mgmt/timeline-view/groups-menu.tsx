import { useMemo, useState } from "react"
import { ChevronDown, Pencil, Plus, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { toast } from "sonner"
import type { Node, NodeGroup } from "@/types/file-mgmt"
import { deleteGroup } from "@/api/file-mgmt"
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
  const [confirmDelete, setConfirmDelete] = useState<NodeGroup | null>(null)

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

  const handleDelete = async () => {
    if (!confirmDelete) return
    try {
      await deleteGroup(collectionId, confirmDelete.group_id)
      toast.success("Group deleted")
      if (focusGroupId === confirmDelete.group_id) onFocusGroup(null)
      setConfirmDelete(null)
      onGroupsChanged()
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  return (
    <div className="relative">
      <Button
        variant="outline"
        size="xs"
        className={cn(
          "text-[10px] h-7 gap-1",
          focusGroupId && "border-[var(--ze-green)]/50 text-[var(--ze-green)]"
        )}
        onClick={() => setOpen((v) => !v)}
      >
        Groups
        <ChevronDown className="h-3 w-3 opacity-60" />
      </Button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full mt-1 z-50 w-56 rounded-lg border border-border bg-popover shadow-xl py-1 text-xs">
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
                onEdit={() => {
                  if (isSystemGroup(g)) return
                  setEditing(g)
                  setFormOpen(true)
                  setOpen(false)
                }}
                onDelete={() => {
                  if (isSystemGroup(g)) return
                  setConfirmDelete(g)
                  setOpen(false)
                }}
              />
            ))}
            <button
              type="button"
              className={cn(
                "w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/50 border-t border-border/60 mt-0.5",
                focusGroupId === UNCATEGORIZED_ID && "bg-[var(--ze-green)]/10 text-[var(--ze-green)]"
              )}
              onClick={() => handleSelect(UNCATEGORIZED_ID)}
            >
              <GroupIconView source={{ name: "Uncategorized" }} />
              <span className="flex-1 truncate">Uncategorized</span>
              <span className="text-[10px] text-muted-foreground tabular-nums">
                {uncategorizedCount}
              </span>
            </button>
            <button
              type="button"
              className="w-full flex items-center gap-2 px-3 py-2 text-left text-[var(--ze-green)] hover:bg-muted/40 border-t border-border/60 mt-0.5"
              onClick={() => {
                setEditing(null)
                setFormOpen(true)
                setOpen(false)
              }}
            >
              <Plus className="h-3.5 w-3.5" />
              Create group
            </button>
          </div>
        </>
      )}

      <GroupFormDialog
        collectionId={collectionId}
        open={formOpen}
        onOpenChange={setFormOpen}
        editing={editing}
        boundFolderIds={boundFolderIds}
        onSaved={onGroupsChanged}
      />

      {confirmDelete && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40">
          <div className="bg-popover border border-border rounded-xl p-4 w-[300px] shadow-xl space-y-3">
            <p className="text-sm font-medium">Delete group “{confirmDelete.name}”?</p>
            <p className="text-[11px] text-muted-foreground">
              Nodes become uncategorized. The bound folder and files are kept.
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="xs" onClick={() => setConfirmDelete(null)}>
                Cancel
              </Button>
              <Button variant="destructive" size="xs" onClick={handleDelete}>
                Delete
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function GroupRow({
  group,
  active,
  onSelect,
  onEdit,
  onDelete,
  readOnly,
}: {
  group: NodeGroup
  active: boolean
  onSelect: () => void
  onEdit?: () => void
  onDelete?: () => void
  readOnly?: boolean
}) {
  return (
    <div
      className={cn(
        "group/row flex items-center gap-1 px-1.5 py-0.5",
        active && "bg-[var(--ze-green)]/10"
      )}
    >
      <button
        type="button"
        className={cn(
          "flex-1 flex items-center gap-2 px-1.5 py-1 rounded text-left min-w-0",
          active ? "text-[var(--ze-green)]" : "hover:bg-muted/40"
        )}
        onClick={onSelect}
      >
        <GroupIconView source={group} />
        <span className="flex-1 truncate">{group.name}</span>
        <span className="text-[10px] text-muted-foreground tabular-nums">
          {group.node_count}
        </span>
      </button>
      {!readOnly && (
        <div className="flex opacity-0 group-hover/row:opacity-100 transition-opacity shrink-0">
          <button
            type="button"
            className="p-1 text-muted-foreground hover:text-foreground"
            onClick={(e) => {
              e.stopPropagation()
              onEdit?.()
            }}
          >
            <Pencil className="h-3 w-3" />
          </button>
          <button
            type="button"
            className="p-1 text-muted-foreground hover:text-red-500"
            onClick={(e) => {
              e.stopPropagation()
              onDelete?.()
            }}
          >
            <Trash2 className="h-3 w-3" />
          </button>
        </div>
      )}
    </div>
  )
}
