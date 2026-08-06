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
          overlayClassName="pm-dialog-overlay--silk"
          className={cn(
            "pm-dialog pm-dialog--silk pm-all-files",
            "!flex flex-col gap-0 p-0 overflow-hidden min-h-0",
            "sm:max-w-3xl w-[min(920px,calc(100%-2rem))]",
            "h-[min(76vh,760px)] max-h-[min(76vh,760px)]",
            "animate-none data-open:animate-none data-closed:animate-none"
          )}
        >
          {/* Header — Premium chrome (Geist label + quiet meta) */}
          <DialogHeader className="pm-all-files-header shrink-0">
            <DialogTitle className="pm-all-files-title">
              <List className="h-4 w-4 shrink-0 text-[var(--pm-muted)]" strokeWidth={1.75} />
              All Files
            </DialogTitle>
            <p className="pm-all-files-sub">
              Flat list of every document in this collection.
            </p>
          </DialogHeader>

          {coverage ? (
            <div className="pm-all-files-coverage">
              <span className="pm-label">Coverage ·</span>
              <span className="pm-meta">{coverage}</span>
            </div>
          ) : null}

          {/* Body: soft list stage + pinned old-versions panel */}
          <div className="pm-all-files-body flex-1 min-h-0 flex flex-col overflow-hidden">
            <div className="pm-all-files-list flex-1 min-h-0 overflow-y-auto overscroll-contain">
              {initialLoading && files.length === 0 ? (
                <div className="pm-all-files-empty">
                  <Loader2 className="h-4 w-4 animate-spin text-[var(--pm-faint)]" />
                  <span className="pm-meta">Loading…</span>
                </div>
              ) : files.length === 0 ? (
                <div className="pm-all-files-empty">
                  <p className="pm-meta">No files yet</p>
                </div>
              ) : (
                <div className="pm-all-files-rows">
                  {files.map((file) => {
                    const ingesting =
                      !!file.file_id && !!ingestingFiles[file.file_id]
                    return (
                      <div
                        key={file.source}
                        className={cn(
                          "pm-all-files-row group",
                          (ingesting || deletingSource === file.source) &&
                            "is-disabled"
                        )}
                        onClick={() => {
                          if (ingesting) {
                            toast.info(
                              "File is still ingesting — open it when progress finishes."
                            )
                            return
                          }
                          setDetailOpen({
                            fileId: file.file_id || null,
                            source: file.source || null,
                          })
                        }}
                      >
                        <div className="pm-all-files-row-main">
                          <div className="pm-all-files-tags">
                            {file.file_type === "note" && (
                              <span className="pm-files-tag">Note</span>
                            )}
                            {file.has_meeting && (
                              <span className="pm-files-tag">Meeting</span>
                            )}
                            {!file.file_type && !file.has_meeting && (
                              <FileTypeIcon
                                source={{
                                  filename: file.display_name || file.source,
                                }}
                                className="h-3.5 w-3.5 text-[var(--pm-muted)]"
                              />
                            )}
                          </div>
                          <span className="pm-all-files-name truncate">
                            {file.display_name || file.source}
                          </span>
                        </div>
                        <span className="pm-all-files-chunks pm-meta tabular-nums shrink-0">
                          {file.chunk_count} chunks
                        </span>
                        {file.has_summary !== null && (
                          <button
                            type="button"
                            className={cn(
                              "pm-all-files-def",
                              file.include_in_summary !== false && "is-on"
                            )}
                            onClick={(e) => void handleToggleDefinitive(file, e)}
                            title={
                              file.include_in_summary !== false
                                ? "Included in collection summary — click to exclude"
                                : "Not included in collection summary — click to include"
                            }
                          >
                            {generatingSummaries.has(file.source) ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : (
                              <span className="pm-all-files-check" aria-hidden>
                                {file.include_in_summary !== false ? "✓" : ""}
                              </span>
                            )}
                            <span>Definitive</span>
                          </button>
                        )}
                        <button
                          type="button"
                          className="pm-all-files-del"
                          onClick={(e) => {
                            e.stopPropagation()
                            setDeleteFileTarget(file.source)
                          }}
                        >
                          Delete
                        </button>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>

            {/* Old versions — soft nested panel */}
            <div className="pm-all-files-history shrink-0">
              <button
                type="button"
                aria-expanded={oldVersionsOpen}
                className="pm-all-files-history-toggle"
                onClick={() => setOldVersionsOpen((v) => !v)}
              >
                <ChevronRight
                  className={cn(
                    "h-3.5 w-3.5 shrink-0 transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]",
                    oldVersionsOpen && "rotate-90"
                  )}
                />
                <History className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
                <span className="pm-label">Old versions</span>
                <span className="pm-all-files-history-count">
                  {oldVersionsLoading ? "…" : oldVersions.length}
                </span>
              </button>

              <div
                className={cn(
                  "grid transition-[grid-template-rows,opacity] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]",
                  oldVersionsOpen
                    ? "grid-rows-[1fr] opacity-100"
                    : "grid-rows-[0fr] opacity-0"
                )}
              >
                <div className="min-h-0 overflow-hidden">
                  <div
                    className="pm-all-files-history-list"
                    inert={!oldVersionsOpen ? true : undefined}
                  >
                    {oldVersionsLoading ? (
                      <div className="pm-all-files-empty !py-4">
                        <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--pm-faint)]" />
                        <span className="pm-meta">Loading…</span>
                      </div>
                    ) : oldVersions.length === 0 ? (
                      <p className="pm-meta px-1 py-2">
                        No archived versions yet. Updating a file keeps the
                        previous blob as an old version (not moved into Archive).
                      </p>
                    ) : (
                      <div className="pm-all-files-rows">
                        {oldVersions.map((ov) => {
                          const blobMissing = ov.blob_available === false
                          return (
                            <div
                              key={ov.version_id}
                              className={cn(
                                "pm-all-files-row is-history",
                                blobMissing && "is-disabled"
                              )}
                              onClick={() =>
                                setDetailOpen({
                                  fileId: ov.file_id,
                                  source: `__file__:${ov.file_id}`,
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
                                className="h-3.5 w-3.5 shrink-0 text-[var(--pm-muted)]"
                              />
                              <div className="flex-1 min-w-0">
                                <div className="truncate pm-all-files-name">
                                  <span className="pm-meta tabular-nums mr-1.5">
                                    v{ov.version_no}
                                  </span>
                                  {ov.filename ||
                                    ov.storage_file_id ||
                                    `version ${ov.version_no}`}
                                  {blobMissing ? (
                                    <span className="ml-1.5 pm-meta text-[var(--pm-danger)]">
                                      · blob missing
                                    </span>
                                  ) : null}
                                </div>
                                <div className="truncate pm-meta">
                                  history of{" "}
                                  {ov.current_display_name ||
                                    ov.current_filename ||
                                    ov.file_id.slice(0, 8)}
                                  {ov.commit_message
                                    ? ` · ${ov.commit_message}`
                                    : ""}
                                </div>
                              </div>
                              <span className="shrink-0 pm-meta tabular-nums hidden sm:inline">
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
        <DialogContent
          overlayClassName="pm-dialog-overlay--silk"
          className="pm-dialog pm-dialog--silk max-w-sm animate-none data-open:animate-none data-closed:animate-none"
        >
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
