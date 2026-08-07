import { useEffect, useMemo, useRef, useState } from "react"
import { Check, ChevronDown, ChevronRight, Search } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { FieldLabel } from "@/components/ui/field-label"
import { Input } from "@/components/ui/input"
import { toast } from "sonner"
import type { FolderTreeNode, NodeGroup } from "@/types/file-mgmt"
import {
  createGroup,
  deleteGroup,
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
    <FolderIconView
      folder={folder}
      boundGroup={boundGroup}
      className="h-3.5 w-3.5 shrink-0"
    />
  )
}

interface GroupFormDialogProps {
  collectionId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  editing?: NodeGroup | null
  boundFolderIds: Set<string>
  onSaved: () => void
  /** After successful delete (edit mode only) */
  onDeleted?: () => void
}

/**
 * Folder-only bind tree — UI language matches Node Detail attach tree
 * (pm-timeline-ftree-*), but still single-selects flat plain folders only.
 */
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
        const isBound =
          boundFolderIds.has(n.folder_id) && n.folder_id !== editingFolderId
        const hasChildren = (n.children?.length ?? 0) > 0
        // Group folders must be flat — cannot bind a folder that has subfolders
        const selectable = n.kind === "plain" && !isBound && !hasChildren
        const isOpen = expanded.has(n.folder_id)
        const selected = selectedId === n.folder_id
        const boundGroup = groupByFolderId.get(n.folder_id) ?? null
        const title = isBound
          ? "Already bound to a group"
          : n.kind !== "plain"
            ? `${n.kind} folders cannot be bound`
            : hasChildren
              ? "Group folders must be flat. Move or remove subfolders first."
              : n.name

        return (
          <div key={n.folder_id}>
            <div
              className={cn(
                "pm-timeline-ftree-row",
                !selectable && !hasChildren && "opacity-45"
              )}
              style={{ paddingLeft: depth * 14 }}
            >
              <button
                type="button"
                className={cn(
                  "pm-timeline-ftree-chev",
                  isOpen && hasChildren && "is-open"
                )}
                disabled={!hasChildren}
                aria-label={isOpen ? "Collapse" : "Expand"}
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (hasChildren) onToggle(n.folder_id)
                }}
              >
                {!hasChildren ? (
                  <span className="inline-block w-3 h-3" />
                ) : isOpen ? (
                  <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.75} />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.75} />
                )}
              </button>
              <button
                type="button"
                className={cn(
                  "pm-timeline-ftree-folder",
                  isOpen && hasChildren && "is-open",
                  selected && selectable && "is-selected",
                  !selectable && "is-muted"
                )}
                disabled={!selectable && !hasChildren}
                title={title}
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (selectable) onSelect(n.folder_id)
                  else if (hasChildren) onToggle(n.folder_id)
                }}
              >
                <span className="pm-timeline-ftree-icon">
                  <FolderRowIcon folder={n} boundGroup={boundGroup} />
                </span>
                <span className="pm-timeline-ftree-name">{n.name}</span>
                {isBound && (
                  <span className="pm-meta text-[var(--pm-faint)] shrink-0">
                    bound
                  </span>
                )}
                {selectable && selected && (
                  <Check
                    className="pm-timeline-ftree-check"
                    strokeWidth={2.25}
                    aria-hidden
                  />
                )}
              </button>
            </div>

            <div
              className={cn(
                "pm-timeline-ftree-kids",
                isOpen && hasChildren && "is-open"
              )}
            >
              <div className="pm-timeline-ftree-kids-inner">
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
            </div>
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

/** Filter folder tree by name (keeps ancestors of matches). */
function filterFolderTree(
  nodes: FolderTreeNode[],
  q: string
): FolderTreeNode[] {
  const needle = q.trim().toLowerCase()
  if (!needle) return nodes
  const walk = (list: FolderTreeNode[]): FolderTreeNode[] => {
    const out: FolderTreeNode[] = []
    for (const n of list) {
      const kids = walk(n.children ?? [])
      if (n.name.toLowerCase().includes(needle) || kids.length > 0) {
        out.push({ ...n, children: kids })
      }
    }
    return out
  }
  return walk(nodes)
}

