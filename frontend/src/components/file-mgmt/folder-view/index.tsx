import { useEffect } from "react"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { Breadcrumb } from "./breadcrumb"
import { IconGrid } from "./icon-grid"
import { MessageSidebar } from "./message-sidebar"
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
    <div className="flex flex-col h-full min-h-0 gap-1">
      <Toolbar collectionId={collectionId} />
      <Breadcrumb collectionId={collectionId} />

      <div className="flex-1 flex min-h-0 gap-2 overflow-hidden">
        <div className="flex-1 min-w-0 min-h-0 overflow-hidden rounded-lg border border-border/40 bg-background/50">
          <IconGrid collectionId={collectionId} />
        </div>
        <div className="min-h-0">
          <MessageSidebar collectionId={collectionId} />
        </div>
      </div>
    </div>
  )
}
