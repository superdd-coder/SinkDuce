import { useState, useCallback, useRef, useEffect, useMemo } from "react"
import type { FolderTreeNode, FileSummary, NodeGroup } from "@/types/file-mgmt"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { listGroups } from "@/api/file-mgmt"
import { Loader2, Star, Check, Pencil } from "lucide-react"
import { cn } from "@/lib/utils"
import {
  FileTypeIcon,
  resolveDocKind,
} from "@/components/file-mgmt/file-type-icon"
import { FolderIconView } from "@/components/file-mgmt/timeline-view/group-icons"
import {
  DEFAULT_ICON_COLOR,
  ZE_GREEN,
  GroupIconView,
  IconPickerPanel,
  buildIconPayload,
} from "@/components/file-mgmt/timeline-view/group-icons"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

/** Open delay (ms) — avoid flicker while sweeping the icon grid */
const TIP_OPEN_MS = 450
/** Close delay (ms) — keep open long enough to reach the edit pencil */
const TIP_CLOSE_MS = 200
/** Compact tooltip width */
const TIP_MAX = "max-w-[11rem]"

function suggestUniqueName(name: string, taken: string[]): string {
  const have = new Set(taken.map((n) => n.trim().toLowerCase()).filter(Boolean))
  const base = name.trim() || "unnamed"
  if (!have.has(base.toLowerCase())) return base
  let n = 1
  while (have.has(`${base} (${n})`.toLowerCase())) n += 1
  return `${base} (${n})`
}
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useT } from "@/i18n/use-t"
import { systemFolderDisplayName } from "@/i18n/system-folder"
import {
  getSubfolders,
  sortFolderGridItems,
} from "@/components/file-mgmt/folder-view/sorted-items"
import {
  FileListRow,
  FolderListRow,
} from "@/components/file-mgmt/folder-view/file-list"

