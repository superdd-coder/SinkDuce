import { useState, useRef } from "react"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { Button } from "@/components/ui/button"
import {
  FolderPlus,
  Upload,
  FolderInput,
  MoveRight,
  Link2,
  Archive,
  ArchiveRestore,
  Trash2,
  X,
  Star,
} from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { FolderTreeNode } from "@/types/file-mgmt"
import { cn } from "@/lib/utils"

export function Toolbar({ collectionId }: { collectionId: string }) {
  const {
    currentFolderId,
    selectedFileIds,
    selectedFolderIds,
    currentFolderFiles,
    folderTree,
    createSubFolder,
    uploadFile,
    uploadFolder,
    moveFilesToFolder,
    copyFilesToFolder,
    removeFilesFromCurrentFolder,
    archiveFiles,
    unarchiveFiles,
    permanentlyDeleteFiles,
    toggleDefinitive,
    removeFolder,
    clearFolderSelection,
  } = useFileMgmtStore()

  const [newFolderDialog, setNewFolderDialog] = useState(false)
  const [newFolderName, setNewFolderName] = useState("")
  const [moveDialogOpen, setMoveDialogOpen] = useState(false)
  const [copyDialogOpen, setCopyDialogOpen] = useState(false)
  const [confirmAction, setConfirmAction] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)

  const isArchivedView = currentFolderId === "__archived__"
  const hasFileSelection = selectedFileIds.size > 0
  const hasFolderSelection = selectedFolderIds.size > 0
  const selectedFiles = currentFolderFiles.filter((f) => selectedFileIds.has(f.file_id))
  const selectedIds = Array.from(selectedFileIds)
  const selectedFolderIdsArr = Array.from(selectedFolderIds)
  const hasSystemFolder = selectedFolderIdsArr.some(fid => {
    const f = findFolderInTree(folderTree, fid)
    return f?.is_system ?? false
  })

  const handleCreateFolder = async () => {
    if (!newFolderName.trim()) return
    await createSubFolder(collectionId, newFolderName.trim())
    setNewFolderName("")
    setNewFolderDialog(false)
  }

  const availableFolders = collectFolders(folderTree, currentFolderId)

  return (
    <div className="flex items-center gap-1 py-1.5 px-1 flex-wrap">
      {!isArchivedView && (
        <>
          <Button variant="ghost" size="xs" onClick={() => setNewFolderDialog(true)} title="New folder">
            <FolderPlus className="h-3.5 w-3.5" />
            New Folder
          </Button>
          <Button variant="ghost" size="xs" onClick={() => fileInputRef.current?.click()} title="Upload file">
            <Upload className="h-3.5 w-3.5" />
            Upload
          </Button>
          <Button variant="ghost" size="xs" onClick={() => folderInputRef.current?.click()} title="Upload folder">
            <FolderInput className="h-3.5 w-3.5" />
            Upload Folder
          </Button>
        </>
      )}

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

      {hasFolderSelection && (
        <>
          <div className="w-px h-4 bg-border/40 mx-0.5" />
          <span className="text-[10px] text-muted-foreground px-1">{selectedFolderIdsArr.length} folder(s)</span>
          {!hasSystemFolder && (
            <Button
              variant="ghost"
              size="xs"
              className="text-destructive hover:bg-destructive/10"
              onClick={() => setConfirmAction("deleteFolder")}
              title="Delete folder(s)"
            >
              <Trash2 className="h-3 w-3" />
              Delete Folder
            </Button>
          )}
        </>
      )}

      {hasFileSelection && (
        <>
          <div className="w-px h-4 bg-border/40 mx-0.5" />
          <span className="text-[10px] text-muted-foreground px-1">{selectedIds.length} selected</span>
          <Button variant="ghost" size="xs" onClick={() => setMoveDialogOpen(true)} title="Move to folder (removes from current)">
            <MoveRight className="h-3 w-3" />
            Move
          </Button>
          <Button variant="ghost" size="xs" onClick={() => setCopyDialogOpen(true)} title="Link to another folder (keeps in current)">
            <Link2 className="h-3 w-3" />
            Link
          </Button>
          {!isArchivedView && (
            <Button variant="ghost" size="xs" onClick={() => setConfirmAction("unlink")} title="Unlink from this folder">
              <X className="h-3 w-3" />
              Unlink
            </Button>
          )}
          {selectedFiles.some((f) => f.archived || f.is_greyed) ? (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => setConfirmAction("unarchive")}
              title="Unarchive (file or path-level)"
            >
              <ArchiveRestore className="h-3 w-3" />
              Unarchive
            </Button>
          ) : (
            <Button variant="ghost" size="xs" onClick={() => setConfirmAction("archive")} title="Archive">
              <Archive className="h-3 w-3" />
              Archive
            </Button>
          )}
          {selectedFiles.length === 1 && !selectedFiles[0].archived && !selectedFiles[0].is_greyed && (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => setConfirmAction("definitive")}
              title={selectedFiles[0].is_definitive ? "Remove definitive" : "Mark as definitive"}
            >
              <Star className={cn("h-3 w-3", selectedFiles[0].is_definitive && "fill-amber-400 text-amber-400")} />
              {selectedFiles[0].is_definitive ? "Unset Definitive" : "Set Definitive"}
            </Button>
          )}
          <Button
            variant="ghost"
            size="xs"
            className="text-destructive hover:bg-destructive/10"
            onClick={() => setConfirmAction("delete")}
            title="Permanently delete"
          >
            <Trash2 className="h-3 w-3" />
            Delete
          </Button>
        </>
      )}

      <Dialog open={newFolderDialog} onOpenChange={setNewFolderDialog}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>New Folder</DialogTitle>
          </DialogHeader>
          <Input
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            placeholder="Folder name"
            onKeyDown={(e) => { if (e.key === "Enter") handleCreateFolder() }}
            autoFocus
          />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setNewFolderDialog(false)}>Cancel</Button>
            <Button size="sm" onClick={handleCreateFolder} disabled={!newFolderName.trim()}>Create</Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={moveDialogOpen} onOpenChange={setMoveDialogOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Move {selectedIds.length} file(s) to...</DialogTitle>
          </DialogHeader>
          <ScrollArea className="max-h-[300px]">
            <div className="flex flex-col gap-0.5">
              {availableFolders.map((f) => (
                <button
                  key={f.id}
                  className="text-left text-xs px-2 py-1.5 rounded hover:bg-muted transition-colors truncate"
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
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Link {selectedIds.length} file(s) to...</DialogTitle>
          </DialogHeader>
          <ScrollArea className="max-h-[300px]">
            <div className="flex flex-col gap-0.5">
              {availableFolders.map((f) => (
                <button
                  key={f.id}
                  className="text-left text-xs px-2 py-1.5 rounded hover:bg-muted transition-colors truncate"
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

      <Dialog open={!!confirmAction} onOpenChange={(v) => !v && setConfirmAction(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>
              {confirmAction === "delete" && "Permanently delete files?"}
              {confirmAction === "deleteFolder" && "Delete folder(s)?"}
              {confirmAction === "archive" && "Archive files?"}
              {confirmAction === "unarchive" && "Unarchive files?"}
              {confirmAction === "unlink" && "Unlink from this folder?"}
              {confirmAction === "definitive" && (selectedFiles[0]?.is_definitive ? "Remove definitive status?" : "Mark as definitive?")}
            </DialogTitle>
          </DialogHeader>
          <p className="text-xs text-muted-foreground">
            {confirmAction === "delete" && "This will delete all file data, versions, and Qdrant chunks. This cannot be undone."}
            {confirmAction === "deleteFolder" && "This will delete the folder(s) and all their contents. Files will lose this path but are not deleted from storage."}
            {confirmAction === "archive" && "Files will be archived and excluded from search. They can be restored from the Archived folder."}
            {confirmAction === "unarchive" &&
              "Restore selected files. Path-archived branch files are re-activated in this folder; fully archived files return to normal search."}
            {confirmAction === "unlink" && "Files will be unlinked from this folder (path only). The file itself is not deleted."}
            {confirmAction === "definitive" && "Definitive files are included in the collection summary consolidation."}
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setConfirmAction(null)}>Cancel</Button>
            <Button
              variant={confirmAction === "delete" || confirmAction === "deleteFolder" ? "destructive" : "default"}
              size="sm"
              onClick={async () => {
                if (confirmAction === "delete") await permanentlyDeleteFiles(collectionId, selectedIds)
                else if (confirmAction === "deleteFolder") {
                  for (const fid of selectedFolderIdsArr) await removeFolder(collectionId, fid)
                  clearFolderSelection()
                }
                else if (confirmAction === "archive") await archiveFiles(collectionId, selectedIds, selectedFiles)
                else if (confirmAction === "unarchive") await unarchiveFiles(collectionId, selectedIds, selectedFiles)
                else if (confirmAction === "unlink") await removeFilesFromCurrentFolder(collectionId, selectedIds)
                else if (confirmAction === "definitive") {
                  const f = selectedFiles[0]
                  if (f) await toggleDefinitive(collectionId, f.file_id, !f.is_definitive, f.version)
                }
                setConfirmAction(null)
              }}
            >
              {confirmAction === "delete" || confirmAction === "deleteFolder" ? "Delete" : "Confirm"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function findFolderInTree(tree: FolderTreeNode[], fid: string): FolderTreeNode | null {
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
  result: { id: string; name: string; depth: number }[] = [],
  depth = 0
): { id: string; name: string; depth: number }[] {
  for (const n of tree) {
    if (n.folder_id !== currentFolderId && n.name !== "Archived") {
      result.push({ id: n.folder_id, name: n.name, depth })
      collectFolders(n.children, currentFolderId, result, depth + 1)
    }
  }
  return result
}
