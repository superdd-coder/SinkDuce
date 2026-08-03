import { useEffect, useState } from "react"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { Breadcrumb } from "./breadcrumb"
import { IconGrid } from "./icon-grid"
import { MessageSidebar } from "./message-sidebar"
import { NameConflictDialog } from "./name-conflict-dialog"
import { FolderUploadConfirmDialog } from "./folder-upload-confirm-dialog"
import { Toolbar } from "./toolbar"
import { FileMgmtDetailDialog } from "@/components/file-mgmt/file-detail"

export function FolderView({ collectionId }: { collectionId: string }) {
  const {
    fetchFolderTree,
    navigateToRoot,
    selectFolder,
    perCollectionFolderCache,
    hydrateFolderFileSort,
  } = useFileMgmtStore()

  /** Phase 8: file detail dialog (local — does not touch folder grid layout). */
  const [detailFileId, setDetailFileId] = useState<string | null>(null)

  // When collectionId changes, reset state and load new tree
  useEffect(() => {
    if (collectionId) {
      setDetailFileId(null)
      // Restore per-collection sort (default: type)
      hydrateFolderFileSort(collectionId)
      const cachedFolder = perCollectionFolderCache[collectionId]
      if (cachedFolder !== undefined) {
        if (cachedFolder === null) {
          navigateToRoot(collectionId)
        } else {
          selectFolder(collectionId, cachedFolder)
        }
      } else {
        navigateToRoot(collectionId)
      }
      fetchFolderTree(collectionId)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collectionId])

  return (
    <div className="flex flex-col h-full min-h-0 gap-0">
      {/* z-20 so toolbar dropdowns paint above the icon grid (grid is later in DOM) */}
      <div className="relative z-20 shrink-0">
        <Toolbar collectionId={collectionId} />
      </div>

      {/*
        overflow-visible so message-panel shadow is not clipped.
        Scroll containment stays on the left grid only (overflow-hidden there).
      */}
      <div className="relative z-0 flex-1 flex min-h-0 gap-1.5 overflow-visible pt-0.5">
        <div className="flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden transition-[flex-basis] duration-300">
          <div className="flex-1 min-h-0 overflow-hidden">
            <IconGrid
              collectionId={collectionId}
              onOpenFile={(fileId) => setDetailFileId(fileId)}
            />
          </div>
          <div className="shrink-0 border-t border-border/30">
            <Breadcrumb collectionId={collectionId} />
          </div>
        </div>
        <MessageSidebar collectionId={collectionId} />
      </div>
      <NameConflictDialog />
      <FolderUploadConfirmDialog />

      <FileMgmtDetailDialog
        collectionId={collectionId}
        fileId={detailFileId}
        open={!!detailFileId}
        onOpenChange={(open) => {
          if (!open) setDetailFileId(null)
        }}
        onDeleted={() => setDetailFileId(null)}
        onNavigateToFolder={(folderId) => {
          void selectFolder(collectionId, folderId)
        }}
      />
    </div>
  )
}
