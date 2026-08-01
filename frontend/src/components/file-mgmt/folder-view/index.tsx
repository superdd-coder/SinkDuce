import { useEffect } from "react"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { Breadcrumb } from "./breadcrumb"
import { IconGrid } from "./icon-grid"
import { MessageSidebar } from "./message-sidebar"
import { NameConflictDialog } from "./name-conflict-dialog"
import { Toolbar } from "./toolbar"

export function FolderView({ collectionId }: { collectionId: string }) {
 const { fetchFolderTree, navigateToRoot, selectFolder, perCollectionFolderCache } =
   useFileMgmtStore()
 

 // When collectionId changes, reset state and load new tree
 useEffect(() => {
   if (collectionId) {
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
      <Toolbar collectionId={collectionId} />

      {/*
        overflow-visible so message-panel shadow is not clipped.
        Scroll containment stays on the left grid only (overflow-hidden there).
      */}
      <div className="flex-1 flex min-h-0 gap-1.5 overflow-visible pt-0.5">
        <div className="flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden transition-[flex-basis] duration-300">
          <div className="flex-1 min-h-0 overflow-hidden">
            <IconGrid collectionId={collectionId} />
          </div>
          <div className="shrink-0 border-t border-border/30">
            <Breadcrumb collectionId={collectionId} />
          </div>
        </div>
        <MessageSidebar collectionId={collectionId} />
      </div>
      <NameConflictDialog />
    </div>
  )
}
