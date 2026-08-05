import { useEffect, useMemo, useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { toast } from "sonner"
import type { FolderTreeNode, NodeGroup } from "@/types/file-mgmt"
import {
  createGroup,
  getFolderTree,
  getNameConflict,
  listGroups,
  updateGroup,
} from "@/api/file-mgmt"
import {
  DEFAULT_ICON_COLOR,
  FolderIconView,
  GroupIconView,
  IconPickerPanel,
  buildIconPayload,
} from "./group-icons"
import { cn } from "@/lib/utils"

function FolderRowIcon({
  folder,
  boundGroup,
}: {
  folder: FolderTreeNode
  boundGroup?: NodeGroup | null
}) {
  return (
    <FolderIconView folder={folder} boundGroup={boundGroup} className="h-3 w-3" />
  )
}

interface GroupFormDialogProps {
  collectionId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  editing?: NodeGroup | null
  boundFolderIds: Set<string>
  onSaved: () => void
}

/** Folder tree for picking an unbound plain folder. */
function FolderBindTree({
  nodes,
  boundFolderIds,
  groupByFolderId,
  editingFolderId,
  selectedId,
  onSelect,
  depth = 0,
  expanded,
  onToggle,
}: {
  nodes: FolderTreeNode[]
  boundFolderIds: Set<string>
  groupByFolderId: Map<string, NodeGroup>
  editingFolderId: string | null
  selectedId: string
  onSelect: (id: string) => void
  depth?: number
  expanded: Set<string>
  onToggle: (id: string) => void
}) {
  return (
    <>
      {nodes.map((n) => {
        const isBound = boundFolderIds.has(n.folder_id) && n.folder_id !== editingFolderId
        const hasChildren = (n.children?.length ?? 0) > 0
        // Group folders must be flat — cannot bind a folder that has subfolders
        const selectable = n.kind === "plain" && !isBound && !hasChildren
        const isOpen = expanded.has(n.folder_id)
        const selected = selectedId === n.folder_id
        const boundGroup = groupByFolderId.get(n.folder_id) ?? null

        return (
          <div key={n.folder_id}>
            <div
              className={cn(
                "flex items-center gap-0.5 py-0.5 rounded text-[10px]",
                selected && selectable && "bg-primary/10 text-primary",
                !selectable && "opacity-50"
              )}
              style={{ paddingLeft: depth * 12 + 2 }}
            >
              <button
                type="button"
                className="p-0.5 shrink-0 text-muted-foreground"
                disabled={!hasChildren}
                onClick={() => hasChildren && onToggle(n.folder_id)}
              >
                {hasChildren ? (
                  isOpen ? (
                    <ChevronDown className="h-3 w-3" />
                  ) : (
                    <ChevronRight className="h-3 w-3" />
                  )
                ) : (
                  <span className="inline-block w-3" />
                )}
              </button>
              <button
                type="button"
                className={cn(
                  "flex-1 flex items-center gap-1.5 min-w-0 text-left px-1 py-0.5 rounded",
                  selectable ? "hover:bg-muted/40 cursor-pointer" : "cursor-not-allowed"
                )}
                disabled={!selectable}
                onClick={() => selectable && onSelect(n.folder_id)}
                title={
                  isBound
                    ? "Already bound to a group"
                    : n.kind !== "plain"
                      ? `${n.kind} folders cannot be bound`
                      : hasChildren
                        ? "Group folders must be flat. Move or remove subfolders first."
                        : n.name
                }
              >
                <FolderRowIcon folder={n} boundGroup={boundGroup} />
                <span className="truncate">{n.name}</span>
                {isBound && (
                  <span className="text-[9px] text-muted-foreground shrink-0">bound</span>
                )}
              </button>
            </div>
            {hasChildren && isOpen && (
              <FolderBindTree
                nodes={n.children}
                boundFolderIds={boundFolderIds}
                groupByFolderId={groupByFolderId}
                editingFolderId={editingFolderId}
                selectedId={selectedId}
                onSelect={onSelect}
                depth={depth + 1}
                expanded={expanded}
                onToggle={onToggle}
              />
            )}
          </div>
        )
      })}
    </>
  )
}

function collectExpandableIds(nodes: FolderTreeNode[]): string[] {
  const ids: string[] = []
  const walk = (list: FolderTreeNode[]) => {
    for (const n of list) {
      if (n.children?.length) {
        ids.push(n.folder_id)
        walk(n.children)
      }
    }
  }
  walk(nodes)
  return ids
}

function findFolderName(
  nodes: FolderTreeNode[],
  folderId: string
): string | null {
  for (const n of nodes) {
    if (n.folder_id === folderId) return n.name
    if (n.children?.length) {
      const found = findFolderName(n.children, folderId)
      if (found) return found
    }
  }
  return null
}

export function GroupFormDialog({
  collectionId,
  open,
  onOpenChange,
  editing,
  boundFolderIds,
  onSaved,
}: GroupFormDialogProps) {
  const isEdit = !!editing
  const [name, setName] = useState("")
  /** lucide when picking line icon; emoji when using symbol field */
  const [iconMode, setIconMode] = useState<"lucide" | "emoji">("lucide")
  const [iconKey, setIconKey] = useState("folder")
  const [iconColor, setIconColor] = useState(DEFAULT_ICON_COLOR)
  const [symbol, setSymbol] = useState("")
  const [folderMode, setFolderMode] = useState<"new" | "existing">("new")
  const [folderId, setFolderId] = useState("")
  const [folderTree, setFolderTree] = useState<FolderTreeNode[]>([])
  const [groups, setGroups] = useState<NodeGroup[]>([])
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [submitting, setSubmitting] = useState(false)

  const groupByFolderId = useMemo(() => {
    const m = new Map<string, NodeGroup>()
    for (const g of groups) {
      if (g.folder_id) m.set(g.folder_id, g)
    }
    return m
  }, [groups])

  useEffect(() => {
    if (!open) return
    if (editing) {
      setName(editing.name)
      if (editing.icon_type === "emoji" && editing.icon_value) {
        setIconMode("emoji")
        setSymbol(editing.icon_value)
        setIconKey("folder")
      } else {
        setIconMode("lucide")
        setIconKey(editing.icon_value || "users")
        setIconColor(editing.icon_color || DEFAULT_ICON_COLOR)
        setSymbol("")
      }
      setFolderMode("new")
    } else {
      setName("")
      setIconMode("lucide")
      setIconKey("folder")
      setIconColor(DEFAULT_ICON_COLOR)
      setSymbol("")
      setFolderMode("new")
      setFolderId("")
    }
  }, [open, editing])

  useEffect(() => {
    if (!open) return
    Promise.all([getFolderTree(collectionId), listGroups(collectionId)])
      .then(([tree, gs]) => {
        setFolderTree(tree)
        setGroups(gs)
        setExpanded(new Set(collectExpandableIds(tree)))
      })
      .catch(() => {
        setFolderTree([])
        setGroups([])
      })
  }, [open, collectionId])

  const previewSource = useMemo(
    () =>
      iconMode === "emoji" && symbol
        ? { name, icon_type: "emoji" as const, icon_value: symbol }
        : {
            name,
            icon_type: "lucide" as const,
            icon_value: iconKey,
            icon_color: iconColor,
          },
    [iconMode, name, symbol, iconKey, iconColor]
  )

  const handleSubmit = async () => {
    const trimmed = name.trim()
    if (!trimmed) {
      toast.error("Name is required")
      return
    }
    if (folderMode === "existing" && !folderId && !isEdit) {
      toast.error("Select a folder")
      return
    }
    if (iconMode === "emoji" && !symbol.trim()) {
      toast.error("Enter a symbol or pick a line icon")
      return
    }
    setSubmitting(true)
    try {
      const iconPayload = buildIconPayload({
        iconMode,
        iconKey,
        iconColor,
        symbol,
      })

      if (isEdit && editing) {
        await updateGroup(collectionId, editing.group_id, {
          name: trimmed,
          ...iconPayload,
          ...(folderMode === "existing" && folderId
            ? { rebind_folder_id: folderId }
            : {}),
        })
        toast.success("Group updated")
      } else {
        await createGroup(collectionId, {
          name: trimmed,
          ...iconPayload,
          bind_existing_folder_id: folderMode === "existing" ? folderId : null,
        })
        toast.success("Group created")
      }
      onSaved()
      onOpenChange(false)
    } catch (err) {
      const conflict = getNameConflict(err)
      if (conflict) {
        setName(conflict.suggested_name)
        toast.error(
          `${conflict.message} Suggested: ${conflict.suggested_name}`
        )
      } else {
        toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
      }
    } finally {
      setSubmitting(false)
    }
  }

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-sm">
            {isEdit ? "Edit Group" : "Create Group"}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4 mt-1">
          <div className="flex items-center gap-3">
            <div
              key={`${iconMode}-${iconKey}-${iconColor}-${symbol}`}
              className="h-10 w-10 rounded-lg border border-border flex items-center justify-center bg-muted/30 shrink-0"
              title="Preview"
            >
              <GroupIconView source={previewSource} className="h-5 w-5" />
            </div>
            <div className="flex-1 min-w-0">
              <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 block mb-1">
                Name
              </label>
              <input
                className="w-full text-xs border rounded px-2 py-1.5 bg-background"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Group name"
                autoFocus
              />
            </div>
          </div>

          <IconPickerPanel
            iconMode={iconMode}
            iconKey={iconKey}
            iconColor={iconColor}
            symbol={symbol}
            onIconMode={setIconMode}
            onIconKey={setIconKey}
            onIconColor={setIconColor}
            onSymbol={setSymbol}
          />

          <div>
            <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 block mb-1.5">
              Folder binding
            </label>
            <div className="flex gap-2 mb-2">
              {!isEdit && (
                <button
                  type="button"
                  className={cn(
                    "text-[10px] px-2 py-1 rounded border flex-1",
                    folderMode === "new"
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border"
                  )}
                  onClick={() => setFolderMode("new")}
                >
                  New folder
                </button>
              )}
              <button
                type="button"
                className={cn(
                  "text-[10px] px-2 py-1 rounded border flex-1",
                  folderMode === "existing" || isEdit
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border"
                )}
                onClick={() => setFolderMode("existing")}
              >
                {isEdit ? "Rebind folder" : "Existing"}
              </button>
              {isEdit && (
                <button
                  type="button"
                  className={cn(
                    "text-[10px] px-2 py-1 rounded border flex-1",
                    folderMode === "new"
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border"
                  )}
                  onClick={() => {
                    setFolderMode("new")
                    setFolderId("")
                  }}
                >
                  Keep current
                </button>
              )}
            </div>
            {folderMode === "new" && !isEdit ? (
              <p className="text-[10px] text-muted-foreground/60">
                Creates a folder named “{name.trim() || "…"}” and binds it to this group.
              </p>
            ) : folderMode === "new" && isEdit ? (
              <p className="text-[10px] text-muted-foreground/60">
                Keep the current bound folder. Attachment paths are not moved.
              </p>
            ) : (
              <div className="max-h-44 overflow-auto rounded border border-border/60 bg-background p-1">
                {isEdit && (
                  <p className="text-[9px] text-muted-foreground px-2 py-1 mb-1">
                    Rebinding moves only files attached to this group’s nodes (not other files in the old folder).
                    Target must be a flat plain folder.
                  </p>
                )}
                {folderTree.length === 0 ? (
                  <p className="text-[10px] text-muted-foreground/50 px-2 py-3 text-center">
                    No folders
                  </p>
                ) : (
                  <FolderBindTree
                    nodes={folderTree}
                    boundFolderIds={boundFolderIds}
                    groupByFolderId={groupByFolderId}
                    editingFolderId={editing?.folder_id ?? null}
                    selectedId={folderId}
                    onSelect={setFolderId}
                    expanded={expanded}
                    onToggle={toggleExpand}
                  />
                )}
                {folderId && (
                  <p className="text-[10px] text-primary px-2 pt-1 border-t border-border/40 mt-1">
                    Selected · {findFolderName(folderTree, folderId) ?? folderId.slice(0, 8)}
                  </p>
                )}
              </div>
            )}
          </div>
        </div>

        <DialogFooter className="gap-2 pt-2">
          <Button
            variant="outline"
            size="xs"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button size="xs" onClick={handleSubmit} disabled={submitting}>
            {submitting ? "Saving…" : isEdit ? "Save" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
