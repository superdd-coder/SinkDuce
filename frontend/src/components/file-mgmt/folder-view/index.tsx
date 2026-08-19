import { useCallback, useEffect, useState } from "react"
import { useShallow } from "zustand/react/shallow"
import { PanelRight } from "lucide-react"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { Breadcrumb } from "./breadcrumb"
import { IconGrid } from "./icon-grid"
import { MessageSidebar } from "./message-sidebar"
import { NameConflictDialog } from "./name-conflict-dialog"
import { FolderUploadConfirmDialog } from "./folder-upload-confirm-dialog"
import { Toolbar } from "./toolbar"
import { FileMgmtDetailDialog } from "@/components/file-mgmt/file-detail"
import { cn } from "@/lib/utils"

/**
 * Files surface — dual column when Messages open (Overview geometry).
 *
 * Soft rail collapse (ENGINEERING §4.5) — curtain clip:
 * - Right track width animates; panel keeps fixed width + pin-right
 * - overflow:hidden clips without content reflow (no half-size squish)
 * - Reopen pill in the toolbar band, right-aligned
 */
export function FolderView({
  collectionId,
  railCovered = false,
}: {
  collectionId: string
  /** Quick Chat open — fade message stack in place (same as Overview rail). */
  railCovered?: boolean
}) {
  const {
    fetchFolderTree,
    navigateToRoot,
    selectFolder,
    perCollectionFolderCache,
    hydrateFolderFileSort,
    messageSidebarOpen,
    toggleMessageSidebar,
    currentFolderMessages,
  } = useFileMgmtStore(
    useShallow((s) => ({
      fetchFolderTree: s.fetchFolderTree,
      navigateToRoot: s.navigateToRoot,
      selectFolder: s.selectFolder,
      perCollectionFolderCache: s.perCollectionFolderCache,
      hydrateFolderFileSort: s.hydrateFolderFileSort,
      messageSidebarOpen: s.messageSidebarOpen,
      toggleMessageSidebar: s.toggleMessageSidebar,
      currentFolderMessages: s.currentFolderMessages,
    })),
  )

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

  const msgCollapsed = !messageSidebarOpen
  const msgCount = currentFolderMessages.length
  const setMessageSidebarOpen = useFileMgmtStore((s) => s.setMessageSidebarOpen)
  const handleDetailOpenChange = useCallback((next: boolean) => {
    if (!next) setDetailFileId(null)
  }, [])
  const handleDetailDeleted = useCallback(() => setDetailFileId(null), [])
  const handleNavigateToFolder = useCallback(
    (folderId: string) => {
      void selectFolder(collectionId, folderId)
    },
    [collectionId, selectFolder],
  )

  /*
   * Quick Chat portals into [data-pm-rail-anchor] on .pm-files-right.
   * When Messages is collapsed the track is width:0 + pointer-events:none —
   * QC cannot paint or receive clicks. Expand the rail whenever QC covers it.
   */
  useEffect(() => {
    if (railCovered && !messageSidebarOpen) {
      setMessageSidebarOpen(true)
    }
  }, [railCovered, messageSidebarOpen, setMessageSidebarOpen])

  return (
    <div
      className={cn("pm-files", msgCollapsed && "is-msg-collapsed")}
    >
      {/* LEFT — one paper card: toolbar + icon field + breadcrumb */}
      <div className="pm-files-left">
        <div className="pm-files-browser">
          <div className="pm-files-browser-toolbar relative z-20 shrink-0">
            <Toolbar
              collectionId={collectionId}
              trailing={
                <button
                  type="button"
                  onClick={toggleMessageSidebar}
                  title="Show messages"
                  className="pm-files-msg-reopen"
                  tabIndex={msgCollapsed ? 0 : -1}
                  aria-hidden={!msgCollapsed}
                >
                  <PanelRight className="h-3.5 w-3.5 shrink-0" aria-hidden />
                  <span>Messages</span>
                  {msgCount > 0 && (
                    <span className="pm-files-msg-count">{msgCount}</span>
                  )}
                </button>
              }
            />
          </div>
          <div className="pm-files-browser-body flex-1 min-h-0 overflow-hidden">
            <IconGrid
              collectionId={collectionId}
              onOpenFile={(fileId) => setDetailFileId(fileId)}
            />
          </div>
          <div className="pm-files-browser-foot shrink-0">
            <Breadcrumb collectionId={collectionId} />
          </div>
        </div>
      </div>

      {/*
        RIGHT — fixed open width, animates to 0. Panel inside stays full open
        width and is clipped (curtain), not squashed.
      */}
      <aside
        className={cn(
          "pm-files-right pm-overview-right relative",
          msgCollapsed && "is-collapsed",
          railCovered && "is-qc-covered"
        )}
        data-pm-rail-anchor
      >
        <div
          className={cn(
            "pm-rail-stack h-full min-h-0",
            railCovered && "pointer-events-none"
          )}
        >
          <MessageSidebar collectionId={collectionId} />
        </div>
      </aside>

      <NameConflictDialog />
      <FolderUploadConfirmDialog />

      <FileMgmtDetailDialog
        collectionId={collectionId}
        fileId={detailFileId}
        open={!!detailFileId}
        onOpenChange={handleDetailOpenChange}
        onDeleted={handleDetailDeleted}
        onNavigateToFolder={handleNavigateToFolder}
      />
    </div>
  )
}