export function IconGrid({
  collectionId,
  onOpenFile,
}: {
  collectionId: string
  /** Double-click a file to open detail dialog (Phase 8). */
  onOpenFile?: (fileId: string) => void
}) {
  const t = useT()
  const {
    folderTree,
    currentFolderId,
    currentFolderFiles,
    filesLoading,
    selectedFileIds,
    selectedFolderIds,
    multiSelectMode,
    selectFolder,
    toggleSelection,
    selectSingleFile,
    selectSingleFolder,
    toggleFolderSelection,
    clearSelection,
    clearFolderSelection,
    exitMultiSelectMode,
    uploadFile,
    uploadFolder,
    updateFolderDetails,
    renameFile,
    ingestingFiles,
    folderFileSort,
    folderFileView,
  } = useFileMgmtStore()

  const [dragOver, setDragOver] = useState(false)
  const [groups, setGroups] = useState<NodeGroup[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)

  // Edit dialog (opened from hover-tooltip pencil)
  type EditTarget =
    | { kind: "folder"; folder: FolderTreeNode }
    | { kind: "file"; file: FileSummary }
  const [editTarget, setEditTarget] = useState<EditTarget | null>(null)
  const [editName, setEditName] = useState("")
  const [editIconMode, setEditIconMode] = useState<"lucide" | "emoji">("lucide")
  const [editIconKey, setEditIconKey] = useState("folder")
  const [editIconColor, setEditIconColor] = useState(DEFAULT_ICON_COLOR)
  const [editSymbol, setEditSymbol] = useState("")
  const [editSaving, setEditSaving] = useState(false)
  const [editClash, setEditClash] = useState<string | null>(null)

  const editFileExt = useMemo(() => {
    if (!editTarget || editTarget.kind !== "file") return ""
    const fn = editTarget.file.filename || ""
    const fromMeta = (editTarget.file.original_ext || "").replace(/^\./, "")
    if (fromMeta) {
      const suf = `.${fromMeta}`
      if (fn.toLowerCase().endsWith(suf.toLowerCase())) return fn.slice(-suf.length)
      return suf
    }
    const i = fn.lastIndexOf(".")
    return i > 0 ? fn.slice(i) : ""
  }, [editTarget])

  const openEditFolder = useCallback((folder: FolderTreeNode) => {
    if (folder.is_system) return
    setEditTarget({ kind: "folder", folder })
    setEditClash(null)
    setEditName(folder.name || "")
    if (folder.kind === "plain" || folder.kind === "branch") {
      setEditIconMode("lucide")
      setEditSymbol("")
      setEditIconKey(folder.kind === "plain" ? "folder" : "git-branch")
      setEditIconColor(
        folder.icon_color ||
          (folder.kind === "branch" ? ZE_GREEN : DEFAULT_ICON_COLOR)
      )
    } else if (folder.icon_type === "emoji" && folder.icon_value) {
      setEditIconMode("emoji")
      setEditSymbol(folder.icon_value)
      setEditIconKey("users")
      setEditIconColor(DEFAULT_ICON_COLOR)
    } else {
      setEditIconMode("lucide")
      const key = folder.icon_value || "users"
      setEditIconKey(
        key === "folder" || key === "git-branch" ? "users" : key
      )
      setEditIconColor(folder.icon_color || DEFAULT_ICON_COLOR)
      setEditSymbol("")
    }
  }, [])

  const openEditFile = useCallback((file: FileSummary) => {
    setEditTarget({ kind: "file", file })
    setEditClash(null)
    const fn = file.filename || ""
    const fromMeta = (file.original_ext || "").replace(/^\./, "")
    if (fromMeta) {
      const suf = `.${fromMeta}`
      setEditName(
        fn.toLowerCase().endsWith(suf.toLowerCase())
          ? fn.slice(0, -suf.length)
          : fn
      )
    } else {
      const i = fn.lastIndexOf(".")
      setEditName(i > 0 ? fn.slice(0, i) : fn)
    }
  }, [])

  const editFolderKind =
    editTarget?.kind === "folder" ? editTarget.folder.kind : null
  const editFolderPreview = useMemo(() => {
    if (editFolderKind === "plain") {
      return {
        name: editName,
        icon_type: "lucide" as const,
        icon_value: "folder",
        icon_color: editIconColor,
      }
    }
    if (editFolderKind === "branch") {
      return {
        name: editName,
        icon_type: "lucide" as const,
        icon_value: "git-branch",
        icon_color: editIconColor,
      }
    }
    return editIconMode === "emoji" && editSymbol
      ? {
          name: editName,
          icon_type: "emoji" as const,
          icon_value: editSymbol,
        }
      : {
          name: editName,
          icon_type: "lucide" as const,
          icon_value: editIconKey,
          icon_color: editIconColor,
        }
  }, [
    editFolderKind,
    editIconMode,
    editName,
    editSymbol,
    editIconKey,
    editIconColor,
  ])

  const handleSaveEdit = async () => {
    const name = editName.trim()
    if (!name || !editTarget) return
    setEditSaving(true)
    setEditClash(null)
    try {
      if (editTarget.kind === "folder") {
        if (
          editTarget.folder.kind !== "plain" &&
          editTarget.folder.kind !== "branch" &&
          editIconMode === "emoji" &&
          !editSymbol.trim()
        )
          return
        const siblings = getSubfolders(folderTree, currentFolderId)
        const taken = siblings
          .filter((f) => f.folder_id !== editTarget.folder.folder_id)
          .map((f) => f.name || "")
        if (taken.some((n) => n.trim().toLowerCase() === name.toLowerCase())) {
          const suggested = suggestUniqueName(name, taken)
          setEditClash(t("fileMgmt.folderExists", { name }))
          setEditName(suggested)
          return
        }
        const lockedKey =
          editTarget.folder.kind === "plain"
            ? "folder"
            : editTarget.folder.kind === "branch"
              ? "git-branch"
              : null
        const icon = buildIconPayload({
          iconMode: lockedKey ? "lucide" : editIconMode,
          iconKey: lockedKey ?? editIconKey,
          iconColor: editIconColor,
          symbol: lockedKey ? "" : editSymbol,
        })
        const clash = await updateFolderDetails(
          collectionId,
          editTarget.folder.folder_id,
          editTarget.folder.version ?? 1,
          {
            name,
            icon_type: icon.icon_type,
            icon_value: icon.icon_value,
            icon_color: icon.icon_color,
          }
        )
        if (clash) {
          setEditClash(clash.message)
          setEditName(clash.suggested_name)
          return
        }
      } else {
        let stem = name
        if (editFileExt) {
          const low = editFileExt.toLowerCase()
          if (stem.toLowerCase().endsWith(low)) {
            stem = stem.slice(0, -editFileExt.length)
          }
        }
        stem = stem.replace(/[/\\]/g, "").trim() || "unnamed"
        const finalName = editFileExt ? `${stem}${editFileExt}` : stem
        const takenFiles = currentFolderFiles
          .filter((f) => f.file_id !== editTarget.file.file_id)
          .map((f) => (f.display_name || f.filename || "").trim())
        if (
          takenFiles.some((n) => n.toLowerCase() === finalName.toLowerCase())
        ) {
          const suggested = suggestUniqueName(finalName, takenFiles)
          setEditClash(t("fileMgmt.fileExists", { name: finalName }))
          if (
            editFileExt &&
            suggested.toLowerCase().endsWith(editFileExt.toLowerCase())
          ) {
            setEditName(suggested.slice(0, -editFileExt.length))
          } else {
            setEditName(suggested)
          }
          return
        }
        const clash = await renameFile(
          collectionId,
          editTarget.file.file_id,
          finalName,
          editTarget.file.version
        )
        if (clash) {
          setEditClash(clash.message)
          const sug = clash.suggested_name
          if (editFileExt && sug.toLowerCase().endsWith(editFileExt.toLowerCase())) {
            setEditName(sug.slice(0, -editFileExt.length))
          } else {
            setEditName(sug)
          }
          return
        }
      }
      setEditTarget(null)
    } finally {
      setEditSaving(false)
    }
  }

  useEffect(() => {
    if (!collectionId) return
    listGroups(collectionId)
      .then(setGroups)
      .catch(() => setGroups([]))
  }, [collectionId, folderTree])

  const groupByFolderId = useMemo(() => {
    const m = new Map<string, NodeGroup>()
    for (const g of groups) {
      if (g.folder_id) m.set(g.folder_id, g)
    }
    return m
  }, [groups])

  const sortedItems = useMemo(() => {
    const subfolders = getSubfolders(folderTree, currentFolderId)
    return sortFolderGridItems(subfolders, currentFolderFiles, folderFileSort)
  }, [folderTree, currentFolderId, currentFolderFiles, folderFileSort])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(true)
  }, [])

  const handleDragLeave = useCallback(() => setDragOver(false), [])

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragOver(false)
      if (currentFolderId === "__archived__") return

      const items = e.dataTransfer.items
      const fallbackFiles = Array.from(e.dataTransfer.files)

      // Synchronously collect entries (must be done before any await)
      const entries: FileSystemEntry[] = []
      if (
        items &&
        items.length > 0 &&
        typeof items[0].webkitGetAsEntry === "function"
      ) {
        for (let i = 0; i < items.length; i++) {
          const entry = items[i].webkitGetAsEntry()
          if (entry) entries.push(entry)
        }
      }

      if (entries.length > 0) {
        ;(async () => {
          const looseFiles: File[] = []
          const structuredFiles: File[] = []
          let hasDirectory = false

          for (const entry of entries) {
            if (entry.isDirectory) {
              hasDirectory = true
              // Prefix with top-level dir name so structure is preserved at root
              const dirFiles = await traverseDirectory(
                entry as FileSystemDirectoryEntry,
                entry.name
              )
              structuredFiles.push(...dirFiles)
            } else if (entry.isFile) {
              const file = await entryToFile(entry as FileSystemFileEntry)
              if (file) looseFiles.push(file)
            }
          }

          // Folder(s) dropped → upload-folder API (creates subfolders)
          if (hasDirectory && structuredFiles.length > 0) {
            await uploadFolder(collectionId, structuredFiles)
          }
          // Only loose files → single-file upload (root = orphan ok)
          for (const file of looseFiles) {
            await uploadFile(collectionId, file)
          }

          if (
            !hasDirectory &&
            looseFiles.length === 0 &&
            fallbackFiles.length > 0
          ) {
            for (const file of fallbackFiles) {
              await uploadFile(collectionId, file)
            }
          }
        })()
      } else if (fallbackFiles.length > 0) {
        for (const file of fallbackFiles) {
          uploadFile(collectionId, file)
        }
      }
    },
    [collectionId, currentFolderId, uploadFile, uploadFolder]
  )

  return (
    <div
      className={cn(
        "h-full min-h-0 flex flex-col transition-colors duration-200",
        dragOver && "bg-[var(--pm-green-wash)] ring-2 ring-[var(--pm-green-soft)] ring-inset rounded-[var(--pm-r-sm)]"
      )}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onMouseDown={(e) => {
        // Click on empty area (not on a file/folder button) → exit select mode / clear selection
        const target = e.target as HTMLElement
        if (target.tagName !== "BUTTON" && !target.closest("button")) {
          if (multiSelectMode) {
            exitMultiSelectMode()
          } else {
            clearSelection()
            clearFolderSelection()
          }
        }
      }}
      data-multi-select={multiSelectMode ? "true" : undefined}
    >
      {/* Hidden file inputs */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files) {
            for (const f of Array.from(e.target.files)) uploadFile(collectionId, f)
            e.target.value = ""
          }
        }}
      />
      <input
        ref={folderInputRef}
        type="file"
        // @ts-expect-error webkitdirectory is not in the type defs
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

      <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden">
        {filesLoading && sortedItems.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <Loader2 className="h-5 w-5 animate-spin text-[var(--pm-faint)]" />
          </div>
        ) : (
          <TooltipProvider delay={TIP_OPEN_MS} closeDelay={TIP_CLOSE_MS}>
            <div
              className={
                folderFileView === "list" ? "pm-files-list" : "pm-files-grid"
              }
            >
              {sortedItems.map((item) =>
                item.kind === "folder" ? (
                  folderFileView === "list" ? (
                    <FolderListRow
                      key={item.folder.folder_id}
                      folder={item.folder}
                      selected={selectedFolderIds.has(item.folder.folder_id)}
                      multiSelectMode={multiSelectMode}
                      sortMode={folderFileSort}
                      boundGroup={
                        groupByFolderId.get(item.folder.folder_id) ?? null
                      }
                      onOpen={() =>
                        selectFolder(collectionId, item.folder.folder_id)
                      }
                      onSelect={() => {
                        if (multiSelectMode)
                          toggleFolderSelection(item.folder.folder_id)
                        else selectSingleFolder(item.folder.folder_id)
                      }}
                      onEdit={
                        item.folder.is_system
                          ? undefined
                          : () => openEditFolder(item.folder)
                      }
                    />
                  ) : (
                  <FolderIconItem
                    key={item.folder.folder_id}
                    folder={item.folder}
                    selected={selectedFolderIds.has(item.folder.folder_id)}
                    multiSelectMode={multiSelectMode}
                    onOpen={() =>
                      selectFolder(collectionId, item.folder.folder_id)
                    }
                    onSelect={() => {
                      if (multiSelectMode)
                        toggleFolderSelection(item.folder.folder_id)
                      else selectSingleFolder(item.folder.folder_id)
                    }}
                    boundGroup={
                      groupByFolderId.get(item.folder.folder_id) ?? null
                    }
                    onEdit={
                      item.folder.is_system
                        ? undefined
                        : () => openEditFolder(item.folder)
                    }
                  />
                  )
                ) : folderFileView === "list" ? (
                  <FileListRow
                    key={item.file.file_id}
                    file={item.file}
                    selected={selectedFileIds.has(item.file.file_id)}
                    multiSelectMode={multiSelectMode}
                    ingesting={ingestingFiles[item.file.file_id] ?? null}
                    sortMode={folderFileSort}
                    onSelect={() => {
                      if (ingestingFiles[item.file.file_id]) return
                      if (multiSelectMode) toggleSelection(item.file.file_id)
                      else selectSingleFile(item.file.file_id)
                    }}
                    onOpen={
                      multiSelectMode || ingestingFiles[item.file.file_id]
                        ? undefined
                        : () => onOpenFile?.(item.file.file_id)
                    }
                    onEdit={
                      ingestingFiles[item.file.file_id]
                        ? undefined
                        : () => openEditFile(item.file)
                    }
                  />
                ) : (
                  <FileIconItem
                    key={item.file.file_id}
                    file={item.file}
                    selected={selectedFileIds.has(item.file.file_id)}
                    multiSelectMode={multiSelectMode}
                    ingesting={ingestingFiles[item.file.file_id] ?? null}
                    onSelect={() => {
                      if (ingestingFiles[item.file.file_id]) return
                      if (multiSelectMode) toggleSelection(item.file.file_id)
                      else selectSingleFile(item.file.file_id)
                    }}
                    onOpen={
                      multiSelectMode || ingestingFiles[item.file.file_id]
                        ? undefined
                        : () => onOpenFile?.(item.file.file_id)
                    }
                    onEdit={
                      ingestingFiles[item.file.file_id]
                        ? undefined
                        : () => openEditFile(item.file)
                    }
                  />
                )
              )}
              {sortedItems.length === 0 && (
                <div className="pm-files-empty">
                  {t("fileMgmt.emptyFolderHint")}
                </div>
              )}
            </div>
          </TooltipProvider>
        )}
      </div>

      <Dialog
        open={!!editTarget}
        onOpenChange={(open) => {
          if (!open) {
            setEditTarget(null)
            setEditSaving(false)
          }
        }}
      >
        <DialogContent className="pm-dialog max-w-md">
          <DialogHeader>
            <DialogTitle>
              {editTarget?.kind === "folder"
                ? t("fileMgmt.editFolder")
                : t("fileMgmt.renameFile")}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-1">
            {editTarget?.kind === "folder" ? (
              <>
                <div className="flex items-center gap-3">
                  <div
                    key={`${editIconMode}-${editIconKey}-${editIconColor}-${editSymbol}`}
                    className="h-10 w-10 rounded-[var(--pm-r-sm)] flex items-center justify-center bg-[var(--pm-green-wash)] shrink-0"
                  >
                    <GroupIconView
                      source={editFolderPreview}
                      className="h-5 w-5"
                    />
                  </div>
                  <div className="flex-1 min-w-0">
                    <label className="pm-field-label">{t("common.name")}</label>
                    <Input
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      placeholder={t("fileMgmt.folderName")}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") void handleSaveEdit()
                      }}
                      autoFocus
                      className="h-8"
                    />
                  </div>
                </div>
                <IconPickerPanel
                  iconMode={
                    editTarget.folder.kind === "plain" ||
                    editTarget.folder.kind === "branch"
                      ? "lucide"
                      : editIconMode
                  }
                  iconKey={
                    editTarget.folder.kind === "plain"
                      ? "folder"
                      : editTarget.folder.kind === "branch"
                        ? "git-branch"
                        : editIconKey
                  }
                  iconColor={editIconColor}
                  symbol={editSymbol}
                  onIconMode={setEditIconMode}
                  onIconKey={setEditIconKey}
                  onIconColor={setEditIconColor}
                  onSymbol={setEditSymbol}
                  variant={
                    editTarget.folder.kind === "plain" ||
                    editTarget.folder.kind === "branch"
                      ? "plain"
                      : editTarget.folder.kind === "user_group"
                        ? "group"
                        : "full"
                  }
                />
              </>
            ) : (
              <div>
                <label className="pm-field-label">{t("fileMgmt.fileName")}</label>
                <div className="flex items-center gap-1.5 min-w-0">
                  <Input
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    placeholder={t("fileMgmt.filename")}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void handleSaveEdit()
                    }}
                    autoFocus
                    className="h-8 flex-1 min-w-0 font-mono"
                  />
                  {editFileExt && (
                    <span
                      className="shrink-0 pm-meta font-mono bg-[var(--pm-green-wash)] rounded-[var(--pm-r-sm)] px-2 h-8 inline-flex items-center"
                      title={t("fileMgmt.extCannotChange")}
                    >
                      {editFileExt}
                    </span>
                  )}
                </div>
                <p className="pm-meta mt-1.5">
                  {t("fileMgmt.extensionFixed")}
                </p>
              </div>
            )}
            {editClash ? (
              <p className="text-[13px] text-[var(--pm-danger,#b42318)] leading-snug">
                {editClash} {t("fileMgmt.clashSuggested")}
              </p>
            ) : null}
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="ghost"
              size="xs"
              onClick={() => setEditTarget(null)}
              disabled={editSaving}
            >
              {t("common.cancel")}
            </Button>
            <Button
              size="xs"
              onClick={() => void handleSaveEdit()}
              disabled={
                editSaving ||
                !editName.trim() ||
                (editTarget?.kind === "folder" &&
                  editTarget.folder.kind !== "plain" &&
                  editTarget.folder.kind !== "branch" &&
                  editIconMode === "emoji" &&
                  !editSymbol.trim())
              }
            >
              {editSaving ? t("common.saving") : t("common.save")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function FolderIconItem({
  folder,
  selected,
  multiSelectMode,
  onOpen,
  onSelect,
  boundGroup,
  onEdit,
}: {
  folder: FolderTreeNode
  selected: boolean
  multiSelectMode: boolean
  /** Double-click: enter folder */
  onOpen: () => void
  /** Single-click: select (or toggle in multi-select) */
  onSelect: () => void
  boundGroup?: NodeGroup | null
  onEdit?: () => void
}) {
  const t = useT()
  const fullName = systemFolderDisplayName(folder.name || "", t) || t("common.untitled")
  // Badge = direct files + direct subfolders (not recursive)
  const itemCount =
    (folder.file_count || 0) + (folder.children?.length || 0)
  return (
    <Tooltip>
      <TooltipTrigger
        delay={TIP_OPEN_MS}
        closeDelay={TIP_CLOSE_MS}
        render={
          <button
            type="button"
            className={cn(
              "pm-files-item group",
              selected && "is-selected",
              folder.archived && "is-archived"
            )}
            onClick={onSelect}
            onDoubleClick={(e) => {
              e.preventDefault()
              onOpen()
            }}
          >
            <div className="relative">
              <span className="inline-flex h-10 w-10 items-center justify-center">
                <FolderIconView
                  folder={folder}
                  boundGroup={boundGroup}
                  className="h-10 w-10"
                />
              </span>
              {itemCount > 0 && (
                <span
                  className="pm-files-item-badge"
                  title={t("fileMgmt.fileAndFolderCounts", {
                    files: folder.file_count || 0,
                    folders: folder.children?.length || 0,
                  })}
                >
                  {itemCount}
                </span>
              )}
              {selected && multiSelectMode && (
                <div className="pm-files-item-check">
                  <Check className="h-2.5 w-2.5" />
                </div>
              )}
            </div>
            <span className="pm-files-item-name">{fullName}</span>
          </button>
        }
      />
      <TooltipContent
        side="bottom"
        sideOffset={6}
        className={cn(TIP_MAX, "p-0 overflow-hidden")}
      >
        <div className={cn("flex items-start gap-1 px-2 py-1.5", TIP_MAX)}>
          <span
            className="flex-1 min-w-0 pm-meta leading-snug line-clamp-2 break-all"
            style={{ color: "inherit" }}
            title={fullName}
          >
            {fullName}
          </span>
          {onEdit && (
            <button
              type="button"
              className="shrink-0 p-0.5 rounded hover:bg-background/20 text-background/90 hover:text-background transition-colors"
              title={t("common.edit")}
              onClick={(e) => {
                e.preventDefault()
                e.stopPropagation()
                onEdit()
              }}
              onPointerDown={(e) => e.stopPropagation()}
            >
              <Pencil className="h-3 w-3" />
            </button>
          )}
        </div>
      </TooltipContent>
    </Tooltip>
  )
}

function FileIconItem({
  file,
  selected,
  multiSelectMode,
  ingesting,
  onSelect,
  onOpen,
  onEdit,
}: {
  file: FileSummary
  selected: boolean
  multiSelectMode: boolean
  /** Async upload / version ingest in progress */
  ingesting?: { taskId: string; progress: number; message: string } | null
  onSelect: () => void
  /** Double-click: open file detail (disabled in multi-select). */
  onOpen?: () => void
  onEdit?: () => void
}) {
  const t = useT()
  const ext = file.original_ext || ""
  // Unified: file-level or path-level archive both look "archived"
  const isArchived = file.is_greyed || file.archived
  const isIngesting = !!ingesting
  const fullName = file.display_name || file.filename || t("common.untitled")
  const progressPct = Math.max(
    0,
    Math.min(100, Math.round(ingesting?.progress ?? 0))
  )

  return (
    <Tooltip>
      <TooltipTrigger
        delay={TIP_OPEN_MS}
        closeDelay={TIP_CLOSE_MS}
        render={
          <button
            type="button"
            className={cn(
              "pm-files-item group",
              selected && !isIngesting && "is-selected",
              isArchived && "is-archived",
              isIngesting && "is-busy"
            )}
            onClick={onSelect}
            onDoubleClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
              if (isIngesting) return
              if (!onOpen) return
              onOpen()
            }}
          >
            <div className="relative overflow-visible">
              <span
                className={cn(
                  "inline-flex h-10 w-10 items-center justify-center overflow-visible",
                  isIngesting && "opacity-70"
                )}
              >
                <FileTypeIcon
                  source={{
                    filename: file.filename,
                    original_ext: file.original_ext,
                    unsupported: file.unsupported,
                    source: file.source,
                    kind: resolveDocKind(file),
                  }}
                  className="h-10 w-10"
                />
              </span>
              {isIngesting && (
                <div
                  className="absolute inset-0 flex items-center justify-center rounded-md bg-[var(--pm-float)]/55 pointer-events-none"
                  title={
                    ingesting?.message
                      ? t("fileMgmt.openWhenDone", { message: ingesting.message })
                      : t("fileMgmt.ingestingOpenWhenDone")
                  }
                >
                  <Loader2 className="h-4 w-4 animate-spin text-[var(--pm-green)]" />
                </div>
              )}
              {file.is_definitive && !isIngesting && (
                <Star className="absolute -top-0.5 -right-0.5 h-3.5 w-3.5 text-[var(--pm-green)] fill-[var(--pm-green)]" />
              )}
              {selected && multiSelectMode && (
                <div className="pm-files-item-check">
                  <Check className="h-2.5 w-2.5" />
                </div>
              )}
            </div>
            <span className="pm-files-item-name">{fullName}</span>
            {isIngesting ? (
              <span className="pm-files-item-meta is-busy">
                {progressPct > 0 ? `${progressPct}%` : t("fileMgmt.updating")}
              </span>
            ) : isArchived ? (
              <span className="pm-files-item-meta">{t("fileMgmt.archived")}</span>
            ) : ext ? (
              <span className="pm-files-item-meta">{ext}</span>
            ) : null}
          </button>
        }
      />
      <TooltipContent
        side="bottom"
        sideOffset={6}
        className={cn(TIP_MAX, "p-0 overflow-hidden")}
      >
        <div className={cn("flex items-start gap-1 px-2 py-1.5", TIP_MAX)}>
          <div className="flex-1 min-w-0">
            <span
              className="block pm-meta leading-snug line-clamp-2 break-all"
              style={{ color: "inherit" }}
              title={fullName}
            >
              {fullName}
            </span>
            {isIngesting && (
              <span className="block pm-meta mt-0.5 line-clamp-2 opacity-90">
                {ingesting?.message || t("fileMgmt.ingestingEllipsis")}
                {progressPct > 0 ? ` · ${progressPct}%` : ""}
                {" · "}
                {t("fileMgmt.cannotOpenUntilDone")}
              </span>
            )}
          </div>
          {onEdit && !isIngesting && (
            <button
              type="button"
              className="shrink-0 p-0.5 rounded hover:bg-background/20 text-background/90 hover:text-background transition-colors"
              title={t("common.rename")}
              onClick={(e) => {
                e.preventDefault()
                e.stopPropagation()
                onEdit()
              }}
              onPointerDown={(e) => e.stopPropagation()}
            >
              <Pencil className="h-3 w-3" />
            </button>
          )}
        </div>
      </TooltipContent>
    </Tooltip>
  )
}

