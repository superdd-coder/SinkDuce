import { useState, useEffect, useCallback, useRef } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger, TabsIndicator } from "@/components/ui/tabs"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { FolderTreeIcon, GitBranchPlus, Settings } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useAppStore } from "@/stores/app-store"
import { getFiles, getFileChunks, deleteDocument, getTasks, type FileListItem, type ChunkDetail } from "@/api/client"
import { CollectionList } from "./collection-list"
import { CreateCollectionDialog } from "./create-collection-dialog"
import { DeleteCollectionDialog } from "./delete-collection-dialog"
import { RenameCollectionDialog } from "./rename-collection-dialog"
import { CollectionConfig } from "./collection-config"
import { InfoPanel } from "./info-panel"
import { FileDetailDialog } from "./file-detail-dialog"
import { QuickChat } from "./quick-chat"
import { FolderView } from "@/components/file-mgmt/folder-view"
import { TimelineView } from "@/components/file-mgmt/timeline-view"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { cn } from "@/lib/utils"

// Module-level: allows note-editor-dialog to trigger files refresh after ingestion
let _refreshFilesCallback: (() => void) | null = null
export function _triggerFilesRefresh() {
  _refreshFilesCallback?.()
}

type DbViewMode = "classic" | "folders" | "timeline"
type DbTab = "info" | "files" | "config"

function loadDbUi<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(`rag_${key}`)
    return v !== null ? (JSON.parse(v) as T) : fallback
  } catch {
    return fallback
  }
}

function saveDbUi(key: string, value: unknown) {
  try {
    localStorage.setItem(`rag_${key}`, JSON.stringify(value))
  } catch { /* ignore */ }
}

