import { useState, useEffect, useCallback, useRef } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger, TabsIndicator } from "@/components/ui/tabs"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Database } from "lucide-react"
import { useAppStore } from "@/stores/app-store"
import { getCollectionConfig, getFiles, getFileChunks, deleteDocument, uploadFiles, getTasks, clearCompletedTasks, cancelTask, retryTask, type FileListItem, type ChunkDetail, type TaskInfo } from "@/api/client"
import { toast } from "sonner"
import { CollectionList } from "./collection-list"
import { CreateCollectionDialog } from "./create-collection-dialog"
import { DeleteCollectionDialog } from "./delete-collection-dialog"
import { RenameCollectionDialog } from "./rename-collection-dialog"
import { CollectionConfig } from "./collection-config"
import { InfoPanel } from "./info-panel"
import { FileDetailDialog } from "./file-detail-dialog"
import { UploadSection } from "./upload-section"

// Module-level: allows note-editor-dialog to trigger files refresh after ingestion
let _refreshFilesCallback: (() => void) | null = null
export function _triggerFilesRefresh() {
  _refreshFilesCallback?.()
}

export function DatabaseView() {
  const { activeCollection, setActiveCollection, removeDeletedCollection, pendingCreateCollection, setPendingCreateCollection, pendingOpenFile, setPendingOpenFile, collections, fetchCollections } = useAppStore()
  const [createOpen, setCreateOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [renameTarget, setRenameTarget] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState("info")
  const [files, setFiles] = useState<FileListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [dialogKey, setDialogKey] = useState(0)
  const [chunks, setChunks] = useState<ChunkDetail[]>([])
  const [chunksTotal, setChunksTotal] = useState(0)
  const [chunksLoading, setChunksLoading] = useState(false)
  const [tasks, setTasks] = useState<TaskInfo[]>([])
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const fetchFilesRef = useRef<() => void>(() => {})
  const [deleteFileTarget, setDeleteFileTarget] = useState<string | null>(null)
  const [allowedFileTypes, setAllowedFileTypes] = useState<string[]>([])

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
    const handler = () => setActiveTab("info")
    window.addEventListener("show-meeting-log", handler)
    return () => window.removeEventListener("show-meeting-log", handler)
  }, [])

  // Open file detail from Meeting Log
  useEffect(() => {
    if (pendingOpenFile) {
      openFileDetail(pendingOpenFile)
      setPendingOpenFile(null)
    }
  }, [pendingOpenFile, setPendingOpenFile])

  const fetchFiles = useCallback(async () => {
    if (!activeCollection) return
    setLoading(true)
    try {
      const res = await getFiles(activeCollection)
      setFiles(res.files)
    } catch {
      setFiles([])
    } finally {
      setLoading(false)
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
    try {
      const res = await getTasks(activeCollection)
      setTasks(res.tasks)
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
    // Fetch allowed file types for this collection
    if (activeCollection) {
      getCollectionConfig(activeCollection).then((cfg) => {
        const types = cfg.allowed_file_types as string[] | undefined
        setAllowedFileTypes(types && types.length > 0 ? types : [])
      }).catch(() => setAllowedFileTypes([]))
    } else {
      setAllowedFileTypes([])
    }
    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }
    }
  }, [fetchFiles, fetchTasks, activeCollection])

  const handleUpload = async (fileList: FileList | null) => {
    if (!fileList?.length) return
    try {
      await uploadFiles(fileList, activeCollection)
      fetchTasks()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      toast.error(msg || "Upload failed")
    }
  }

  const handleCancelTask = async (taskId: string) => {
    try {
      await cancelTask(taskId)
      fetchTasks()
    } catch { /* ignore */ }
  }

  const handleRetryTask = async (taskId: string) => {
    try {
      await retryTask(taskId)
      fetchTasks()
    } catch { /* ignore */ }
  }

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
            {/* Collection name header */}
            <div className="flex items-baseline justify-between mb-5">
              <span className="text-[12px] font-semibold uppercase tracking-[0.15em] text-foreground">
                {collections.find(c => c.id === activeCollection)?.name || activeCollection}
              </span>
              <span className="text-[10px] text-muted-foreground">
                {files.length > 0 && `${files.length} files · `}{collections.find(c => c.id === activeCollection)?.points_count ?? 0} chunks
              </span>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col min-h-0">
              <TabsList className="w-fit bg-transparent p-0 gap-5 border-b rounded-none border-border relative">
                <TabsIndicator renderBeforeHydration />
                <TabsTrigger
                  value="info"
                  className="text-[10px] font-medium uppercase tracking-[0.12em] px-0 py-1.5 rounded-none bg-transparent data-[state=active]:shadow-none text-muted-foreground after:!opacity-0"
                  style={{ borderColor: "transparent" }}
                >
                  Info
                </TabsTrigger>
                <TabsTrigger
                  value="files"
                  className="text-[10px] font-medium uppercase tracking-[0.12em] px-0 py-1.5 rounded-none bg-transparent data-[state=active]:shadow-none text-muted-foreground after:!opacity-0"
                  style={{ borderColor: "transparent" }}
                >
                  Files
                </TabsTrigger>
                <TabsTrigger
                  value="config"
                  className="text-[10px] font-medium uppercase tracking-[0.12em] px-0 py-1.5 rounded-none bg-transparent data-[state=active]:shadow-none text-muted-foreground after:!opacity-0"
                  style={{ borderColor: "transparent" }}
                >
                  Config
                </TabsTrigger>
              </TabsList>

              <TabsContent key={`info-${activeTab}`} value="info" className="flex-1 mt-2 overflow-hidden min-h-0 animate-tab-in">
                <ScrollArea className="h-full">
                  <InfoPanel collection={activeCollection} />
                </ScrollArea>
              </TabsContent>

              <TabsContent key={`files-${activeTab}`} value="files" className="flex-1 mt-2 overflow-hidden animate-tab-in">
                <div className="h-full flex flex-col gap-4">
                  <UploadSection
                    hasActiveTasks={tasks.some((t) => t.status === "pending" || t.status === "processing")}
                    tasks={tasks}
                    allowedFileTypes={allowedFileTypes}
                    onUpload={handleUpload}
                    onClearCompleted={clearCompletedTasks}
                    onRefreshTasks={fetchTasks}
                    onCancelTask={handleCancelTask}
                    onRetryTask={handleRetryTask}
                  />
                  <div className="flex-1 overflow-auto">
                    {loading ? (
                      <p className="text-sm text-muted-foreground">Loading...</p>
                    ) : files.length === 0 ? (
                      <p className="text-sm text-muted-foreground">No files yet</p>
                    ) : (
                      <div className="space-y-0">
                        {files.map((file) => (
                          <div
                            key={file.source}
                            className="flex items-center gap-3 py-2.5 cursor-pointer text-sm border-b transition-colors group border-b border-dashed border-border text-foreground"
                            onClick={() => openFileDetail(file.source)}
                          >
                            <div className="flex-1 min-w-0 flex items-center gap-3">
                              {/* Fixed-width tag — equal width, text centered */}
                              <span className="shrink-0 flex items-center" style={{ width: "72px" }}>
                                {file.file_type === "note" && (
                                  <span
                                    className="text-[9px] font-medium uppercase tracking-[0.1em] px-1.5 py-0.5 text-center w-full leading-normal"
                                    style={{
                                      background: "rgba(37,99,235,0.08)",
                                      color: "hsl(217.2 91.2% 59.8%)",
                                      borderRadius: "2px",
                                    }}
                                  >
                                    Note
                                  </span>
                                )}
                                {file.has_meeting && (
                                  <span
                                    className="text-[9px] font-medium uppercase tracking-[0.1em] px-1.5 py-0.5 text-center w-full leading-normal"
                                    style={{
                                      background: "rgba(217,119,6,0.08)",
                                      color: "hsl(32.2 94.6% 43.7%)",
                                      borderRadius: "2px",
                                    }}
                                  >
                                    Recording
                                  </span>
                                )}
                              </span>
                              <span className="truncate text-xs">{file.display_name || file.source}</span>
                            </div>
                            <span className="text-[10px] font-medium text-muted-foreground">{file.chunk_count} chunks</span>
                            <button
                              className="text-[10px] opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer text-muted-foreground"
                              style={{ background: "none", border: "none" }}
                              onClick={(e) => { e.stopPropagation(); setDeleteFileTarget(file.source) }}
                            >
                              Delete
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </TabsContent>

              <TabsContent key={`config-${activeTab}`} value="config" className="flex-1 mt-2 overflow-hidden min-h-0 animate-tab-in">
                <ScrollArea className="h-full">
                  <CollectionConfig collection={activeCollection} />
                </ScrollArea>
              </TabsContent>
            </Tabs>
          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-muted-foreground animate-tab-in">
            <div className="text-center">
              <div
                className="w-10 h-10 border mx-auto mb-3 flex items-center justify-center border-border"
                style={{ borderRadius: "3px", opacity: 0.3 }}
              >
                <Database className="h-5 w-5" />
              </div>
              <p className="text-sm" style={{ fontFamily: "var(--font-serif)" }}>Select a collection or create one</p>
            </div>
          </div>
        )}
      </div>

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
        openKey={dialogKey}
        chunks={chunks}
        chunksTotal={chunksTotal}
        loading={chunksLoading}
        onOpenChange={(v) => !v && setSelectedFile(null)}
      />

      {/* File deletion confirmation */}
      <Dialog open={!!deleteFileTarget} onOpenChange={(v) => !v && setDeleteFileTarget(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete File</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Are you sure you want to delete <span className="font-mono font-medium text-foreground">{deleteFileTarget}</span>?
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
