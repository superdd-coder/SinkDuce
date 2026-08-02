import { useState, useEffect, useCallback, useRef } from "react"
import { Tabs, TabsList, TabsTrigger, TabsIndicator } from "@/components/ui/tabs"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { List, Settings } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useAppStore } from "@/stores/app-store"
import { getFiles, deleteDocument, getTasks, type FileListItem } from "@/api/client"
import { CollectionList } from "./collection-list"
import { CreateCollectionDialog } from "./create-collection-dialog"
import { DeleteCollectionDialog } from "./delete-collection-dialog"
import { RenameCollectionDialog } from "./rename-collection-dialog"
import { CollectionConfig } from "./collection-config"
import { InfoPanel } from "./info-panel"
import { ClassicFilesDialog } from "./classic-files-dialog"
import { QuickChat } from "./quick-chat"
import { FolderView } from "@/components/file-mgmt/folder-view"
import { TimelineView } from "@/components/file-mgmt/timeline-view"
import { FileMgmtDetailDialog } from "@/components/file-mgmt/file-detail"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { cn } from "@/lib/utils"

// Module-level: allows note-editor-dialog to trigger files refresh after ingestion
let _refreshFilesCallback: (() => void) | null = null
export function _triggerFilesRefresh() {
  _refreshFilesCallback?.()
}

/** Top-level database tabs: Info | Files (folders) | Timeline | Config */
type DbTab = "info" | "files" | "timeline" | "config"

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

function loadInitialDbTab(): DbTab {
  const t = loadDbUi<string>("dbActiveTab", "info")
  if (t === "info" || t === "files" || t === "timeline" || t === "config") {
    // Migrate legacy: Files tab + Folders/Timeline sub-toggle
    if (t === "files") {
      const mode = loadDbUi<string>("dbViewMode", "folders")
      if (mode === "timeline") return "timeline"
    }
    return t
  }
  return "info"
}

