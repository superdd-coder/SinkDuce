import { useState, useEffect, useCallback, useRef } from "react"
import { useShallow } from "zustand/react/shallow"
import { Tabs, TabsList, TabsTrigger, TabsIndicator } from "@/components/ui/tabs"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { List, MoreVertical } from "lucide-react"
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

export function DatabaseView({ active = true }: { active?: boolean }) {
  const {
    activeCollection,
    setActiveCollection,
    removeDeletedCollection,
    pendingCreateCollection,
    setPendingCreateCollection,
    pendingOpenFile,
    setPendingOpenFile,
    collections,
    fetchCollections,
  } = useAppStore(
    useShallow((s) => ({
      activeCollection: s.activeCollection,
      setActiveCollection: s.setActiveCollection,
      removeDeletedCollection: s.removeDeletedCollection,
      pendingCreateCollection: s.pendingCreateCollection,
      setPendingCreateCollection: s.setPendingCreateCollection,
      pendingOpenFile: s.pendingOpenFile,
      setPendingOpenFile: s.setPendingOpenFile,
      collections: s.collections,
      fetchCollections: s.fetchCollections,
    }))
  )
  const [createOpen, setCreateOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [renameTarget, setRenameTarget] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<DbTab>(() => loadInitialDbTab())
  /**
   * Last Overview/Files/Timeline selection for the pill Tabs.
   * Settings (config) is opened via the ⋮ button — not a Tabs value.
   * Base UI Tabs auto-resets to the first tab when `value` is undefined /
   * unmatched and fires onValueChange, which would undo open-config.
   */
  const [contentTab, setContentTab] = useState<"info" | "files" | "timeline">(
    () => {
      const t = loadInitialDbTab()
      return t === "config" ? "info" : t
    }
  )
  /** Tabs visited at least once — keepMounted after first open for snappy re-switch. */
  const [visitedTabs, setVisitedTabs] = useState<Set<DbTab>>(
    () => new Set([loadInitialDbTab()])
  )
  /**
   * Sequential panel motion (no dual semi-transparent stack = no ghost):
   * 1) fade out stageTab  2) swap stageTab to activeTab  3) fade in
   * Same path for Overview / Files / Timeline / Config.
   */
  const [stageTab, setStageTab] = useState<DbTab>(() => loadInitialDbTab())
  const [stagePhase, setStagePhase] = useState<"shown" | "hiding">("shown")
  const panelMotionGen = useRef(0)
  const PANEL_OUT_MS = 140
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
    if (next === "info" || next === "files" || next === "timeline") {
      setContentTab(next)
    }
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

  /**
   * Collection switch: keep stage + tab panels mounted (no opacity-0, no
   * visitedTabs wipe — that remounted InfoPanel and flashed white).
   * Children update via collection / collectionId props.
   */
  useEffect(() => {
    setStageTab(activeTab)
    setStagePhase("shown")
    panelMotionGen.current += 1
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on collection change
  }, [activeCollection])

  // Sequential fade: hide current paint → swap → show next (never two UIs at once).
  useEffect(() => {
    if (activeTab === stageTab && stagePhase === "shown") return

    // Already on target but still marked hiding — finish show
    if (activeTab === stageTab && stagePhase === "hiding") {
      setStagePhase("shown")
      return
    }

    const gen = ++panelMotionGen.current
    setStagePhase("hiding")
    const t = window.setTimeout(() => {
      if (panelMotionGen.current !== gen) return
      setStageTab(activeTab)
      setStagePhase("shown")
    }, PANEL_OUT_MS)
    return () => {
      window.clearTimeout(t)
    }
  }, [activeTab, stageTab, stagePhase])

  // Close float when leaving rail tabs; always close on Config (FAB hidden there)
  useEffect(() => {
    if (
      quickChatOpen &&
      (activeTab === "config" ||
        (activeTab !== "info" && activeTab !== "files"))
    ) {
      setQuickChatOpen(false)
    }
  }, [activeTab, quickChatOpen])

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

  // From note editor: switch to Files tab → Notes folder → open file detail
  useEffect(() => {
    const handler = async (ev: Event) => {
      const detail = (
        ev as CustomEvent<{
          collectionId?: string
          fileId?: string
          noteId?: string
        }>
      ).detail
      const fileId = detail?.fileId
      if (!fileId) return
      const col = detail.collectionId || activeCollection
      if (!col) return

      handleTabChange("files")
      try {
        const store = useFileMgmtStore.getState()
        await store.fetchFolderTree(col)
        const tree = useFileMgmtStore.getState().folderTree
        type TreeN = {
          name: string
          is_system?: boolean
          folder_id: string
          children?: TreeN[]
        }
        const findNotes = (nodes: TreeN[]): string | null => {
          for (const n of nodes) {
            if (n.name === "Notes" && n.is_system) return n.folder_id
            const hit = findNotes(n.children || [])
            if (hit) return hit
          }
          return null
        }
        const notesFolderId = findNotes(tree as TreeN[])
        if (notesFolderId) {
          await store.selectFolder(col, notesFolderId)
        } else {
          await store.refreshFiles(col, { silent: true })
        }
      } catch {
        /* still open detail */
      }
      const source = detail.noteId
        ? `__note__:${detail.noteId}`
        : `__file__:${fileId}`
      setDetailOpen({ fileId, source })
    }
    window.addEventListener("open-note-file-in-folder", handler)
    return () => window.removeEventListener("open-note-file-in-folder", handler)
  }, [activeCollection, handleTabChange])

  // From Meeting ingest UI: Files tab → Meeting folder → open file detail
  useEffect(() => {
    const handler = async (ev: Event) => {
      const detail = (
        ev as CustomEvent<{
          collectionId?: string
          fileId?: string
          meetingId?: string
          tabId?: string
        }>
      ).detail
      const fileId = detail?.fileId
      if (!fileId) return
      const col = detail.collectionId || activeCollection
      if (!col) return

      if (detail.collectionId) {
        setActiveCollection(detail.collectionId)
      }
      handleTabChange("files")
      try {
        const store = useFileMgmtStore.getState()
        await store.fetchFolderTree(col)
        const tree = useFileMgmtStore.getState().folderTree
        type TreeN = {
          name: string
          is_system?: boolean
          folder_id: string
          children?: TreeN[]
        }
        const findMeeting = (nodes: TreeN[]): string | null => {
          for (const n of nodes) {
            if (n.name === "Meeting" && n.is_system) return n.folder_id
            const hit = findMeeting(n.children || [])
            if (hit) return hit
          }
          return null
        }
        const meetingFolderId = findMeeting(tree as TreeN[])
        if (meetingFolderId) {
          await store.selectFolder(col, meetingFolderId)
        } else {
          await store.refreshFiles(col, { silent: true })
        }
      } catch {
        /* still open detail */
      }
      const source =
        detail.meetingId && detail.tabId
          ? `__meeting__:${detail.meetingId}:${detail.tabId}`
          : `__file__:${fileId}`
      setDetailOpen({ fileId, source })
    }
    window.addEventListener("open-meeting-file-in-folder", handler)
    return () =>
      window.removeEventListener("open-meeting-file-in-folder", handler)
  }, [activeCollection, handleTabChange, setActiveCollection])

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

  // Pause network polling while Collection tab is not the active sidebar view
  useEffect(() => {
    if (!active) {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }
      return
    }
    fetchFiles()
    fetchTasks()
    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }
    }
  }, [fetchFiles, fetchTasks, activeCollection, active])

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

  const collectionDisplayName =
    collections.find((c) => c.id === activeCollection)?.name || activeCollection || ""

  /**
   * Premium pill tabs — Overview | Files | Timeline only.
   * Settings is a ⋮ control on the title row (not part of the tab bar).
   * Always mounted so the sliding pill can animate between views.
   */
  const collectionTabs = (
    <div className="flex items-center gap-2 min-w-0 flex-wrap">
      <Tabs
        value={contentTab}
        onValueChange={handleTabChange}
        className="min-w-0"
      >
        <TabsList
          className={cn(
            "pm-tabs !h-auto w-fit bg-transparent p-0 gap-1 border-0 rounded-none",
            "relative shrink-0 items-center isolate"
          )}
        >
          <TabsIndicator
            renderBeforeHydration
            className="pm-tabs-indicator"
          />
          {(
            [
              ["info", "Overview"],
              ["files", "Files"],
              ["timeline", "Timeline"],
            ] as const
          ).map(([value, label]) => (
            <TabsTrigger
              key={value}
              value={value}
              // While Settings is open, contentTab may still equal this value —
              // onValueChange won't re-fire; force leave Settings on click.
              onClick={() => {
                if (activeTab === "config") handleTabChange(value)
              }}
              className={cn(
                "pm-vtab relative z-[1]",
                "!h-auto min-h-0",
                // Dim tab pill while Settings is open (not a tab)
                activeTab === "config" && "opacity-60",
                "data-[state=active]:shadow-none data-active:bg-transparent",
                "after:!opacity-0 after:!content-none",
                "inline-flex items-center justify-center",
                "transition-colors duration-200 ease-out"
              )}
              style={{ borderColor: "transparent" }}
            >
              {label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {/* Files tab only: All Files flat list */}
      {activeTab === "files" && (
        <Button
          type="button"
          variant="ghost"
          size="xs"
          onClick={() => setClassicFilesOpen(true)}
          title="All Files"
          className="shrink-0 gap-1"
        >
          <List className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">All Files</span>
        </Button>
      )}
    </div>
  )

  return (
    /* overflow visible so stage + collections soft shadows are not clipped */
    <div className="pm-shell-workspace h-full flex gap-3 min-h-0 min-w-0">
      <CollectionList
        collections={collections}
        activeCollection={activeCollection}
        onSelect={setActiveCollection}
        onCreate={() => setCreateOpen(true)}
        onDelete={setDeleteTarget}
        onRename={setRenameTarget}
      />

      <div className="pm-shell-stage-slot flex-1 min-h-0 min-w-0">
        {activeCollection ? (
          /* Big soft stage — stable mount (no key=collectionId remount flash) */
          <div className="pm-stage pm-float-surface h-full min-h-0 flex flex-col overflow-hidden">
            {/* Collection header — title left, Settings ⋮ far right (not in tab bar) */}
            <header
              className="pm-collection-chrome shrink-0 min-w-0 flex items-start justify-between gap-3"
              style={{ marginBottom: "var(--pm-ov-gap, 14px)" }}
            >
              <div className="min-w-0">
                <p className="pm-label mb-0.5">Collection</p>
                <h1 className="pm-display truncate">{collectionDisplayName}</h1>
              </div>
              <button
                type="button"
                title="Collection settings"
                aria-label="Collection settings"
                aria-pressed={activeTab === "config"}
                onClick={() => {
                  // Toggle: open config, or close back to last content tab
                  if (activeTab === "config") handleTabChange(contentTab)
                  else handleTabChange("config")
                }}
                className={cn(
                  "shrink-0 mt-1 inline-flex h-8 w-8 items-center justify-center rounded-full",
                  "transition-colors duration-150",
                  activeTab === "config"
                    ? "text-[var(--pm-green)] bg-[var(--pm-green-soft)]"
                    : "text-[var(--pm-muted)] hover:text-[var(--pm-ink)] hover:bg-black/[0.04]"
                )}
              >
                <MoreVertical className="h-4 w-4" />
              </button>
            </header>

            {/* overflow-visible: QC fab park sits top: -40px above panel */}
            <div className="pm-collection-body flex-1 flex flex-col min-h-0 min-w-0 overflow-visible">
              {/*
                Single always-mounted tab bar (Overview | Files | Timeline).
                Must NOT move between InfoPanel chrome and this shell — remounting
                kills TabsIndicator slide (new instance, no left/width tween).
                value={contentTab} so Config open still keeps last pill for slide-back.
              */}
              <div
                className="pm-tabs-shell is-in shrink-0 min-w-0"
                style={{ marginBottom: "var(--pm-ov-gap, 14px)" }}
              >
                {collectionTabs}
              </div>

              {/*
                Keep-alive surfaces + sequential fade (out → swap → in).
                Same motion for Overview / Files / Timeline / Config.
              */}
              {/*
                overflow-visible so QC diamond park (top: -40px) is not clipped.
                transparent — never paint canvas gray over .pm-stage cream
              */}
              <div className="relative flex-1 min-h-0 min-w-0 bg-transparent isolate overflow-visible">
                {(
                  [
                    ["info", visitedTabs.has("info")] as const,
                    ["files", visitedTabs.has("files")] as const,
                    ["timeline", visitedTabs.has("timeline")] as const,
                    ["config", visitedTabs.has("config")] as const,
                  ] as const
                ).map(([tab, visited]) => {
                  if (!visited) return null
                  const isStage = tab === stageTab
                  const isInteractive =
                    tab === activeTab &&
                    stageTab === activeTab &&
                    stagePhase === "shown"
                  const phaseClass =
                    isStage && stagePhase === "shown"
                      ? "is-active z-20"
                      : isStage && stagePhase === "hiding"
                        ? "is-exiting z-20"
                        : "is-idle z-0 select-none"
                  return (
                    <div
                      key={tab}
                      className={cn(
                        "absolute inset-0 flex flex-col bg-transparent",
                        "pm-panel pm-panel-fade",
                        /*
                         * Overview + Files: overflow visible so rail card shadows paint.
                         * Diamond is parked outside panels (data-pm-qc-fab-anchor).
                         */
                        tab === "info" || tab === "files"
                          ? "overflow-visible"
                          : "overflow-hidden",
                        phaseClass,
                        !isInteractive && "pointer-events-none"
                      )}
                      aria-hidden={!isInteractive}
                      inert={!isInteractive ? true : undefined}
                    >
                      {tab === "info" && (
                        <div className="absolute inset-0 flex min-h-0 flex-col overflow-visible p-0.5">
                          <InfoPanel
                            collection={activeCollection}
                            railCovered={quickChatOpen}
                          />
                        </div>
                      )}
                      {tab === "files" && (
                        <div className="absolute inset-0 flex min-h-0 flex-col overflow-visible p-0.5">
                          <FolderView
                            collectionId={activeCollection}
                            railCovered={quickChatOpen}
                          />
                        </div>
                      )}
                      {tab === "timeline" && (
                        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
                          <TimelineView
                            collectionId={activeCollection}
                            /* Live only when fully shown — avoid pan/measure mid-fade */
                            active={
                              active &&
                              activeTab === "timeline" &&
                              stageTab === "timeline" &&
                              stagePhase === "shown"
                            }
                          />
                        </div>
                      )}
                      {tab === "config" && (
                        <ScrollArea className="h-full">
                          <div className="pr-1 pb-8">
                            <CollectionConfig collection={activeCollection} />
                          </div>
                        </ScrollArea>
                      )}
                    </div>
                  )
                })}

                {/*
                  Stable QC diamond park — same top-right of content for
                  Overview / Files / Timeline. Config hides the icon (fabVisible).
                */}
                <div
                  className="pm-qc-fab-park"
                  data-pm-qc-fab-anchor
                  aria-hidden
                />
              </div>
            </div>
          </div>
        ) : (
          <div className="pm-stage h-full flex items-center justify-center text-muted-foreground pm-collection-enter">
            <div className="pm-collection-chrome text-center">
              <p className="text-sm t-body-family">Select a collection or create one</p>
            </div>
          </div>
        )}
      </div>

      {/*
        Quick Chat: FAB parks on stage (stable). Float still fills right rail
        on Overview/Files. Hidden on Config.
      */}
      {activeCollection && (
        <QuickChat
          collectionId={activeCollection}
          collectionName={
            collections.find((c) => c.id === activeCollection)?.name ||
            activeCollection
          }
          open={quickChatOpen}
          onOpen={() => {
            // Files: Messages curtain may be width-0 — expand before QC portals in
            if (activeTab === "files") {
              useFileMgmtStore.getState().setMessageSidebarOpen(true)
            }
            setQuickChatOpen(true)
          }}
          onClose={() => setQuickChatOpen(false)}
          /* Rail float cover only where right rail exists */
          railActive={activeTab === "info" || activeTab === "files"}
          railKey={activeTab}
          /* Hide diamond on Collection Settings */
          fabVisible={activeTab !== "config"}
          files={files}
          onSourceClick={(source) => {
            // Open file detail only — do not switch away from Overview (or current tab)
            const fileId = source.startsWith("__file__:")
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
        <DialogContent className="pm-dialog max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete File</DialogTitle>
          </DialogHeader>
          <p className="pm-dialog-body">
            Are you sure you want to delete{" "}
            <span className="font-medium text-[var(--pm-ink)] truncate max-w-[200px] inline-block align-bottom">
              {deleteFileDisplay}
            </span>
            ? This will remove all its chunks from the database.
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="ghost"
              onClick={() => setDeleteFileTarget(null)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive-solid"
              onClick={handleDeleteFile}
            >
              Delete
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
