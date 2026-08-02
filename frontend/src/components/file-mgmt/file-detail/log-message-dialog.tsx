/**
 * Log message detail from file detail:
 * - Normal message: single-column view + Edit top-right
 * - Version update: left = Source/Raw/Summary/Chunks for that version;
 *   right = message body with Edit inside the message panel
 * - Non-current versions: Delete on left tab bar (blob + Qdrant + log link)
 */

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Tabs,
  TabsContent,
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
import { deleteFileVersion, updateMessage } from "@/api/file-mgmt"
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
  onSaved?: () => void
  /** After permanently deleting a non-current version */
  onVersionDeleted?: () => void
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
  const isVersionUpdate =
    !!message &&
    ((message.owner_type || "").toLowerCase() === "system_version" || !!version)

  const [editing, setEditing] = useState(false)
  const [content, setContent] = useState("")
  const [saving, setSaving] = useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    if (!open || !message) return
    setEditing(false)
    setDeleteConfirmOpen(false)
    setContent(
      isVersionUpdate
        ? versionUpdateBody(message.body)
        : message.body || ""
    )
  }, [open, message?.message_id, message?.body, isVersionUpdate])

  const handleSave = useCallback(async () => {
    if (!message) return
    const body = isVersionUpdate
      ? versionUpdateBody(content)
      : content.trim()
    if (!body && !isVersionUpdate) return
    setSaving(true)
    try {
      await updateMessage(collectionId, message.message_id, {
        body: body || "version update",
        version: message.version,
      })
      toast.success("Message saved")
      setEditing(false)
      onSaved?.()
    } catch (err) {
      toast.error(
        `Save failed: ${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      setSaving(false)
    }
  }, [message, content, isVersionUpdate, collectionId, onSaved])

  const handleDeleteVersion = useCallback(async () => {
    if (!version || isCurrentVersion) return
    const fileId = version.file_id
    const versionId = version.version_id
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
    version,
    isCurrentVersion,
    collectionId,
    onOpenChange,
    onVersionDeleted,
  ])

  const dialogMotion = cn(
    "duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
    "data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-open:slide-in-from-bottom-3",
    "data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95 data-closed:slide-out-to-bottom-2"
  )

  if (!message) return null

  // ── Version update: dual pane ──
  if (isVersionUpdate) {
    return (
      <>
        <Dialog open={open} onOpenChange={onOpenChange}>
          <DialogContent
            className={cn(
              "w-[min(1200px,94vw)] max-w-[94vw] sm:max-w-[94vw]",
              "h-[min(88vh,820px)] flex flex-col gap-0 p-0 overflow-hidden",
              dialogMotion
            )}
          >
            <DialogHeader className="px-4 py-3 border-b border-border shrink-0">
              <DialogTitle className="text-sm flex items-center gap-2 min-w-0 pr-8">
                <Badge
                  variant="secondary"
                  className="text-[9px] shrink-0 border-transparent bg-[var(--ze-green,#1A5E3D)]/15 text-[var(--ze-green,#1A5E3D)]"
                >
                  version update
                </Badge>
                {version && (
                  <span className="text-xs text-muted-foreground shrink-0">
                    v{version.version_no}
                    {version.archived ? " · archived" : ""}
                    {isCurrentVersion ? " · current" : ""}
                  </span>
                )}
                <span className="text-[11px] text-muted-foreground font-normal tabular-nums ml-auto mr-2">
                  {formatTime(message.created_at)}
                </span>
              </DialogTitle>
            </DialogHeader>

            <div className="flex-1 min-h-0 flex overflow-hidden">
              {/* Left: version file Source / Raw / Summary / Chunks */}
              <div className="flex-[1.35] min-w-0 min-h-0 flex flex-col border-r border-border p-3">
                <VersionFileTabs
                  collectionId={collectionId}
                  docSource={docSource}
                  version={version}
                  isCurrentVersion={isCurrentVersion}
                  onRequestDelete={
                    !isCurrentVersion && version
                      ? () => setDeleteConfirmOpen(true)
                      : undefined
                  }
                />
              </div>

              {/* Right: message content + edit inside panel */}
              <div className="flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden">
                <div className="shrink-0 px-3 py-2 border-b border-border/60 flex items-center justify-between gap-2">
                  <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-medium">
                    Message
                  </p>
                  {editing ? (
                    <div className="flex items-center gap-1.5">
                      <Button
                        variant="outline"
                        size="xs"
                        className="h-7"
                        disabled={saving}
                        onClick={() => {
                          setEditing(false)
                          setContent(versionUpdateBody(message.body))
                        }}
                      >
                        Cancel
                      </Button>
                      <Button
                        size="xs"
                        className="h-7"
                        disabled={saving}
                        onClick={() => void handleSave()}
                      >
                        {saving ? "Saving…" : "Save"}
                      </Button>
                    </div>
                  ) : (
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      className="h-7 gap-1 text-muted-foreground hover:text-foreground"
                      onClick={() => setEditing(true)}
                    >
                      <Pencil className="h-3 w-3" />
                      Edit
                    </Button>
                  )}
                </div>
                <div className="flex-1 min-h-0 overflow-hidden">
                  {editing ? (
                    <div className="h-full min-h-0 overflow-auto p-3">
                      <MarkdownEditor
                        value={content}
                        onChange={setContent}
                        minHeight="240px"
                        placeholder={MESSAGE_EDITOR_PLACEHOLDER}
                        showToolbar={false}
                      />
                    </div>
                  ) : (
                    <ScrollArea className="h-full">
                      <div className="p-4">
                        <MessageBody
                          body={content}
                          className="prose prose-sm dark:prose-invert max-w-none text-sm leading-relaxed break-words [&_p]:my-2"
                        />
                      </div>
                    </ScrollArea>
                  )}
                </div>
              </div>
            </div>
          </DialogContent>
        </Dialog>

        {/* Secondary confirm: permanently delete this non-current version */}
        <Dialog
          open={deleteConfirmOpen}
          onOpenChange={(v) => {
            if (!deleting) setDeleteConfirmOpen(v)
          }}
        >
          <DialogContent className="max-w-sm">
            <DialogHeader>
              <DialogTitle>Delete this version?</DialogTitle>
            </DialogHeader>
            <p className="text-sm text-muted-foreground">
              Permanently remove{" "}
              <span className="font-medium text-foreground">
                v{version?.version_no}
                {version?.storage_file_id
                  ? ` (${version.storage_file_id})`
                  : ""}
              </span>
              . This deletes the version blob, its vectors in the database, and
              the linked log entry. This cannot be undone.
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <Button
                variant="outline"
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

  // ── Normal message: single column, Edit top-right ──
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "w-[min(900px,92vw)] max-w-[92vw] sm:max-w-[92vw]",
          "h-[min(80vh,700px)] flex flex-col overflow-hidden",
          dialogMotion
        )}
      >
        <DialogHeader className="shrink-0">
          <DialogTitle className="text-sm flex items-center gap-2 min-w-0 pr-28">
            <Badge variant="secondary" className="text-[9px] shrink-0">
              message
            </Badge>
            <span className="text-[11px] text-muted-foreground font-normal">
              {message.author_id || "user"}
            </span>
            {message.edited_at && (
              <Badge variant="outline" className="text-[9px]">
                edited
              </Badge>
            )}
            <span className="text-[11px] text-muted-foreground font-normal tabular-nums ml-auto">
              {formatTime(message.created_at)}
            </span>
          </DialogTitle>
          <div className="absolute top-3.5 right-12 z-10 flex items-center gap-1.5">
            {editing ? (
              <>
                <Button
                  variant="outline"
                  size="xs"
                  disabled={saving}
                  onClick={() => {
                    setEditing(false)
                    setContent(message.body || "")
                  }}
                >
                  Cancel
                </Button>
                <Button
                  size="xs"
                  disabled={saving || !content.trim()}
                  onClick={() => void handleSave()}
                >
                  {saving ? "Saving…" : "Save"}
                </Button>
              </>
            ) : (
              message.author_type !== "system" && (
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  className="gap-1 h-7 px-2 text-muted-foreground hover:text-foreground"
                  onClick={() => setEditing(true)}
                >
                  <Pencil className="h-3 w-3" />
                  Edit
                </Button>
              )
            )}
          </div>
        </DialogHeader>
        <div className="flex-1 min-h-0 overflow-auto flex flex-col">
          {editing ? (
            <MarkdownEditor
              value={content}
              onChange={setContent}
              minHeight="280px"
              placeholder={MESSAGE_EDITOR_PLACEHOLDER}
              showToolbar={false}
            />
          ) : (
            <div className="p-4 text-sm leading-relaxed flex-1 overflow-auto">
              <MessageBody
                body={content}
                className="prose prose-sm dark:prose-invert max-w-none text-sm leading-relaxed break-words [&_p]:my-2"
              />
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

// ── Left pane: Source / Raw / Summary / Chunks for a version ──

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
  /** When set (non-current only), show Delete on the tab bar right */
  onRequestDelete?: () => void
}) {
  const [tab, setTab] = useState("source")
  const [previewContent, setPreviewContent] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [chunks, setChunks] = useState<ChunkDetail[]>([])
  const [chunksTotal, setChunksTotal] = useState(0)
  const [chunksLoading, setChunksLoading] = useState(false)
  const [docSummary, setDocSummary] = useState<DocSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)

  // Always bind preview to *this* version's blob. Never fall back to current
  // file resolution when version is known — otherwise an unsupported current
  // version would make every historical Log card look unpreviewable.
  const storageFile = version?.storage_file_id || null
  const ext = (storageFile || "").split(".").pop()?.toLowerCase() || ""
  const isPdf = ext === "pdf"

  const previewUrl = useMemo(() => {
    if (!docSource || !storageFile) return null
    return getFilePreviewUrl(docSource, {
      collection: collectionId,
      storageFile,
      versionId: version?.version_id || undefined,
    })
  }, [docSource, collectionId, storageFile, version?.version_id])

  // Source = parse/extract text for *this* version (parsed.txt / .extracted.txt
  // cache, or text-like file body). Raw = original file (PDF iframe / download).
  useEffect(() => {
    if (!docSource || !collectionId || !storageFile) {
      setPreviewContent(null)
      setPreviewLoading(false)
      return
    }
    let cancelled = false
    setPreviewLoading(true)
    getExtractedText(docSource, collectionId, {
      storageFile,
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
  }, [docSource, collectionId, storageFile])

  // Chunks: this version_id when known (works for archived historical versions).
  // Summary: document-level (one per source), only meaningful for current ingest.
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

    // Chunks for this version
    setChunksLoading(true)
    getFileChunks(collectionId, docSource, 10000, {
      versionId: versionId || undefined,
      // Without version_id, only current (non-archived) chunks
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

    // Summary only for current version (not versioned in store)
    if (!isCurrentVersion) {
      setDocSummary(null)
      setSummaryLoading(false)
    } else {
      setSummaryLoading(true)
      getDocSummary(collectionId, docSource)
        .then((res) => {
          if (!cancelled) setDocSummary(res)
        })
        .catch(() => {
          if (!cancelled) setDocSummary(null)
        })
        .finally(() => {
          if (!cancelled) setSummaryLoading(false)
        })
    }

    return () => {
      cancelled = true
    }
  }, [docSource, collectionId, isCurrentVersion, versionId])

  return (
    <Tabs
      value={tab}
      onValueChange={setTab}
      className="flex flex-col h-full min-h-0 gap-2"
    >
      <div className="shrink-0 flex items-center gap-1 border-b border-border">
        <TabsList className="h-8 flex-1 min-w-0 justify-start bg-transparent p-0 gap-1 rounded-none border-0">
          {(["source", "raw", "summary", "chunks"] as const).map((v) => (
            <TabsTrigger
              key={v}
              value={v}
              className="font-light uppercase tracking-wider text-[11px] after:!opacity-0 data-[state=active]:text-primary rounded-none px-2.5 h-8"
            >
              {v === "chunks" ? (
                <>
                  Chunks
                  {chunksTotal > 0 && (
                    <span className="ml-1 tabular-nums text-[10px] text-muted-foreground">
                      {chunksTotal}
                    </span>
                  )}
                </>
              ) : (
                v
              )}
            </TabsTrigger>
          ))}
        </TabsList>
        {onRequestDelete && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            className="h-7 shrink-0 gap-1 mr-0.5 text-destructive/80 hover:text-destructive hover:bg-destructive/10"
            onClick={onRequestDelete}
            title="Permanently delete this version"
          >
            <Trash2 className="h-3 w-3" />
            Delete
          </Button>
        )}
      </div>

      <TabsContent
        value="source"
        className="flex-1 min-h-0 overflow-hidden data-[state=inactive]:hidden mt-0"
      >
        <div className="h-full rounded-lg border border-border overflow-hidden">
          {previewLoading ? (
            <div className="flex items-center justify-center h-full text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
              Loading…
            </div>
          ) : previewContent ? (
            <ScrollArea className="h-full">
              <div className="p-3">
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
            <div className="flex flex-col items-center justify-center h-full text-sm text-muted-foreground p-4 text-center gap-2">
              {!storageFile ? (
                <p>No version file linked to this message.</p>
              ) : (
                <>
                  <p>No parsed text for this version.</p>
                  <p className="text-xs max-w-sm">
                    Source shows text after parse/ingest. If this version was
                    never ingested (or the parse cache is missing), use{" "}
                    <span className="font-medium text-foreground">Raw</span> for
                    the original file
                    {isPdf ? " (PDF preview)" : ""}.
                  </p>
                </>
              )}
            </div>
          )}
        </div>
      </TabsContent>

      <TabsContent
        value="raw"
        className="flex-1 min-h-0 overflow-hidden data-[state=inactive]:hidden mt-0"
      >
        <RawFileViewer
          url={previewUrl}
          filename={resolveRawFilename(storageFile, version?.storage_file_id)}
          downloadUrl={previewUrl}
          className="h-full"
        />
      </TabsContent>

      <TabsContent
        value="summary"
        className="flex-1 min-h-0 overflow-hidden data-[state=inactive]:hidden mt-0"
      >
        <ScrollArea className="h-full rounded-lg border border-border">
          <div className="p-3">
            {!isCurrentVersion ? (
              <div className="space-y-2 text-sm text-muted-foreground">
                <p>No per-version summary.</p>
                <p className="text-xs">
                  Document summary is generated for the current ingested version
                  only (one summary per file, not per history entry). Open the
                  current version update to view it.
                </p>
              </div>
            ) : summaryLoading ? (
              <div className="flex items-center justify-center py-8 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
                Loading…
              </div>
            ) : docSummary ? (
              <div className="space-y-3 text-sm">
                {(
                  [
                    ["Data", docSummary.data],
                    ["Facts", docSummary.facts],
                    ["Insights", docSummary.insights],
                  ] as const
                ).map(([title, items]) =>
                  items?.length ? (
                    <section key={title}>
                      <h5 className="text-[10px] uppercase text-muted-foreground mb-1">
                        {title}
                      </h5>
                      <ul className="list-disc pl-4 space-y-1">
                        {items.map((t, i) => (
                          <li key={i}>{t}</li>
                        ))}
                      </ul>
                    </section>
                  ) : null
                )}
                {!docSummary.data?.length &&
                  !docSummary.facts?.length &&
                  !docSummary.insights?.length && (
                    <p className="text-muted-foreground">No summary content.</p>
                  )}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No summary yet.</p>
            )}
          </div>
        </ScrollArea>
      </TabsContent>

      <TabsContent
        value="chunks"
        className="flex-1 min-h-0 overflow-hidden data-[state=inactive]:hidden mt-0"
      >
        <ScrollArea className="h-full rounded-lg border border-border">
          <div className="p-3 space-y-2">
            {chunksLoading ? (
              <div className="flex items-center justify-center py-8 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
                Loading…
              </div>
            ) : chunks.length === 0 ? (
              <div className="space-y-2 text-sm text-muted-foreground">
                <p>No chunks for this version.</p>
                <p className="text-xs">
                  Chunks appear after this version was ingested into the vector
                  store. Unsupported uploads and failed ingests leave this empty.
                </p>
              </div>
            ) : (
              chunks.map((chunk) => (
                <div
                  key={chunk.id}
                  className="rounded-md border border-border/50 p-2 text-xs"
                >
                  <div className="flex items-center gap-2 mb-1">
                    <Badge variant="outline" className="text-[9px]">
                      #{chunk.chunk_index}
                    </Badge>
                    {chunk.heading_path && (
                      <span className="text-[10px] text-muted-foreground truncate">
                        {chunk.heading_path}
                      </span>
                    )}
                  </div>
                  <p className="leading-relaxed whitespace-pre-wrap">
                    {chunk.text}
                  </p>
                </div>
              ))
            )}
          </div>
        </ScrollArea>
      </TabsContent>
    </Tabs>
  )
}
