import { useCallback, useEffect, useMemo, useState } from "react"
import { useShallow } from "zustand/react/shallow"
import { ChevronLeft, ChevronRight, PanelRight, Search, X } from "lucide-react"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { Breadcrumb } from "./breadcrumb"
import { IconGrid } from "./icon-grid"
import { MessageSidebar } from "./message-sidebar"
import { NameConflictDialog } from "./name-conflict-dialog"
import { FolderUploadConfirmDialog } from "./folder-upload-confirm-dialog"
import { Toolbar } from "./toolbar"
import { FileMgmtDetailDialog } from "@/components/file-mgmt/file-detail"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"
import { DropdownSelect } from "@/components/ui/dropdown-select"
import { systemFolderDisplayName } from "@/i18n/system-folder"
import { FolderNavigator } from "./folder-navigator"
import { browserPage, currentFolderItems, filterBrowserItems, type BrowserFilter } from "./browser-items"

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
  const t = useT()
  const browse = useFileMgmtStore(useShallow(s => ({
    currentFolderId: s.currentFolderId, folderTree: s.folderTree, currentFolderFiles: s.currentFolderFiles,
    folderFileSort: s.folderFileSort, folderFileView: s.folderFileView, filesLoading: s.filesLoading,
    selectedFileIds: s.selectedFileIds, selectedFolderIds: s.selectedFolderIds,
  })))
  const scope = `${collectionId}:${browse.currentFolderId || "root"}`
  const [search, setSearch] = useState({ scope, query: "", filter: "all" as BrowserFilter })
  const query = search.scope === scope ? search.query : ""
  const filter = search.scope === scope ? search.filter : "all"
  const allItems = useMemo(() => currentFolderItems(browse.folderTree, browse.currentFolderId, browse.currentFolderFiles, browse.folderFileSort), [browse.folderTree, browse.currentFolderId, browse.currentFolderFiles, browse.folderFileSort])
  const filteredItems = useMemo(() => filterBrowserItems(allItems, query, filter, name => systemFolderDisplayName(name, t)), [allItems, query, filter, t])
  const paginationKey = `${scope}:${query}:${filter}:${browse.folderFileView}:${browse.folderFileSort}`
  const [pagination, setPagination] = useState({ key: paginationKey, page: 0 })
  const page = browserPage(filteredItems, pagination.key === paginationKey ? pagination.page : 0, browse.folderFileView === "list" ? 50 : 24)
  const selectableFileIds = useMemo(() => filteredItems.flatMap(item => item.kind === "file" ? [item.file.file_id] : []), [filteredItems])
  const selectionCount = browse.selectedFileIds.size + browse.selectedFolderIds.size
  const updateSearch = (nextQuery: string, nextFilter: BrowserFilter) => {
    setSearch({ scope, query: nextQuery, filter: nextFilter })
    const store = useFileMgmtStore.getState()
    store.clearSelection()
    store.clearFolderSelection()
  }
  const {
    fetchFolderTree,
    navigateToRoot,
    selectFolder,
    perCollectionFolderCache,
    hydrateFolderFileSort,
    hydrateFolderFileView,
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
      hydrateFolderFileView: s.hydrateFolderFileView,
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
      hydrateFolderFileView(collectionId)
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
      className={cn("pm-files sd-files", msgCollapsed && "is-msg-collapsed")}
    >
      {/* LEFT — one paper card: toolbar + icon field + breadcrumb */}
      <div className="pm-files-left">
        <div className="pm-files-browser">
          <div className="sd-files-location">
            <FolderNavigator collectionId={collectionId} />
            <Breadcrumb collectionId={collectionId} />
          </div>
          <div className="sd-files-search-row">
            <div className="sd-library-search">
              <Search size={15} aria-hidden="true" />
              <input value={query} onChange={e => updateSearch(e.target.value, filter)} placeholder={t("fileBrowser.searchFolder")} aria-label={t("fileBrowser.searchFolder")} />
              {query && <button className="sd-search-clear" onClick={() => updateSearch("", filter)} aria-label={t("fileBrowser.clearSearch")}><X size={14} /></button>}
            </div>
            <DropdownSelect className="sd-files-filter" size="tag" chevron value={filter} aria-label={t("fileBrowser.filterItems")} onChange={value => updateSearch(query, value as BrowserFilter)} options={[
              { value: "all", label: t("fileBrowser.allItems") }, { value: "folder", label: t("fileBrowser.foldersOnly") }, { value: "file", label: t("fileBrowser.filesOnly") },
            ]} />
          </div>
          <div className="pm-files-browser-toolbar relative z-20 shrink-0">
            <Toolbar
              collectionId={collectionId}
              selectableFileIds={selectableFileIds}
              selectionFiltered={!!query.trim() || filter !== "all"}
              trailing={
                <button
                  type="button"
                  onClick={toggleMessageSidebar}
                  title={t("fileMgmt.showMessages")}
                  aria-label={t("fileMgmt.showMessages")}
                  className="pm-files-msg-reopen"
                  tabIndex={msgCollapsed ? 0 : -1}
                  aria-hidden={!msgCollapsed}
                >
                  <PanelRight className="h-3.5 w-3.5 shrink-0" aria-hidden />
                  <span>{t("common.messages")}</span>
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
              items={page.items}
              pageKey={`${paginationKey}:${page.page}`}
              filtered={!!query.trim() || filter !== "all"}
              onOpenFile={(fileId) => setDetailFileId(fileId)}
            />
          </div>
          <div className="sd-files-pagination">
            <span aria-live="polite">{browse.filesLoading ? t("common.loading") : t("fileBrowser.itemRange", { start: page.start, end: page.end, total: filteredItems.length })}</span>
            <span className="sd-files-selection-count">{selectionCount > 0 && <>{t("fileMgmt.nSelected", { n: selectionCount })}<button className="sd-search-clear" aria-label={t("fileBrowser.clearSelection")} title={t("fileBrowser.clearSelection")} onClick={() => useFileMgmtStore.getState().exitMultiSelectMode()}><X size={13} /></button></>}</span>
            <button className="sd-files-icon" disabled={page.page === 0} aria-label={t("fileBrowser.previousPage")} onClick={() => setPagination({ key: paginationKey, page: page.page - 1 })}><ChevronLeft size={16} /></button>
            <span>{page.page + 1} / {page.pages}</span>
            <button className="sd-files-icon" disabled={page.page + 1 >= page.pages} aria-label={t("fileBrowser.nextPage")} onClick={() => setPagination({ key: paginationKey, page: page.page + 1 })}><ChevronRight size={16} /></button>
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
