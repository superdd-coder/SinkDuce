import { useState, useCallback, useRef } from "react"
import type { FolderTreeNode, FileSummary } from "@/types/file-mgmt"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { Loader2, FolderIcon, Users, GitBranch, Video, FileText, Archive, FileIcon, FileWarning, Star, Check } from "lucide-react"
import { cn } from "@/lib/utils"

export function IconGrid({ collectionId }: { collectionId: string }) {
  const {
    folderTree,
    currentFolderId,
    currentFolderFiles,
    filesLoading,
    selectedFileIds,
    selectedFolderIds,
    selectFolder,
    toggleSelection,
    toggleFolderSelection,
    clearSelection,
    clearFolderSelection,
    uploadFile,
    uploadFolder,
  } = useFileMgmtStore()

  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)

  // Get subfolders of current folder (or root folders if no current folder)
  const subfolders = getSubfolders(folderTree, currentFolderId)

  // Separate folders by kind for display ordering
  const systemFolders = subfolders.filter((f) => f.kind === "system_group")
  const groupFolders = subfolders.filter((f) => f.kind === "user_group")
  const branchFolders = subfolders.filter((f) => f.kind === "branch")
  const plainFolders = subfolders.filter((f) => f.kind === "plain")

  const orderedFolders = [...systemFolders, ...groupFolders, ...branchFolders, ...plainFolders]

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
      if (items && items.length > 0 && typeof items[0].webkitGetAsEntry === "function") {
        for (let i = 0; i < items.length; i++) {
          const entry = items[i].webkitGetAsEntry()
          if (entry) entries.push(entry)
        }
      }

      if (entries.length > 0) {
        // Process entries asynchronously
        ;(async () => {
          const collected: File[] = []
          for (const entry of entries) {
            if (entry.isDirectory) {
              const dirFiles = await traverseDirectory(entry as FileSystemDirectoryEntry)
              collected.push(...dirFiles)
            } else if (entry.isFile) {
              const file = await entryToFile(entry as FileSystemFileEntry)
              if (file) collected.push(file)
            }
          }
          if (collected.length > 0) {
            uploadFolder(collectionId, collected)
          } else if (fallbackFiles.length > 0) {
            for (const file of fallbackFiles) {
              uploadFile(collectionId, file)
            }
          }
        })()
      } else if (fallbackFiles.length > 0) {
        // Fallback: plain file drop
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
        "h-full min-h-0 flex flex-col transition-colors",
        dragOver && "bg-primary/5 ring-2 ring-primary/20 ring-inset"
      )}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onMouseDown={(e) => {
        // Click on empty area (not on a button) → deselect
        const target = e.target as HTMLElement
        if (target.tagName !== "BUTTON" && !target.closest("button")) {
          clearSelection()
          clearFolderSelection()
        }
      }}
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
        {filesLoading && orderedFolders.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="flex flex-wrap gap-1 p-3">
            {orderedFolders.map((folder) => (
              <FolderIconItem
                key={folder.folder_id}
                folder={folder}
                selected={selectedFolderIds.has(folder.folder_id)}
                onOpen={() => selectFolder(collectionId, folder.folder_id)}
                onToggle={() => toggleFolderSelection(folder.folder_id)}
              />
            ))}
            {currentFolderFiles.map((file) => (
              <FileIconItem
                key={file.file_id}
                file={file}
                selected={selectedFileIds.has(file.file_id)}
                onToggle={() => toggleSelection(file.file_id)}
              />
            ))}
            {orderedFolders.length === 0 && currentFolderFiles.length === 0 && (
              <div className="w-full text-center text-muted-foreground text-sm py-8">
                This folder is empty. Drag files here to upload.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function FolderIconItem({
  folder,
  selected,
  onOpen,
  onToggle,
}: {
  folder: FolderTreeNode
  selected: boolean
  onOpen: () => void
  onToggle: () => void
}) {
  const Icon = getFolderIcon(folder)
  const color = getFolderColor(folder.kind, folder.name)

  return (
    <button
      className={cn(
        "group flex flex-col items-center gap-1 p-2 rounded-lg transition-colors w-[88px] shrink-0",
        selected ? "bg-primary/10 ring-1 ring-primary/30" : "hover:bg-muted/50"
      )}
      onClick={onToggle}
      onDoubleClick={onOpen}
      title={folder.name}
    >
      <div className="relative">
        <Icon className={cn("h-10 w-10", color)} />
        {folder.file_count > 0 && (
          <span className="absolute -bottom-0.5 -right-0.5 text-[9px] font-medium bg-muted text-muted-foreground rounded-full px-1 min-w-[14px] text-center">
            {folder.file_count}
          </span>
        )}
        {selected && (
          <div className="absolute -top-1 -right-1 bg-primary rounded-full p-0.5">
            <Check className="h-2.5 w-2.5 text-primary-foreground" />
          </div>
        )}
      </div>
      <span className="text-[11px] text-center text-muted-foreground truncate w-full leading-tight">
        {folder.name}
      </span>
    </button>
  )
}

function FileIconItem({
  file,
  selected,
  onToggle,
}: {
  file: FileSummary
  selected: boolean
  onToggle: () => void
}) {
  const ext = file.original_ext || ""
  const isGreyed = file.is_greyed || file.archived

  const Icon = file.unsupported ? FileWarning : FileIcon

  return (
    <button
      className={cn(
        "group flex flex-col items-center gap-1 p-2 rounded-lg transition-colors w-[88px] shrink-0",
        selected ? "bg-primary/10 ring-1 ring-primary/30" : "hover:bg-muted/50",
        isGreyed && "opacity-40"
      )}
      onClick={onToggle}
      title={file.filename}
    >
      <div className="relative">
        <Icon className={cn("h-10 w-10", file.unsupported ? "text-amber-500" : "text-muted-foreground")} />
        {file.is_definitive && (
          <Star className="absolute -top-0.5 -right-0.5 h-3.5 w-3.5 text-amber-400 fill-amber-400" />
        )}
        {selected && (
          <div className="absolute -top-1 -right-1 bg-primary rounded-full p-0.5">
            <Check className="h-2.5 w-2.5 text-primary-foreground" />
          </div>
        )}
      </div>
      <span className={cn("text-[11px] text-center truncate w-full leading-tight", isGreyed ? "text-muted-foreground/60" : "text-muted-foreground")}>
        {file.filename}
      </span>
      {file.archived && (
        <span className="text-[9px] text-muted-foreground/50 uppercase tracking-wide">archived</span>
      )}
      {ext && !file.archived && (
        <span className="text-[9px] text-muted-foreground/40 uppercase">{ext}</span>
      )}
    </button>
  )
}

// Helper: traverse a FileSystemDirectoryEntry and collect all files
async function traverseDirectory(dirEntry: FileSystemDirectoryEntry): Promise<File[]> {
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
      if (entry.isDirectory) {
        const subFiles = await traverseDirectory(entry as FileSystemDirectoryEntry)
        files.push(...subFiles)
      } else if (entry.isFile) {
        const file = await entryToFile(entry as FileSystemFileEntry)
        if (file) files.push(file)
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

function getFolderIcon(folder: FolderTreeNode): React.FC<{ className?: string }> {
  if (folder.kind === "system_group") {
    if (folder.name === "Meeting") return Video
    if (folder.name === "Notes") return FileText
    if (folder.name === "Archived") return Archive
    return FolderIcon
  }
  if (folder.kind === "user_group") return Users
  if (folder.kind === "branch") return GitBranch
  return FolderIcon
}

function getFolderColor(kind: string, name: string): string {
  if (kind === "system_group") {
    if (name === "Archived") return "text-muted-foreground/50"
    return "text-blue-400"
  }
  if (kind === "user_group") return "text-purple-400"
  if (kind === "branch") return "text-emerald-400"
  return "text-amber-400"
}