// Helper: traverse a FileSystemDirectoryEntry and collect all files with relative paths
async function traverseDirectory(
  dirEntry: FileSystemDirectoryEntry,
  pathPrefix = ""
): Promise<File[]> {
  const reader = dirEntry.createReader()
  const files: File[] = []
  // readEntries may not return all entries in one call — read until empty
  const readBatch = (): Promise<FileSystemEntry[]> => {
    return new Promise((resolve, reject) => {
      reader.readEntries(resolve, reject)
    })
  }
  let batch: FileSystemEntry[]
  do {
    batch = await readBatch()
    for (const entry of batch) {
      const childPath = pathPrefix
        ? `${pathPrefix}/${entry.name}`
        : entry.name
      if (entry.isDirectory) {
        const subFiles = await traverseDirectory(
          entry as FileSystemDirectoryEntry,
          childPath
        )
        files.push(...subFiles)
      } else if (entry.isFile) {
        const file = await entryToFile(entry as FileSystemFileEntry)
        if (file) {
          // Encode relative path as filename so upload-folder can rebuild tree
          files.push(new File([file], childPath, { type: file.type }))
        }
      }
    }
  } while (batch.length > 0)
  return files
}

// Helper: convert a FileSystemFileEntry to a File
function entryToFile(fileEntry: FileSystemFileEntry): Promise<File | null> {
  return new Promise((resolve) => {
    fileEntry.file(resolve, () => resolve(null))
  })
}




