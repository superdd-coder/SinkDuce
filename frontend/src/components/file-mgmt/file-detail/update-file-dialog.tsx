/**
 * Upload a new file version from file detail.
 *
 * Staging rules (important):
 * - Choosing / dropping a file only stages it in memory + local object URL preview.
 * - No network calls, no file_versions write, no Qdrant ingest until the user
 *   explicitly clicks "Upload version".
 * - Cancel / close / Clear revokes the object URL and drops the staged File;
 *   nothing to delete on the server because nothing was uploaded.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { MESSAGE_EDITOR_PLACEHOLDER } from "@/components/ui/tiptap-editor"
import {
  FileIcon,
  Loader2,
  Upload,
  FileText,
  Image as ImageIcon,
} from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { uploadFileVersion } from "@/api/file-mgmt"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import {
  isRawViewerSupported,
  RawFileViewer,
} from "@/components/file-mgmt/raw-file-viewer"

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function extOf(name: string): string {
  const i = name.lastIndexOf(".")
  return i >= 0 ? name.slice(i + 1).toLowerCase() : ""
}

type PreviewKind = "image" | "file-viewer" | "other"

function previewKind(file: File): PreviewKind {
  const mime = (file.type || "").toLowerCase()
  const ext = extOf(file.name)
  if (mime.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) {
    return "image"
  }
  if (isRawViewerSupported(file.name)) return "file-viewer"
  return "other"
}

interface UpdateFileDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  collectionId: string
  fileId: string
  /** Current filename for header hint */
  currentFilename?: string | null
  onSuccess?: () => void
}

