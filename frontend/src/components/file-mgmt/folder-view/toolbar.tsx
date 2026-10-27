import {
  useState,
  useRef,
  useMemo,
  useEffect,
  useLayoutEffect,
  type ReactNode,
} from "react"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { Button } from "@/components/ui/button"
import { SoftMenu } from "@/components/ui/menu"
import {
  FolderPlus,
  Upload,
  FolderInput,
  MoveRight,
  Link2,
  Archive,
  ArchiveRestore,
  SearchX,
  Trash2,
  Star,
  ChevronDown,
  CheckSquare,
  ChevronLeft,
  ArrowUpDown,
  LayoutGrid,
  List,
  Flame,
  type LucideIcon,
} from "lucide-react"
import {
  isCreatedSortMode,
  isUpdatedSortMode,
  nextCreatedSortMode,
  nextUpdatedSortMode,
} from "@/stores/file-mgmt-store"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import type { FileSummary, FolderTreeNode } from "@/types/file-mgmt"
import { cn } from "@/lib/utils"
import {
  DEFAULT_ICON_COLOR,
  GroupIconView,
  IconPickerPanel,
  buildIconPayload,
} from "@/components/file-mgmt/timeline-view/group-icons"
import { useT } from "@/i18n/use-t"
import { FolderDestCard } from "@/components/file-mgmt/folder-select-tree"
import {
  blockedMoveFolderIds,
  isFileMoveSelectable,
  isFolderMoveSelectable,
} from "./folder-move-dest"
import { isVirtualFolderId } from "./sorted-items"

/** Active in this folder (not file-archived, not path-greyed here). */
function isActiveInFolder(f: FileSummary): boolean {
  return !f.archived && !f.is_greyed
}

/** Path-archived in this folder only (still searchable). */
function isPathArchivedOnly(f: FileSummary): boolean {
  return !f.archived && !!f.is_greyed
}

/** File-level excluded from search. */
function isFileArchived(f: FileSummary): boolean {
  return !!f.archived
}

function ToolbarDivider() {
  return <div className="pm-files-tb-div" aria-hidden />
}

/** Premium Files toolbar — type roles via .pm-files-tb-* (see index.css) */
const tbBtn = "pm-files-tb-btn shrink-0"
const tbIconBtn = "pm-files-tb-icon shrink-0"
const tbLabel = "pm-files-tb-label"

/**
 * Toolbar chrome crossfade — opacity only (no slide/blur/scale; those felt dizzy).
 * Mount-driven CSS keyframes so every toolKey swap still plays under Strict Mode.
 * Keep in sync with .animate-fm-toolbar-in/out duration in index.css.
 */
const TB_SWAP_MS = 180
const tbPanelBase = "absolute inset-y-0 left-0 flex items-center gap-0.5"

type ArchiveUiSnap = {
  showArchiveHere: boolean
  showExcludeSearch: boolean
  showUnarchive: boolean
  showMenu: boolean
}

/** Frozen toolbar layout — any toolKey change triggers a crossfade. */
type ToolbarChrome =
  | {
      kind: "default"
      showCreate: boolean
      /** Unassigned / Archived views: one-click burn of every visible file. */
      showBurnAll: boolean
      multiSelectMode: boolean
    }
  | {
      kind: "multi"
    }
  | {
      kind: "folder"
      folderCount: number
      /** False when selection contains system or timeline-bound folders. */
      canDelete: boolean
      /** Timeline-bound folders selected — delete disabled with a hint. */
      timelineBlocked: boolean
      canMove: boolean
      canArchive: boolean
      canRestore: boolean
    }
  | {
      kind: "file"
      fileCount: number
      multiSelectMode: boolean
      archive: ArchiveUiSnap
      showDefinitive: boolean
      isDefinitive: boolean
      isArchivedView: boolean
      /** Root lists orphans (no path) — no "remove from folder" / path-archive. */
      isRootView: boolean
      /** Unassigned view — same mounting rules as root (move-only). */
      isUnassignedView: boolean
    }
  | {
      kind: "mixed"
      folderCount: number
      canDelete: boolean
      timelineBlocked: boolean
      fileCount: number
      multiSelectMode: boolean
      archive: ArchiveUiSnap
      showDefinitive: boolean
      isDefinitive: boolean
      isArchivedView: boolean
      isRootView: boolean
      isUnassignedView: boolean
      canMoveFolders: boolean
      canArchiveFolders: boolean
      canRestoreFolders: boolean
    }

/** Dropdown surface — nested white + soft shadow; parent toolbar z-index above grid. */
const tbMenuPanel = "pm-files-menu absolute left-0 top-full mt-1 min-w-[260px]"

function chromeToolKey(c: ToolbarChrome): string {
  switch (c.kind) {
    case "default":
      return `default:${c.showCreate ? "c" : "-"}${c.showBurnAll ? "b" : "-"}${c.multiSelectMode ? "m" : ""}`
    case "multi":
      return "multi"
    case "folder":
      return `folder:${c.canDelete ? "del" : c.timelineBlocked ? "tl" : "sys"}:${c.canMove ? "mv" : "-"}${c.canArchive ? "a" : "-"}${c.canRestore ? "r" : "-"}`
    case "file":
      return [
        "file",
        c.archive.showMenu ? "a" : "-",
        c.archive.showUnarchive ? "u" : "-",
        c.archive.showArchiveHere ? "h" : "-",
        c.archive.showExcludeSearch ? "e" : "-",
        c.showDefinitive ? "d" : "-",
        c.multiSelectMode ? "m" : "-",
        c.isRootView || c.isUnassignedView ? "root" : "f",
      ].join(":")
    case "mixed":
      return [
        "mixed",
        c.canDelete ? "del" : c.timelineBlocked ? "tl" : "sys",
        c.archive.showMenu ? "a" : "-",
        c.showDefinitive ? "d" : "-",
        c.multiSelectMode ? "m" : "-",
        c.isRootView || c.isUnassignedView ? "root" : "f",
        c.canMoveFolders ? "mv" : "-",
        c.canArchiveFolders ? "fa" : "-",
        c.canRestoreFolders ? "fr" : "-",
      ].join(":")
  }
}

/** v2: icon-only toolbar action — label slides out to the right on hover. */
function TbAct({
  icon: Icon,
  label,
  onClick,
  title,
  danger,
  active,
  disabled,
  tabIndex,
  chevron,
  iconClassName,
}: {
  icon: LucideIcon
  label: string
  onClick: () => void
  title?: string
  danger?: boolean
  active?: boolean
  disabled?: boolean
  tabIndex?: number
  chevron?: boolean
  iconClassName?: string
}) {
  return (
    <Button
      variant="ghost"
      size="xs"
      disabled={disabled}
      onClick={onClick}
      title={title ?? label}
      aria-label={title ?? label}
      tabIndex={tabIndex}
      className={cn(
        "pm-files-tb-act shrink-0",
        danger && "is-danger",
        active && "is-on"
      )}
    >
      <Icon className={iconClassName} aria-hidden />
      <span className="pm-tb-act-label">{label}</span>
      {chevron && <ChevronDown className="pm-tb-act-chevron" aria-hidden />}
    </Button>
  )
}

