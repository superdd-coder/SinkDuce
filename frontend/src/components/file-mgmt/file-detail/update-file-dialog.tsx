/**
 * Upload a new file version from file detail.
 *
 * Staging rules (important):
 * - Choosing / dropping a file only stages it in memory + local object URL preview.
 * - No network calls, no file_versions write, no Qdrant ingest until the user
 *   explicitly clicks "Upload version".
 * - Cancel / close / Clear revokes the object URL and drops the staged File;
 *   nothing to delete on the server because nothing was uploaded.
 *
 * Premium shell (aligned with File detail / Note / Message silk dialogs):
 * - pm-dialog--silk + overlay--silk (open/close + mask fade, symmetric)
 * - pm-workspace dual-pane: nested white cards, no hairline dividers
 * - type roles only; green accent for drag / staged state
 */

import { useCallback, useEffect, useRef, useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
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
  if (
    mime.startsWith("image/") ||
    ["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)
  ) {
    return "image"
  }
  if (isRawViewerSupported(file.name)) return "file-viewer"
  return "other"
}

/** Silk shell — same clock as File detail / Message / Note dialogs */
const silkShell = cn(
  "pm-dialog pm-dialog--silk pm-workspace pm-ws-dialog",
  "animate-none data-open:animate-none data-closed:animate-none"
)

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
  /** Latest TipTap markdown — avoid stale state if user clicks Upload mid-keystroke. */
  const messageRef = useRef("")
  const [busy, setBusy] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const previewUrlRef = useRef<string | null>(null)
  /** Prevent double-submit; only set true inside explicit Upload click. */
  const uploadStartedRef = useRef(false)

  /**
   * TipTap empty doc often yields "" or whitespace-only markdown.
   * Empty note → backend stores default "version update".
   * Non-empty → becomes the single system_version log body (not a file message).
   */
  const normalizeVersionNote = (raw: string): string => {
    const t = (raw || "").trim()
    if (!t) return ""
    // Strip common empty markdown shells
    const plain = t
      .replace(/<[^>]+>/g, "")
      .replace(/[#>*_`~\-\[\]()]/g, "")
      .replace(/\s+/g, "")
    return plain ? t : ""
  }

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
      messageRef.current = ""
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
      messageRef.current = ""
    }
    onOpenChange(next)
  }

  /**
   * Queue new version + full async ingest (same pipeline as folder upload).
   * Does not wait for MinerU / embed — returns as soon as the task is queued.
   * Preview / cancel / clear must never call this.
   *
   * The note is sent as commit_message only → backend writes ONE
   * system_version message for this version (not a separate file message).
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
      // Prefer ref (latest editor emit) over React state
      const versionNote = normalizeVersionNote(
        messageRef.current || message
      )
      const result = await uploadFileVersion(
        collectionId,
        fileId,
        pendingFile,
        versionNote
      )
      clearPending()
      setMessage("")
      messageRef.current = ""
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
        showCloseButton
        overlayClassName="pm-dialog-overlay--silk"
        className={cn(
          silkShell,
          "!max-w-[94vw] !w-[min(1280px,94vw)] h-[min(88vh,900px)]",
          "flex flex-col p-0 !gap-0 overflow-hidden"
        )}
      >
        {/* Chrome — File detail title language (serif name + quiet meta) */}
        <div className="pm-ws-chrome">
          <DialogHeader className="shrink-0 flex-1 min-w-0 !p-0 !space-y-0">
            <DialogTitle className="flex items-center gap-2 min-w-0 text-left">
              <span className="pm-ws-title truncate">Update file</span>
            </DialogTitle>
            <DialogDescription className="pm-meta !mt-1 normal-case tracking-normal text-left">
              {currentFilename
                ? `New version of “${currentFilename}”. Local preview only — nothing is saved until you upload.`
                : "Local preview only — nothing is saved until you upload."}
            </DialogDescription>
          </DialogHeader>
          {/* Primary actions sit with chrome (ghost + green pri); room for X */}
          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              className="pm-btn-ghost pm-btn-xs"
              disabled={busy}
              onClick={() => handleClose(false)}
            >
              Cancel
            </button>
            <button
              type="button"
              className="pm-btn-pri pm-btn-xs gap-1"
              disabled={busy || !pendingFile}
              onClick={() => void handleConfirmUpload()}
            >
              {busy ? (
                <>
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Queuing…
                </>
              ) : (
                <>
                  <Upload className="h-3 w-3" strokeWidth={1.75} />
                  Upload version
                </>
              )}
            </button>
          </div>
          <div className="w-8 shrink-0" aria-hidden />
        </div>

        {/*
          Body overflow stays visible (pm-ws-body) so card box-shadows breathe.
          Clip lives on *inner* content only — never on the card shell
          (see .pm-ws-card--main overflow:visible + .pm-ws-side-card overflow:visible).
        */}
        <div className="pm-ws-body">
          {/* ── Left: drop / stage / preview (nested white card) ── */}
          <div className="pm-ws-main pm-ws-card pm-ws-card--main">
            {/*
              Exactly one visible fill child under --main (see index.css
              `.pm-ws-card--main > *` overflow clip). Keep file input inside.
            */}
            <div className="pm-update-main-stage">
              <input
                ref={inputRef}
                type="file"
                className="hidden"
                disabled={busy}
                onChange={(e) => {
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
                    const f = e.dataTransfer.files?.[0]
                    if (f) void stageFileForPreview(f)
                  }}
                  className={cn(
                    "flex-1 min-h-0 w-full m-0 border-0 cursor-pointer",
                    "flex flex-col items-center justify-center gap-2.5 px-6",
                    "rounded-[inherit] bg-transparent",
                    "transition-[background,color] duration-200",
                    "ease-[cubic-bezier(0.22,1,0.36,1)]",
                    "focus-visible:outline-none focus-visible:ring-2",
                    "focus-visible:ring-[var(--pm-green-soft)]",
                    "disabled:opacity-60 disabled:cursor-not-allowed",
                    dragOver
                      ? "bg-[var(--pm-green-wash)] text-[var(--pm-green)]"
                      : "text-[var(--pm-muted)] hover:bg-[var(--pm-green-wash)] hover:text-[var(--pm-text)]"
                  )}
                >
                  <span
                    className={cn(
                      "inline-flex h-11 w-11 items-center justify-center rounded-full",
                      "transition-colors duration-200",
                      dragOver
                        ? "bg-[var(--pm-green-soft)] text-[var(--pm-green)]"
                        : "bg-[rgba(18,20,16,0.05)] text-[var(--pm-faint)]"
                    )}
                  >
                    <Upload className="h-5 w-5" strokeWidth={1.5} />
                  </span>
                  <span className="pm-title text-[var(--pm-ink)]">
                    Drop a file here or click to choose
                  </span>
                  <span className="pm-meta max-w-[28ch] text-center">
                    Staged locally — not uploaded until you confirm
                  </span>
                </button>
              ) : (
                <div className="flex flex-col flex-1 min-h-0 p-3 gap-3">
                  {/* Staged file chip — soft wash, no hard border grid */}
                  <div
                    className={cn(
                      "shrink-0 flex items-center gap-2.5 px-3 py-2.5",
                      "rounded-[var(--pm-r-sm)] bg-[var(--pm-green-wash)]"
                    )}
                  >
                    {kind === "image" ? (
                      <ImageIcon
                        className="h-4 w-4 text-[var(--pm-green)] shrink-0"
                        strokeWidth={1.75}
                      />
                    ) : kind === "file-viewer" ? (
                      <FileText
                        className="h-4 w-4 text-[var(--pm-green)] shrink-0"
                        strokeWidth={1.75}
                      />
                    ) : (
                      <FileIcon
                        className="h-4 w-4 text-[var(--pm-green)] shrink-0"
                        strokeWidth={1.75}
                      />
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="pm-title truncate text-[var(--pm-ink)]">
                        {pendingFile.name}
                      </p>
                      <p className="pm-meta mt-0.5">
                        {formatBytes(pendingFile.size)}
                        {pendingFile.type ? ` · ${pendingFile.type}` : ""}
                        {" · "}
                        <span className="text-[var(--pm-green)]">
                          staged · local only
                        </span>
                      </p>
                    </div>
                    <button
                      type="button"
                      className="pm-btn-ghost pm-btn-xs shrink-0"
                      disabled={busy}
                      onClick={() => inputRef.current?.click()}
                    >
                      Replace
                    </button>
                  </div>

                  {/* Preview stage — soft canvas well inside white card */}
                  <div
                    className={cn(
                      "flex-1 min-h-0 overflow-hidden",
                      "rounded-[var(--pm-r)] bg-[var(--pm-canvas)]"
                    )}
                  >
                    {kind === "image" && previewUrl && (
                      <div className="h-full w-full flex items-center justify-center p-3 overflow-auto">
                        <img
                          src={previewUrl}
                          alt={pendingFile.name}
                          className="max-w-full max-h-full object-contain rounded-[var(--pm-r-sm)]"
                        />
                      </div>
                    )}
                    {kind === "file-viewer" && previewUrl && (
                      <RawFileViewer
                        url={previewUrl}
                        filename={pendingFile.name}
                        downloadUrl={previewUrl}
                        className="h-full border-0 rounded-none bg-transparent"
                      />
                    )}
                    {kind === "other" && (
                      <div className="h-full flex flex-col items-center justify-center gap-2.5 p-8 text-center">
                        <span className="inline-flex h-11 w-11 items-center justify-center rounded-full bg-[rgba(18,20,16,0.05)] text-[var(--pm-faint)]">
                          <FileIcon className="h-5 w-5" strokeWidth={1.5} />
                        </span>
                        <p className="pm-title text-[var(--pm-ink)]">
                          No in-browser preview
                        </p>
                        <p className="pm-meta max-w-[28ch]">
                          You can still upload this file as a new version.
                        </p>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/*
            Right: version note — becomes the single system_version Log entry
            for this upload (commit_message). Never creates a separate file message.
            Side card shell stays overflow:visible (shadow); clip only the editor body.
          */}
          <div className="pm-ws-side">
            <section
              className="pm-ws-side-card min-h-0 flex flex-col"
              style={{ flex: "1 1 0", minHeight: 0 }}
            >
              <div className="pm-ws-side-h">
                <span
                  className="pm-label"
                  style={{
                    textTransform: "none",
                    letterSpacing: "0.02em",
                  }}
                >
                  Version note
                </span>
                <span className="pm-meta ml-auto shrink-0">optional</span>
              </div>
              <p className="pm-meta px-3.5 pb-1.5 shrink-0">
                Saved on this version&apos;s Update log — not a separate message.
              </p>
              <div className="pm-update-side-body flex-1 min-h-0 flex flex-col overflow-hidden">
                <div className="flex-1 min-h-0 flex flex-col overflow-hidden pm-msg-editor-host">
                  {open && (
                    <MarkdownEditor
                      key="update-file-message"
                      value={message}
                      onChange={(v) => {
                        messageRef.current = v
                        setMessage(v)
                      }}
                      minHeight="100%"
                      placeholder={MESSAGE_EDITOR_PLACEHOLDER}
                      showToolbar={false}
                      flush
                      className="flex-1 min-h-0"
                    />
                  )}
                </div>
              </div>
            </section>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
