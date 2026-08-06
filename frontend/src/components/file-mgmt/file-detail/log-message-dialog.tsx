/**
 * Log message detail from file detail / Messages rail:
 *
 * - Normal message → same shell as Add Message (`pm-msg-dialog` + silk)
 * - Version update → same shell as File detail (`pm-workspace` + silk):
 *     left  = Preview / Parse / Summary / Chunks (pm-ws-main card + tabs)
 *     right = Message body (pm-ws-side float card)
 *
 * Motion: pm-dialog--silk + overlay--silk (open/close + mask fade, symmetric).
 */

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Tabs,
  TabsContent,
  TabsIndicator,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs"
import { Loader2, Pencil, Trash2 } from "lucide-react"
import { cn, transformImageBlocks } from "@/lib/utils"
import { TiptapEditor } from "@/components/ui/tiptap-editor"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { MESSAGE_EDITOR_PLACEHOLDER } from "@/components/ui/tiptap-editor"
import { MessageBody } from "@/components/file-mgmt/message-card"
import type { FileVersion, Message } from "@/types/file-mgmt"
import {
  getDocSummary,
  getExtractedText,
  getFileChunks,
  getFilePreviewUrl,
  type ChunkDetail,
  type DocSummary,
} from "@/api/client"
import {
  deleteFileVersion,
  FileMgmtApiError,
  getFileDetail,
  updateMessage,
} from "@/api/file-mgmt"
import {
  RawFileViewer,
  resolveRawFilename,
} from "@/components/file-mgmt/raw-file-viewer"
import { toast } from "sonner"

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

function versionUpdateBody(body: string | null | undefined): string {
  const t = (body || "").trim()
  return t || "version update"
}

const silkShell = cn(
  "pm-dialog pm-dialog--silk",
  "animate-none data-open:animate-none data-closed:animate-none"
)

const proseBodyClass =
  "prose prose-sm max-w-none leading-relaxed break-words [&_p]:my-2 font-[family-name:var(--pm-ff-prose)]"

export interface LogMessageDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  collectionId: string
  /** Document source for current-file APIs (__file__:{id}) */
  docSource: string | null
  message: Message | null
  /** When set, dual-pane version layout */
  version?: FileVersion | null
  /** True when version is the file's current version */
  isCurrentVersion?: boolean
  /** Called with the updated message so parents can refresh lock version. */
  onSaved?: (msg: Message) => void
  /** After permanently deleting a non-current version */
  onVersionDeleted?: () => void
}

/**
 * Re-read optimistic-lock version from file detail (system_version / file msgs).
 * Returns null when the message cannot be resolved.
 */
async function fetchMessageLockVersion(
  collectionId: string,
  msg: Message
): Promise<Message | null> {
  const ot = (msg.owner_type || "").toLowerCase()
  if (ot !== "system_version" && ot !== "file") return null
  if (!msg.owner_id) return null
  try {
    const detail = await getFileDetail(collectionId, msg.owner_id)
    return (
      detail.messages?.find((m) => m.message_id === msg.message_id) ?? null
    )
  } catch {
    return null
  }
}