function MenuItem({
  icon,
  title,
  description,
  onClick,
  destructive,
}: {
  icon: ReactNode
  title: string
  description: string
  onClick: () => void
  destructive?: boolean
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className={cn("pm-files-menu-item", destructive && "is-danger")}
      onClick={onClick}
    >
      <span
        className={cn(
          "mt-0.5 shrink-0",
          destructive ? "text-[var(--pm-danger)]" : "text-[var(--pm-muted)]"
        )}
      >
        {icon}
      </span>
      <span className="min-w-0">
        <span className="pm-files-menu-item-title">{title}</span>
        <span className="pm-files-menu-item-desc">{description}</span>
      </span>
    </button>
  )
}

export function Toolbar({
  collectionId,
  trailing,
}: {
  collectionId: string
  trailing?: ReactNode
}) {
  const t = useT()
  const {
    currentFolderId,
    currentFolder,
    selectedFileIds,
    selectedFolderIds,
    currentFolderFiles,
    folderTree,
    ingestingFiles,
    createSubFolder,
    uploadFile,
    uploadFolder,
    folderFileSort,
    setFolderFileSort,
    moveFilesToFolder,
    moveFolder,
    copyFilesToFolder,
    removeFilesFromCurrentFolder,
    archiveFilesForFolder,
    excludeFilesFromSearch,
    unarchiveFiles,
    permanentlyDeleteFiles,
    toggleDefinitive,
    removeFolder,
    removeFilesFromRoot,
    archiveFolders,
    unarchiveFolders,
    clearFolderSelection,
    multiSelectMode,
    folderFileView,
    setFolderFileView,
    enterMultiSelectMode,
    exitMultiSelectMode,
    selectAllFiles,
    navigateToRoot,
    selectFolder,
  } = useFileMgmtStore()

  const [newFolderDialog, setNewFolderDialog] = useState(false)
  const [newFolderName, setNewFolderName] = useState("")
  const [newFolderIconMode, setNewFolderIconMode] = useState<"lucide" | "emoji">(
    "lucide"
  )
  const [newFolderIconKey, setNewFolderIconKey] = useState("folder")
  const [newFolderIconColor, setNewFolderIconColor] =
    useState(DEFAULT_ICON_COLOR)
  const [newFolderSymbol, setNewFolderSymbol] = useState("")
  const [moveDialogOpen, setMoveDialogOpen] = useState(false)
  const [moveDialogMode, setMoveDialogMode] = useState<
    "files" | "folders" | "mixed"
  >("files")
  /** undefined = none, null = root, string = folder */
  const [moveTarget, setMoveTarget] = useState<string | null | undefined>(
    undefined
  )
  const [copyDialogOpen, setCopyDialogOpen] = useState(false)
  const [copyTarget, setCopyTarget] = useState<string | null | undefined>(
    undefined
  )
  const [confirmAction, setConfirmAction] = useState<string | null>(null)
  /** At most one action menu open: move | archive | delete */
  const [openMenu, setOpenMenu] = useState<
    "move" | "archive" | "delete" | "deleteFolder" | "sort" | null
  >(
    null
  )
  const menusRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)

  /** Archived view: virtual id OR the real Archived system folder tile. */
  const isArchivedView =
    currentFolderId === "__archived__" ||
    (!!currentFolder &&
      currentFolder.is_system &&
      currentFolder.name === "Archived")
  /** Root = orphan / no-path files (currentFolderId is null). */
  const isRootView = currentFolderId == null
  /** Unassigned system folder — holding pen for files that lost every path. */
  const isUnassignedView =
    !!currentFolder && currentFolder.is_system && currentFolder.name === "Unassigned"
  /** Files in the current view flagged definitive — burn confirmation warns. */
  const burnDefinitiveCount = currentFolderFiles.filter(
    (f) => f.is_definitive
  ).length
  /** Branch/group folders are read-only observation windows (v2 R4). */
  const timelineWindowView =
    !!currentFolder &&
    !currentFolder.is_system &&
    (currentFolder.kind === "branch" || currentFolder.kind === "user_group")
  // Exclude ingesting files — they must not drive the file action toolbar
  const selectedFiles = currentFolderFiles.filter(
    (f) => selectedFileIds.has(f.file_id) && !ingestingFiles[f.file_id]
  )
  const selectedIds = selectedFiles.map((f) => f.file_id)
  const hasFileSelection = selectedFiles.length > 0
  const hasFolderSelection = selectedFolderIds.size > 0
  const selectedFolderIdsArr = Array.from(selectedFolderIds)
  const selectedFolderNodes = selectedFolderIdsArr
    .map((fid) => findFolderInTree(folderTree, fid))
    .filter((f): f is FolderTreeNode => !!f)
  // Virtual containers (Branches/Groups) are not in the real tree — treat a
  // selected virtual id like a system folder: no toolbar actions available.
  const hasSystemFolder =
    selectedFolderNodes.some((f) => f.is_system) ||
    selectedFolderIdsArr.some((fid) => isVirtualFolderId(fid))
  /** Branch / user_group folders are timeline-bound — Library cannot delete them. */
  const hasTimelineFolder = selectedFolderNodes.some(
    (f) => f.kind === "branch" || f.kind === "user_group"
  )
  const allSelectedFoldersPlain =
    !hasSystemFolder &&
    selectedFolderNodes.length > 0 &&
    selectedFolderNodes.every((f) => f.kind === "plain" && !f.is_system)
  const canMoveFolders = allSelectedFoldersPlain
  const canArchiveFolders =
    allSelectedFoldersPlain && selectedFolderNodes.some((f) => !f.archived)
  const canRestoreFolders =
    allSelectedFoldersPlain && selectedFolderNodes.some((f) => !!f.archived)

  const archiveUi = useMemo(() => {
    if (selectedFiles.length === 0) {
      return {
        showArchiveHere: false,
        showExcludeSearch: false,
        showUnarchive: false,
        showMenu: false,
      }
    }
    if (isArchivedView) {
      const showUnarchive = selectedFiles.some(isFileArchived)
      return {
        showArchiveHere: false,
        showExcludeSearch: false,
        showUnarchive,
        showMenu: showUnarchive,
      }
    }
    const anyActive = selectedFiles.some(isActiveInFolder)
    const anyPathOnly = selectedFiles.some(isPathArchivedOnly)
    const anyFileArchived = selectedFiles.some(isFileArchived)
    // Root files have no path — path-level "archive in this folder" is meaningless
    const showArchiveHere =
      anyActive && !isRootView && !isUnassignedView
    const showExcludeSearch = anyActive || anyPathOnly
    const showUnarchive = anyPathOnly || anyFileArchived
    return {
      showArchiveHere,
      showExcludeSearch,
      showUnarchive,
      showMenu: showArchiveHere || showExcludeSearch || showUnarchive,
    }
  }, [selectedFiles, isArchivedView, isRootView, isUnassignedView])

  // Close Move / Archive / Delete dropdowns when clicking blank area (or Esc).
  // Capture-phase pointerdown so icon-grid stopPropagation cannot swallow it.
  useEffect(() => {
    if (!openMenu) return
    const onPointerDown = (e: PointerEvent) => {
      const t = e.target
      if (!(t instanceof globalThis.Node)) return
      if (menusRef.current?.contains(t)) return
      setOpenMenu(null)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpenMenu(null)
    }
    document.addEventListener("pointerdown", onPointerDown, true)
    document.addEventListener("keydown", onKey, true)
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true)
      document.removeEventListener("keydown", onKey, true)
    }
  }, [openMenu])

  useEffect(() => {
    if (!hasFileSelection) setOpenMenu(null)
  }, [hasFileSelection])

  const resetNewFolderForm = () => {
    setNewFolderName("")
    setNewFolderIconMode("lucide")
    setNewFolderIconKey("folder")
    setNewFolderIconColor(DEFAULT_ICON_COLOR)
    setNewFolderSymbol("")
  }

  const handleCreateFolder = async () => {
    if (!newFolderName.trim()) return
    const icon = buildIconPayload({
      iconMode: "lucide",
      iconKey: "folder",
      iconColor: newFolderIconColor,
      symbol: "",
    })
    await createSubFolder(collectionId, newFolderName.trim(), icon)
    resetNewFolderForm()
    setNewFolderDialog(false)
  }

  const newFolderPreview = useMemo(
    () => ({
      name: newFolderName,
      icon_type: "lucide" as const,
      icon_value: "folder",
      icon_color: newFolderIconColor,
    }),
    [newFolderName, newFolderIconColor]
  )

  const moveBlocked = useMemo(
    () => blockedMoveFolderIds(folderTree, selectedFolderIdsArr),
    [folderTree, selectedFolderIdsArr]
  )

  const pickMenuAction = (action: string) => {
    setOpenMenu(null)
    setConfirmAction(action)
  }

  const toggleMenu = (id: "move" | "archive" | "delete" | "deleteFolder") => {
    setOpenMenu((cur) => (cur === id ? null : id))
  }

  const canGoUp = !!currentFolderId
  const handleGoUp = () => {
    if (!currentFolderId) return
    // 虚拟容器与归档的上一级是根
    if (currentFolderId === "__archived__" || isVirtualFolderId(currentFolderId)) {
      navigateToRoot(collectionId)
      return
    }
    // 分组/支链窗口里的文件夹：上一级是对应的虚拟容器（v2 显示层级）
    if (currentFolder?.kind === "branch") {
      void selectFolder(collectionId, "__branches__")
      return
    }
    if (currentFolder?.kind === "user_group") {
      void selectFolder(collectionId, "__groups__")
      return
    }
    if (!currentFolder?.parent_folder_id) {
      navigateToRoot(collectionId)
      return
    }
    void selectFolder(collectionId, currentFolder.parent_folder_id)
  }

  /** Live chrome snapshot — toolKey changes drive the crossfade. */
  const liveChrome = useMemo((): ToolbarChrome => {
    const showDefinitive =
      selectedFiles.length === 1 && isActiveInFolder(selectedFiles[0])
    const isDefinitive = selectedFiles[0]?.is_definitive ?? false
    if (hasFileSelection && hasFolderSelection) {
      return {
        kind: "mixed",
        folderCount: selectedFolderIdsArr.length,
        canDelete: !hasSystemFolder && !hasTimelineFolder,
        timelineBlocked: hasTimelineFolder,
        fileCount: selectedIds.length,
        multiSelectMode,
        archive: archiveUi,
        showDefinitive,
        isDefinitive,
        isArchivedView,
        isRootView,
        isUnassignedView,
        canMoveFolders,
        canArchiveFolders,
        canRestoreFolders,
      }
    }
    if (hasFileSelection) {
      return {
        kind: "file",
        fileCount: selectedIds.length,
        multiSelectMode,
        archive: archiveUi,
        showDefinitive,
        isDefinitive,
        isArchivedView,
        isRootView,
        isUnassignedView,
      }
    }
    if (hasFolderSelection) {
      return {
        kind: "folder",
        folderCount: selectedFolderIdsArr.length,
        canDelete: !hasSystemFolder && !hasTimelineFolder,
        timelineBlocked: hasTimelineFolder,
        canMove: canMoveFolders,
        canArchive: canArchiveFolders,
        canRestore: canRestoreFolders,
      }
    }
    if (multiSelectMode) {
      return { kind: "multi" }
    }
    return {
      kind: "default",
      showCreate:
        !isArchivedView &&
        !isVirtualFolderId(currentFolderId) &&
        !timelineWindowView,
      showBurnAll: isUnassignedView || isArchivedView,
      multiSelectMode: false,
    }
  }, [
    hasFileSelection,
    hasFolderSelection,
    selectedFolderIdsArr.length,
    selectedIds.length,
    hasSystemFolder,
    hasTimelineFolder,
    multiSelectMode,
    archiveUi,
    selectedFiles,
    isArchivedView,
    isRootView,
    isUnassignedView,
    currentFolderId,
    canMoveFolders,
    canArchiveFolders,
    canRestoreFolders,
  ])

  const toolKey = chromeToolKey(liveChrome)

  /**
   * Dual-layer swap with mount-driven CSS keyframes.
   * - view advances only in layout effect (avoids half-frame where toolKey
   *   changed but swapId did not — that remount skipped the animation)
   * - leaveChromeRef tracks the latest chrome for the displayed key so the
   *   outgoing layer freezes accurate content
   * - same toolKey → return same state reference (no loop / no skipped motion)
   */
  type SwapLayer = { key: string; chrome: ToolbarChrome; swapId: number }
  type SwapView = {
    key: string
    swapId: number
    leaving: SwapLayer | null
  }
  const [view, setView] = useState<SwapView>(() => ({
    key: toolKey,
    swapId: 0,
    leaving: null,
  }))
  const leaveChromeRef = useRef(liveChrome)

  useLayoutEffect(() => {
    setView((v) => {
      if (v.key === toolKey) {
        leaveChromeRef.current = liveChrome
        return v
      }
      const nextId = v.swapId + 1
      const leaving: SwapLayer = {
        key: v.key,
        chrome: leaveChromeRef.current,
        swapId: nextId,
      }
      leaveChromeRef.current = liveChrome
      return { key: toolKey, swapId: nextId, leaving }
    })
  }, [toolKey, liveChrome])

  // Close menus when the tool set actually changes
  const prevToolKeyForMenuRef = useRef(toolKey)
  useLayoutEffect(() => {
    if (prevToolKeyForMenuRef.current !== toolKey) {
      prevToolKeyForMenuRef.current = toolKey
      setOpenMenu(null)
    }
  }, [toolKey])

  useEffect(() => {
    if (!view.leaving) return
    const id = view.leaving.swapId
    const t = window.setTimeout(() => {
      setView((v) =>
        v.leaving && v.leaving.swapId === id ? { ...v, leaving: null } : v
      )
    }, TB_SWAP_MS)
    return () => window.clearTimeout(t)
  }, [view.leaving])

  // Live chrome while displayed key matches; frozen while waiting for layout swap
  const activeChrome =
    view.key === toolKey ? liveChrome : leaveChromeRef.current
  const frontInteractive = true
  const leaving = view.leaving
  const swapId = view.swapId

  const renderChrome = (
    chrome: ToolbarChrome,
    opts: { interactive: boolean; menus: boolean }
  ) => {
    const tab = opts.interactive ? 0 : -1
    const on = opts.interactive

    if (chrome.kind === "default") {
      return (
        <>
          {chrome.showCreate && (
            <>
              <Button
                variant="ghost"
                size="xs"
                onClick={() => on && setNewFolderDialog(true)}
                title={t("fileMgmt.newFolder")}
                className={tbBtn}
                tabIndex={tab}
              >
                <FolderPlus />
                {t("fileMgmt.newFolder")}
              </Button>
              <Button
                variant="ghost"
                size="xs"
                onClick={() => on && fileInputRef.current?.click()}
                title={t("fileMgmt.uploadFile")}
                className={tbBtn}
                tabIndex={tab}
              >
                <Upload />
                {t("common.upload")}
              </Button>
              <Button
                variant="ghost"
                size="xs"
                onClick={() => on && folderInputRef.current?.click()}
                title={t("fileMgmt.uploadFolder")}
                className={tbBtn}
                tabIndex={tab}
              >
                <FolderInput />
                {t("fileMgmt.uploadFolder")}
              </Button>
              <ToolbarDivider />
            </>
          )}
          {chrome.showBurnAll && currentFolderFiles.length > 0 && (
            <>
              <TbAct
                icon={Flame}
                label={t("fileMgmt.burnAll")}
                title={t("fileMgmt.burnAllTitle")}
                danger
                onClick={() => on && pickMenuAction("burnAll")}
                tabIndex={tab}
              />
              <ToolbarDivider />
            </>
          )}
          {/* Sort / Select — secondary cluster */}
          <div className="relative">
            <Button
              variant="ghost"
              size="xs"
              onClick={() =>
                on &&
                setOpenMenu((m) => (m === "sort" ? null : "sort"))
              }
              title={t("fileMgmt.sortFiles")}
              className={cn(tbBtn, openMenu === "sort" && opts.menus && "is-on")}
              tabIndex={tab}
            >
              <ArrowUpDown />
              {t("fileMgmt.sort")}
              <ChevronDown className="size-3 opacity-60" />
            </Button>
            {opts.menus && (
              <SoftMenu
                open={openMenu === "sort"}
                className={cn(tbMenuPanel, "min-w-[200px]")}
              >
                {(
                  [
                    { id: "name" as const, label: t("fileMgmt.sortByName") },
                    { id: "type" as const, label: t("fileMgmt.sortByType") },
                  ] as const
                ).map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    role="menuitem"
                    className={cn(
                      "pm-files-menu-opt",
                      folderFileSort === opt.id && "is-on"
                    )}
                    onClick={() => {
                      setFolderFileSort(collectionId, opt.id)
                      setOpenMenu(null)
                    }}
                  >
                    {opt.label}
                  </button>
                ))}
                <button
                  type="button"
                  role="menuitem"
                  className={cn(
                    "pm-files-menu-opt",
                    isCreatedSortMode(folderFileSort) && "is-on"
                  )}
                  title={t("fileMgmt.clickToggleSort")}
                  onClick={() => {
                    setFolderFileSort(
                      collectionId,
                      nextCreatedSortMode(folderFileSort)
                    )
                    setOpenMenu(null)
                  }}
                >
                  {t("fileMgmt.sortByCreated")}
                  {folderFileSort === "created_desc" && (
                    <span className="text-[var(--pm-faint)]"> · {t("fileMgmt.newest")}</span>
                  )}
                  {folderFileSort === "created_asc" && (
                    <span className="text-[var(--pm-faint)]"> · {t("fileMgmt.oldest")}</span>
                  )}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className={cn(
                    "pm-files-menu-opt",
                    isUpdatedSortMode(folderFileSort) && "is-on"
                  )}
                  title={t("fileMgmt.sortByUpdatedHint")}
                  onClick={() => {
                    setFolderFileSort(
                      collectionId,
                      nextUpdatedSortMode(folderFileSort)
                    )
                    setOpenMenu(null)
                  }}
                >
                  {t("fileMgmt.sortByUpdated")}
                  {folderFileSort === "updated_desc" && (
                    <span className="text-[var(--pm-faint)]"> · {t("fileMgmt.newest")}</span>
                  )}
                  {folderFileSort === "updated_asc" && (
                    <span className="text-[var(--pm-faint)]"> · {t("fileMgmt.oldest")}</span>
                  )}
                </button>
              </SoftMenu>
            )}
          </div>
          <Button
            variant="ghost"
            size="xs"
            onClick={() => on && enterMultiSelectMode()}
            title={t("fileMgmt.selectMultiple")}
            className={tbBtn}
            tabIndex={tab}
          >
            <CheckSquare />
            {t("common.select")}
          </Button>
        </>
      )
    }

    if (chrome.kind === "multi") {
      return (
        <>
          <Button
            variant="secondary"
            size="xs"
            onClick={() => on && exitMultiSelectMode()}
            title={t("fileMgmt.exitMultiSelect")}
            className={cn(tbBtn, "is-accent")}
            tabIndex={tab}
          >
            <CheckSquare />
            {t("common.select")}
          </Button>
          <ToolbarDivider />
          <span className={cn(tbLabel, "is-accent")}>{t("fileMgmt.tapToSelect")}</span>
          <Button
            variant="ghost"
            size="xs"
            onClick={() => on && selectAllFiles()}
            title={t("fileMgmt.selectAllFolder")}
            className={tbBtn}
            tabIndex={tab}
          >
            {t("fileMgmt.selectAll")}
          </Button>
        </>
      )
    }

    if (chrome.kind === "folder") {
      return (
        <>
          <span className={tbLabel}>
            {chrome.folderCount === 1
              ? t("fileMgmt.nFolders", { n: chrome.folderCount })
              : t("fileMgmt.nFoldersPlural", { n: chrome.folderCount })}
          </span>
          {(chrome.canMove || chrome.canArchive || chrome.canRestore) && (
            <ToolbarDivider />
          )}
          {chrome.canMove && (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => {
                if (!on) return
                setMoveDialogMode("folders")
                setMoveDialogOpen(true)
              }}
              title={t("fileMgmt.moveFolder")}
              className={tbBtn}
              tabIndex={tab}
            >
              <MoveRight />
              {t("fileMgmt.move")}
            </Button>
          )}
          {chrome.canArchive && (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => on && setConfirmAction("archiveFolders")}
              title={t("fileMgmt.archiveFolder")}
              className={tbBtn}
              tabIndex={tab}
            >
              <Archive />
              {t("common.archive")}
            </Button>
          )}
          {chrome.canRestore && (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => on && setConfirmAction("restoreFolders")}
              title={t("common.restore")}
              className={tbBtn}
              tabIndex={tab}
            >
              <ArchiveRestore />
              {t("common.restore")}
            </Button>
          )}
          {chrome.canDelete ? (
            <>
              <ToolbarDivider />
              <TbAct
                icon={Trash2}
                label={t("common.delete")}
                title={t("fileMgmt.deleteFolderTitle")}
                danger
                onClick={() => on && pickMenuAction("deleteFolder")}
                tabIndex={tab}
              />
            </>
          ) : chrome.timelineBlocked ? (
            <>
              <ToolbarDivider />
              <Button
                variant="ghost"
                size="xs"
                disabled
                className={cn(tbBtn, "is-danger")}
                title={t("fileMgmt.deleteDisabledTimeline")}
                tabIndex={tab}
              >
                <Trash2 />
                {t("common.delete")}
              </Button>
            </>
          ) : null}
        </>
      )
    }

    // file | mixed
    const filePart = chrome.kind === "file" || chrome.kind === "mixed" ? chrome : null
    const folderPart = chrome.kind === "mixed" ? chrome : null

    return (
      <>
        {folderPart && (
          <>
            <span className={tbLabel}>
              {folderPart.folderCount === 1
                ? t("fileMgmt.nFolders", { n: folderPart.folderCount })
                : t("fileMgmt.nFoldersPlural", { n: folderPart.folderCount })}
            </span>
            {(folderPart.canMoveFolders ||
              folderPart.canArchiveFolders ||
              folderPart.canRestoreFolders) && <ToolbarDivider />}
            {folderPart.canMoveFolders && (
              <TbAct
                icon={MoveRight}
                label={t("fileMgmt.move")}
                title={t("fileMgmt.move")}
                onClick={() => {
                  if (!on) return
                  setMoveDialogMode("mixed")
                  setMoveDialogOpen(true)
                }}
                tabIndex={tab}
              />
            )}
            {folderPart.canArchiveFolders && (
              <TbAct
                icon={Archive}
                label={t("common.archive")}
                title={t("fileMgmt.archiveFolder")}
                onClick={() => on && setConfirmAction("archiveFolders")}
                tabIndex={tab}
              />
            )}
            {folderPart.canRestoreFolders && (
              <TbAct
                icon={ArchiveRestore}
                label={t("common.restore")}
                title={t("common.restore")}
                onClick={() => on && setConfirmAction("restoreFolders")}
                tabIndex={tab}
              />
            )}
            {folderPart.canDelete ? (
              <>
                <ToolbarDivider />
                <TbAct
                  icon={Trash2}
                  label={t("common.delete")}
                  title={t("fileMgmt.deleteFolderTitle")}
                  danger
                  onClick={() => on && pickMenuAction("deleteFolder")}
                  tabIndex={tab}
                />
              </>
            ) : folderPart.timelineBlocked ? (
              <>
                <ToolbarDivider />
                <Button
                  variant="ghost"
                  size="xs"
                  disabled
                  className={cn(tbBtn, "is-danger")}
                  title={t("fileMgmt.deleteDisabledTimeline")}
                  tabIndex={tab}
                >
                  <Trash2 />
                  {t("common.delete")}
                </Button>
              </>
            ) : null}
            {filePart && <ToolbarDivider />}
          </>
        )}

        {filePart && (
          <>
            <span className={tbLabel}>{t("fileMgmt.nSelected", { n: filePart.fileCount })}</span>
            {filePart.multiSelectMode && (
              <Button
                variant="ghost"
                size="xs"
                onClick={() => on && selectAllFiles()}
                title={t("fileMgmt.selectAllFolder")}
                className={tbBtn}
                tabIndex={tab}
              >
                {t("common.all")}
              </Button>
            )}

            <ToolbarDivider />

            {/*
              v2 动词区：移动到 / 复制到 / 归档 / 删除（只摘当前挂载，兜底未分配）/ 焚毁（唯一销毁）。
              纯图标 + hover 右滑出文字；支链/分组窗口只读：仅复制到 + 归档。
            */}
            {filePart.isArchivedView ? (
              <>
                {filePart.archive.showUnarchive && (
                  <TbAct
                    icon={ArchiveRestore}
                    label={t("common.restore")}
                    title={t("fileMgmt.restoreSearch")}
                    onClick={() => on && pickMenuAction("unarchive")}
                    tabIndex={tab}
                  />
                )}
                <TbAct
                  icon={Flame}
                  label={t("fileMgmt.burn")}
                  title={t("fileMgmt.burnTitle")}
                  danger
                  onClick={() => on && pickMenuAction("delete")}
                  tabIndex={tab}
                />
              </>
            ) : filePart.isUnassignedView ? (
              <>
                <TbAct
                  icon={MoveRight}
                  label={t("fileMgmt.move")}
                  title={t("fileMgmt.moveSelected")}
                  onClick={() => {
                    if (!on) return
                    setOpenMenu(null)
                    setMoveDialogMode("files")
                    setMoveDialogOpen(true)
                  }}
                  tabIndex={tab}
                />
                <TbAct
                  icon={Flame}
                  label={t("fileMgmt.burn")}
                  title={t("fileMgmt.burnTitle")}
                  danger
                  onClick={() => on && pickMenuAction("delete")}
                  tabIndex={tab}
                />
              </>
            ) : filePart.isRootView ? (
              <>
                <TbAct
                  icon={MoveRight}
                  label={t("fileMgmt.move")}
                  title={t("fileMgmt.moveSelected")}
                  onClick={() => {
                    if (!on) return
                    setOpenMenu(null)
                    setMoveDialogMode("files")
                    setMoveDialogOpen(true)
                  }}
                  tabIndex={tab}
                />
                {filePart.archive.showExcludeSearch && (
                  <TbAct
                    icon={SearchX}
                    label={t("common.archive")}
                    title={t("fileMgmt.excludeSearchEverywhere")}
                    onClick={() => on && pickMenuAction("excludeSearch")}
                    tabIndex={tab}
                  />
                )}
                {filePart.archive.showUnarchive && (
                  <TbAct
                    icon={ArchiveRestore}
                    label={t("common.restore")}
                    title={t("fileMgmt.restoreSelected")}
                    onClick={() => on && pickMenuAction("unarchive")}
                    tabIndex={tab}
                  />
                )}
                <TbAct
                  icon={Trash2}
                  label={t("common.delete")}
                  title={t("fileMgmt.deleteHereTitle")}
                  danger
                  onClick={() => on && pickMenuAction("deleteRoot")}
                  tabIndex={tab}
                />
                <TbAct
                  icon={Flame}
                  label={t("fileMgmt.burn")}
                  title={t("fileMgmt.burnTitle")}
                  danger
                  onClick={() => on && pickMenuAction("delete")}
                  tabIndex={tab}
                />
              </>
            ) : timelineWindowView ? (
              <>
                <TbAct
                  icon={Link2}
                  label={t("fileMgmt.mirrorTo")}
                  title={t("fileMgmt.mirrorToDest")}
                  onClick={() => {
                    if (!on) return
                    setOpenMenu(null)
                    setCopyDialogOpen(true)
                  }}
                  tabIndex={tab}
                />
                {filePart.archive.showMenu && (
                  <div className="relative">
                    <TbAct
                      icon={Archive}
                      label={t("common.archive")}
                      title={t("fileMgmt.archiveOptions")}
                      chevron
                      active={openMenu === "archive" && opts.menus}
                      onClick={() => on && toggleMenu("archive")}
                      tabIndex={tab}
                    />
                    {opts.menus && (
                      <SoftMenu open={openMenu === "archive"} className={tbMenuPanel}>
                        {filePart.archive.showUnarchive && (
                          <MenuItem
                            icon={<ArchiveRestore className="h-3.5 w-3.5" />}
                            title={t("common.restore")}
                            description={t("fileMgmt.restoreSearch")}
                            onClick={() => pickMenuAction("unarchive")}
                          />
                        )}
                        {filePart.archive.showExcludeSearch && (
                          <MenuItem
                            icon={<SearchX className="h-3.5 w-3.5" />}
                            title={t("fileMgmt.archiveGlobally")}
                            description={t("fileMgmt.excludeSearchEverywhere")}
                            onClick={() => pickMenuAction("excludeSearch")}
                          />
                        )}
                      </SoftMenu>
                    )}
                  </div>
                )}
              </>
            ) : (
              <>
                <TbAct
                  icon={MoveRight}
                  label={t("fileMgmt.moveTo")}
                  title={t("fileMgmt.moveTo")}
                  onClick={() => {
                    if (!on) return
                    setOpenMenu(null)
                    setMoveDialogMode("files")
                    setMoveDialogOpen(true)
                  }}
                  tabIndex={tab}
                />
                <TbAct
                  icon={Link2}
                  label={t("fileMgmt.mirrorTo")}
                  title={t("fileMgmt.mirrorToDest")}
                  onClick={() => {
                    if (!on) return
                    setOpenMenu(null)
                    setCopyDialogOpen(true)
                  }}
                  tabIndex={tab}
                />
                {filePart.archive.showMenu && (
                  <div className="relative">
                    <TbAct
                      icon={Archive}
                      label={t("common.archive")}
                      title={t("fileMgmt.archiveOptions")}
                      chevron
                      active={openMenu === "archive" && opts.menus}
                      onClick={() => on && toggleMenu("archive")}
                      tabIndex={tab}
                    />
                    {opts.menus && (
                      <SoftMenu open={openMenu === "archive"} className={tbMenuPanel}>
                        {filePart.archive.showUnarchive && (
                          <MenuItem
                            icon={<ArchiveRestore className="h-3.5 w-3.5" />}
                            title={t("common.restore")}
                            description={
                              filePart.isArchivedView
                                ? t("fileMgmt.restoreSearch")
                                : t("fileMgmt.restoreInFolder")
                            }
                            onClick={() => pickMenuAction("unarchive")}
                          />
                        )}
                        {filePart.archive.showArchiveHere && (
                          <MenuItem
                            icon={<Archive className="h-3.5 w-3.5" />}
                            title={t("fileMgmt.archiveInFolder")}
                            description={t("fileMgmt.archiveInFolderBody")}
                            onClick={() => pickMenuAction("archiveFolder")}
                          />
                        )}
                        {filePart.archive.showExcludeSearch && (
                          <MenuItem
                            icon={<SearchX className="h-3.5 w-3.5" />}
                            title={t("fileMgmt.archiveGlobally")}
                            description={t("fileMgmt.excludeSearchEverywhere")}
                            onClick={() => pickMenuAction("excludeSearch")}
                          />
                        )}
                      </SoftMenu>
                    )}
                  </div>
                )}
                <TbAct
                  icon={Trash2}
                  label={t("common.delete")}
                  title={t("fileMgmt.deleteHereTitle")}
                  danger
                  onClick={() => on && pickMenuAction("unlink")}
                  tabIndex={tab}
                />
                <TbAct
                  icon={Flame}
                  label={t("fileMgmt.burn")}
                  title={t("fileMgmt.burnTitle")}
                  danger
                  onClick={() => on && pickMenuAction("delete")}
                  tabIndex={tab}
                />
              </>
            )}

            {filePart.showDefinitive && (
              <>
                <ToolbarDivider />
                <TbAct
                  icon={Star}
                  label={t("library.definitive")}
                  title={
                    filePart.isDefinitive
                      ? t("fileMgmt.removeDefinitive")
                      : t("fileMgmt.markDefinitive")
                  }
                  onClick={() => {
                    if (!on) return
                    const f = selectedFiles[0]
                    if (!f) return
                    void toggleDefinitive(
                      collectionId,
                      f.file_id,
                      !f.is_definitive,
                      f.version ?? 1
                    )
                  }}
                  tabIndex={tab}
                  active={filePart.isDefinitive}
                  iconClassName={
                    filePart.isDefinitive
                      ? "fill-[var(--pm-green)] text-[var(--pm-green)]"
                      : "text-[var(--pm-green)]"
                  }
                />
              </>
            )}
          </>
        )}
      </>
    )
  }

  return (
    <>
    <div className="pm-files-toolbar">
      {/* Nav well — stays put while tool chrome crossfades */}
      <div className="pm-files-tb-nav">
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={handleGoUp}
          disabled={!canGoUp}
          title={canGoUp ? t("fileMgmt.goUp") : t("fileMgmt.alreadyAtRoot")}
          className={tbIconBtn}
        >
          <ChevronLeft />
        </Button>
      </div>

      <div className="pm-files-tb-sep" aria-hidden />

      {/* Primary tool strip — dual-layer crossfade on mode swap */}
      <div className="pm-files-toolbar-tools relative min-w-0 overflow-visible" ref={menusRef}>
        {leaving && (
          <div
            key={`leave-${leaving.swapId}`}
            className={cn(tbPanelBase, "animate-fm-toolbar-out")}
            aria-hidden
          >
            {renderChrome(leaving.chrome, { interactive: false, menus: false })}
          </div>
        )}
        <div
          key={`in-${swapId}`}
          className={cn(
            tbPanelBase,
            // First mount (swapId 0) is static; every real swap remounts with in-anim
            swapId > 0 && "animate-fm-toolbar-in"
          )}
        >
          {renderChrome(activeChrome, {
            interactive: frontInteractive,
            menus: frontInteractive,
          })}
        </div>
      </div>
      {trailing}
      <div className="pm-files-tb-sep" aria-hidden />
      <div className="pm-files-view-toggle" role="group" aria-label={t("fileMgmt.gridView")}>
        <Button
          variant="ghost"
          size="icon-sm"
          className={cn(tbIconBtn, folderFileView === "grid" && "is-on")}
          title={t("fileMgmt.gridView")}
          onClick={() => setFolderFileView(collectionId, "grid")}
        >
          <LayoutGrid />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          className={cn(tbIconBtn, folderFileView === "list" && "is-on")}
          title={t("fileMgmt.listView")}
          onClick={() => setFolderFileView(collectionId, "list")}
        >
          <List />
        </Button>
      </div>
    </div>

      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files) {
            for (const f of Array.from(e.target.files))
              uploadFile(collectionId, f)
            e.target.value = ""
          }
        }}
      />
      <input
        ref={folderInputRef}
        type="file"
        // @ts-expect-error webkitdirectory is not in type defs
        webkitdirectory=""
        directory=""
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files) {
            uploadFolder(collectionId, Array.from(e.target.files))
            e.target.value = ""
          }
        }}
      />

      <Dialog
        open={newFolderDialog}
        onOpenChange={(open) => {
          setNewFolderDialog(open)
          if (!open) resetNewFolderForm()
        }}
      >
        <DialogContent className="pm-dialog max-w-md">
          <DialogHeader>
            <DialogTitle>{t("fileMgmt.newFolder")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-1">
            <div className="flex items-center gap-3">
              <div
                key={`${newFolderIconMode}-${newFolderIconKey}-${newFolderIconColor}-${newFolderSymbol}`}
                className="h-10 w-10 rounded-[var(--pm-r-sm)] flex items-center justify-center bg-[var(--pm-green-wash)] shrink-0"
                title={t("common.preview")}
              >
                <GroupIconView source={newFolderPreview} className="h-5 w-5" />
              </div>
              <div className="flex-1 min-w-0">
                <label className="pm-field-label">{t("common.name")}</label>
                <Input
                  value={newFolderName}
                  onChange={(e) => setNewFolderName(e.target.value)}
                  placeholder={t("fileMgmt.folderName")}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void handleCreateFolder()
                  }}
                  autoFocus
                  className="h-8"
                />
              </div>
            </div>
            <IconPickerPanel
              iconMode="lucide"
              iconKey="folder"
              iconColor={newFolderIconColor}
              symbol=""
              onIconMode={setNewFolderIconMode}
              onIconKey={setNewFolderIconKey}
              onIconColor={setNewFolderIconColor}
              onSymbol={setNewFolderSymbol}
              variant="plain"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setNewFolderDialog(false)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              size="sm"
              onClick={() => void handleCreateFolder()}
              disabled={!newFolderName.trim()}
            >
              {t("common.create")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog
        open={moveDialogOpen}
        onOpenChange={(open) => {
          setMoveDialogOpen(open)
          if (open) setMoveTarget(undefined)
        }}
      >
        <DialogContent className="pm-dialog pm-files-dest-dialog">
          <DialogHeader className="pm-group-dialog-head">
            <DialogTitle className="pm-group-dialog-title">
              {moveDialogMode === "files"
                ? t("fileMgmt.moveToN", { n: selectedIds.length })
                : moveDialogMode === "folders"
                  ? t("fileMgmt.moveFoldersToN", {
                      n: selectedFolderIdsArr.length,
                    })
                  : t("fileMgmt.moveItemsToN", {
                      n: selectedIds.length + selectedFolderIdsArr.length,
                    })}
            </DialogTitle>
          </DialogHeader>
          <div className="pm-files-dest-body">
            <FolderDestCard
              key={moveDialogOpen ? "move-open" : "move-closed"}
              collectionId={collectionId}
              nodes={folderTree}
              selectedId={moveTarget}
              onSelect={setMoveTarget}
              includeRoot={
                moveDialogMode !== "files" && currentFolderId != null
              }
              isSelectable={(n) =>
                moveDialogMode === "files"
                  ? isFileMoveSelectable(n, currentFolderId)
                  : isFolderMoveSelectable(n, {
                      currentParentId: currentFolderId,
                      blocked: moveBlocked,
                    })
              }
            />
          </div>
          <DialogFooter className="pm-group-dialog-foot gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setMoveDialogOpen(false)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              size="sm"
              disabled={
                moveDialogMode === "files"
                  ? !moveTarget
                  : moveTarget === undefined
              }
              onClick={async () => {
                if (moveDialogMode === "files") {
                  if (!moveTarget) return
                  await moveFilesToFolder(collectionId, selectedIds, moveTarget)
                } else {
                  if (moveTarget === undefined) return
                  for (const node of selectedFolderNodes) {
                    await moveFolder(
                      collectionId,
                      node.folder_id,
                      moveTarget,
                      node.version
                    )
                  }
                  if (
                    moveDialogMode === "mixed" &&
                    selectedIds.length &&
                    moveTarget
                  ) {
                    await moveFilesToFolder(
                      collectionId,
                      selectedIds,
                      moveTarget
                    )
                  }
                }
                setMoveDialogOpen(false)
              }}
            >
              {t("fileMgmt.move")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={copyDialogOpen}
        onOpenChange={(open) => {
          setCopyDialogOpen(open)
          if (open) setCopyTarget(undefined)
        }}
      >
        <DialogContent className="pm-dialog pm-files-dest-dialog">
          <DialogHeader className="pm-group-dialog-head">
            <DialogTitle className="pm-group-dialog-title">
              {t("fileMgmt.mirrorToN", { n: selectedIds.length })}
            </DialogTitle>
          </DialogHeader>
          <div className="pm-files-dest-body">
            <FolderDestCard
              key={copyDialogOpen ? "copy-open" : "copy-closed"}
              collectionId={collectionId}
              nodes={folderTree}
              selectedId={copyTarget}
              onSelect={setCopyTarget}
              isSelectable={(n) => isFileMoveSelectable(n, currentFolderId)}
            />
          </div>
          <DialogFooter className="pm-group-dialog-foot gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setCopyDialogOpen(false)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              size="sm"
              disabled={!copyTarget}
              onClick={async () => {
                if (!copyTarget) return
                await copyFilesToFolder(collectionId, selectedIds, copyTarget)
                setCopyDialogOpen(false)
              }}
            >
              {t("fileMgmt.mirrorTo")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!confirmAction}
        onOpenChange={(v) => !v && setConfirmAction(null)}
      >
        <DialogContent className="pm-dialog max-w-sm">
          <DialogHeader>
            <DialogTitle>
              {confirmAction === "delete" && t("fileMgmt.burnFilesQ")}
              {confirmAction === "burnAll" && t("fileMgmt.burnAllQ")}
              {confirmAction === "deleteRoot" && t("fileMgmt.deleteHereQ")}
              {confirmAction === "deleteFolder" && t("fileMgmt.deleteFoldersQ")}
              {confirmAction === "archiveFolder" && t("fileMgmt.archiveInFolderQ")}
              {confirmAction === "excludeSearch" && t("fileMgmt.archiveGloballyQ")}
              {confirmAction === "unarchive" && t("fileMgmt.restoreFilesQ")}
              {confirmAction === "archiveFolders" && t("fileMgmt.archiveFolderQ")}
              {confirmAction === "restoreFolders" && t("fileMgmt.restoreFolderQ")}
              {confirmAction === "unlink" && t("fileMgmt.deleteHereQ")}
            </DialogTitle>
          </DialogHeader>
          <p className="pm-dialog-body">
            {confirmAction === "delete" && t("fileMgmt.burnBody")}
            {confirmAction === "burnAll" && t("fileMgmt.burnAllBody")}
            {(confirmAction === "deleteRoot" || confirmAction === "unlink") &&
              t("fileMgmt.deleteHereBody")}
            {confirmAction === "deleteFolder" && t("fileMgmt.deleteFoldersBody")}
            {burnDefinitiveCount > 0 && confirmAction !== "deleteFolder" && (
              <span className="block mt-1 text-[var(--pm-danger)]">
                {t("fileMgmt.burnDefinitiveWarn", { n: burnDefinitiveCount })}
              </span>
            )}
            {confirmAction === "archiveFolder" && t("fileMgmt.archiveInFolderBody")}
            {confirmAction === "excludeSearch" && t("fileMgmt.excludeSearchEverywhere")}
            {confirmAction === "unarchive" &&
              (isArchivedView
                ? t("fileMgmt.restoreSearch")
                : t("fileMgmt.restoreInFolder"))}
            {confirmAction === "archiveFolders" && t("fileMgmt.archiveFolderBody")}
            {confirmAction === "restoreFolders" && t("fileMgmt.restoreFolderBody")}
          </p>
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setConfirmAction(null)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              variant={
                confirmAction === "delete" ||
                confirmAction === "deleteFolder" ||
                confirmAction === "deleteRoot" ||
                confirmAction === "burnAll"
                  ? "destructive"
                  : "default"
              }
              size="sm"
              onClick={async () => {
                if (confirmAction === "delete")
                  await permanentlyDeleteFiles(collectionId, selectedIds)
                else if (confirmAction === "burnAll")
                  await permanentlyDeleteFiles(
                    collectionId,
                    currentFolderFiles.map((f) => f.file_id)
                  )
                else if (confirmAction === "deleteRoot")
                  await removeFilesFromRoot(collectionId, selectedIds)
                else if (confirmAction === "deleteFolder") {
                  for (const fid of selectedFolderIdsArr)
                    await removeFolder(collectionId, fid)
                  clearFolderSelection()
                } else if (confirmAction === "archiveFolder") {
                  const targets = selectedFiles.filter(isActiveInFolder)
                  if (targets.length)
                    await archiveFilesForFolder(
                      collectionId,
                      targets.map((f) => f.file_id),
                      targets
                    )
                } else if (confirmAction === "excludeSearch") {
                  const targets = selectedFiles.filter((f) => !isFileArchived(f))
                  if (targets.length)
                    await excludeFilesFromSearch(
                      collectionId,
                      targets.map((f) => f.file_id),
                      targets
                    )
                } else if (confirmAction === "unarchive") {
                  const targets = selectedFiles.filter(
                    (f) => isPathArchivedOnly(f) || isFileArchived(f)
                  )
                  if (targets.length)
                    await unarchiveFiles(
                      collectionId,
                      targets.map((f) => f.file_id),
                      targets
                    )
                } else if (confirmAction === "archiveFolders") {
                  await archiveFolders(
                    collectionId,
                    selectedFolderNodes
                      .filter((f) => f.kind === "plain" && !f.archived)
                      .map((f) => f.folder_id)
                  )
                  clearFolderSelection()
                } else if (confirmAction === "restoreFolders") {
                  await unarchiveFolders(
                    collectionId,
                    selectedFolderNodes
                      .filter((f) => f.kind === "plain" && f.archived)
                      .map((f) => f.folder_id)
                  )
                  clearFolderSelection()
                } else if (confirmAction === "unlink")
                  await removeFilesFromCurrentFolder(collectionId, selectedIds)
                setConfirmAction(null)
              }}
            >
            {confirmAction === "delete" || confirmAction === "burnAll"
              ? t("fileMgmt.burn")
              : confirmAction === "deleteFolder" ||
                  confirmAction === "deleteRoot"
                ? t("common.delete")
                : t("common.confirm")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}

function findFolderInTree(
  tree: FolderTreeNode[],
  fid: string
): FolderTreeNode | null {
  for (const n of tree) {
    if (n.folder_id === fid) return n
    const found = findFolderInTree(n.children, fid)
    if (found) return found
  }
  return null
}


