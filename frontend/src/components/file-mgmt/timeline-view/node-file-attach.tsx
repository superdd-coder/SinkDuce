import { useEffect, useRef, useState, type DragEvent } from "react"
import { Search, Upload, X } from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import type { FileSummary } from "@/types/file-mgmt"
import {
  attachFileToNode,
  detachFileFromNode,
  getNameConflict,
  uploadFileToNode,
} from "@/api/file-mgmt"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { FileTreePicker } from "./file-tree-picker"

/** Drop zone compact; expand when Select existing shows the file tree. */
const DROP_ZONE_H = "h-[112px]"
const SELECT_TREE_H = "h-[min(420px,55vh)]"

interface NodeFileAttachProps {
  collectionId: string
  nodeId: string
  /** Already attached file ids (shown as selected in tree). */
  attachedIds?: string[]
  onAttached: () => void
  /** Focused file for external left preview panel (parent renders it). */
  onPreviewFile?: (file: FileSummary | null) => void
  /** Notify parent when select mode opens/closes (clear preview on close). */
  onSelectOpenChange?: (open: boolean) => void
  /**
   * Optional section title (e.g. "Attachments (3)"). Shown on the same row
   * as the Select existing control.
   */
  title?: string
}

export function NodeFileAttach({
  collectionId,
  nodeId,
  attachedIds = [],
  onAttached,
  onPreviewFile,
  onSelectOpenChange,
  title,
}: NodeFileAttachProps) {
  const [selectOpen, setSelectOpen] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  /** Optimistic selection so multi-click works before server refresh. */
  const [selectedIds, setSelectedIds] = useState<Set<string>>(
    () => new Set(attachedIds)
  )
  /** Per-file in-flight toggles — tree stays interactive. */
  const busyRef = useRef<Set<string>>(new Set())
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Sync from server when attachment list actually changes (not every render)
  const attachedKey = [...attachedIds].sort().join(",")
  useEffect(() => {
    setSelectedIds(new Set(attachedIds))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when ids change
  }, [attachedKey])

  const setSelect = (open: boolean) => {
    setSelectOpen(open)
    onSelectOpenChange?.(open)
    if (!open) onPreviewFile?.(null)
  }

  /**
   * Multi-select: click to attach, click again to detach.
   * Optimistic UI — tree never unmounts / blocks for the whole list.
   */
  const toggleExisting = async (file: FileSummary) => {
    const fid = file.file_id
    if (busyRef.current.has(fid)) return
    busyRef.current.add(fid)

    const wasSelected = selectedIds.has(fid)
    // Optimistic toggle
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (wasSelected) next.delete(fid)
      else next.add(fid)
      return next
    })

    try {
      if (wasSelected) {
        await detachFileFromNode(collectionId, nodeId, fid)
        toast.success(`Detached “${file.display_name || file.filename}”`)
      } else {
        await attachFileToNode(collectionId, nodeId, fid)
        toast.success(`Attached “${file.display_name || file.filename}”`)
      }
      // Parent should silent-refresh attachments (no full-panel Loading)
      onAttached()
    } catch (err) {
      // Roll back optimistic state
      setSelectedIds((prev) => {
        const next = new Set(prev)
        if (wasSelected) next.add(fid)
        else next.delete(fid)
        return next
      })
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      busyRef.current.delete(fid)
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
        // Track ingest so file detail can lock to Preview-only while processing
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
            ? `Uploaded “${list[0].name}” — ingesting…`
            : `Uploaded and attached “${list[0].name}”`
          : `Uploaded ${list.length} file(s)` +
              (started ? ` · ${started} ingesting` : "")
      )
      setSelect(false)
      onAttached()
    } catch (err) {
      const conflict = getNameConflict(err)
      if (conflict) {
        toast.error(
          `A file named “${conflict.name}” already exists. Please rename and try again.`
        )
      } else {
        toast.error(
          `Failed: ${err instanceof Error ? err.message : String(err)}`
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
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2 min-w-0">
        {title ? (
          <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 truncate min-w-0">
            {title}
          </label>
        ) : (
          <span />
        )}
        <button
          type="button"
          className="text-[10px] font-medium text-primary hover:underline flex items-center gap-1 shrink-0"
          onClick={() => setSelect(!selectOpen)}
          disabled={uploading}
        >
          <Search className="h-3 w-3" />
          {selectOpen ? "Close select" : "Select existing"}
        </button>
      </div>

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

      <div
        data-file-select-zone
        className={cn(
          selectOpen ? SELECT_TREE_H : DROP_ZONE_H,
          "rounded border border-dashed p-2 transition-[height,colors] duration-200 overflow-hidden flex flex-col",
          dragOver ? "border-primary bg-primary/5" : "border-border bg-muted/10",
          uploading && !selectOpen && "opacity-60 pointer-events-none"
        )}
        onDragOver={handleDragOver}
        onDragEnter={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {!selectOpen ? (
          <button
            type="button"
            className="flex-1 flex flex-col items-center justify-center gap-1.5 text-center px-3 cursor-pointer rounded hover:bg-muted/30 transition-colors"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
          >
            <Upload className="h-5 w-5 text-muted-foreground/40" />
            <p className="text-[11px] text-muted-foreground/60">
              {uploading ? "Uploading..." : "Drag & drop files here"}
            </p>
            <p className="text-[10px] text-muted-foreground/40">
              or click to browse
            </p>
          </button>
        ) : (
          <div className="flex-1 min-h-0 flex flex-col gap-1 overflow-hidden">
            <div className="flex items-center justify-between gap-1 shrink-0">
              <span className="text-[10px] text-muted-foreground">
                Multi-select · click to attach · click again to detach
              </span>
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground"
                onClick={() => setSelect(false)}
                title="Close"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
              <FileTreePicker
                collectionId={collectionId}
                selectedIds={selectedIds}
                onSelectFile={(file) => void toggleExisting(file)}
                onPreviewFile={(file) => onPreviewFile?.(file)}
                maxHeightClass="h-full max-h-full"
                className="h-full flex flex-col"
              />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