export function LogMessageDialog({
  open,
  onOpenChange,
  collectionId,
  docSource,
  message,
  version = null,
  isCurrentVersion = false,
  onSaved,
  onVersionDeleted,
}: LogMessageDialogProps) {
  /**
   * Keep last payload while closing so exit animation can finish.
   * Parent often clears `message` in the same tick as `open=false`.
   */
  const [held, setHeld] = useState<{
    message: Message
    version: FileVersion | null
    isCurrentVersion: boolean
  } | null>(null)

  /**
   * Working copy while the dialog is open — owns optimistic-lock `version`
   * after save. Parent props can lag behind (loadDetail does not always
   * rewrite logMsgOpen), so we must not keep using a stale lock.
   */
  const [workingMsg, setWorkingMsg] = useState<Message | null>(null)

  useEffect(() => {
    if (!message) return
    setHeld({
      message,
      version: version ?? null,
      isCurrentVersion,
    })
    setWorkingMsg((prev) => {
      // New message id → always take prop
      if (!prev || prev.message_id !== message.message_id) return message
      // Prefer the higher lock version (local save may be ahead of parent)
      if ((prev.version ?? 0) > (message.version ?? 0)) return prev
      return message
    })
  }, [message, version, isCurrentVersion])

  const activeMessage = workingMsg ?? message ?? held?.message ?? null
  const activeVersion = message ? version ?? null : held?.version ?? null
  const activeIsCurrent = message
    ? isCurrentVersion
    : (held?.isCurrentVersion ?? false)

  const isVersionUpdate =
    !!activeMessage &&
    ((activeMessage.owner_type || "").toLowerCase() === "system_version" ||
      !!activeVersion)

  const [editing, setEditing] = useState(false)
  const [content, setContent] = useState("")
  const [saving, setSaving] = useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    if (!open || !activeMessage) return
    setEditing(false)
    setDeleteConfirmOpen(false)
    setContent(
      isVersionUpdate
        ? versionUpdateBody(activeMessage.body)
        : activeMessage.body || ""
    )
    // Only reset editor when opening / switching message — not on lock bumps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, activeMessage?.message_id, isVersionUpdate])

  const applySavedMessage = useCallback((updated: Message) => {
    setWorkingMsg(updated)
    setHeld((h) =>
      h && h.message.message_id === updated.message_id
        ? { ...h, message: updated }
        : h
    )
    setContent(
      (updated.owner_type || "").toLowerCase() === "system_version"
        ? versionUpdateBody(updated.body)
        : updated.body || ""
    )
  }, [])

  const handleSave = useCallback(async () => {
    if (!activeMessage) return
    const body = isVersionUpdate
      ? versionUpdateBody(content)
      : content.trim()
    if (!body && !isVersionUpdate) return

    setSaving(true)
    try {
      /**
       * Optimistic lock is messages.version (row concurrency), NOT file
       * version_no. Parent list / dialog props are often stale after a prior
       * edit — especially when editing a historical system_version note.
       * Always resolve the current lock from the server before PATCH.
       */
      let lockMsg = activeMessage
      const fresh = await fetchMessageLockVersion(collectionId, activeMessage)
      if (fresh && typeof fresh.version === "number") {
        lockMsg = fresh
        setWorkingMsg(fresh)
      } else if (
        typeof lockMsg.version !== "number" ||
        Number.isNaN(lockMsg.version)
      ) {
        toast.error("Save failed: could not read message version — reopen and try again")
        return
      }

      const payload = {
        body: body || "version update",
        version: lockMsg.version,
      }
      let updated: Message
      try {
        updated = await updateMessage(
          collectionId,
          activeMessage.message_id,
          payload
        )
      } catch (err) {
        // Rare race: someone else saved between our GET and PATCH — retry once
        if (err instanceof FileMgmtApiError && err.status === 409) {
          const again = await fetchMessageLockVersion(
            collectionId,
            activeMessage
          )
          if (!again || typeof again.version !== "number") throw err
          updated = await updateMessage(
            collectionId,
            activeMessage.message_id,
            { body: payload.body, version: again.version }
          )
        } else {
          throw err
        }
      }
      applySavedMessage(updated)
      toast.success("Message saved")
      setEditing(false)
      onSaved?.(updated)
    } catch (err) {
      const msg =
        err instanceof FileMgmtApiError && err.status === 409
          ? "Save failed: message changed while saving — try again"
          : `Save failed: ${err instanceof Error ? err.message : String(err)}`
      toast.error(msg)
    } finally {
      setSaving(false)
    }
  }, [
    activeMessage,
    content,
    isVersionUpdate,
    collectionId,
    onSaved,
    applySavedMessage,
  ])

  const handleDeleteVersion = useCallback(async () => {
    if (!activeVersion || activeIsCurrent) return
    const fileId = activeVersion.file_id
    const versionId = activeVersion.version_id
    if (!fileId || !versionId) return
    setDeleting(true)
    try {
      await deleteFileVersion(collectionId, fileId, versionId)
      toast.success("Version deleted")
      setDeleteConfirmOpen(false)
      onOpenChange(false)
      onVersionDeleted?.()
    } catch (err) {
      toast.error(
        `Delete failed: ${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      setDeleting(false)
    }
  }, [
    activeVersion,
    activeIsCurrent,
    collectionId,
    onOpenChange,
    onVersionDeleted,
  ])

  if (!activeMessage) return null

  const handleCancelEdit = () => {
    setEditing(false)
    setContent(
      isVersionUpdate
        ? versionUpdateBody(activeMessage.body)
        : activeMessage.body || ""
    )
  }

  /**
   * Message card title actions:
   * Idle → pill Edit; click → Cancel/Save slide out to the left (symmetric close).
   */
  const messageCardActions = (
    <div
      className={cn("pm-log-msg-card-actions", editing && "is-editing")}
    >
      {/* Expand host: width 0 → auto; content slides left into place */}
      <div className="pm-log-msg-expand" aria-hidden={!editing}>
        <div className="pm-log-msg-expand-inner">
          <button
            type="button"
            className="pm-btn-ghost pm-btn-xs pm-log-msg-action-btn"
            disabled={saving}
            tabIndex={editing ? 0 : -1}
            onClick={handleCancelEdit}
          >
            Cancel
          </button>
          <button
            type="button"
            className="pm-btn-pri pm-btn-xs pm-log-msg-action-btn"
            disabled={saving || !content.trim()}
            tabIndex={editing ? 0 : -1}
            onClick={() => void handleSave()}
          >
            {saving ? (
              <>
                <Loader2 className="h-3 w-3 animate-spin" />
                Saving…
              </>
            ) : (
              "Save"
            )}
          </button>
        </div>
      </div>
      <button
        type="button"
        className="pm-log-msg-edit"
        aria-expanded={editing}
        aria-label="Edit message"
        tabIndex={editing ? -1 : 0}
        onClick={() => {
          if (!editing) setEditing(true)
        }}
      >
        <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} />
        Edit
      </button>
    </div>
  )

  /** Normal (single-column) message dialog chrome — Save | Edit only, no slide. */
  const chromeActions = editing ? (
    <button
      type="button"
      className="pm-btn-pri pm-btn-xs"
      disabled={saving || !content.trim()}
      onClick={() => void handleSave()}
    >
      {saving ? (
        <>
          <Loader2 className="h-3 w-3 animate-spin" />
          Saving…
        </>
      ) : (
        "Save"
      )}
    </button>
  ) : (
    <button
      type="button"
      className="pm-btn-ghost pm-btn-xs gap-1"
      onClick={() => setEditing(true)}
    >
      <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} />
      Edit
    </button>
  )

  /** Preview body — click to enter edit (Version Update notes are always editable). */
  const messageEditorOrBody = (minHeight: string, allowEdit: boolean) =>
    editing ? (
      <div className="flex-1 min-h-0 flex flex-col pm-msg-editor-host">
        <MarkdownEditor
          value={content}
          onChange={setContent}
          minHeight={minHeight}
          placeholder={MESSAGE_EDITOR_PLACEHOLDER}
          showToolbar
          flush
          className="flex-1 min-h-0"
        />
      </div>
    ) : (
      <div
        className={cn(
          "p-5 pm-prose max-w-none flex-1 overflow-auto",
          allowEdit && "cursor-text"
        )}
        onClick={() => {
          if (allowEdit) setEditing(true)
        }}
        onKeyDown={(e) => {
          if (allowEdit && (e.key === "Enter" || e.key === " ")) {
            e.preventDefault()
            setEditing(true)
          }
        }}
        role={allowEdit ? "button" : undefined}
        tabIndex={allowEdit ? 0 : undefined}
        title={allowEdit ? "Click to edit" : undefined}
      >
        <MessageBody body={content} className={proseBodyClass} />
      </div>
    )

  // ═══════════════════════════════════════════════════════════
  // Version update — File detail workspace shell + Message chrome actions
  // ═══════════════════════════════════════════════════════════
  if (isVersionUpdate) {
    const versionLabel = activeVersion
      ? `v${activeVersion.version_no}${
          activeVersion.archived ? " · archived" : ""
        }${activeIsCurrent ? " · current" : ""}`
      : null

    return (
      <>
        <Dialog open={open} onOpenChange={onOpenChange}>
          <DialogContent
            showCloseButton
            overlayClassName="pm-dialog-overlay--silk"
            className={cn(
              silkShell,
              "pm-workspace pm-ws-dialog",
              "!max-w-[94vw] !w-[94vw] h-[88vh] flex flex-col p-0 !gap-0 overflow-hidden"
            )}
          >
            {/* Chrome — File detail title bar (X only; Edit/Save live in Message card) */}
            <div className="pm-ws-chrome">
              <DialogHeader className="shrink-0 flex-1 min-w-0 !p-0">
                <DialogTitle className="flex items-center gap-2 min-w-0 text-left">
                  <span className="pm-ws-title truncate">Version Update</span>
                  <span className="pm-meta tabular-nums shrink-0">
                    {formatTime(activeMessage.created_at)}
                  </span>
                  {/* Version tag (vN · current) — far right of title row */}
                  {versionLabel && (
                    <Badge
                      variant="secondary"
                      className={cn(
                        "pm-ws-badge ml-auto shrink-0",
                        activeIsCurrent && "is-live"
                      )}
                    >
                      {versionLabel}
                    </Badge>
                  )}
                </DialogTitle>
              </DialogHeader>
              <div className="w-8 shrink-0" />
            </div>

            <div className="pm-ws-body">
              {/* Left: Preview / Parse / Summary / Chunks — identical card to File detail */}
              <div className="pm-ws-main pm-ws-card pm-ws-card--main">
                <VersionFileTabs
                  collectionId={collectionId}
                  docSource={docSource}
                  version={activeVersion}
                  isCurrentVersion={activeIsCurrent}
                  onRequestDelete={
                    !activeIsCurrent && activeVersion
                      ? () => setDeleteConfirmOpen(true)
                      : undefined
                  }
                />
              </div>

              {/* Right: Message float card — Edit/Save in card title bar */}
              <div className="pm-ws-side">
                <section
                  className="pm-ws-side-card min-h-0 !overflow-hidden flex flex-col"
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
                      Message
                    </span>
                    <div className="ml-auto shrink-0">{messageCardActions}</div>
                  </div>
                  <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
                    {/* Version Update notes are always editable (system_version) */}
                    {messageEditorOrBody("200px", true)}
                  </div>
                </section>
              </div>
            </div>
          </DialogContent>
        </Dialog>

        {/* Delete version confirm — compact silk dialog */}
        <Dialog
          open={deleteConfirmOpen}
          onOpenChange={(v) => {
            if (!deleting) setDeleteConfirmOpen(v)
          }}
        >
          <DialogContent
            overlayClassName="pm-dialog-overlay--silk"
            className={cn(silkShell, "pm-dialog max-w-sm gap-4")}
          >
            <DialogHeader>
              <DialogTitle>Delete this version?</DialogTitle>
            </DialogHeader>
            <p className="pm-dialog-body">
              Permanently remove{" "}
              <span className="text-[var(--pm-ink)]">
                v{activeVersion?.version_no}
                {activeVersion?.storage_file_id
                  ? ` (${activeVersion.storage_file_id})`
                  : ""}
              </span>
              . This deletes the version blob, its vectors in the database, and
              the linked log entry. This cannot be undone.
            </p>
            <div className="flex justify-end gap-2 pt-1">
              <Button
                variant="ghost"
                size="sm"
                disabled={deleting}
                onClick={() => setDeleteConfirmOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                disabled={deleting}
                onClick={() => void handleDeleteVersion()}
              >
                {deleting ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                    Deleting…
                  </>
                ) : (
                  "Delete version"
                )}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </>
    )
  }

  // ═══════════════════════════════════════════════════════════
  // Normal message — same shell as Add Message
  // ═══════════════════════════════════════════════════════════
  const authorLabel =
    activeMessage.author_id &&
    activeMessage.author_id !== "local" &&
    activeMessage.author_id !== "user"
      ? activeMessage.author_id
      : null
  /**
   * system_version notes (file Version Update log) are user-editable.
   * Other system messages stay read-only.
   */
  const canEdit =
    activeMessage.author_type !== "system" ||
    (activeMessage.owner_type || "").toLowerCase() === "system_version"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        overlayClassName="pm-dialog-overlay--silk"
        className={cn(
          silkShell,
          "pm-msg-dialog w-[min(900px,92vw)] max-w-[92vw] sm:max-w-[92vw]",
          "h-[min(80vh,700px)] flex flex-col gap-0 p-0 overflow-hidden"
        )}
      >
        <DialogHeader className="pm-msg-dialog-chrome shrink-0">
          <DialogTitle className="flex items-center gap-2 min-w-0 text-left">
            <span className="shrink-0">Message</span>
            {authorLabel && (
              <span className="pm-meta normal-case tracking-normal">
                {authorLabel}
              </span>
            )}
            {activeMessage.edited_at && (
              <span className="pm-meta normal-case tracking-normal px-1.5 py-0.5 rounded text-[var(--pm-muted)] bg-[rgba(18,20,16,0.05)]">
                edited
              </span>
            )}
            <span className="pm-meta tabular-nums shrink-0">
              {formatTime(activeMessage.created_at)}
            </span>
          </DialogTitle>
          {canEdit && (
            <div className="pm-msg-dialog-actions">{chromeActions}</div>
          )}
        </DialogHeader>
        <div className="pm-msg-dialog-stage flex-1 min-h-0 overflow-hidden flex flex-col">
          <div className="pm-msg-dialog-card flex-1 min-h-0 flex flex-col overflow-hidden">
            {messageEditorOrBody("280px", canEdit)}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

// ── Left pane: same tab chrome as File detail (Preview / Parse / Summary / Chunks) ──

function VersionFileTabs({
  collectionId,
  docSource,
  version,
  isCurrentVersion,
  onRequestDelete,
}: {
  collectionId: string
  docSource: string | null
  version: FileVersion | null
  isCurrentVersion: boolean
  onRequestDelete?: () => void
}) {
  const [tab, setTab] = useState("raw")
  const [previewContent, setPreviewContent] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [chunks, setChunks] = useState<ChunkDetail[]>([])
  const [chunksTotal, setChunksTotal] = useState(0)
  const [chunksLoading, setChunksLoading] = useState(false)
  const [docSummary, setDocSummary] = useState<DocSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)

  const storageFile = version?.storage_file_id || null
  const ext = (storageFile || "").split(".").pop()?.toLowerCase() || ""
  const isPdf = ext === "pdf"

  const previewUrl = useMemo(() => {
    if (!docSource) return null
    if (!storageFile && !version?.version_id) return null
    return getFilePreviewUrl(docSource, {
      collection: collectionId,
      storageFile: storageFile || undefined,
      versionId: version?.version_id || undefined,
    })
  }, [docSource, collectionId, storageFile, version?.version_id])

  useEffect(() => {
    if (!docSource || !collectionId) {
      setPreviewContent(null)
      setPreviewLoading(false)
      return
    }
    if (!storageFile && !version?.version_id) {
      setPreviewContent(null)
      setPreviewLoading(false)
      return
    }
    let cancelled = false
    setPreviewLoading(true)
    getExtractedText(docSource, collectionId, {
      storageFile: storageFile || undefined,
      versionId: version?.version_id || undefined,
    })
      .then((res) => {
        if (!cancelled) setPreviewContent(res.text?.trim() ? res.text : null)
      })
      .catch(() => {
        if (!cancelled) setPreviewContent(null)
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [docSource, collectionId, storageFile, version?.version_id])

  const versionId = version?.version_id || null
  useEffect(() => {
    if (!docSource || !collectionId) {
      setChunks([])
      setChunksTotal(0)
      setDocSummary(null)
      setChunksLoading(false)
      setSummaryLoading(false)
      return
    }
    let cancelled = false

    setChunksLoading(true)
    getFileChunks(collectionId, docSource, 10000, {
      versionId: versionId || undefined,
    })
      .then((res) => {
        if (!cancelled) {
          setChunks(res.chunks)
          setChunksTotal(res.total)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setChunks([])
          setChunksTotal(0)
        }
      })
      .finally(() => {
        if (!cancelled) setChunksLoading(false)
      })

    setSummaryLoading(true)
    getDocSummary(collectionId, docSource, {
      versionId: versionId || version?.version_id || undefined,
    })
      .then((res) => {
        if (!cancelled) setDocSummary(res)
      })
      .catch(() => {
        if (!cancelled) setDocSummary(null)
      })
      .finally(() => {
        if (!cancelled) setSummaryLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [docSource, collectionId, isCurrentVersion, versionId, version?.version_id])

  const tabTriggerClass = cn(
    "pm-vtab relative z-[1]",
    "!h-auto min-h-0",
    "data-[state=active]:shadow-none data-active:bg-transparent",
    "after:!opacity-0 after:!content-none"
  )

  return (
    <Tabs
      value={tab}
      onValueChange={setTab}
      className="flex flex-col h-full min-h-0"
    >
      <div className="pm-ws-main-head flex items-center justify-between gap-2 shrink-0">
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
          <TabsTrigger value="raw" className={tabTriggerClass}>
            Preview
          </TabsTrigger>
          <TabsTrigger value="source" className={tabTriggerClass}>
            Parse
          </TabsTrigger>
          <TabsTrigger value="summary" className={tabTriggerClass}>
            Summary
          </TabsTrigger>
          <TabsTrigger value="chunks" className={tabTriggerClass}>
            Chunks
            {chunksTotal > 0 && (
              <span className="ml-1.5 tabular-nums pm-meta normal-case tracking-normal">
                {chunksTotal}
              </span>
            )}
          </TabsTrigger>
        </TabsList>
        {onRequestDelete && (
          <button
            type="button"
            className="pm-ws-link shrink-0 inline-flex items-center gap-1 !text-[var(--pm-danger)]"
            onClick={onRequestDelete}
            title="Permanently delete this version"
          >
            <Trash2 className="h-3 w-3" strokeWidth={1.75} />
            Delete
          </button>
        )}
      </div>

      {/* Preview — original file */}
      <TabsContent
        value="raw"
        className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
      >
        <div className="pm-ws-doc-stage">
          <RawFileViewer
            url={previewUrl}
            filename={resolveRawFilename(storageFile, version?.storage_file_id)}
            downloadUrl={previewUrl}
            className="h-full !rounded-none !border-0 !bg-white"
          />
        </div>
      </TabsContent>

      {/* Parse — extracted text */}
      <TabsContent
        value="source"
        className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
      >
        <div className="pm-ws-doc-stage">
          {previewLoading ? (
            <div className="pm-ws-loading h-full">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading…
            </div>
          ) : previewContent ? (
            <ScrollArea className="h-full">
              <div className="p-4">
                <TiptapEditor
                  value={transformImageBlocks(
                    previewContent,
                    collectionId,
                    version?.file_id || undefined
                  )}
                  readonly
                  showToolbar={false}
                />
              </div>
            </ScrollArea>
          ) : (
            <div className="pm-ws-empty h-full flex flex-col items-center justify-center gap-2 px-6">
              {!storageFile ? (
                <p className="pm-meta">No version file linked to this message.</p>
              ) : (
                <>
                  <p className="pm-meta">No parsed text for this version.</p>
                  <p className="pm-meta max-w-sm">
                    Parse shows text after parse/ingest. If this version was
                    never ingested (or the parse cache is missing), use{" "}
                    <span className="text-[var(--pm-green)]">Preview</span> for
                    the original file
                    {isPdf ? " (PDF preview)" : ""}.
                  </p>
                </>
              )}
            </div>
          )}
        </div>
      </TabsContent>

      {/* Summary */}
      <TabsContent
        value="summary"
        className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
      >
        <ScrollArea className="pm-ws-doc-stage">
          <div className="p-4">
            {summaryLoading ? (
              <div className="pm-ws-loading py-8">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading…
              </div>
            ) : docSummary ? (
              <div className="space-y-4">
                {!isCurrentVersion && (
                  <p className="pm-meta px-0.5">
                    Read-only summary for this version. Re-summarize only on the
                    current version.
                  </p>
                )}
                {(
                  [
                    ["Data", docSummary.data],
                    ["Facts", docSummary.facts],
                    ["Insights", docSummary.insights],
                  ] as const
                ).map(([title, items]) =>
                  items?.length ? (
                    <section key={title}>
                      <h5 className="pm-label mb-1.5">{title}</h5>
                      <ul className="list-disc pl-4 space-y-1">
                        {items.map((t, i) => (
                          <li key={i} className="pm-ws-prose-item">
                            {t}
                          </li>
                        ))}
                      </ul>
                    </section>
                  ) : null
                )}
                {!docSummary.data?.length &&
                  !docSummary.facts?.length &&
                  !docSummary.insights?.length && (
                    <p className="pm-meta">No summary content.</p>
                  )}
              </div>
            ) : (
              <div className="pm-ws-empty flex flex-col items-center justify-center py-8 gap-2 px-4">
                <p className="pm-meta">No summary for this version.</p>
                {!isCurrentVersion && (
                  <p className="pm-meta max-w-sm">
                    Summaries are only generated for the current version.
                  </p>
                )}
              </div>
            )}
          </div>
        </ScrollArea>
      </TabsContent>

      {/* Chunks */}
      <TabsContent
        value="chunks"
        className="flex-1 overflow-hidden min-h-0 data-[state=inactive]:hidden"
      >
        <div className="pm-ws-doc-stage flex flex-col">
          <ScrollArea className="flex-1 min-h-0">
            <div className="p-3 space-y-2">
              {chunksLoading ? (
                <div className="pm-ws-loading py-12">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading…
                </div>
              ) : chunks.length === 0 ? (
                <div className="pm-ws-empty flex flex-col items-center justify-center py-8 gap-2 px-4">
                  <p className="pm-meta">No chunks for this version.</p>
                  <p className="pm-meta max-w-sm">
                    Chunks appear after this version was ingested into the
                    vector store. Unsupported uploads and failed ingests leave
                    this empty.
                  </p>
                </div>
              ) : (
                chunks.map((chunk) => (
                  <div key={chunk.id} className="pm-ws-tile">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="pm-meta tabular-nums text-[var(--pm-green)]">
                        #{chunk.chunk_index}
                      </span>
                      {chunk.heading_path && (
                        <span className="pm-meta truncate">
                          {chunk.heading_path}
                        </span>
                      )}
                    </div>
                    <p className="pm-ws-prose-item whitespace-pre-wrap">
                      {chunk.text}
                    </p>
                  </div>
                ))
              )}
            </div>
          </ScrollArea>
        </div>
      </TabsContent>
    </Tabs>
  )
}