export function GroupFormDialog({
  collectionId,
  open,
  onOpenChange,
  editing,
  boundFolderIds,
  onSaved,
  onDeleted,
}: GroupFormDialogProps) {
  const [name, setName] = useState("")
  /** lucide when picking line icon; emoji when using symbol field */
  const [iconMode, setIconMode] = useState<"lucide" | "emoji">("lucide")
  const [iconKey, setIconKey] = useState("folder")
  const [iconColor, setIconColor] = useState(DEFAULT_ICON_COLOR)
  const [symbol, setSymbol] = useState("")
  const [folderMode, setFolderMode] = useState<"new" | "existing">("new")
  const [folderId, setFolderId] = useState("")
  /**
   * Tree panel open/closed — independent of folderMode so Rebind stays
   * selected (and folderId kept) when Appearance is expanded again.
   */
  const [treePanelOpen, setTreePanelOpen] = useState(false)
  const [folderSearch, setFolderSearch] = useState("")
  const [folderTree, setFolderTree] = useState<FolderTreeNode[]>([])
  const [groups, setGroups] = useState<NodeGroup[]>([])
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [submitting, setSubmitting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  /**
   * Latch edit target when the dialog opens. Parent often clears `editing`
   * as soon as close starts (onOpenChange(false)), which would flash the
   * title to "Create Group" mid silk fade-out if we read props live.
   */
  const [sessionEditing, setSessionEditing] = useState<NodeGroup | null>(null)
  const isEdit = !!sessionEditing
  const nameInputRef = useRef<HTMLInputElement>(null)

  const groupByFolderId = useMemo(() => {
    const m = new Map<string, NodeGroup>()
    for (const g of groups) {
      if (g.folder_id) m.set(g.folder_id, g)
    }
    return m
  }, [groups])

  useEffect(() => {
    if (!open) return
    setSessionEditing(editing ?? null)
    setConfirmDelete(false)
    setDeleting(false)
    setTreePanelOpen(false)
    setFolderSearch("")
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
      setFolderId("")
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

  /* Focus name after silk enter (280ms) so open fade isn't interrupted by focus scroll */
  useEffect(() => {
    if (!open) return
    const t = window.setTimeout(() => {
      nameInputRef.current?.focus({ preventScroll: true })
    }, 300)
    return () => window.clearTimeout(t)
  }, [open])

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

      if (isEdit && sessionEditing) {
        await updateGroup(collectionId, sessionEditing.group_id, {
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

  const handleDelete = async () => {
    if (!sessionEditing) return
    setDeleting(true)
    try {
      await deleteGroup(collectionId, sessionEditing.group_id)
      toast.success("Group deleted")
      onDeleted?.()
      onOpenChange(false)
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  /**
   * Tree panel ↔ Appearance full are mutually exclusive for height.
   * folderMode / folderId stay put when collapsing the tree via Appearance.
   * Pure CSS flex-grow hand-off — no JS pixel tween (that caused dual-track jank).
   */
  const treeOpen = treePanelOpen
  const appearanceOpen = !treePanelOpen

  const openAppearance = () => {
    /* Collapse tree only — keep Rebind/Existing + selected folder */
    setTreePanelOpen(false)
  }

  const openTree = () => {
    setFolderMode("existing")
    setTreePanelOpen(true)
  }

  const chooseKeepOrNew = () => {
    setFolderMode("new")
    setFolderId("")
    setFolderSearch("")
    setTreePanelOpen(false)
  }

  const segIndex = folderMode === "existing" ? 1 : 0

  const visibleFolders = useMemo(
    () => filterFolderTree(folderTree, folderSearch),
    [folderTree, folderSearch]
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          /* Silk shell + kill TW keyframe enter/exit (must not fight opacity/scale) */
          "pm-dialog pm-dialog--silk pm-group-dialog",
          "max-w-[26rem] sm:max-w-[26rem]",
          "!animate-none data-open:!animate-none data-closed:!animate-none"
        )}
        overlayClassName="pm-dialog-overlay--silk"
      >
        <DialogHeader className="pm-group-dialog-head">
          <DialogTitle className="pm-group-dialog-title">
            {isEdit ? "Edit Group" : "Create Group"}
          </DialogTitle>
        </DialogHeader>

        {/* Fixed dialog shell; Appearance body + tree slot fold inside */}
        <div className="pm-dialog-body pm-group-dialog-body">
          <section className="pm-group-card pm-group-card--identity">
            <div className="pm-group-identity">
              <div
                key={`${iconMode}-${iconKey}-${iconColor}-${symbol}`}
                className="pm-group-preview"
                title="Icon preview"
              >
                <GroupIconView source={previewSource} className="h-6 w-6" />
              </div>
              <div className="pm-group-identity-fields min-w-0 flex-1">
                <FieldLabel htmlFor="pm-group-name">Name</FieldLabel>
                <Input
                  ref={nameInputRef}
                  id="pm-group-name"
                  className="pm-group-name-input w-full"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Design review"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault()
                      void handleSubmit()
                    }
                  }}
                />
              </div>
            </div>
          </section>

          {/*
            Mid stack: pure CSS flex-grow hand-off (§3.7 model A).
            One Appearance card (head + body fold) ↔ Folder (chrome + tree fold).
            No pixel tween, no extra slot wrapper.
          */}
          <div className="pm-group-swap-stack">
            <section
              className={cn(
                "pm-group-card pm-group-card--appearance",
                appearanceOpen ? "is-open" : "is-compact"
              )}
            >
              <button
                type="button"
                className={cn(
                  "pm-group-appearance-head",
                  appearanceOpen && "is-static"
                )}
                onClick={() => {
                  if (!appearanceOpen) openAppearance()
                }}
                title={appearanceOpen ? undefined : "Edit icon and color"}
                aria-expanded={appearanceOpen}
              >
                <span className="pm-group-appearance-row-text">
                  <span className="pm-group-card-kicker">Appearance</span>
                  <span className="pm-meta text-[var(--pm-faint)]">
                    {appearanceOpen
                      ? "Icon & color"
                      : "Icon & color · tap to expand"}
                  </span>
                </span>
                <ChevronDown
                  className={cn(
                    "pm-group-appearance-row-chev h-3.5 w-3.5",
                    appearanceOpen && "is-open"
                  )}
                  strokeWidth={1.75}
                  aria-hidden
                />
              </button>
              <div
                className={cn(
                  "pm-group-appearance-body",
                  appearanceOpen && "is-open"
                )}
              >
                <div className="pm-group-card-scroll">
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
                </div>
              </div>
            </section>

            <section
              className={cn(
                "pm-group-card pm-group-card--folder",
                treeOpen && "is-tree-open"
              )}
            >
              <header className="pm-group-card-head">
                <span className="pm-group-card-kicker">Folder</span>
              </header>

              <div
                className="pm-group-seg"
                role="tablist"
                aria-label="Folder mode"
                data-on={segIndex}
              >
                <span className="pm-group-seg-pill" aria-hidden />
                {!isEdit && (
                  <button
                    type="button"
                    role="tab"
                    aria-selected={folderMode === "new"}
                    className={cn(
                      "pm-group-seg-btn",
                      folderMode === "new" && "is-on"
                    )}
                    onClick={chooseKeepOrNew}
                  >
                    New folder
                  </button>
                )}
                {isEdit && (
                  <button
                    type="button"
                    role="tab"
                    aria-selected={folderMode === "new"}
                    className={cn(
                      "pm-group-seg-btn",
                      folderMode === "new" && "is-on"
                    )}
                    onClick={chooseKeepOrNew}
                  >
                    Keep current
                  </button>
                )}
                <button
                  type="button"
                  role="tab"
                  aria-selected={folderMode === "existing"}
                  className={cn(
                    "pm-group-seg-btn",
                    folderMode === "existing" && "is-on"
                  )}
                  onClick={openTree}
                >
                  {isEdit ? "Rebind" : "Existing"}
                </button>
              </div>

              {/* One-line mode hint in the fixed empty band when tree is closed */}
              {!treeOpen && (
                <p className="pm-group-folder-hint">
                  {isEdit
                    ? "Keep the folder currently bound to this group."
                    : "Creates a new library folder bound to this group."}
                </p>
              )}

              {/*
                Fold slot always mounted. Search is sticky chrome (outside the
                scrolling list); only the folder list scrolls.
              */}
              <div className={cn("pm-group-tree-slot", treeOpen && "is-open")}>
                <div className="pm-group-tree-slot-inner">
                  <div className="pm-timeline-ftree-search-wrap pm-group-folder-search">
                    <Search
                      className="pm-timeline-ftree-search-icon"
                      strokeWidth={1.75}
                    />
                    <input
                      className="pm-timeline-ftree-search pm-group-folder-search-input"
                      placeholder="Search folders…"
                      value={folderSearch}
                      onChange={(e) => setFolderSearch(e.target.value)}
                      tabIndex={treeOpen ? 0 : -1}
                    />
                  </div>
                  <div className="pm-group-tree-list">
                    {folderTree.length === 0 ? (
                      <p className="pm-meta text-center py-6 text-[var(--pm-faint)]">
                        No folders yet
                      </p>
                    ) : visibleFolders.length === 0 ? (
                      <p className="pm-meta text-center py-6 text-[var(--pm-faint)]">
                        No matches
                      </p>
                    ) : (
                      <div className="pm-timeline-ftree pm-group-ftree">
                        <div className="pm-timeline-ftree-scroll pm-group-ftree-scroll">
                          <FolderBindTree
                            nodes={visibleFolders}
                            boundFolderIds={boundFolderIds}
                            groupByFolderId={groupByFolderId}
                            editingFolderId={sessionEditing?.folder_id ?? null}
                            selectedId={folderId}
                            onSelect={setFolderId}
                            expanded={expanded}
                            onToggle={toggleExpand}
                          />
                        </div>
                      </div>
                    )}
                    {folderId && (
                      <p className="pm-group-folder-selected">
                        Selected ·{" "}
                        <span className="text-[var(--pm-ink)]">
                          {findFolderName(folderTree, folderId) ??
                            folderId.slice(0, 8)}
                        </span>
                      </p>
                    )}
                  </div>
                </div>
              </div>
            </section>
          </div>
        </div>

        <DialogFooter className="pm-group-dialog-foot gap-2 sm:justify-between">
          {isEdit ? (
            confirmDelete ? (
              <div className="pm-group-delete-confirm min-w-0 flex-1 mr-2">
                <p className="pm-meta text-[var(--pm-muted)] leading-snug">
                  Delete “{sessionEditing?.name}”? Nodes become uncategorized;
                  folder and files stay.
                </p>
                <div className="flex gap-2 mt-2">
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={() => setConfirmDelete(false)}
                    disabled={deleting}
                  >
                    Keep
                  </Button>
                  <Button
                    variant="destructive"
                    size="xs"
                    onClick={() => void handleDelete()}
                    disabled={deleting}
                  >
                    {deleting ? "Deleting…" : "Delete group"}
                  </Button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                className="pm-group-delete-link"
                onClick={() => setConfirmDelete(true)}
                disabled={submitting}
              >
                Delete group
              </button>
            )
          ) : (
            <span className="flex-1" />
          )}
          <div className="flex gap-2 shrink-0 ml-auto">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onOpenChange(false)}
              disabled={submitting || deleting}
            >
              Cancel
            </Button>
            <Button
              variant="default"
              size="sm"
              onClick={() => void handleSubmit()}
              disabled={submitting || deleting || confirmDelete}
            >
              {submitting ? "Saving…" : isEdit ? "Save changes" : "Create group"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
