import { useEffect, useMemo, useRef, useState, type DragEvent } from "react"
import { FolderOpen, Paperclip, Star, Upload, XCircle } from "lucide-react"
import { toast } from "sonner"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"
import { cn } from "@/lib/utils"
import type { FileSummary, NodeAttachment } from "@/types/file-mgmt"
import {
  attachFileToNode,
  detachFileFromNode,
  getNameConflict,
  uploadFileToNode,
} from "@/api/file-mgmt"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { FileTreePicker } from "./file-tree-picker"

/** Slide-left + collapse height before parent removes the row */
const ATTACH_EXIT_MS = 280

interface NodeFileAttachProps {
  collectionId: string
  nodeId: string
  /** Parent attach panel open — false resets From Collection draft. */
  active?: boolean
  /** Already attached files (list + tree baseline). */
  attachments?: NodeAttachment[]
  onAttached: () => void
  /** Focused file for external left preview panel (parent renders it). */
  onPreviewFile?: (file: FileSummary | null) => void
  /** Notify parent when collection picker opens/closes. */
  onSelectOpenChange?: (open: boolean) => void
  onOpenFile?: (fileId: string) => void
  onToggleDefinitive?: (
    fileId: string,
    currentDefinitive: boolean,
    version: number
  ) => void
  onDetach?: (fileId: string) => void
}

function setsEqual(a: Set<string>, b: Set<string>): boolean {
  if (a.size !== b.size) return false
  for (const id of a) if (!b.has(id)) return false
  return true
}