export function DatabaseView() {
  const { activeCollection, setActiveCollection, removeDeletedCollection, pendingCreateCollection, setPendingCreateCollection, pendingOpenFile, setPendingOpenFile, collections, fetchCollections } = useAppStore()
  const [createOpen, setCreateOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [renameTarget, setRenameTarget] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<DbTab>(() => {
    const t = loadDbUi<string>("dbActiveTab", "info")
    return t === "info" || t === "files" || t === "config" ? t : "info"
  })
  const [files, setFiles] = useState<FileListItem[]>([])
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [dialogKey, setDialogKey] = useState(0)
  const [chunks, setChunks] = useState<ChunkDetail[]>([])
  const [chunksTotal, setChunksTotal] = useState(0)
  const [chunksLoading, setChunksLoading] = useState(false)
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const fetchFilesRef = useRef<() => void>(() => {})
  // Per-fetch stale-response tokens. fetchFiles and fetchTasks each own their
  // own counter because fetchTasks polls every 1s and would otherwise bump a
  // shared token on every tick, causing an in-flight fetchFiles response to
  // be discarded as "stale" right when the user switches to the uploading
  // collection.
  const filesTokenRef = useRef(0)
  const tasksTokenRef = useRef(0)
  const [deleteFileTarget, setDeleteFileTarget] = useState<string | null>(null)
  const deleteFileDisplay = files.find(f => f.source === deleteFileTarget)?.display_name || deleteFileTarget
  const [quickChatOpen, setQuickChatOpen] = useState(false)
  const [highlightChunkIndex, setHighlightChunkIndex] = useState<number | undefined>(undefined)
  // Phase 6: view mode for folder view vs timeline view
  const [dbViewMode, setDbViewMode] = useState<DbViewMode>(() => {
    const m = loadDbUi<string>("dbViewMode", "folders")
    // UI toggle is Folders | Timeline only (legacy "classic" → folders)
    return m === "timeline" ? "timeline" : "folders"
  })

  const handleTabChange = useCallback((tab: string) => {
    const next: DbTab = tab === "info" || tab === "files" || tab === "config" ? tab : "info"
    setActiveTab(next)
    saveDbUi("dbActiveTab", next)
  }, [])

  const handleDbViewMode = useCallback((mode: DbViewMode) => {
    // UI only offers folders | timeline; classic kept for type compat
    const next = mode === "timeline" ? "timeline" : "folders"
    setDbViewMode(next)
    saveDbUi("dbViewMode", next)
  }, [])

  // In-app jump from message mini-graph → Timeline (same SPA route, no new window)
  const timelineNavRequest = useFileMgmtStore((s) => s.timelineNavRequest)
  useEffect(() => {
    if (!timelineNavRequest) return
    handleTabChange("files")
    handleDbViewMode("timeline")
  }, [timelineNavRequest, handleDbViewMode, handleTabChange])

  // Listen for "Create New Database" events from other components (e.g. meeting ingest)
  useEffect(() => {
    const handler = () => {
      setCreateOpen(true)
      const { setSidebarView } = useAppStore.getState()
      setSidebarView("database")
    }
    window.addEventListener("open-create-collection", handler)
    return () => window.removeEventListener("open-create-collection", handler)
  }, [])

  // Check pending create flag on mount
  useEffect(() => {
    if (pendingCreateCollection) {
      setCreateOpen(true)
      setPendingCreateCollection(false)
    }
  }, [pendingCreateCollection, setPendingCreateCollection])

  // Switch to Info tab when navigating from Meeting page
  useEffect(() => {
    const handler = () => handleTabChange("info")
    window.addEventListener("show-meeting-log", handler)
    return () => window.removeEventListener("show-meeting-log", handler)
  }, [handleTabChange])

  // Open file detail from Meeting Log
  useEffect(() => {
    if (pendingOpenFile) {
      openFileDetail(pendingOpenFile)
      setPendingOpenFile(null)
    }
  }, [pendingOpenFile, setPendingOpenFile])

  const fetchFiles = useCallback(async () => {
    if (!activeCollection) return
    const token = ++filesTokenRef.current
    try {
      const res = await getFiles(activeCollection)
      if (token !== filesTokenRef.current) return  // stale, a newer fetch has started
      setFiles(res.files)
    } catch {
      if (token !== filesTokenRef.current) return
      setFiles([])
    }
  }, [activeCollection])

  // Keep ref in sync so polling always calls the latest fetchFiles
  fetchFilesRef.current = fetchFiles

  // Wire module-level callback for external files refresh (e.g. note ingestion)
  useEffect(() => {
    _refreshFilesCallback = fetchFiles
    return () => { _refreshFilesCallback = null }
  }, [fetchFiles])

  const fetchTasks = useCallback(async () => {
    const token = ++tasksTokenRef.current
    try {
      const res = await getTasks(activeCollection)
      if (token !== tasksTokenRef.current) return  // stale, a newer fetch has started
      if (res.processing > 0 || res.pending > 0) {
        if (!pollingRef.current) {
          pollingRef.current = setInterval(fetchTasks, 1000)
        }
      } else {
        if (pollingRef.current) {
          clearInterval(pollingRef.current)
          pollingRef.current = null
        }
        fetchFilesRef.current()
      }
    } catch {
      // ignore
    }
  }, [activeCollection])

  useEffect(() => {
    fetchCollections()
  }, [])

  useEffect(() => {
    fetchFiles()
    fetchTasks()
    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }
    }
  }, [fetchFiles, fetchTasks, activeCollection])

  const handleDeleteFile = async () => {
    if (!deleteFileTarget) return
    try {
      await deleteDocument(activeCollection, deleteFileTarget)
      setDeleteFileTarget(null)
      fetchFiles()
    } catch {
      // ignore
    }
  }

  const openFileDetail = async (source: string) => {
    setSelectedFile(source)
    setDialogKey(k => k + 1)
    setChunksLoading(true)
    try {
      const res = await getFileChunks(activeCollection, source, 10000)
      setChunks(res.chunks)
      setChunksTotal(res.total)
    } catch {
      setChunks([])
      setChunksTotal(0)
    } finally {
      setChunksLoading(false)
    }
  }

  return (
    <div className="h-full flex">
      <CollectionList
        collections={collections}
        activeCollection={activeCollection}
        onSelect={setActiveCollection}
        onCreate={() => setCreateOpen(true)}
        onDelete={setDeleteTarget}
        onRename={setRenameTarget}
      />

      <div className="flex-1 overflow-hidden" key={activeCollection || "empty"}>
        {activeCollection ? (
          <div className="h-full flex flex-col px-10 py-8 animate-tab-in">
            {/* Collection name header — AI-COMP-001 Heading LG */}
            <div className="flex items-center justify-between gap-3 mb-5">
              <span
                className="truncate t-body-family min-w-0"
                style={{
                  fontSize: "24px",
                  fontWeight: 300,
                  letterSpacing: "-0.01em",
                  lineHeight: 1.2,
                  color: "var(--ze-ink)",
                }}
              >
                {collections.find(c => c.id === activeCollection)?.name || activeCollection}
              </span>
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-[10px] text-muted-foreground tabular-nums">
                  {files.length > 0 && `${files.length} files · `}
                  {collections.find(c => c.id === activeCollection)?.points_count ?? 0} chunks
                </span>
                <button
                  type="button"
                  onClick={() => handleTabChange("config")}
                  title="Collection settings"
                  className={cn(
                    "p-1.5 rounded-md transition-colors",
                    activeTab === "config"
                      ? "text-primary bg-primary/10"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
                  )}
                >
                  <Settings className="h-4 w-4" />
                </button>
              </div>
            </div>

            <Tabs value={activeTab} onValueChange={handleTabChange} className="flex-1 flex flex-col min-h-0">
              {/*
                Shared row height: text vertically centered (字对齐);
                underline sits at bottom-0 of the row = toggle bottom edge.
              */}
              <div className="flex items-stretch gap-0 min-w-0 h-7">
                <TabsList className="!h-7 w-fit bg-transparent p-0 gap-5 border-0 rounded-none relative shrink-0 items-center">
                  <TabsIndicator
                    renderBeforeHydration
                    className="!bottom-0 h-0.5"
                  />
                  <TabsTrigger
                    value="info"
                    className={cn(
                      "!h-7 min-h-0 px-0 py-0 rounded-none bg-transparent",
                      "data-[state=active]:shadow-none data-active:bg-transparent",
                      "text-[10px] font-medium uppercase tracking-[0.12em] leading-none",
                      "text-muted-foreground data-active:text-primary",
                      "after:!opacity-0 after:!content-none",
                      "inline-flex items-center justify-center"
                    )}
                    style={{ borderColor: "transparent" }}
                  >
                    Info
                  </TabsTrigger>
                  <TabsTrigger
                    value="files"
                    className={cn(
                      "!h-7 min-h-0 px-0 py-0 rounded-none bg-transparent",
                      "data-[state=active]:shadow-none data-active:bg-transparent",
                      "text-[10px] font-medium uppercase tracking-[0.12em] leading-none",
                      "text-muted-foreground data-active:text-primary",
                      "after:!opacity-0 after:!content-none",
                      "inline-flex items-center justify-center"
                    )}
                    style={{ borderColor: "transparent" }}
                  >
                    Files
                  </TabsTrigger>
                </TabsList>

                <div
                  className={cn(
                    "overflow-hidden transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
                    "flex items-stretch h-7",
                    activeTab === "files"
                      ? "max-w-[300px] opacity-100 ml-3"
                      : "max-w-0 opacity-0 ml-0 pointer-events-none"
                  )}
                >
                  <div
                    className={cn(
                      "flex items-center gap-2.5 whitespace-nowrap h-7",
                      "transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
                      activeTab === "files" ? "translate-x-0" : "-translate-x-2"
                    )}
                  >
                    <span
                      className="w-px h-3.5 bg-border/60 shrink-0 self-center"
                      aria-hidden
                    />
                    {/* Full row height so bottom edge lines up with tab underline */}
                    <div
                      role="group"
                      aria-label="Files view mode"
                      className="relative grid grid-cols-2 h-full rounded-md bg-muted/50 p-0.5"
                    >
                      <span
                        aria-hidden
                        className={cn(
                          "pointer-events-none absolute inset-y-0.5 left-0.5 w-[calc(50%-2px)] rounded-[5px]",
                          "bg-primary shadow-sm",
                          "transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]"
                        )}
                        style={{
                          transform:
                            dbViewMode === "timeline"
                              ? "translateX(100%)"
                              : "translateX(0)",
                        }}
                      />
                      <button
                        type="button"
                        className={cn(
                          "relative z-10 h-full px-2.5 inline-flex items-center justify-center gap-1",
                          "text-[10px] font-medium uppercase tracking-[0.12em] leading-none",
                          "transition-colors duration-200",
                          dbViewMode === "folders"
                            ? "text-primary-foreground"
                            : "text-muted-foreground hover:text-foreground"
                        )}
                        onClick={() => handleDbViewMode("folders")}
                        aria-pressed={dbViewMode === "folders"}
                      >
                        <FolderTreeIcon className="size-3 shrink-0 opacity-90" />
                        Folders
                      </button>
                      <button
                        type="button"
                        className={cn(
                          "relative z-10 h-full px-2.5 inline-flex items-center justify-center gap-1",
                          "text-[10px] font-medium uppercase tracking-[0.12em] leading-none",
                          "transition-colors duration-200",
                          dbViewMode === "timeline"
                            ? "text-primary-foreground"
                            : "text-muted-foreground hover:text-foreground"
                        )}
                        onClick={() => handleDbViewMode("timeline")}
                        aria-pressed={dbViewMode === "timeline"}
                      >
                        <GitBranchPlus className="size-3 shrink-0 opacity-90" />
                        Timeline
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              <TabsContent key={`info-${activeTab}`} value="info" className="flex-1 mt-1 overflow-hidden min-h-0 animate-tab-in">
                <ScrollArea className="h-full">
                  <InfoPanel collection={activeCollection} />
                </ScrollArea>
              </TabsContent>

              <TabsContent
                key={`files-${activeTab}`}
                value="files"
                // overflow-visible: folder message sidebar shadow must paint outside its box.
                // Timeline / grid keep their own overflow-hidden scroll roots.
                className="flex-1 flex flex-col mt-1 overflow-visible min-h-0 animate-tab-in"
              >
                {dbViewMode === "timeline" ? (
                  <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
                    <TimelineView collectionId={activeCollection} />
                  </div>
                ) : (
                  <div className="flex-1 flex flex-col min-h-0 overflow-visible">
                    <FolderView collectionId={activeCollection} />
                  </div>
                )}
              </TabsContent>

              <TabsContent key={`config-${activeTab}`} value="config" className="flex-1 mt-1 overflow-hidden min-h-0 animate-tab-in">
                <ScrollArea className="h-full">
                  <CollectionConfig collection={activeCollection} />
                </ScrollArea>
              </TabsContent>
            </Tabs>
          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-muted-foreground animate-tab-in">
            <div className="text-center">
              <p className="text-sm t-body-family">Select a collection or create one</p>
            </div>
          </div>
        )}
      </div>

      {/* Quick Chat — always mounted for floating button, sidebar shown on demand */}
      {activeCollection && (
        <QuickChat
          collectionId={activeCollection}
          collectionName={collections.find(c => c.id === activeCollection)?.name || activeCollection}
          open={quickChatOpen}
          onOpen={() => setQuickChatOpen(true)}
          onClose={() => setQuickChatOpen(false)}
          files={files}
          onSourceClick={(source, chunkIndex) => {
            setActiveTab("files")
            setHighlightChunkIndex(chunkIndex)
            openFileDetail(source)
          }}
        />
      )}

      <CreateCollectionDialog open={createOpen} onOpenChange={setCreateOpen} onCreated={fetchCollections} />
      <DeleteCollectionDialog
        collectionId={deleteTarget}
        collectionName={deleteTarget ? collections.find(c => c.id === deleteTarget)?.name || "" : ""}
        onOpenChange={(v) => !v && setDeleteTarget(null)}
        onDeleted={() => { if (deleteTarget) removeDeletedCollection(deleteTarget); setDeleteTarget(null); fetchCollections() }}
      />
      {renameTarget && (
        <RenameCollectionDialog
          collectionId={renameTarget}
          currentName={collections.find(c => c.id === renameTarget)?.name || ""}
          open={!!renameTarget}
          onOpenChange={(v) => !v && setRenameTarget(null)}
          onRenamed={() => { setRenameTarget(null); fetchCollections() }}
        />
      )}

      <FileDetailDialog
        collection={activeCollection}
        source={selectedFile}
        displayName={files.find(f => f.source === selectedFile)?.display_name}
        fileType={files.find(f => f.source === selectedFile)?.file_type}
        originalExt={files.find(f => f.source === selectedFile)?.original_ext}
        openKey={dialogKey}
        chunks={chunks}
        chunksTotal={chunksTotal}
        loading={chunksLoading}
        highlightChunkIndex={highlightChunkIndex}
        onOpenChange={(v) => { if (!v) { setSelectedFile(null); setHighlightChunkIndex(undefined); fetchFiles() } }}
      />

      {/* File deletion confirmation */}
      <Dialog open={!!deleteFileTarget} onOpenChange={(v) => !v && setDeleteFileTarget(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete File</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Are you sure you want to delete <span className="font-medium text-foreground truncate max-w-[200px] inline-block align-bottom">{deleteFileDisplay}</span>?
            This will remove all its chunks from the database.
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setDeleteFileTarget(null)}>Cancel</Button>
            <Button variant="destructive" onClick={handleDeleteFile}>Delete</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
