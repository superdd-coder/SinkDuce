/**
 * All Files dialog — flat document list.
 * Opens the unified FileMgmtDetailDialog for file detail.
 * Bottom collapsible section lists non-current (old) versions —
 * version-level history only, not the system Archive folder.
 */
import { useState, useEffect, useCallback, useRef } from "react"
import { ChevronRight, History, Loader2, List } from "lucide-react"
import { toast } from "sonner"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import {
  getCollectionConfig,
  getFiles,
  deleteDocument,
  getDocSummary,
  setDocSummaryInclude,
  generateDocSummary,
  type FileListItem,
} from "@/api/client"
import { listOldVersions } from "@/api/file-mgmt"
import type { OldVersion } from "@/types/file-mgmt"
import { FileMgmtDetailDialog } from "@/components/file-mgmt/file-detail"
import { FileTypeIcon } from "@/components/file-mgmt/file-type-icon"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { cn } from "@/lib/utils"

function formatTime(iso: string | null | undefined): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

interface ClassicFilesDialogProps {
  collectionId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ClassicFilesDialog({
  collectionId,
  open,
  onOpenChange,
}: ClassicFilesDialogProps) {
  const [files, setFiles] = useState<FileListItem[]>([])
  /** Full-list spinner only on first load (empty list); never on silent refresh. */
  const [initialLoading, setInitialLoading] = useState(false)
  const [coverage, setCoverage] = useState("")
  const [generatingSummaries, setGeneratingSummaries] = useState<Set<string>>(
    () => new Set()
  )
  const [deletingSource, setDeletingSource] = useState<string | null>(null)

  /** Open detail with managed id + optional historical version focus. */
  const [detailOpen, setDetailOpen] = useState<{
    fileId?: string | null
    source?: string | null
    versionId?: string | null
    storageFileId?: string | null
  } | null>(null)
  const ingestingFiles = useFileMgmtStore((s) => s.ingestingFiles)
  const [deleteFileTarget, setDeleteFileTarget] = useState<string | null>(null)

  /** Non-current versions (version-level archive history). */
  const [oldVersions, setOldVersions] = useState<OldVersion[]>([])
  const [oldVersionsLoading, setOldVersionsLoading] = useState(false)
  const [oldVersionsOpen, setOldVersionsOpen] = useState(false)

  const filesTokenRef = useRef(0)
  const oldVersionsTokenRef = useRef(0)
  const hasLoadedRef = useRef(false)

  const deleteFileDisplay =
    files.find((f) => f.source === deleteFileTarget)?.display_name ||
    deleteFileTarget

  const fetchFiles = useCallback(
    async (opts?: { silent?: boolean }) => {
      if (!collectionId) return
      const silent = opts?.silent ?? hasLoadedRef.current
      const token = ++filesTokenRef.current
      if (!silent) setInitialLoading(true)
      try {
        const res = await getFiles(collectionId)
        if (token !== filesTokenRef.current) return
        setFiles(res.files)
        hasLoadedRef.current = true
      } catch {
        if (token !== filesTokenRef.current) return
        if (!silent) setFiles([])
      } finally {
        if (token === filesTokenRef.current && !silent) {
          setInitialLoading(false)
        }
      }
    },
    [collectionId]
  )

  const fetchOldVersions = useCallback(async () => {
    if (!collectionId) return
    const token = ++oldVersionsTokenRef.current
    setOldVersionsLoading(true)
    try {
      const rows = await listOldVersions(collectionId)
      if (token !== oldVersionsTokenRef.current) return
      setOldVersions(rows)
    } catch {
      if (token !== oldVersionsTokenRef.current) return
      setOldVersions([])
    } finally {
      if (token === oldVersionsTokenRef.current) {
        setOldVersionsLoading(false)
      }
    }
  }, [collectionId])

  useEffect(() => {
    if (!open || !collectionId) return
    hasLoadedRef.current = false
    setOldVersionsOpen(false)
    void fetchFiles({ silent: false })
    void fetchOldVersions()
    getCollectionConfig(collectionId)
      .then((cfg) => {
        setCoverage((cfg.coverage as string) || "")
      })
      .catch(() => setCoverage(""))
  }, [open, collectionId, fetchFiles, fetchOldVersions])

  const handleDeleteFile = async () => {
    if (!deleteFileTarget) return
    const src = deleteFileTarget
    setDeleteFileTarget(null)
    setDeletingSource(src)
    setFiles((prev) => prev.filter((f) => f.source !== src))
    try {
      await deleteDocument(collectionId, src)
      void fetchFiles({ silent: true })
    } catch {
      toast.error("Failed to delete file")
      void fetchFiles({ silent: true })
    } finally {
      setDeletingSource(null)
    }
  }

  const handleToggleDefinitive = async (
    file: FileListItem,
    e: React.MouseEvent
  ) => {
    e.stopPropagation()
    const src = file.source
    const currentInclude = file.include_in_summary !== false

    if (!file.has_summary) {
      setGeneratingSummaries((prev) => new Set(prev).add(src))
      try {
        await generateDocSummary(collectionId, src)
        const start = Date.now()
        while (Date.now() - start < 300_000) {
          await new Promise((r) => setTimeout(r, 2000))
          try {
            const ds = await getDocSummary(collectionId, src)
            if (ds) {
              setFiles((prev) =>
                prev.map((f) =>
                  f.source === src
                    ? { ...f, has_summary: true, include_in_summary: true }
                    : f
                )
              )
              break
            }
          } catch {
            /* still generating */
          }
        }
      } catch (err) {
        toast.error(
          `Summary generation failed: ${err instanceof Error ? err.message : String(err)}`
        )
      } finally {
        setGeneratingSummaries((prev) => {
          const next = new Set(prev)
          next.delete(src)
          return next
        })
      }
    } else {
      const newInclude = !currentInclude
      setFiles((prev) =>
        prev.map((f) =>
          f.source === src ? { ...f, include_in_summary: newInclude } : f
        )
      )
      try {
        await setDocSummaryInclude(collectionId, src, newInclude)
      } catch (err) {
        setFiles((prev) =>
          prev.map((f) =>
            f.source === src
              ? { ...f, include_in_summary: currentInclude }
              : f
          )
        )
        toast.error(
          `Failed: ${err instanceof Error ? err.message : String(err)}`
        )
      }
    }
  }

  const handleOpenChange = (next: boolean) => {
    onOpenChange(next)
    if (!next) {
      void useFileMgmtStore.getState().fetchFolderTree(collectionId)
      void useFileMgmtStore.getState().refreshFiles(collectionId)
      setDetailOpen(null)
      setDeleteFileTarget(null)
      setOldVersionsOpen(false)
      hasLoadedRef.current = false
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent
          className={cn(
            "pm-dialog !flex flex-col gap-3 p-5 overflow-hidden min-h-0",
            "sm:max-w-3xl w-[min(920px,calc(100%-2rem))]",
            "h-[min(72vh,720px)] max-h-[min(72vh,720px)]"
          )}
        >
          <DialogHeader className="shrink-0 pr-8">
            <DialogTitle className="flex items-center gap-2 text-base font-normal">
              <List className="h-4 w-4 text-muted-foreground" />
              All Files
            </DialogTitle>
            <p className="text-[11px] text-muted-foreground">
              Flat list of every document in this collection.
            </p>
          </DialogHeader>

          {coverage ? (
            <div className="shrink-0 text-[11px] leading-relaxed px-3 py-2 border border-dashed border-border bg-muted/30 t-sans-family">
              <span className="font-medium uppercase tracking-[0.1em] text-muted-foreground/70">
                Coverage ·{" "}
              </span>
              <span className="text-muted-foreground">{coverage}</span>
            </div>
          ) : null}

          {/* Two panes: current files scroll independently; old versions stay pinned at bottom */}
          <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
            <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain pr-1">
              {initialLoading && files.length === 0 ? (
                <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading…
                </div>
              ) : files.length === 0 ? (
                <p className="text-sm text-muted-foreground py-6">No files yet</p>
              ) : (
                <div className="space-y-0">
                  {files.map((file) => {
                    const ingesting =
                      !!file.file_id && !!ingestingFiles[file.file_id]
                    return (
                    <div
                      key={file.source}
                      className={cn(
                        "flex items-center gap-3 py-2.5 text-sm border-b border-dashed border-border text-foreground transition-opacity group",
                        ingesting
                          ? "opacity-50 cursor-not-allowed"
                          : "cursor-pointer",
                        deletingSource === file.source &&
                          "opacity-50 pointer-events-none"
                      )}
                      onClick={() => {
                        if (ingesting) {
                          toast.info(
                            "File is still ingesting — open it when progress finishes."
                          )
                          return
                        }
                        setDetailOpen({
                          // fileId → right panel (paths/versions)
                          fileId: file.file_id || null,
                          // document source → chunks/source/summary (__meeting__/__note__)
                          source: file.source || null,
                        })
                      }}
                    >
                      <div className="flex-1 min-w-0 flex items-center gap-3">
                        <span
                          className="shrink-0 flex items-center"
                          style={{ width: "72px" }}
                        >
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
                        <span className="truncate text-xs">
                          {file.display_name || file.source}
                        </span>
                      </div>
                      <span className="text-[10px] font-medium text-muted-foreground tabular-nums">
                        {file.chunk_count} chunks
                      </span>
                      {file.has_summary !== null && (
                        <button
                          type="button"
                          className="shrink-0 flex items-center gap-1.5 cursor-pointer"
                          style={{ background: "none", border: "none" }}
                          onClick={(e) => void handleToggleDefinitive(file, e)}
                          title={
                            file.include_in_summary !== false
                              ? "Included in collection summary — click to exclude"
                              : "Not included in collection summary — click to include"
                          }
                        >
                          {generatingSummaries.has(file.source) ? (
                            <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                          ) : (
                            <span
                              className={`flex items-center justify-center w-3.5 h-3.5 rounded-sm border transition-colors ${
                                file.include_in_summary !== false
                                  ? "border-primary bg-primary text-primary-foreground"
                                  : "border-muted-foreground/30 bg-transparent"
                              }`}
                            >
                              {file.include_in_summary !== false && (
                                <svg
                                  className="w-2.5 h-2.5"
                                  fill="none"
                                  viewBox="0 0 24 24"
                                  stroke="currentColor"
                                  strokeWidth={3}
                                >
                                  <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    d="M5 13l4 4L19 7"
                                  />
                                </svg>
                              )}
                            </span>
                          )}
                          <span
                            className={`text-[10px] font-medium uppercase tracking-[0.1em] ${
                              file.include_in_summary !== false
                                ? "text-foreground"
                                : "text-muted-foreground"
                            }`}
                          >
                            Definitive
                          </span>
                        </button>
                      )}
                      <button
                        type="button"
                        className="text-[10px] opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer text-muted-foreground"
                        style={{ background: "none", border: "none" }}
                        onClick={(e) => {
                          e.stopPropagation()
                          setDeleteFileTarget(file.source)
                        }}
                      >
                        Delete
                      </button>
                    </div>
                  )})}
                </div>
              )}
            </div>

            {/* Pinned bottom: collapsible old versions (version history, not Archive folder) */}
            <div className="shrink-0 border-t border-border bg-background pt-2 relative z-10">
              <button
                type="button"
                aria-expanded={oldVersionsOpen}
                className="w-full shrink-0 flex items-center gap-2 py-1.5 text-left text-xs text-muted-foreground hover:text-foreground transition-colors"
                onClick={() => setOldVersionsOpen((v) => !v)}
              >
                <ChevronRight
                  className={cn(
                    "h-3.5 w-3.5 shrink-0 transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
                    oldVersionsOpen && "rotate-90"
                  )}
                />
                <History className="h-3.5 w-3.5 shrink-0" />
                <span className="font-medium uppercase tracking-[0.08em] text-[10px]">
                  Old versions
                </span>
                <span className="tabular-nums text-[10px] text-muted-foreground/80">
                  {oldVersionsLoading ? "…" : oldVersions.length}
                </span>
              </button>

              {/* Height animate via grid 0fr → 1fr (keeps exit animation smooth) */}
              <div
                className={cn(
                  "grid transition-[grid-template-rows,opacity] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
                  oldVersionsOpen
                    ? "grid-rows-[1fr] opacity-100"
                    : "grid-rows-[0fr] opacity-0"
                )}
              >
                <div className="min-h-0 overflow-hidden">
                  <div
                    className={cn(
                      "overflow-y-auto overscroll-contain pb-1 bg-background max-h-[min(32vh,260px)]",
                      "transition-[transform] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
                      oldVersionsOpen
                        ? "translate-y-0"
                        : "-translate-y-1"
                    )}
                    // Keep panel non-interactive while collapsed
                    inert={!oldVersionsOpen ? true : undefined}
                  >
                    {oldVersionsLoading ? (
                      <div className="flex items-center gap-2 py-3 text-xs text-muted-foreground">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        Loading…
                      </div>
                    ) : oldVersions.length === 0 ? (
                      <p className="text-xs text-muted-foreground py-2 pl-6">
                        No archived versions yet. Updating a file keeps the
                        previous blob as an old version (not moved into Archive).
                      </p>
                    ) : (
                      <div className="space-y-0">
                        {oldVersions.map((ov) => {
                          const blobMissing = ov.blob_available === false
                          return (
                          <div
                            key={ov.version_id}
                            className={cn(
                              "flex items-center gap-2.5 py-2 pl-1 pr-1 text-xs border-b border-dashed border-border/70 cursor-pointer hover:bg-muted/40 transition-colors bg-background",
                              blobMissing && "opacity-70"
                            )}
                            onClick={() =>
                              setDetailOpen({
                                fileId: ov.file_id,
                                source: `__file__:${ov.file_id}`,
                                // Focus this historical version (not latest)
                                versionId: ov.version_id,
                                storageFileId: ov.storage_file_id,
                              })
                            }
                            title={
                              blobMissing
                                ? "Blob missing on disk — Raw cannot show this version"
                                : ov.commit_message ||
                                  `${ov.filename || "version"} of ${ov.current_display_name || ov.current_filename}`
                            }
                          >
                            <FileTypeIcon
                              source={{
                                filename: ov.filename,
                                original_ext: ov.original_ext,
                              }}
                              className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                            />
                            <div className="flex-1 min-w-0">
                              <div className="truncate text-foreground/90">
                                {/* Always this history row's blob — never current label */}
                                <span className="text-muted-foreground tabular-nums mr-1.5">
                                  v{ov.version_no}
                                </span>
                                {ov.filename ||
                                  ov.storage_file_id ||
                                  `version ${ov.version_no}`}
                                {blobMissing ? (
                                  <span className="ml-1.5 text-[10px] text-amber-600 dark:text-amber-500">
                                    · blob missing
                                  </span>
                                ) : null}
                              </div>
                              <div className="truncate text-[10px] text-muted-foreground">
                                history of{" "}
                                {ov.current_display_name ||
                                  ov.current_filename ||
                                  ov.file_id.slice(0, 8)}
                                {ov.commit_message
                                  ? ` · ${ov.commit_message}`
                                  : ""}
                              </div>
                            </div>
                            <span className="shrink-0 text-[10px] text-muted-foreground/80 tabular-nums hidden sm:inline">
                              {formatTime(ov.created_at)}
                            </span>
                          </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <FileMgmtDetailDialog
        // Remount when switching current vs historical version of same file
        key={
          detailOpen
            ? `${detailOpen.fileId || detailOpen.source || "x"}:${detailOpen.versionId || "current"}`
            : "closed"
        }
        collectionId={collectionId}
        fileId={detailOpen?.fileId}
        source={detailOpen?.source}
        versionId={detailOpen?.versionId}
        storageFileId={detailOpen?.storageFileId}
        open={!!detailOpen}
        onOpenChange={(v) => {
          if (!v) {
            setDetailOpen(null)
            void fetchFiles({ silent: true })
            void fetchOldVersions()
          }
        }}
        onDeleted={() => {
          // Optimistic remove so All Files does not flash a stale row
          const src = detailOpen?.source
          const fid = detailOpen?.fileId
          setDetailOpen(null)
          if (src || fid) {
            setFiles((prev) =>
              prev.filter((f) => {
                if (src && f.source === src) return false
                if (fid && f.file_id === fid) return false
                return true
              })
            )
          }
          void fetchFiles({ silent: true })
          void fetchOldVersions()
        }}
      />

      <Dialog
        open={!!deleteFileTarget}
        onOpenChange={(v) => !v && setDeleteFileTarget(null)}
      >
        <DialogContent className="pm-dialog max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete File</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Are you sure you want to delete{" "}
            <span className="font-medium text-foreground truncate max-w-[200px] inline-block align-bottom">
              {deleteFileDisplay}
            </span>
            ? This will remove all its chunks from the database.
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              onClick={() => setDeleteFileTarget(null)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleDeleteFile()}
            >
              Delete
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