export function NodeFileAttach({
  collectionId,
  nodeId,
  active = true,
  attachments = [],
  onAttached,
  onPreviewFile,
  onSelectOpenChange,
  onOpenFile,
  onToggleDefinitive,
  onDetach,
}: NodeFileAttachProps) {
  const t = useT()
  const attachedIds = useMemo(
    () => new Set(attachments.map((a) => a.file_id)),
    [attachments]
  )
  const attachedKey = useMemo(
    () =>
      [...attachments.map((a) => a.file_id)]
        .sort()
        .join(","),
    [attachments]
  )

  const [selectOpen, setSelectOpen] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  /** Draft selection while picking — applied only on Confirm. */
  const [draftIds, setDraftIds] = useState<Set<string>>(() => new Set(attachedIds))
  const [confirming, setConfirming] = useState(false)
  const [uploading, setUploading] = useState(false)
  /** Rows animating out (slide left + collapse); detach fires after exit. */
  const [exitingIds, setExitingIds] = useState<Set<string>>(() => new Set())
  /** Keep row data while exiting if parent already dropped it. */
  const exitingRowsRef = useRef<Map<string, NodeAttachment>>(new Map())
  const exitTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(
    new Map()
  )
  const fileInputRef = useRef<HTMLInputElement>(null)
  const onSelectOpenChangeRef = useRef(onSelectOpenChange)
  onSelectOpenChangeRef.current = onSelectOpenChange
  const onPreviewFileRef = useRef(onPreviewFile)
  onPreviewFileRef.current = onPreviewFile
  const onDetachRef = useRef(onDetach)
  onDetachRef.current = onDetach

  /** List for render: live attachments + any still-exiting rows. */
  const displayAttachments = useMemo(() => {
    const liveIds = new Set(attachments.map((a) => a.file_id))
    const extras: NodeAttachment[] = []
    for (const [id, row] of exitingRowsRef.current) {
      if (exitingIds.has(id) && !liveIds.has(id)) extras.push(row)
    }
    return extras.length ? [...attachments, ...extras] : attachments
  }, [attachments, exitingIds])

  useEffect(() => {
    return () => {
      for (const t of exitTimersRef.current.values()) clearTimeout(t)
      exitTimersRef.current.clear()
    }
  }, [])

  const requestDetach = (att: NodeAttachment) => {
    const fid = att.file_id
    if (exitingIds.has(fid) || exitTimersRef.current.has(fid)) return
    exitingRowsRef.current.set(fid, att)
    setExitingIds((prev) => {
      const next = new Set(prev)
      next.add(fid)
      return next
    })
    const timer = setTimeout(() => {
      exitTimersRef.current.delete(fid)
      onDetachRef.current?.(fid)
      exitingRowsRef.current.delete(fid)
      setExitingIds((prev) => {
        const next = new Set(prev)
        next.delete(fid)
        return next
      })
    }, ATTACH_EXIT_MS)
    exitTimersRef.current.set(fid, timer)
  }

  // Sync draft when server attachments change (and not mid-confirm)
  useEffect(() => {
    if (!confirming) {
      setDraftIds(new Set(attachedIds))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when ids change
  }, [attachedKey])

  // Parent collapsed Attachments → leave picker + discard draft
  useEffect(() => {
    if (!active) {
      setSelectOpen(false)
      setDraftIds(new Set(attachedIds))
      onSelectOpenChangeRef.current?.(false)
      onPreviewFileRef.current?.(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset on active edge
  }, [active])

  const dirty = useMemo(
    () => !setsEqual(draftIds, attachedIds),
    [draftIds, attachedIds]
  )

  const setSelect = (open: boolean) => {
    setSelectOpen(open)
    onSelectOpenChange?.(open)
    if (!open) {
      onPreviewFile?.(null)
      // Discard uncommitted draft when leaving picker
      setDraftIds(new Set(attachedIds))
    } else {
      setDraftIds(new Set(attachedIds))
    }
  }

  /** Local toggle only — no API until Confirm. */
  const toggleDraft = (file: FileSummary) => {
    const fid = file.file_id
    setDraftIds((prev) => {
      const next = new Set(prev)
      if (next.has(fid)) next.delete(fid)
      else next.add(fid)
      return next
    })
  }

  const applyConfirm = async () => {
    if (!dirty || confirming) return
    setConfirming(true)
    const toAttach: string[] = []
    const toDetach: string[] = []
    for (const id of draftIds) {
      if (!attachedIds.has(id)) toAttach.push(id)
    }
    for (const id of attachedIds) {
      if (!draftIds.has(id)) toDetach.push(id)
    }
    try {
      for (const fid of toAttach) {
        await attachFileToNode(collectionId, nodeId, fid)
      }
      for (const fid of toDetach) {
        await detachFileFromNode(collectionId, nodeId, fid)
      }
      const parts: string[] = []
      if (toAttach.length) parts.push(`+${toAttach.length}`)
      if (toDetach.length) parts.push(`−${toDetach.length}`)
      toast.success(
        parts.length
          ? t("fileMgmt.attachmentsUpdatedParts", { parts: parts.join(" · ") })
          : t("fileMgmt.attachmentsUpdated")
      )
      setSelect(false)
      onAttached()
    } catch (err) {
      toast.error(
        t("fileMgmt.failed", { error: formatApiError(err, t) })
      )
      // Keep picker open with draft so user can retry
    } finally {
      setConfirming(false)
    }
  }

  const uploadAndAttach = async (files: FileList | File[]) => {
    const list = Array.from(files)
    if (list.length === 0) return
    setUploading(true)
    try {
      let started = 0
      for (const file of list) {
        const result = await uploadFileToNode(collectionId, nodeId, file)
        if (result.task_id && result.file_id) {
          useFileMgmtStore
            .getState()
            ._startTaskPolling(collectionId, result.task_id, result.file_id)
          started += 1
        }
      }
      toast.success(
        list.length === 1
          ? started
            ? t("fileMgmt.uploadedIngestingName", { name: list[0].name })
            : t("fileMgmt.uploadedAttachedName", { name: list[0].name })
          : started
            ? t("fileMgmt.uploadedNIngesting", { n: list.length, m: started })
            : t("fileMgmt.uploadedN", { n: list.length })
      )
      onAttached()
    } catch (err) {
      const conflict = getNameConflict(err)
      if (conflict) {
        toast.error(t("fileMgmt.fileExists", { name: conflict.name }))
      } else {
        toast.error(
          t("fileMgmt.failed", { error: formatApiError(err, t) })
        )
      }
    } finally {
      setUploading(false)
    }
  }

  const handleDragOver = (e: DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.dataTransfer.types.includes("Files")) setDragOver(true)
  }

  const handleDragLeave = (e: DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.currentTarget === e.target) setDragOver(false)
  }

  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
    if (e.dataTransfer.files?.length) {
      void uploadAndAttach(e.dataTransfer.files)
    }
  }

  return (
    <div
      className={cn(
        "pm-timeline-attach-stack",
        selectOpen && "is-picking"
      )}
    >
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files?.length) void uploadAndAttach(e.target.files)
          e.target.value = ""
        }}
      />

      {/* Drop / upload zone — full when idle, one-line when picking from collection */}
      <div
        data-file-select-zone
        className={cn(
          "pm-timeline-attach-zone shrink-0",
          selectOpen ? "is-compact" : "is-drop",
          dragOver && "is-drag",
          uploading && "opacity-60 pointer-events-none"
        )}
        onDragOver={handleDragOver}
        onDragEnter={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <button
          type="button"
          className={cn(
            "w-full flex items-center justify-center gap-1.5 text-center cursor-pointer rounded-[var(--pm-r-sm)] transition-colors",
            selectOpen
              ? "h-full px-2 hover:bg-white/70"
              : "flex-1 flex-col min-h-[88px] px-3 py-3 gap-1.5 hover:bg-white/70"
          )}
          onClick={() => {
            if (selectOpen) {
              // Compact row: leave From Collection → restore full drop zone
              // (do not open the system file manager)
              setSelect(false)
              return
            }
            fileInputRef.current?.click()
          }}
          disabled={uploading}
          title={
            selectOpen
              ? "Back to upload"
              : uploading
                ? "Uploading…"
                : "Browse files"
          }
        >
          <Upload
            className={cn(
              "text-[var(--pm-faint)] shrink-0",
              selectOpen ? "h-3.5 w-3.5" : "h-5 w-5"
            )}
          />
          {selectOpen ? (
            <span className="pm-meta text-[var(--pm-muted)]">{t("fileMgmt.uploadFileBtn")}</span>
          ) : (
            <>
              <p className="pm-meta text-[var(--pm-muted)]">
                {uploading ? t("common.uploading") : t("fileMgmt.dragDrop")}
              </p>
              <p className="pm-meta text-[var(--pm-faint)]">{t("fileMgmt.orClickBrowse")}</p>
            </>
          )}
        </button>
      </div>

      {/* From Collection → Confirm (same slot) */}
      <div className="pm-timeline-attach-action-row shrink-0">
        {selectOpen ? (
          <button
            type="button"
            className={cn(
              "pm-timeline-attach-confirm",
              dirty && !confirming && "is-ready"
            )}
            onClick={() => void applyConfirm()}
            disabled={!dirty || confirming}
          >
            {confirming ? t("common.saving") : t("common.confirm")}
          </button>
        ) : (
          <button
            type="button"
            className="pm-timeline-attach-from-collection"
            onClick={() => setSelect(true)}
            disabled={uploading}
          >
            <FolderOpen className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
            {t("fileMgmt.fromCollection")}
          </button>
        )}
      </div>

      {/* Below: attached list (idle) or file tree (picker) — silk swap */}
      <div className="pm-timeline-attach-body flex-1 min-h-0">
        {/* Attached list — folds away when picker opens */}
        <div
          className={cn(
            "pm-timeline-attach-list-pane",
            !selectOpen && "is-open"
          )}
        >
          <div className="pm-timeline-attach-list-inner">
            {displayAttachments.length === 0 ? (
              <p className="pm-meta text-[var(--pm-faint)] px-1 py-2">
                No files attached yet
              </p>
            ) : (
              <ul className="pm-ws-list pm-timeline-attach-list">
                {displayAttachments.map((att) => {
                  const isExit = exitingIds.has(att.file_id)
                  return (
                    <li
                      key={att.file_id}
                      className={cn(
                        "pm-timeline-attach-item",
                        isExit && "is-exit"
                      )}
                    >
                      <div className="pm-timeline-attach-item-inner">
                        <div className="pm-ws-list-item is-clickable group/file pm-timeline-attach-item-row">
                          <div className="flex items-center gap-2 min-w-0 w-full">
                            <Paperclip
                              className="h-3 w-3 shrink-0 text-[var(--pm-faint)]"
                              strokeWidth={1.75}
                            />
                            <button
                              type="button"
                              className="flex-1 min-w-0 text-left break-words hover:text-[var(--pm-green)] transition-colors"
                              title={
                                att.display_name || att.filename || att.file_id
                              }
                              onClick={() => onOpenFile?.(att.file_id)}
                              disabled={isExit}
                            >
                              {att.display_name || att.filename || att.file_id}
                            </button>
                            {att.archived && (
                              <span className="pm-meta shrink-0">archived</span>
                            )}
                            <button
                              type="button"
                              className={cn(
                                "opacity-0 group-hover/file:opacity-100 transition-opacity shrink-0",
                                att.is_definitive
                                  ? "text-[var(--pm-green)]"
                                  : "text-[var(--pm-faint)]"
                              )}
                              onClick={(e) => {
                                e.stopPropagation()
                                if (isExit) return
                                onToggleDefinitive?.(
                                  att.file_id,
                                  att.is_definitive,
                                  att.version ?? 1
                                )
                              }}
                              disabled={isExit}
                              title={
                                att.is_definitive
                                  ? "Remove definitive"
                                  : "Mark as definitive"
                              }
                            >
                              <Star
                                className={cn(
                                  "h-3 w-3",
                                  att.is_definitive && "fill-[var(--pm-green)]"
                                )}
                              />
                            </button>
                            <button
                              type="button"
                              className="opacity-0 group-hover/file:opacity-100 transition-opacity text-[var(--pm-faint)] hover:text-[var(--pm-danger)] shrink-0"
                              onClick={(e) => {
                                e.stopPropagation()
                                requestDetach(att)
                              }}
                              disabled={isExit}
                              title={t("fileMgmt.removeAttachment")}
                            >
                              <XCircle className="h-3 w-3" />
                            </button>
                          </div>
                        </div>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        </div>

        {/* File tree — flat under Confirm (no nested card shell) */}
        <div
          className={cn(
            "pm-timeline-attach-tree-pane",
            selectOpen && "is-open"
          )}
          data-file-select-zone={selectOpen ? true : undefined}
        >
          <div className="pm-timeline-attach-tree-inner">
            {selectOpen ? (
              <FileTreePicker
                collectionId={collectionId}
                selectedIds={draftIds}
                onSelectFile={toggleDraft}
                onPreviewFile={(file) => onPreviewFile?.(file)}
                maxHeightClass="h-full max-h-full"
                className="h-full flex flex-col"
              />
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
