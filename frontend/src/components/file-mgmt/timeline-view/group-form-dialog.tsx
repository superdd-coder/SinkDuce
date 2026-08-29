import { useEffect, useMemo, useRef, useState } from "react"
import { ChevronDown } from "lucide-react"
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
import { useT } from "@/i18n/use-t"

import { formatApiError } from "@/api/http"
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
  GroupIconView,
  IconPickerPanel,
  buildIconPayload,
} from "./group-icons"
import { cn } from "@/lib/utils"
import { FolderSelectTree } from "@/components/file-mgmt/folder-select-tree"

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

export function GroupFormDialog({
  collectionId,
  open,
  onOpenChange,
  editing,
  boundFolderIds,
  onSaved,
  onDeleted,
}: GroupFormDialogProps) {
  const t = useT()
  const [name, setName] = useState("")
  /** lucide when picking line icon; emoji when using symbol field */
  const [iconMode, setIconMode] = useState<"lucide" | "emoji">("lucide")
  const [iconKey, setIconKey] = useState("users")
  const [iconColor, setIconColor] = useState(DEFAULT_ICON_COLOR)
  const [symbol, setSymbol] = useState("")
  const [folderMode, setFolderMode] = useState<"new" | "existing">("new")
  const [folderId, setFolderId] = useState("")
  /**
   * Tree panel open/closed — independent of folderMode so Rebind stays
   * selected (and folderId kept) when Appearance is expanded again.
   */
  const [treePanelOpen, setTreePanelOpen] = useState(false)
  const [folderTree, setFolderTree] = useState<FolderTreeNode[]>([])
  const [groups, setGroups] = useState<NodeGroup[]>([])
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
    if (editing) {
      setName(editing.name)
      if (editing.icon_type === "emoji" && editing.icon_value) {
        setIconMode("emoji")
        setSymbol(editing.icon_value)
        setIconKey("users")
      } else {
        setIconMode("lucide")
        setIconKey(
          !editing.icon_value ||
            editing.icon_value === "folder" ||
            editing.icon_value === "git-branch"
            ? "users"
            : editing.icon_value
        )
        setIconColor(editing.icon_color || DEFAULT_ICON_COLOR)
        setSymbol("")
      }
      setFolderMode("new")
      setFolderId("")
    } else {
      setName("")
      setIconMode("lucide")
      setIconKey("users")
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
      toast.error(t("fileMgmt.nameRequired"))
      return
    }
    if (folderMode === "existing" && !folderId && !isEdit) {
      toast.error(t("fileMgmt.selectFolder"))
      return
    }
    if (iconMode === "emoji" && !symbol.trim()) {
      toast.error(t("fileMgmt.enterSymbol"))
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
        toast.success(t("fileMgmt.groupUpdated"))
      } else {
        await createGroup(collectionId, {
          name: trimmed,
          ...iconPayload,
          bind_existing_folder_id: folderMode === "existing" ? folderId : null,
        })
        toast.success(t("fileMgmt.groupCreated"))
      }
      onSaved()
      onOpenChange(false)
    } catch (err) {
      const conflict = getNameConflict(err)
      if (conflict) {
        setName(conflict.suggested_name)
        toast.error(
          t("errors.name_conflict", {
            name: conflict.name,
            suggested_name: conflict.suggested_name,
          })
        )
      } else {
        toast.error(t("fileMgmt.failed", { error: formatApiError(err, t) }))
      }
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async () => {
    if (!sessionEditing) return
    setDeleting(true)
    try {
      await deleteGroup(collectionId, sessionEditing.group_id)
      toast.success(t("fileMgmt.groupDeleted"))
      onDeleted?.()
      onOpenChange(false)
    } catch (err) {
      toast.error(t("fileMgmt.failed", { error: formatApiError(err, t) }))
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
    setTreePanelOpen(false)
  }

  const segIndex = folderMode === "existing" ? 1 : 0

  const origHasChildren = (id: string) => {
    const walk = (list: FolderTreeNode[]): boolean => {
      for (const n of list) {
        if (n.folder_id === id) return (n.children?.length ?? 0) > 0
        if (n.children?.length && walk(n.children)) return true
      }
      return false
    }
    return walk(folderTree)
  }

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
            {isEdit ? t("fileMgmt.editGroup") : t("fileMgmt.createGroup")}
          </DialogTitle>
        </DialogHeader>

        {/* Fixed dialog shell; Appearance body + tree slot fold inside */}
        <div className="pm-dialog-body pm-group-dialog-body">
          <section className="pm-group-card pm-group-card--identity">
            <div className="pm-group-identity">
              <div
                key={`${iconMode}-${iconKey}-${iconColor}-${symbol}`}
                className="pm-group-preview"
                title={t("fileMgmt.iconPreview")}
              >
                <GroupIconView source={previewSource} className="h-6 w-6" />
              </div>
              <div className="pm-group-identity-fields min-w-0 flex-1">
                <FieldLabel htmlFor="pm-group-name">{t("common.name")}</FieldLabel>
                <Input
                  ref={nameInputRef}
                  id="pm-group-name"
                  className="pm-group-name-input w-full"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t("fileMgmt.egDesignReview")}
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
                title={appearanceOpen ? undefined : t("fileMgmt.editIconColor")}
                aria-expanded={appearanceOpen}
              >
                <span className="pm-group-appearance-row-text">
                  <span className="pm-group-card-kicker">{t("fileMgmt.appearance")}</span>
                  <span className="pm-meta text-[var(--pm-faint)]">
                    {appearanceOpen
                      ? t("fileMgmt.iconAndColor")
                      : t("fileMgmt.iconColorExpand")}
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
                    iconKey={
                      iconKey === "folder" || iconKey === "git-branch"
                        ? "users"
                        : iconKey
                    }
                    iconColor={iconColor}
                    symbol={symbol}
                    onIconMode={setIconMode}
                    onIconKey={setIconKey}
                    onIconColor={setIconColor}
                    onSymbol={setSymbol}
                    variant="group"
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
                <span className="pm-group-card-kicker">{t("common.folder")}</span>
              </header>

              <div
                className="pm-group-seg"
                role="tablist"
                aria-label={t("fileMgmt.folderMode")}
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
                    {t("fileMgmt.newFolder")}
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
                    {t("fileMgmt.keepCurrent")}
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
                  {isEdit ? t("fileMgmt.rebind") : t("fileMgmt.existing")}
                </button>
              </div>

              {/* One-line mode hint in the fixed empty band when tree is closed */}
              {!treeOpen && (
                <p className="pm-group-folder-hint">
                  {isEdit
                    ? t("fileMgmt.keepCurrentFolderHint")
                    : t("fileMgmt.createsNewFolder")}
                </p>
              )}

              {/*
                Fold slot always mounted. Search is sticky chrome (outside the
                scrolling list); only the folder list scrolls.
              */}
              <div className={cn("pm-group-tree-slot", treeOpen && "is-open")}>
                <div className="pm-group-tree-slot-inner">
                  <FolderSelectTree
                    key={`${collectionId}-${open}`}
                    nodes={folderTree}
                    selectedId={folderId || undefined}
                    onSelect={(id) => {
                      if (id) setFolderId(id)
                    }}
                    isSelectable={(n) => {
                      const isBound =
                        boundFolderIds.has(n.folder_id) &&
                        n.folder_id !== (sessionEditing?.folder_id ?? null)
                      return (
                        n.kind === "plain" &&
                        !isBound &&
                        !origHasChildren(n.folder_id)
                      )
                    }}
                    groupByFolderId={groupByFolderId}
                    badge={(n) =>
                      boundFolderIds.has(n.folder_id) &&
                      n.folder_id !== (sessionEditing?.folder_id ?? null) ? (
                        <span className="pm-meta text-[var(--pm-faint)] shrink-0">
                          bound
                        </span>
                      ) : null
                    }
                    searchTabIndex={treeOpen ? 0 : -1}
                    showSelectedCaption
                  />
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
                  {t("fileMgmt.deleteNamedQ", { name: sessionEditing?.name ?? "" })}
                </p>
                <div className="flex gap-2 mt-2">
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={() => setConfirmDelete(false)}
                    disabled={deleting}
                  >
                    {t("fileMgmt.keep")}
                  </Button>
                  <Button
                    variant="destructive"
                    size="xs"
                    onClick={() => void handleDelete()}
                    disabled={deleting}
                  >
                    {deleting ? t("fileMgmt.deleting") : t("fileMgmt.deleteGroup")}
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
                {t("fileMgmt.deleteGroup")}
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
              {t("common.cancel")}
            </Button>
            <Button
              variant="default"
              size="sm"
              onClick={() => void handleSubmit()}
              disabled={submitting || deleting || confirmDelete}
            >
              {submitting
                ? t("common.saving")
                : isEdit
                  ? t("fileMgmt.saveChanges")
                  : t("fileMgmt.createGroup")}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