export function DatabaseView() {
  const { activeCollection, setActiveCollection, removeDeletedCollection, pendingCreateCollection, setPendingCreateCollection, pendingOpenFile, setPendingOpenFile, collections, fetchCollections } = useAppStore()
  const [createOpen, setCreateOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [renameTarget, setRenameTarget] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<DbTab>(() => loadInitialDbTab())
  /** Tabs visited at least once — keepMounted after first open for snappy re-switch. */
  const [visitedTabs, setVisitedTabs] = useState<Set<DbTab>>(
    () => new Set([loadInitialDbTab()])
  )
  const [files, setFiles] = useState<FileListItem[]>([])
  /** Unified file detail — fileId for metadata, source for chunks/meeting/note. */
  const [detailOpen, setDetailOpen] = useState<{
    fileId?: string | null
    source?: string | null
  } | null>(null)
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
  /** All Files flat list — dialog only, not a top-level tab. */
  const [classicFilesOpen, setClassicFilesOpen] = useState(false)

  const handleTabChange = useCallback((tab: string) => {
    const next: DbTab =
      tab === "info" || tab === "files" || tab === "timeline" || tab === "config"
        ? tab
        : "info"
    setActiveTab(next)
    setVisitedTabs((prev) => {
      if (prev.has(next)) return prev
      const n = new Set(prev)
      n.add(next)
      return n
    })
    saveDbUi("dbActiveTab", next)
  }, [])

  // In-app jump from message mini-graph → Timeline (same SPA route, no new window)
  const timelineNavRequest = useFileMgmtStore((s) => s.timelineNavRequest)
  useEffect(() => {
    if (!timelineNavRequest) return
    handleTabChange("timeline")
  }, [timelineNavRequest, handleTabChange])

  // Collection switch remounts the panel tree (key=activeCollection); reset visit
  // cache so we don't keepMounted-mount every prior tab for the new collection.
  useEffect(() => {
    setVisitedTabs(new Set([activeTab]))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on collection change
  }, [activeCollection])

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

  // Open unified file detail from Meeting Log / Info panel / etc.
  useEffect(() => {
    if (pendingOpenFile) {
      const raw = pendingOpenFile
      const fileId =
        raw.startsWith("__file__:")
          ? raw.slice("__file__:".length)
          : /^[a-f0-9]{32}$/i.test(raw.trim())
            ? raw.trim()
            : null
      setDetailOpen({
        fileId,
        // Keep original when it's a document source; when only fileId, detail will fill source
        source: fileId && raw === fileId ? `__file__:${fileId}` : raw,
      })
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

            <div className="flex-1 flex flex-col min-h-0 min-w-0">
              {/*
                Tab list only uses Base UI Tabs (for indicator).
                Panels are custom — NO TabsContent: its [hidden]/starting-style
                transitions zero size and flash Timeline on every switch.
              */}
              <div className="shrink-0 flex items-center justify-between gap-3 min-w-0 w-full h-7">
                <Tabs
                  value={activeTab}
                  onValueChange={handleTabChange}
                  className="min-w-0"
                >
                  <TabsList className="!h-7 w-fit bg-transparent p-0 gap-5 border-0 rounded-none relative shrink-0 items-center">
                    <TabsIndicator
                      renderBeforeHydration
                      className="!bottom-0 h-0.5"
                    />
                    {(
                      [
                        ["info", "Info"],
                        ["files", "Files"],
                        ["timeline", "Timeline"],
                      ] as const
                    ).map(([value, label]) => (
                      <TabsTrigger
                        key={value}
                        value={value}
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
                        {label}
                      </TabsTrigger>
                    ))}
                  </TabsList>
                </Tabs>
                {/* Files tab only: All Files flat list dialog — right edge of tab bar row */}
                {activeTab === "files" && (
                  <button
                    type="button"
                    onClick={() => setClassicFilesOpen(true)}
                    title="All Files"
                    className={cn(
                      "shrink-0 inline-flex items-center gap-1.5 h-7 px-1.5 rounded-md ml-auto",
                      "text-[10px] font-medium uppercase tracking-[0.12em] leading-none",
                      "text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
                    )}
                  >
                    <List className="h-3.5 w-3.5" />
                    <span className="hidden sm:inline">All Files</span>
                  </button>
                )}
              </div>

              {/*
                Keep-alive tab panels (SaaS-style):
                - Visit once → stay mounted (no remount / Loading flash).
                - Inactive = opacity-0 + inert (NOT display:none, NOT visibility:hidden).
                  · opacity cannot be re-opened by descendants → no residual bleed.
                  · display:none zeroed Timeline's viewport → wrong pan, nodes clipped on return.
                  · visibility:hidden can be overridden by child visibility:visible.
                - Active panel has solid bg + higher z so nothing shows through.
              */}
              <div className="relative flex-1 min-h-0 min-w-0 mt-1 bg-background isolate">
                {(
                  [
                    ["info", visitedTabs.has("info")] as const,
                    ["files", visitedTabs.has("files")] as const,
                    ["timeline", visitedTabs.has("timeline")] as const,
                    ["config", visitedTabs.has("config")] as const,
                  ] as const
                ).map(([tab, visited]) => {
                  if (!visited) return null
                  const isActive = activeTab === tab
                  return (
                    <div
                      key={tab}
                      className={cn(
                        "absolute inset-0 flex flex-col overflow-hidden bg-background",
                        "transition-opacity duration-0",
                        isActive
                          ? "z-10 opacity-100"
                          : "z-0 opacity-0 pointer-events-none select-none"
                      )}
                      aria-hidden={!isActive}
                      inert={!isActive ? true : undefined}
                    >
                      {tab === "info" && (
                        <ScrollArea className="h-full">
                          <InfoPanel collection={activeCollection} />
                        </ScrollArea>
                      )}
                      {tab === "files" && (
                        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
                          <FolderView collectionId={activeCollection} />
                        </div>
                      )}
                      {tab === "timeline" && (
                        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
                          <TimelineView
                            collectionId={activeCollection}
                            active={isActive}
                          />
                        </div>
                      )}
                      {tab === "config" && (
                        <ScrollArea className="h-full">
                          <CollectionConfig collection={activeCollection} />
                        </ScrollArea>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
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
          onSourceClick={(source) => {
            setActiveTab("files")
            const fileId =
              source.startsWith("__file__:")
                ? source.slice("__file__:".length)
                : /^[a-f0-9]{32}$/i.test(source.trim())
                  ? source.trim()
                  : null
            setDetailOpen({ fileId, source })
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

      {activeCollection && (
        <FileMgmtDetailDialog
          collectionId={activeCollection}
          fileId={detailOpen?.fileId}
          source={detailOpen?.source}
          open={!!detailOpen}
          onOpenChange={(v) => {
            if (!v) {
              setDetailOpen(null)
              fetchFiles()
              void useFileMgmtStore.getState().refreshFiles(activeCollection)
            }
          }}
          onDeleted={() => {
            setDetailOpen(null)
            fetchFiles()
          }}
        />
      )}

      {activeCollection && (
        <ClassicFilesDialog
          collectionId={activeCollection}
          open={classicFilesOpen}
          onOpenChange={setClassicFilesOpen}
        />
      )}

      {/* File deletion confirmation (Quick Chat / pendingOpenFile paths) */}
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