export function UpdateFileDialog({
  open,
  onOpenChange,
  collectionId,
  fileId,
  currentFilename,
  onSuccess,
}: UpdateFileDialogProps) {
  const [pendingFile, setPendingFile] = useState<File | null>(null)
  /** Local temp preview — always revoked on replace / cancel / unmount. */
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [message, setMessage] = useState("")
  const [busy, setBusy] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const previewUrlRef = useRef<string | null>(null)
  /** Prevent double-submit; only set true inside explicit Upload click. */
  const uploadStartedRef = useRef(false)

  const revokePreview = useCallback(() => {
    if (previewUrlRef.current) {
      URL.revokeObjectURL(previewUrlRef.current)
      previewUrlRef.current = null
    }
    setPreviewUrl(null)
  }, [])

  const clearPending = useCallback(() => {
    revokePreview()
    setPendingFile(null)
    uploadStartedRef.current = false
    if (inputRef.current) inputRef.current.value = ""
  }, [revokePreview])

  /**
   * Stage a file for local preview only.
   * Must NOT call uploadFileVersion / any file-mgmt write API.
   */
  const stageFileForPreview = useCallback(
    async (file: File | null) => {
      if (busy || uploadStartedRef.current) return
      revokePreview()
      setPendingFile(file)
      if (!file) {
        if (inputRef.current) inputRef.current.value = ""
        return
      }
      // Local-only preview — no server round-trip
      const kind = previewKind(file)
      if (kind === "image" || kind === "file-viewer") {
        const url = URL.createObjectURL(file)
        previewUrlRef.current = url
        setPreviewUrl(url)
      }
    },
    [busy, revokePreview]
  )

  // Reset + cleanup when dialog closes / unmounts
  useEffect(() => {
    if (!open) {
      clearPending()
      setMessage("")
      setBusy(false)
      setDragOver(false)
    }
  }, [open, clearPending])

  useEffect(() => {
    return () => {
      if (previewUrlRef.current) {
        URL.revokeObjectURL(previewUrlRef.current)
        previewUrlRef.current = null
      }
    }
  }, [])

  const handleClose = (next: boolean) => {
    // While uploading, block dismiss so we don't leave a half-finished version
    if (busy || uploadStartedRef.current) return
    if (!next) {
      // Cancel: discard staged File + object URL only (no server cleanup needed)
      clearPending()
      setMessage("")
    }
    onOpenChange(next)
  }

  /**
   * Queue new version + full async ingest (same pipeline as folder upload).
   * Does not wait for MinerU / embed — returns as soon as the task is queued.
   * Preview / cancel / clear must never call this.
   */
  const handleConfirmUpload = async () => {
    if (!pendingFile || !fileId) {
      toast.error("Choose a file first")
      return
    }
    if (busy || uploadStartedRef.current) return
    uploadStartedRef.current = true
    setBusy(true)
    try {
      const result = await uploadFileVersion(
        collectionId,
        fileId,
        pendingFile,
        message.trim()
      )
      clearPending()
      setMessage("")
      onOpenChange(false)
      // Refresh metadata immediately (version row / message); chunks when task done
      onSuccess?.()
      if (result.unsupported) {
        toast.warning(
          "Version saved — file type not supported for ingest"
        )
      } else if (result.task_id) {
        toast.info("Version uploaded, ingesting…")
        useFileMgmtStore
          .getState()
          ._startTaskPolling(collectionId, result.task_id, fileId)
      } else {
        toast.success("New version uploaded")
      }
    } catch (err) {
      uploadStartedRef.current = false
      toast.error(
        `Update failed: ${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      setBusy(false)
    }
  }

  const kind = pendingFile ? previewKind(pendingFile) : null

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent
        className={cn(
          "pm-dialog w-[min(1280px,96vw)] max-w-[96vw] sm:max-w-[96vw]",
          "h-[min(88vh,900px)] flex flex-col gap-0 p-0 overflow-hidden"
        )}
      >
        <DialogHeader className="px-4 py-3 shrink-0 space-y-1 shadow-[inset_0_-1px_0_color-mix(in_srgb,var(--pm-ink)_8%,transparent)]">
          <DialogTitle>Update file</DialogTitle>
          <DialogDescription className="pm-meta">
            {currentFilename
              ? `New version of “${currentFilename}”. Preview is local only — nothing is saved or ingested until you click Upload version.`
              : "Preview is local only — nothing is saved or ingested until you click Upload version."}
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 min-h-0 flex overflow-hidden">
          {/* Left: select + preview (no title bar) */}
          <div className="flex-[1.15] min-w-0 flex flex-col shadow-[inset_-1px_0_0_color-mix(in_srgb,var(--pm-ink)_8%,transparent)] overflow-hidden p-3 gap-3">
            <input
              ref={inputRef}
              type="file"
              className="hidden"
              disabled={busy}
              onChange={(e) => {
                // Stage only — never upload here
                const f = e.target.files?.[0] ?? null
                void stageFileForPreview(f)
              }}
            />

            {!pendingFile ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => inputRef.current?.click()}
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragOver(true)
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDragOver(false)
                  // Stage only — never upload here
                  const f = e.dataTransfer.files?.[0]
                  if (f) void stageFileForPreview(f)
                }}
                className={cn(
                  "flex-1 min-h-[200px] rounded-lg border-2 border-dashed",
                  "flex flex-col items-center justify-center gap-2 px-4",
                  "text-muted-foreground transition-colors",
                  dragOver
                    ? "border-primary bg-primary/5 text-primary"
                    : "border-border hover:border-primary/40 hover:bg-muted/30"
                )}
              >
                <Upload className="h-8 w-8 opacity-50" />
                <span className="text-sm font-medium">
                  Drop a file here or click to choose
                </span>
                <span className="text-[11px] text-muted-foreground/70">
                  Local preview only — not uploaded until you confirm
                </span>
              </button>
            ) : (
              <>
                <div className="shrink-0 flex items-center gap-2 rounded-md border border-border/50 bg-muted/20 px-2.5 py-2">
                  {kind === "image" ? (
                    <ImageIcon className="h-4 w-4 text-muted-foreground shrink-0" />
                  ) : kind === "file-viewer" ? (
                    <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
                  ) : (
                    <FileIcon className="h-4 w-4 text-muted-foreground shrink-0" />
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium truncate">
                      {pendingFile.name}
                    </p>
                    <p className="text-[10px] text-muted-foreground">
                      {formatBytes(pendingFile.size)}
                      {pendingFile.type ? ` · ${pendingFile.type}` : ""}
                      {" · "}
                      <span className="text-amber-600 dark:text-amber-400">
                        not uploaded yet
                      </span>
                    </p>
                  </div>
                  <Button
                    type="button"
                    size="xs"
                    variant="outline"
                    className="h-6 text-[10px] shrink-0"
                    disabled={busy}
                    onClick={() => inputRef.current?.click()}
                  >
                    Replace
                  </Button>
                </div>

                <div className="flex-1 min-h-0 rounded-lg border border-border/50 bg-muted/10 overflow-hidden">
                  {kind === "image" && previewUrl && (
                    <div className="h-full w-full flex items-center justify-center p-2 overflow-auto">
                      <img
                        src={previewUrl}
                        alt={pendingFile.name}
                        className="max-w-full max-h-full object-contain rounded"
                      />
                    </div>
                  )}
                  {kind === "file-viewer" && previewUrl && (
                    <RawFileViewer
                      url={previewUrl}
                      filename={pendingFile.name}
                      downloadUrl={previewUrl}
                      className="h-full border-0 rounded-none"
                    />
                  )}
                  {kind === "other" && (
                    <div className="h-full flex flex-col items-center justify-center gap-2 text-muted-foreground p-6 text-center">
                      <FileIcon className="h-10 w-10 opacity-40" />
                      <p className="text-xs">
                        No in-browser preview for this file type.
                      </p>
                      <p className="text-[11px] text-muted-foreground/70">
                        You can still upload it as a new version.
                      </p>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>

          {/* Right: same MarkdownEditor as other message editors (Tiptap MD) */}
          <div className="flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden p-3">
            <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 block mb-1.5 shrink-0">
              Message (optional)
            </label>
            <div className="flex-1 min-h-0 min-w-0 overflow-auto rounded border border-border">
              {/* Remount when dialog opens so placeholder is never stuck from a prior session */}
              {open && (
                <MarkdownEditor
                  key="update-file-message"
                  value={message}
                  onChange={setMessage}
                  minHeight="100%"
                  placeholder={MESSAGE_EDITOR_PLACEHOLDER}
                  showToolbar={false}
                />
              )}
            </div>
          </div>
        </div>

        <div className="shrink-0 px-4 py-3 flex items-center justify-end gap-2 shadow-[inset_0_1px_0_color-mix(in_srgb,var(--pm-ink)_8%,transparent)]">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() => handleClose(false)}
          >
            Cancel
          </Button>
          <Button
            type="button"
            size="sm"
            className="pm-btn-pri"
            disabled={busy || !pendingFile}
            onClick={() => void handleConfirmUpload()}
          >
            {busy ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
                Queuing…
              </>
            ) : (
              <>
                <Upload className="h-3.5 w-3.5 mr-1" />
                Upload version
              </>
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
