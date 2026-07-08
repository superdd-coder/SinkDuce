import { useState, useEffect, useCallback, useRef } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger, TabsIndicator } from "@/components/ui/tabs"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useAppStore } from "@/stores/app-store"
import { getCollectionConfig, getFiles, getFileChunks, deleteDocument, uploadFiles, getTasks, clearCompletedTasks, cancelTask, retryTask, getDocSummary, setDocSummaryInclude, generateDocSummary, type FileListItem, type ChunkDetail, type TaskInfo } from "@/api/client"
import { toast } from "sonner"
import { CollectionList } from "./collection-list"
import { CreateCollectionDialog } from "./create-collection-dialog"
import { DeleteCollectionDialog } from "./delete-collection-dialog"
import { RenameCollectionDialog } from "./rename-collection-dialog"
import { CollectionConfig } from "./collection-config"
import { InfoPanel } from "./info-panel"
import { FileDetailDialog } from "./file-detail-dialog"
import { UploadUI, TaskQueueList } from "./upload-section"
import { QuickChat } from "./quick-chat"

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
  // Per-fetch stale-response tokens. fetchFiles and fetchTasks each own their
  // own counter because fetchTasks polls every 1s and would otherwise bump a
  // shared token on every tick, causing an in-flight fetchFiles response to
  // be discarded as "stale" right when the user switches to the uploading
  // collection.
  const filesTokenRef = useRef(0)
  const tasksTokenRef = useRef(0)
  const [deleteFileTarget, setDeleteFileTarget] = useState<string | null>(null)
  const deleteFileDisplay = files.find(f => f.source === deleteFileTarget)?.display_name || deleteFileTarget
  const [allowedFileTypes, setAllowedFileTypes] = useState<string[]>([])
  const [coverage, setCoverage] = useState<string>("")
  const [generatingSummaries, setGeneratingSummaries] = useState<Set<string>>(new Set())
  const [quickChatOpen, setQuickChatOpen] = useState(false)
  const [highlightChunkIndex, setHighlightChunkIndex] = useState<number | undefined>(undefined)

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
    const token = ++filesTokenRef.current
    setLoading(true)
    try {
      const res = await getFiles(activeCollection)
      if (token !== filesTokenRef.current) return  // stale, a newer fetch has started
      setFiles(res.files)
    } catch {
      if (token !== filesTokenRef.current) return
      setFiles([])
    } finally {
      if (token === filesTokenRef.current) setLoading(false)
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
        setCoverage((cfg.coverage as string) || "")
      }).catch(() => { setAllowedFileTypes([]); setCoverage("") })
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

  const handleToggleDefinitive = async (file: FileListItem, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!activeCollection) return

    const src = file.source
    const currentInclude = file.include_in_summary !== false

    if (!file.has_summary) {
      // No summary yet — generate one (auto-checks include_in_summary)
      setGeneratingSummaries(prev => new Set(prev).add(src))
      try {
        await generateDocSummary(activeCollection, src)
        // Poll for completion
        const start = Date.now()
        while (Date.now() - start < 300_000) {
          await new Promise(r => setTimeout(r, 2000))
          try {
            const ds = await getDocSummary(activeCollection, src)
            if (ds) {
              // Update local file state
              setFiles(prev => prev.map(f =>
                f.source === src
                  ? { ...f, has_summary: true, include_in_summary: true }
                  : f
              ))
              break
            }
          } catch { /* still generating */ }
        }
      } catch (err) {
        toast.error(`Summary generation failed: ${err instanceof Error ? err.message : String(err)}`)
      } finally {
        setGeneratingSummaries(prev => {
          const next = new Set(prev)
          next.delete(src)
          return next
        })
      }
    } else {
      // Summary exists — toggle include_in_summary
      const newInclude = !currentInclude
      // Optimistic update
      setFiles(prev => prev.map(f =>
        f.source === src ? { ...f, include_in_summary: newInclude } : f
      ))
      try {
        await setDocSummaryInclude(activeCollection, src, newInclude)
      } catch (err) {
        // Revert on error
        setFiles(prev => prev.map(f =>
          f.source === src ? { ...f, include_in_summary: currentInclude } : f
        ))
        toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
      }
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
            <div className="flex items-baseline justify-between mb-5">
              <span
                className="truncate t-body-family"
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
                  {coverage && (
                    <div className="text-[11px] leading-relaxed px-3 py-2 border border-dashed border-border bg-muted/30 t-sans-family">
                      <span className="font-medium uppercase tracking-[0.1em] text-muted-foreground/70">Coverage · </span>
                      <span className="text-muted-foreground">{coverage}</span>
                    </div>
                  )}
                  {/* Upload UI stays at top, always accessible (not in scroll). */}
                  <UploadUI
                    hasActiveTasks={tasks.some((t) => t.status === "pending" || t.status === "processing")}
                    allowedFileTypes={allowedFileTypes}
                    onUpload={handleUpload}
                  />
                  {/* One shared scroll area: Upload Queue + File List scroll together
                      so a long task queue can never push the file list out of view. */}
                  <div className="flex-1 overflow-auto">
                    <TaskQueueList
                      hasActiveTasks={tasks.some((t) => t.status === "pending" || t.status === "processing")}
                      tasks={tasks}
                      onClearCompleted={clearCompletedTasks}
                      onRefreshTasks={fetchTasks}
                      onCancelTask={handleCancelTask}
                      onRetryTask={handleRetryTask}
                    />
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
                                    className="text-[10px] font-medium uppercase tracking-[0.1em] px-1.5 py-0.5 text-center w-full leading-normal"
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
                                    className="text-[10px] font-medium uppercase tracking-[0.1em] px-1.5 py-0.5 text-center w-full leading-normal"
                                    style={{
                                      background: "rgba(217,119,6,0.08)",
                                      color: "hsl(32.2 94.6% 43.7%)",
                                      borderRadius: "2px",
                                    }}
                                  >
                                    Meeting
                                  </span>
                                )}
                              </span>
                              <span className="truncate text-xs">{file.display_name || file.source}</span>
                            </div>
                            <span className="text-[10px] font-medium text-muted-foreground">{file.chunk_count} chunks</span>
                            {/* Definitive toggle */}
                            {file.has_summary !== null && (
                              <button
                                className="shrink-0 flex items-center gap-1.5 cursor-pointer"
                                style={{ background: "none", border: "none" }}
                                onClick={(e) => handleToggleDefinitive(file, e)}
                                title={file.include_in_summary !== false ? "Included in collection summary — click to exclude" : "Not included in collection summary — click to include"}
                              >
                                {generatingSummaries.has(file.source) ? (
                                  <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                                ) : (
                                  <span className={`flex items-center justify-center w-3.5 h-3.5 rounded-sm border transition-colors ${
                                    file.include_in_summary !== false
                                      ? "border-primary bg-primary text-primary-foreground"
                                      : "border-muted-foreground/30 bg-transparent"
                                  }`}>
                                    {file.include_in_summary !== false && (
                                      <svg className="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                                      </svg>
                                    )}
                                  </span>
                                )}
                                <span className={`text-[10px] font-medium uppercase tracking-[0.1em] ${
                                  file.include_in_summary !== false ? "text-foreground" : "text-muted-foreground"
                                }`}>
                                  Definitive
                                </span>
                              </button>
                            )}
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
