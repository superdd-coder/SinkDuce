import { useRef, useState, type DragEvent } from "react"
import { Search, Upload, X } from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import type { FileSummary } from "@/types/file-mgmt"
import {
  attachFileToNode,
  detachFileFromNode,
  uploadFileToNode,
} from "@/api/file-mgmt"
import { FileTreePicker } from "./file-tree-picker"

/** Fixed drop-zone height so Select/tree does not grow the box. */
const DROP_ZONE_H = "h-[200px]"

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
}

export function NodeFileAttach({
  collectionId,
  nodeId,
  attachedIds = [],
  onAttached,
  onPreviewFile,
  onSelectOpenChange,
}: NodeFileAttachProps) {
  const [selectOpen, setSelectOpen] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [loading, setLoading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const attachedSet = new Set(attachedIds)

  const setSelect = (open: boolean) => {
    setSelectOpen(open)
    onSelectOpenChange?.(open)
    if (!open) onPreviewFile?.(null)
  }

  /** Toggle: attach if not yet on node; detach if already attached. */
  const toggleExisting = async (file: FileSummary) => {
    setLoading(true)
    try {
      if (attachedSet.has(file.file_id)) {
        await detachFileFromNode(collectionId, nodeId, file.file_id)
        toast.success(`Detached “${file.display_name || file.filename}”`)
      } else {
        await attachFileToNode(collectionId, nodeId, file.file_id)
        toast.success(`Attached “${file.display_name || file.filename}”`)
      }
      onAttached()
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setLoading(false)
    }
  }

  const uploadAndAttach = async (files: FileList | File[]) => {
    const list = Array.from(files)
    if (list.length === 0) return
    setLoading(true)
    try {
      for (const file of list) {
        await uploadFileToNode(collectionId, nodeId, file)
      }
      toast.success(
        list.length === 1
          ? `Uploaded and attached “${list[0].name}”`
          : `Uploaded and attached ${list.length} files`
      )
      setSelect(false)
      onAttached()
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setLoading(false)
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
      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          className="text-[10px] font-medium text-primary hover:underline flex items-center gap-1"
          onClick={() => setSelect(!selectOpen)}
          disabled={loading}
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
        className={cn(
          DROP_ZONE_H,
          "rounded border border-dashed p-2 transition-colors overflow-hidden flex flex-col",
          dragOver ? "border-primary bg-primary/5" : "border-border bg-muted/10",
          loading && "opacity-60 pointer-events-none"
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
            disabled={loading}
          >
            <Upload className="h-5 w-5 text-muted-foreground/40" />
            <p className="text-[11px] text-muted-foreground/60">
              {loading ? "Uploading..." : "Drag & drop files here"}
            </p>
            <p className="text-[10px] text-muted-foreground/40">
              or click to browse
            </p>
          </button>
        ) : (
          <div className="flex-1 min-h-0 flex flex-col gap-1 overflow-hidden">
            <div className="flex items-center justify-between gap-1 shrink-0">
              <span className="text-[10px] text-muted-foreground">
                Click to attach · click again to detach
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
                selectedIds={attachedIds}
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
