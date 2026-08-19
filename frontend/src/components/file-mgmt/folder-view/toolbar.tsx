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
  X,
  Star,
  ChevronDown,
  CheckSquare,
  ChevronLeft,
  ArrowUpDown,
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
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { FileSummary, FolderTreeNode } from "@/types/file-mgmt"
import { cn } from "@/lib/utils"
import {
  DEFAULT_ICON_COLOR,
  GroupIconView,
  IconPickerPanel,
  buildIconPayload,
} from "@/components/file-mgmt/timeline-view/group-icons"

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
      multiSelectMode: boolean
    }
  | {
      kind: "multi"
    }
  | {
      kind: "folder"
      folderCount: number
      canDelete: boolean
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
    }
  | {
      kind: "mixed"
      folderCount: number
      canDelete: boolean
      fileCount: number
      multiSelectMode: boolean
      archive: ArchiveUiSnap
      showDefinitive: boolean
      isDefinitive: boolean
      isArchivedView: boolean
      isRootView: boolean
    }

/** Dropdown surface — nested white + soft shadow; parent toolbar z-index above grid. */
const tbMenuPanel = "pm-files-menu absolute left-0 top-full mt-1 min-w-[260px]"

function chromeToolKey(c: ToolbarChrome): string {
  switch (c.kind) {
    case "default":
      return `default:${c.showCreate ? "c" : "-"}${c.multiSelectMode ? "m" : ""}`
    case "multi":
      return "multi"
    case "folder":
      return `folder:${c.canDelete ? "del" : "sys"}`
    case "file":
      return [
        "file",
        c.archive.showMenu ? "a" : "-",
        c.archive.showUnarchive ? "u" : "-",
        c.archive.showArchiveHere ? "h" : "-",
        c.archive.showExcludeSearch ? "e" : "-",
        c.showDefinitive ? "d" : "-",
        c.multiSelectMode ? "m" : "-",
        c.isRootView ? "root" : "f",
      ].join(":")
    case "mixed":
      return [
        "mixed",
        c.canDelete ? "del" : "sys",
        c.archive.showMenu ? "a" : "-",
        c.showDefinitive ? "d" : "-",
        c.multiSelectMode ? "m" : "-",
        c.isRootView ? "root" : "f",
      ].join(":")
  }
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
    copyFilesToFolder,
    removeFilesFromCurrentFolder,
    archiveFilesForFolder,
    excludeFilesFromSearch,
    unarchiveFiles,
    permanentlyDeleteFiles,
    toggleDefinitive,
    removeFolder,
    clearFolderSelection,
    multiSelectMode,
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
  const [copyDialogOpen, setCopyDialogOpen] = useState(false)
  const [confirmAction, setConfirmAction] = useState<string | null>(null)
  /** At most one action menu open: move | archive | delete */
  const [openMenu, setOpenMenu] = useState<
    "move" | "archive" | "delete" | "sort" | null
  >(
    null
  )
  const menusRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)

  const isArchivedView = currentFolderId === "__archived__"
  /** Root = orphan / no-path files (currentFolderId is null). */
  const isRootView = currentFolderId == null
  // Exclude ingesting files — they must not drive the file action toolbar
  const selectedFiles = currentFolderFiles.filter(
    (f) => selectedFileIds.has(f.file_id) && !ingestingFiles[f.file_id]
  )
  const selectedIds = selectedFiles.map((f) => f.file_id)
  const hasFileSelection = selectedFiles.length > 0
  const hasFolderSelection = selectedFolderIds.size > 0
  const selectedFolderIdsArr = Array.from(selectedFolderIds)
  const hasSystemFolder = selectedFolderIdsArr.some((fid) => {
    const f = findFolderInTree(folderTree, fid)
    return f?.is_system ?? false
  })

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
    const showArchiveHere = anyActive && !isRootView
    const showExcludeSearch = anyActive || anyPathOnly
    const showUnarchive = anyPathOnly || anyFileArchived
    return {
      showArchiveHere,
      showExcludeSearch,
      showUnarchive,
      showMenu: showArchiveHere || showExcludeSearch || showUnarchive,
    }
  }, [selectedFiles, isArchivedView, isRootView])

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
    if (newFolderIconMode === "emoji" && !newFolderSymbol.trim()) {
      return
    }
    const icon = buildIconPayload({
      iconMode: newFolderIconMode,
      iconKey: newFolderIconKey,
      iconColor: newFolderIconColor,
      symbol: newFolderSymbol,
    })
    await createSubFolder(collectionId, newFolderName.trim(), icon)
    resetNewFolderForm()
    setNewFolderDialog(false)
  }

  const newFolderPreview = useMemo(
    () =>
      newFolderIconMode === "emoji" && newFolderSymbol
        ? {
            name: newFolderName,
            icon_type: "emoji" as const,
            icon_value: newFolderSymbol,
          }
        : {
            name: newFolderName,
            icon_type: "lucide" as const,
            icon_value: newFolderIconKey,
            icon_color: newFolderIconColor,
          },
    [
      newFolderIconMode,
      newFolderName,
      newFolderSymbol,
      newFolderIconKey,
      newFolderIconColor,
    ]
  )

  const availableFolders = collectFolders(folderTree, currentFolderId)

  const pickMenuAction = (action: string) => {
    setOpenMenu(null)
    setConfirmAction(action)
  }

  const toggleMenu = (id: "move" | "archive" | "delete") => {
    setOpenMenu((cur) => (cur === id ? null : id))
  }

  const canGoUp = !!currentFolderId
  const handleGoUp = () => {
    if (!currentFolderId) return
    if (
      currentFolderId === "__archived__" ||
      !currentFolder?.parent_folder_id
    ) {
      navigateToRoot(collectionId)
    } else {
      void selectFolder(collectionId, currentFolder.parent_folder_id)
    }
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
        canDelete: !hasSystemFolder,
        fileCount: selectedIds.length,
        multiSelectMode,
        archive: archiveUi,
        showDefinitive,
        isDefinitive,
        isArchivedView,
        isRootView,
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
      }
    }
    if (hasFolderSelection) {
      return {
        kind: "folder",
        folderCount: selectedFolderIdsArr.length,
        canDelete: !hasSystemFolder,
      }
    }
    if (multiSelectMode) {
      return { kind: "multi" }
    }
    return {
      kind: "default",
      showCreate: !isArchivedView,
      multiSelectMode: false,
    }
  }, [
    hasFileSelection,
    hasFolderSelection,
    selectedFolderIdsArr.length,
    selectedIds.length,
    hasSystemFolder,
    multiSelectMode,
    archiveUi,
    selectedFiles,
    isArchivedView,
    isRootView,
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
                title="New folder"
                className={tbBtn}
                tabIndex={tab}
              >
                <FolderPlus />
                New Folder
              </Button>
              <Button
                variant="ghost"
                size="xs"
                onClick={() => on && fileInputRef.current?.click()}
                title="Upload file"
                className={tbBtn}
                tabIndex={tab}
              >
                <Upload />
                Upload
              </Button>
              <Button
                variant="ghost"
                size="xs"
                onClick={() => on && folderInputRef.current?.click()}
                title="Upload folder"
                className={tbBtn}
                tabIndex={tab}
              >
                <FolderInput />
                Upload Folder
              </Button>
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
              title="Sort files"
              className={cn(tbBtn, openMenu === "sort" && opts.menus && "is-on")}
              tabIndex={tab}
            >
              <ArrowUpDown />
              Sort
              <ChevronDown className="size-3 opacity-60" />
            </Button>
            {opts.menus && (
              <SoftMenu
                open={openMenu === "sort"}
                className={cn(tbMenuPanel, "min-w-[200px]")}
              >
                {(
                  [
                    { id: "name" as const, label: "By name" },
                    { id: "type" as const, label: "By type" },
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
                  title="Click again to toggle newest ↔ oldest"
                  onClick={() => {
                    setFolderFileSort(
                      collectionId,
                      nextCreatedSortMode(folderFileSort)
                    )
                    setOpenMenu(null)
                  }}
                >
                  By created
                  {folderFileSort === "created_desc" && (
                    <span className="text-[var(--pm-faint)]"> · newest</span>
                  )}
                  {folderFileSort === "created_asc" && (
                    <span className="text-[var(--pm-faint)]"> · oldest</span>
                  )}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className={cn(
                    "pm-files-menu-opt",
                    isUpdatedSortMode(folderFileSort) && "is-on"
                  )}
                  title="Folder time = latest file update inside. Click again to toggle direction."
                  onClick={() => {
                    setFolderFileSort(
                      collectionId,
                      nextUpdatedSortMode(folderFileSort)
                    )
                    setOpenMenu(null)
                  }}
                >
                  By updated
                  {folderFileSort === "updated_desc" && (
                    <span className="text-[var(--pm-faint)]"> · newest</span>
                  )}
                  {folderFileSort === "updated_asc" && (
                    <span className="text-[var(--pm-faint)]"> · oldest</span>
                  )}
                </button>
              </SoftMenu>
            )}
          </div>
          <Button
            variant="ghost"
            size="xs"
            onClick={() => on && enterMultiSelectMode()}
            title="Select multiple files or folders"
            className={tbBtn}
            tabIndex={tab}
          >
            <CheckSquare />
            Select
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
            title="Exit multi-select (or click empty area)"
            className={cn(tbBtn, "is-accent")}
            tabIndex={tab}
          >
            <CheckSquare />
            Select
          </Button>
          <ToolbarDivider />
          <span className={cn(tbLabel, "is-accent")}>Tap to select</span>
          <Button
            variant="ghost"
            size="xs"
            onClick={() => on && selectAllFiles()}
            title="Select all files in this folder"
            className={tbBtn}
            tabIndex={tab}
          >
            Select All
          </Button>
        </>
      )
    }

    if (chrome.kind === "folder") {
      return (
        <>
          <span className={tbLabel}>
            {chrome.folderCount} folder{chrome.folderCount === 1 ? "" : "s"}
          </span>
          {chrome.canDelete && (
            <>
              <ToolbarDivider />
              <Button
                variant="ghost"
                size="xs"
                className={cn(tbBtn, "is-danger")}
                onClick={() => on && setConfirmAction("deleteFolder")}
                title="Delete folder(s)"
                tabIndex={tab}
              >
                <Trash2 />
                Delete
              </Button>
            </>
          )}
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
              {folderPart.folderCount} folder
              {folderPart.folderCount === 1 ? "" : "s"}
            </span>
            {folderPart.canDelete && (
              <>
                <ToolbarDivider />
                <Button
                  variant="ghost"
                  size="xs"
                  className={cn(tbBtn, "is-danger")}
                  onClick={() => on && setConfirmAction("deleteFolder")}
                  title="Delete folder(s)"
                  tabIndex={tab}
                >
                  <Trash2 />
                  Delete
                </Button>
              </>
            )}
            {filePart && <ToolbarDivider />}
          </>
        )}

        {filePart && (
          <>
            <span className={tbLabel}>{filePart.fileCount} selected</span>
            {filePart.multiSelectMode && (
              <Button
                variant="ghost"
                size="xs"
                onClick={() => on && selectAllFiles()}
                title="Select all files in this folder"
                className={tbBtn}
                tabIndex={tab}
              >
                All
              </Button>
            )}

            <ToolbarDivider />

            {/*
              Root: orphans have no path — only Move to / Archive globally / Delete globally.
              Each is a single action → plain buttons (no empty dropdowns).
            */}
            {filePart.isRootView ? (
              <>
                <Button
                  variant="ghost"
                  size="xs"
                  onClick={() => {
                    if (!on) return
                    setOpenMenu(null)
                    setMoveDialogOpen(true)
                  }}
                  title="Move selected files to a folder"
                  className={tbBtn}
                  tabIndex={tab}
                >
                  <MoveRight />
                  Move
                </Button>
                {filePart.archive.showExcludeSearch && (
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={() => on && pickMenuAction("excludeSearch")}
                    title="Exclude selected files from search everywhere"
                    className={tbBtn}
                    tabIndex={tab}
                  >
                    <SearchX />
                    Archive
                  </Button>
                )}
                {filePart.archive.showUnarchive && (
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={() => on && pickMenuAction("unarchive")}
                    title="Restore selected files"
                    className={tbBtn}
                    tabIndex={tab}
                  >
                    <ArchiveRestore />
                    Restore
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="xs"
                  onClick={() => on && pickMenuAction("delete")}
                  title="Permanently delete selected files"
                  className={cn(tbBtn, "is-danger")}
                  tabIndex={tab}
                >
                  <Trash2 />
                  Delete
                </Button>
              </>
            ) : (
              <>
                <div className="relative">
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={() => on && toggleMenu("move")}
                    className={cn(
                      tbBtn,
                      openMenu === "move" && opts.menus && "is-on"
                    )}
                    title="Move or mirror to another folder"
                    tabIndex={tab}
                  >
                    <MoveRight />
                    Move
                    <ChevronDown className="size-3 opacity-60" />
                  </Button>
                  {opts.menus && (
                    <SoftMenu
                      open={openMenu === "move"}
                      className={cn(tbMenuPanel, "min-w-[240px]")}
                    >
                      <MenuItem
                        icon={<MoveRight className="h-3.5 w-3.5" />}
                        title="Move to…"
                        description="Move selected files to the destination."
                        onClick={() => {
                          setOpenMenu(null)
                          setMoveDialogOpen(true)
                        }}
                      />
                      <MenuItem
                        icon={<Link2 className="h-3.5 w-3.5" />}
                        title="Mirror to…"
                        description="Create a mirror of selected files in the destination."
                        onClick={() => {
                          setOpenMenu(null)
                          setCopyDialogOpen(true)
                        }}
                      />
                    </SoftMenu>
                  )}
                </div>

                {filePart.archive.showMenu && (
                  <div className="relative">
                    <Button
                      variant="ghost"
                      size="xs"
                      onClick={() => on && toggleMenu("archive")}
                      className={cn(
                        tbBtn,
                        openMenu === "archive" && opts.menus && "is-on"
                      )}
                      title="Archive options"
                      tabIndex={tab}
                    >
                      <Archive />
                      Archive
                      <ChevronDown className="size-3 opacity-60" />
                    </Button>
                    {opts.menus && (
                      <SoftMenu open={openMenu === "archive"} className={tbMenuPanel}>
                        {filePart.archive.showUnarchive && (
                          <MenuItem
                            icon={<ArchiveRestore className="h-3.5 w-3.5" />}
                            title="Restore"
                            description={
                              filePart.isArchivedView
                                ? "Re-enable search for selected files."
                                : "Restore selected files in this folder."
                            }
                            onClick={() => pickMenuAction("unarchive")}
                          />
                        )}
                        {filePart.archive.showArchiveHere && (
                          <MenuItem
                            icon={<Archive className="h-3.5 w-3.5" />}
                            title="Archive in this folder"
                            description="Grey out selected files in this folder only."
                            onClick={() => pickMenuAction("archiveFolder")}
                          />
                        )}
                        {filePart.archive.showExcludeSearch && (
                          <MenuItem
                            icon={<SearchX className="h-3.5 w-3.5" />}
                            title="Archive globally"
                            description="Exclude selected files from search everywhere."
                            onClick={() => pickMenuAction("excludeSearch")}
                          />
                        )}
                      </SoftMenu>
                    )}
                  </div>
                )}

                <div className="relative">
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={() => on && toggleMenu("delete")}
                    className={cn(
                      tbBtn,
                      "is-danger",
                      openMenu === "delete" && opts.menus && "is-on"
                    )}
                    title="Remove or delete"
                    tabIndex={tab}
                  >
                    <Trash2 />
                    Delete
                    <ChevronDown className="size-3 opacity-60" />
                  </Button>
                  {opts.menus && (
                    <SoftMenu open={openMenu === "delete"} className={tbMenuPanel}>
                      {!filePart.isArchivedView && (
                        <MenuItem
                          icon={<X className="h-3.5 w-3.5" />}
                          title="Remove from this folder"
                          description="Remove selected files from this folder only."
                          onClick={() => pickMenuAction("unlink")}
                        />
                      )}
                      <MenuItem
                        icon={<Trash2 className="h-3.5 w-3.5" />}
                        title="Delete file globally"
                        description="Permanently delete selected files everywhere."
                        destructive
                        onClick={() => pickMenuAction("delete")}
                      />
                    </SoftMenu>
                  )}
                </div>
              </>
            )}

            {filePart.showDefinitive && (
              <>
                <ToolbarDivider />
                <Button
                  variant="ghost"
                  size="xs"
                  className={tbBtn}
                  tabIndex={tab}
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
                  title={
                    filePart.isDefinitive
                      ? "Remove definitive"
                      : "Mark as definitive"
                  }
                >
                  <Star
                    className={cn(
                      filePart.isDefinitive
                        ? "fill-[var(--pm-green)] text-[var(--pm-green)]"
                        : "text-[var(--pm-green)]"
                    )}
                  />
                  Definitive
                </Button>
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
          title={canGoUp ? "Go up one level" : "Already at root"}
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
            <DialogTitle>New Folder</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-1">
            <div className="flex items-center gap-3">
              <div
                key={`${newFolderIconMode}-${newFolderIconKey}-${newFolderIconColor}-${newFolderSymbol}`}
                className="h-10 w-10 rounded-[var(--pm-r-sm)] flex items-center justify-center bg-[var(--pm-green-wash)] shrink-0"
                title="Preview"
              >
                <GroupIconView source={newFolderPreview} className="h-5 w-5" />
              </div>
              <div className="flex-1 min-w-0">
                <label className="pm-field-label">Name</label>
                <Input
                  value={newFolderName}
                  onChange={(e) => setNewFolderName(e.target.value)}
                  placeholder="Folder name"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void handleCreateFolder()
                  }}
                  autoFocus
                  className="h-8"
                />
              </div>
            </div>
            <IconPickerPanel
              iconMode={newFolderIconMode}
              iconKey={newFolderIconKey}
              iconColor={newFolderIconColor}
              symbol={newFolderSymbol}
              onIconMode={setNewFolderIconMode}
              onIconKey={setNewFolderIconKey}
              onIconColor={setNewFolderIconColor}
              onSymbol={setNewFolderSymbol}
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setNewFolderDialog(false)}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => void handleCreateFolder()}
              disabled={
                !newFolderName.trim() ||
                (newFolderIconMode === "emoji" && !newFolderSymbol.trim())
              }
            >
              Create
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={moveDialogOpen} onOpenChange={setMoveDialogOpen}>
        <DialogContent className="pm-dialog max-w-sm">
          <DialogHeader>
            <DialogTitle>Move to… ({selectedIds.length} file(s))</DialogTitle>
          </DialogHeader>
          <ScrollArea className="max-h-[300px]">
            <div className="flex flex-col gap-0.5">
              {availableFolders.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  className="pm-files-menu-opt truncate text-left"
                  style={{ paddingLeft: `${8 + f.depth * 12}px` }}
                  onClick={async () => {
                    await moveFilesToFolder(collectionId, selectedIds, f.id)
                    setMoveDialogOpen(false)
                  }}
                >
                  {f.name}
                </button>
              ))}
            </div>
          </ScrollArea>
        </DialogContent>
      </Dialog>

      <Dialog open={copyDialogOpen} onOpenChange={setCopyDialogOpen}>
        <DialogContent className="pm-dialog max-w-sm">
          <DialogHeader>
            <DialogTitle>Mirror to… ({selectedIds.length} file(s))</DialogTitle>
          </DialogHeader>
          <ScrollArea className="max-h-[300px]">
            <div className="flex flex-col gap-0.5">
              {availableFolders.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  className="pm-files-menu-opt truncate text-left"
                  style={{ paddingLeft: `${8 + f.depth * 12}px` }}
                  onClick={async () => {
                    await copyFilesToFolder(collectionId, selectedIds, f.id)
                    setCopyDialogOpen(false)
                  }}
                >
                  {f.name}
                </button>
              ))}
            </div>
          </ScrollArea>
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!confirmAction}
        onOpenChange={(v) => !v && setConfirmAction(null)}
      >
        <DialogContent className="pm-dialog max-w-sm">
          <DialogHeader>
            <DialogTitle>
              {confirmAction === "delete" && "Delete file globally?"}
              {confirmAction === "deleteFolder" && "Delete folder(s)?"}
              {confirmAction === "archiveFolder" && "Archive in this folder?"}
              {confirmAction === "excludeSearch" && "Archive globally?"}
              {confirmAction === "unarchive" && "Restore files?"}
              {confirmAction === "unlink" && "Remove from this folder?"}
            </DialogTitle>
          </DialogHeader>
          <p className="pm-dialog-body">
            {confirmAction === "delete" &&
              "Permanently delete selected files everywhere. This cannot be undone."}
            {confirmAction === "deleteFolder" &&
              "Delete selected folders. Files inside stay in storage."}
            {confirmAction === "archiveFolder" &&
              "Grey out selected files in this folder only."}
            {confirmAction === "excludeSearch" &&
              "Exclude selected files from search everywhere."}
            {confirmAction === "unarchive" &&
              (isArchivedView
                ? "Re-enable search for selected files."
                : "Restore selected files in this folder.")}
            {confirmAction === "unlink" &&
              "Remove selected files from this folder only."}
          </p>
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setConfirmAction(null)}
            >
              Cancel
            </Button>
            <Button
              variant={
                confirmAction === "delete" || confirmAction === "deleteFolder"
                  ? "destructive"
                  : "default"
              }
              size="sm"
              className={
                confirmAction === "delete" || confirmAction === "deleteFolder"
                  ? undefined
                  : ""
              }
              onClick={async () => {
                if (confirmAction === "delete")
                  await permanentlyDeleteFiles(collectionId, selectedIds)
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
                } else if (confirmAction === "unlink")
                  await removeFilesFromCurrentFolder(collectionId, selectedIds)
                setConfirmAction(null)
              }}
            >
              {confirmAction === "delete" || confirmAction === "deleteFolder"
                ? "Delete"
                : "Confirm"}
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

function collectFolders(
  tree: FolderTreeNode[],
  currentFolderId: string | null,
  depth = 0
): { id: string; name: string; depth: number }[] {
  const result: { id: string; name: string; depth: number }[] = []
  for (const n of tree) {
    if (n.folder_id !== currentFolderId && n.name !== "Archived") {
      result.push({ id: n.folder_id, name: n.name, depth })
    }
    if (n.children?.length) {
      result.push(...collectFolders(n.children, currentFolderId, depth + 1))
    }
  }
  return result
}
