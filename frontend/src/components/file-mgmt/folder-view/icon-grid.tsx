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

export function IconGrid({
  collectionId,
  onOpenFile,
}: {
  collectionId: string
  /** Double-click a file to open detail dialog (Phase 8). */
  onOpenFile?: (fileId: string) => void
}) {
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
    if (folder.icon_type === "emoji" && folder.icon_value) {
      setEditIconMode("emoji")
      setEditSymbol(folder.icon_value)
      setEditIconKey("folder")
      setEditIconColor(DEFAULT_ICON_COLOR)
    } else {
      setEditIconMode("lucide")
      setEditIconKey(folder.icon_value || "folder")
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

  const editFolderPreview = useMemo(
    () =>
      editIconMode === "emoji" && editSymbol
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
          },
    [editIconMode, editName, editSymbol, editIconKey, editIconColor]
  )

  const handleSaveEdit = async () => {
    const name = editName.trim()
    if (!name || !editTarget) return
    setEditSaving(true)
    setEditClash(null)
    try {
      if (editTarget.kind === "folder") {
        if (editIconMode === "emoji" && !editSymbol.trim()) return
        const siblings = getSubfolders(folderTree, currentFolderId)
        const taken = siblings
          .filter((f) => f.folder_id !== editTarget.folder.folder_id)
          .map((f) => f.name || "")
        if (taken.some((n) => n.trim().toLowerCase() === name.toLowerCase())) {
          const suggested = suggestUniqueName(name, taken)
          setEditClash(
            `A folder named '${name}' already exists here.`
          )
          setEditName(suggested)
          return
        }
        const icon = buildIconPayload({
          iconMode: editIconMode,
          iconKey: editIconKey,
          iconColor: editIconColor,
          symbol: editSymbol,
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
          setEditClash(`A file named '${finalName}' already exists here.`)
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

  /**
   * Unified grid items: folders + files sorted by the same mode.
   * - name / type / created / updated all include folders
   * - folder updated time = content_updated_at (max of contained files)
   */
  type GridItem =
    | { kind: "folder"; folder: FolderTreeNode }
    | { kind: "file"; file: FileSummary }

  const sortedItems = useMemo(() => {
    const subfolders = getSubfolders(folderTree, currentFolderId)
    const items: GridItem[] = [
      ...subfolders.map((folder) => ({ kind: "folder" as const, folder })),
      ...currentFolderFiles.map((file) => ({ kind: "file" as const, file })),
    ]

    const collator = new Intl.Collator(undefined, {
      numeric: true,
      sensitivity: "base",
    })
    const nameOf = (it: GridItem) =>
      it.kind === "folder"
        ? (it.folder.name || "").toLowerCase()
        : (it.file.display_name || it.file.filename || "").toLowerCase()
    /**
     * By type order:
     * 0 system folders → 1 branch → 2 group → 3 plain folders → 4+ files by ext
     */
    const typeRank = (it: GridItem): string => {
      if (it.kind === "folder") {
        const k = it.folder.kind
        if (k === "system_group") return "0-system"
        if (k === "branch") return "1-branch"
        if (k === "user_group") return "2-group"
        return "3-plain" // plain and any other folder kinds
      }
      const fromMeta = (it.file.original_ext || "")
        .replace(/^\./, "")
        .toLowerCase()
      if (fromMeta) return `4-${fromMeta}`
      const n = it.file.filename || ""
      const i = n.lastIndexOf(".")
      const ext = i > 0 ? n.slice(i + 1).toLowerCase() : ""
      return ext ? `4-${ext}` : "4-"
    }
    const createdOf = (it: GridItem) =>
      it.kind === "folder"
        ? it.folder.created_at || ""
        : it.file.created_at || ""
    const updatedOf = (it: GridItem) => {
      if (it.kind === "folder") {
        return (
          it.folder.content_updated_at ||
          it.folder.updated_at ||
          it.folder.created_at ||
          ""
        )
      }
      return it.file.updated_at || it.file.created_at || ""
    }
    const cmpName = (a: GridItem, b: GridItem) =>
      collator.compare(nameOf(a), nameOf(b))

    if (folderFileSort === "type") {
      items.sort((a, b) => {
        const t = typeRank(a).localeCompare(typeRank(b))
        if (t !== 0) return t
        return cmpName(a, b)
      })
    } else if (
      folderFileSort === "created_desc" ||
      folderFileSort === "created_asc"
    ) {
      const desc = folderFileSort === "created_desc"
      items.sort((a, b) => {
        const ta = createdOf(a)
        const tb = createdOf(b)
        const t = desc ? tb.localeCompare(ta) : ta.localeCompare(tb)
        if (t !== 0) return t
        return cmpName(a, b)
      })
    } else if (
      folderFileSort === "updated_desc" ||
      folderFileSort === "updated_asc"
    ) {
      const desc = folderFileSort === "updated_desc"
      items.sort((a, b) => {
        const ta = updatedOf(a)
        const tb = updatedOf(b)
        const t = desc ? tb.localeCompare(ta) : ta.localeCompare(tb)
        if (t !== 0) return t
        return cmpName(a, b)
      })
    } else {
      // name
      items.sort(cmpName)
    }
    return items
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
            <div className="pm-files-grid">
              {sortedItems.map((item) =>
                item.kind === "folder" ? (
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
                  This folder is empty. Drag files here to upload.
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
              {editTarget?.kind === "folder" ? "Edit Folder" : "Rename File"}
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
                    <label className="pm-field-label">Name</label>
                    <Input
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      placeholder="Folder name"
                      onKeyDown={(e) => {
                        if (e.key === "Enter") void handleSaveEdit()
                      }}
                      autoFocus
                      className="h-8"
                    />
                  </div>
                </div>
                <IconPickerPanel
                  iconMode={editIconMode}
                  iconKey={editIconKey}
                  iconColor={editIconColor}
                  symbol={editSymbol}
                  onIconMode={setEditIconMode}
                  onIconKey={setEditIconKey}
                  onIconColor={setEditIconColor}
                  onSymbol={setEditSymbol}
                />
              </>
            ) : (
              <div>
                <label className="pm-field-label">File name</label>
                <div className="flex items-center gap-1.5 min-w-0">
                  <Input
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    placeholder="filename"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void handleSaveEdit()
                    }}
                    autoFocus
                    className="h-8 flex-1 min-w-0 font-mono"
                  />
                  {editFileExt && (
                    <span
                      className="shrink-0 pm-meta font-mono bg-[var(--pm-green-wash)] rounded-[var(--pm-r-sm)] px-2 h-8 inline-flex items-center"
                      title="Extension cannot be changed"
                    >
                      {editFileExt}
                    </span>
                  )}
                </div>
                <p className="pm-meta mt-1.5">
                  Extension is fixed and cannot be changed.
                </p>
              </div>
            )}
            {editClash ? (
              <p className="text-[13px] text-[var(--pm-danger,#b42318)] leading-snug">
                {editClash} Suggested name is filled in — Save to apply, or type another.
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
              Cancel
            </Button>
            <Button
              size="xs"
              onClick={() => void handleSaveEdit()}
              disabled={
                editSaving ||
                !editName.trim() ||
                (editTarget?.kind === "folder" &&
                  editIconMode === "emoji" &&
                  !editSymbol.trim())
              }
            >
              {editSaving ? "Saving…" : "Save"}
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
  const fullName = folder.name || "Untitled"
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
              selected && "is-selected"
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
                  title={`${folder.file_count || 0} file(s), ${folder.children?.length || 0} folder(s)`}
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
              title="Edit"
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
  const ext = file.original_ext || ""
  // Unified: file-level or path-level archive both look "archived"
  const isArchived = file.is_greyed || file.archived
  const isIngesting = !!ingesting
  const fullName = file.display_name || file.filename || "Untitled"
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
                      ? `${ingesting.message} — open when done`
                      : "Ingesting… open when done"
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
                {progressPct > 0 ? `${progressPct}%` : "updating…"}
              </span>
            ) : isArchived ? (
              <span className="pm-files-item-meta">archived</span>
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
                {ingesting?.message || "Ingesting…"}
                {progressPct > 0 ? ` · ${progressPct}%` : ""}
                {" · "}
                cannot open until done
              </span>
            )}
          </div>
          {onEdit && !isIngesting && (
            <button
              type="button"
              className="shrink-0 p-0.5 rounded hover:bg-background/20 text-background/90 hover:text-background transition-colors"
              title="Rename"
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

// Helper: get subfolders from tree by parent_folder_id
function getSubfolders(tree: FolderTreeNode[], parentId: string | null): FolderTreeNode[] {
  if (!parentId) {
    return tree
  }
  // Special: Archived virtual view
  if (parentId === "__archived__") return []
  // Search tree
  function search(nodes: FolderTreeNode[]): FolderTreeNode[] | null {
    for (const n of nodes) {
      if (n.folder_id === parentId) return n.children
      const found = search(n.children)
      if (found) return found
    }
    return null
  }
  return search(tree) ?? []
}


