import { useState, useCallback, useEffect, useMemo } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { MESSAGE_EDITOR_PLACEHOLDER } from "@/components/ui/tiptap-editor"
import { Calendar, Loader2, Paperclip, Pencil } from "lucide-react"
import type { Message, NodeDetail, NodeGroup } from "@/types/file-mgmt"
import {
  getNodeDetail,
  getNodeMessages,
  listGroups,
  updateMessage,
} from "@/api/file-mgmt"
import { formatMessageSourceTag, MessageBody, MessagePreview } from "../message-card"
import { MiniChainGraph } from "../mini-chain-graph"
import { cn } from "@/lib/utils"

interface MessageEditorDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  initialContent: string
  onSave: (content: string) => void
  /** Initial open mode; user can switch to edit via in-dialog button */
  readonly?: boolean
  /** When set and owner is node, show detail + chain graph layout */
  message?: Message | null
  collectionId?: string
  /** Active folder scope — folder msgs with this owner show "Current folder" */
  highlightFolderId?: string | null
  folderMsgsAreCurrentScope?: boolean
  /** In-app navigation to timeline (must not open a new window) */
  onNavigateToNode?: (nodeId: string, chainId: string) => void
  /**
   * Parent sync when user picks another message on the same node.
   * Prefer this so save still targets the correct message_id.
   */
  onSelectNodeMessage?: (msg: Message) => void
}

export function MessageEditorDialog({
  open,
  onOpenChange,
  title,
  initialContent,
  onSave,
  readonly = true,
  message = null,
  collectionId,
  highlightFolderId = null,
  folderMsgsAreCurrentScope = false,
  onNavigateToNode,
  onSelectNodeMessage,
}: MessageEditorDialogProps) {
  /** Currently viewed/edited message (can switch via node message list). */
  const [activeMsg, setActiveMsg] = useState<Message | null>(message)
  const [content, setContent] = useState(initialContent)
  const [editing, setEditing] = useState(!readonly)
  const [saving, setSaving] = useState(false)

  const isNodeMsg =
    !!activeMsg &&
    (activeMsg.owner_type || "").toLowerCase() === "node" &&
    !!activeMsg.owner_id
  const isSystem = activeMsg?.author_type === "system"
  const sourceTag = activeMsg
    ? formatMessageSourceTag(activeMsg, {
        highlightFolderId,
        folderMsgsAreCurrentScope,
      })
    : null

  const [nodeDetail, setNodeDetail] = useState<NodeDetail | null>(null)
  const [nodeMsgs, setNodeMsgs] = useState<Message[]>([])
  const [groups, setGroups] = useState<NodeGroup[]>([])
  const [nodeLoading, setNodeLoading] = useState(false)

  // Sync when parent opens / switches message (key remount or prop change)
  useEffect(() => {
    if (open) {
      setActiveMsg(message)
      setContent(initialContent)
      setEditing(!readonly)
    }
  }, [open, message?.message_id, initialContent, readonly, message])

  const ownerId = activeMsg?.owner_id

  useEffect(() => {
    if (!open || !isNodeMsg || !collectionId || !ownerId) {
      setNodeDetail(null)
      setNodeMsgs([])
      return
    }
    let cancelled = false
    setNodeLoading(true)
    Promise.all([
      getNodeDetail(collectionId, ownerId),
      getNodeMessages(collectionId, ownerId),
      listGroups(collectionId),
    ])
      .then(([d, msgs, gs]) => {
        if (cancelled) return
        setNodeDetail(d)
        setNodeMsgs(msgs)
        setGroups(gs)
      })
      .catch(() => {
        if (!cancelled) {
          setNodeDetail(null)
          setNodeMsgs([])
        }
      })
      .finally(() => {
        if (!cancelled) setNodeLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, isNodeMsg, collectionId, ownerId])

  const handleOpenChange = useCallback(
    (o: boolean) => {
      if (o) {
        setActiveMsg(message)
        setContent(initialContent)
        setEditing(!readonly)
      }
      onOpenChange(o)
    },
    [initialContent, onOpenChange, readonly, message]
  )

  const handleSave = useCallback(async () => {
    const trimmed = content.trim()
    if (!trimmed) return
    setSaving(true)
    try {
      // New message: no activeMsg yet — always create via parent onSave
      if (!activeMsg) {
        onSave(trimmed)
        setEditing(false)
        onOpenChange(false)
        return
      }
      // Same message the parent opened (or parent did not pass message): parent onSave
      if (!message || activeMsg.message_id === message.message_id) {
        onSave(trimmed)
        setEditing(false)
        onOpenChange(false)
        return
      }
      // Switched to another node message inside the dialog — patch via API
      if (collectionId) {
        const updated = await updateMessage(collectionId, activeMsg.message_id, {
          body: trimmed,
          version: activeMsg.version,
        })
        setActiveMsg(updated)
        setNodeMsgs((prev) =>
          prev.map((m) => (m.message_id === updated.message_id ? updated : m))
        )
        setContent(updated.body || "")
        setEditing(false)
        onSelectNodeMessage?.(updated)
      } else {
        onSave(trimmed)
        setEditing(false)
        onOpenChange(false)
      }
    } finally {
      setSaving(false)
    }
  }, [
    content,
    activeMsg,
    message,
    onSave,
    onOpenChange,
    collectionId,
    onSelectNodeMessage,
  ])

  const handleCancelEdit = useCallback(() => {
    setContent(activeMsg?.body || initialContent)
    setEditing(false)
  }, [activeMsg?.body, initialContent])

  const handleSelectNodeMessage = useCallback(
    (m: Message) => {
      if (m.message_id === activeMsg?.message_id) return
      // Discard in-progress edits when switching
      setActiveMsg(m)
      setContent(m.body || "")
      setEditing(false)
      onSelectNodeMessage?.(m)
    },
    [activeMsg?.message_id, onSelectNodeMessage]
  )

  const groupName = nodeDetail?.group_id
    ? groups.find((g) => g.group_id === nodeDetail.group_id)?.name ?? "—"
    : "未分类"

  const sortedNodeMsgs = useMemo(() => {
    return [...nodeMsgs].sort((a, b) => {
      const ta = a.created_at || ""
      const tb = b.created_at || ""
      return tb.localeCompare(ta)
    })
  }, [nodeMsgs])

  // Enter/exit must stay mounted (portal keepMounted + no key flip on close)
  // Symmetric open/close durations (Premium motion)
  const dialogMotion = cn(
    "duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]",
    "data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-open:slide-in-from-bottom-2",
    "data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95 data-closed:slide-out-to-bottom-2",
    "data-closed:duration-300 data-open:duration-300"
  )

  // Non-node: single-column dialog; Edit / Cancel / Save sit top-right
  if (!isNodeMsg) {
    return (
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent
          className={cn(
            "pm-dialog w-[1200px] max-w-[90vw] sm:max-w-[90vw] h-[85vh] flex flex-col overflow-hidden",
            dialogMotion
          )}
        >
          <DialogHeader className="shrink-0">
            <DialogTitle className="flex items-center gap-2 min-w-0 pr-28">
              <span className="shrink-0">{title}</span>
              {sourceTag && (
                <span
                  className={cn(
                    "pm-meta px-1.5 py-0.5 rounded truncate max-w-[12rem]",
                    sourceTag.isCurrentFolder
                      ? "text-[var(--pm-green)] bg-[var(--pm-green-soft)]"
                      : "text-[var(--pm-muted)] bg-[rgba(18,20,16,0.05)]"
                  )}
                  title={sourceTag.full}
                >
                  {sourceTag.label}
                </span>
              )}
            </DialogTitle>
            {/* Top-right actions — leave room for dialog close (X) */}
            <div className="absolute top-3.5 right-12 z-10 flex items-center gap-1.5">
              {editing ? (
                <>
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={handleCancelEdit}
                    disabled={saving}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="xs"
                    className="pm-btn-pri"
                    onClick={() => void handleSave()}
                    disabled={!content.trim() || saving}
                  >
                    {saving ? "Saving…" : "Save"}
                  </Button>
                </>
              ) : (
                !isSystem &&
                activeMsg && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    className="gap-1 h-7 px-2 text-[var(--pm-muted)] hover:text-[var(--pm-ink)]"
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
              <div className="p-4 pm-prose max-w-none flex-1 overflow-auto">
                <MessageBody
                  body={activeMsg?.body || initialContent}
                  className="prose prose-sm max-w-none leading-relaxed break-words [&_p]:my-2"
                />
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    )
  }

  // Node message: message | detail | timeline
  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className={cn(
          "pm-dialog w-[1200px] max-w-[92vw] sm:max-w-[92vw] h-[88vh] flex flex-col gap-0 p-0 overflow-hidden",
          dialogMotion
        )}
      >
        <DialogHeader className="px-4 py-3 shrink-0 shadow-[inset_0_-1px_0_color-mix(in_srgb,var(--pm-ink)_8%,transparent)]">
          <DialogTitle className="flex items-center gap-2 min-w-0 pr-8">
            <span className="shrink-0">{title}</span>
            {sourceTag && (
              <span
                className="pm-meta text-[var(--pm-green)] bg-[var(--pm-green-soft)] px-1.5 py-0.5 rounded truncate max-w-[14rem]"
                title={sourceTag.full}
              >
                {sourceTag.label}
              </span>
            )}
          </DialogTitle>
        </DialogHeader>

        <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
          <div className="flex-1 min-h-0 flex shadow-[inset_0_-1px_0_color-mix(in_srgb,var(--pm-ink)_8%,transparent)] overflow-hidden">
            {/* Left: active message preview / edit */}
            <div className="flex-[1.4] min-w-0 min-h-0 flex flex-col shadow-[inset_-1px_0_0_color-mix(in_srgb,var(--pm-ink)_8%,transparent)] overflow-hidden relative">
              <div className="absolute top-2 right-2 z-10 flex items-center gap-1.5">
                {editing ? (
                  <>
                    <Button
                      variant="ghost"
                      size="xs"
                      onClick={handleCancelEdit}
                      disabled={saving}
                    >
                      Cancel
                    </Button>
                    <Button
                      size="xs"
                      className="pm-btn-pri"
                      onClick={() => void handleSave()}
                      disabled={!content.trim() || saving}
                    >
                      {saving ? "Saving…" : "Save"}
                    </Button>
                  </>
                ) : (
                  !isSystem && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      className="gap-1 h-7 px-2 text-[var(--pm-muted)] hover:text-[var(--pm-ink)]"
                      onClick={() => setEditing(true)}
                    >
                      <Pencil className="h-3 w-3" />
                      Edit
                    </Button>
                  )
                )}
              </div>
              <div className="flex-1 min-h-0 overflow-auto">
                {/* key forces a short fade when switching node messages */}
                <div
                  key={activeMsg?.message_id ?? "empty"}
                  className="h-full min-h-0 animate-in fade-in-0 duration-200"
                >
                  {editing ? (
                    <div className="p-4 pt-10 h-full min-h-0">
                      <MarkdownEditor
                        value={content}
                        onChange={setContent}
                        minHeight="140px"
                        placeholder={MESSAGE_EDITOR_PLACEHOLDER}
                        showToolbar={false}
                      />
                    </div>
                  ) : (
                    <div className="p-4 pt-10 pr-16 pm-prose max-w-none">
                      <MessageBody
                        body={activeMsg?.body || ""}
                        className="prose prose-sm max-w-none leading-relaxed break-words [&_p]:my-2"
                      />
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Right: node meta + clickable message list */}
            <div className="flex-1 min-w-0 min-h-0 flex flex-col bg-[var(--pm-green-wash)] overflow-hidden">
              <div className="shrink-0 p-4 space-y-3 shadow-[inset_0_-1px_0_color-mix(in_srgb,var(--pm-ink)_8%,transparent)]">
                <p className="pm-label">Node detail</p>
                {nodeLoading ? (
                  <div className="flex justify-center py-4">
                    <Loader2 className="h-4 w-4 animate-spin text-[var(--pm-faint)]" />
                  </div>
                ) : !nodeDetail ? (
                  <p className="pm-meta">Node not found</p>
                ) : (
                  <>
                    <div>
                      <p className="pm-field-label mb-0.5">Title</p>
                      <p className="pm-title">{nodeDetail.title || "Untitled"}</p>
                    </div>
                    <div className="flex gap-4">
                      <div>
                        <p className="pm-field-label mb-0.5">Group</p>
                        <p className="pm-meta">{groupName}</p>
                      </div>
                      <div>
                        <p className="pm-field-label mb-0.5">Type</p>
                        <p className="pm-meta uppercase">{nodeDetail.node_type}</p>
                      </div>
                    </div>
                    {nodeDetail.event_time && (
                      <div className="flex items-center gap-1.5 pm-meta">
                        <Calendar className="h-3 w-3" />
                        {nodeDetail.event_time.slice(0, 10)}
                      </div>
                    )}
                    <div>
                      <p className="pm-field-label mb-1">
                        Attachments ({nodeDetail.attachments?.length ?? 0})
                      </p>
                      {(nodeDetail.attachments?.length ?? 0) === 0 ? (
                        <p className="pm-meta">None</p>
                      ) : (
                        <ul className="space-y-1">
                          {nodeDetail.attachments.map((a) => (
                            <li
                              key={a.file_id}
                              className="flex items-center gap-1.5 pm-meta px-2 py-1 rounded-[var(--pm-r-sm)] bg-white"
                            >
                              <Paperclip className="h-3 w-3 text-[var(--pm-faint)] shrink-0" />
                              <span className="truncate">
                                {a.filename || a.file_id}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  </>
                )}
              </div>

              {/* Message list fills remaining height; fixed row height */}
              <div className="flex-1 min-h-0 flex flex-col p-3 pt-2.5">
                <p className="pm-label shrink-0 mb-1.5">
                  Node messages ({sortedNodeMsgs.length})
                </p>
                {nodeLoading ? null : sortedNodeMsgs.length === 0 ? (
                  <p className="pm-meta">None</p>
                ) : (
                  <div className="flex-1 min-h-0 overflow-y-auto space-y-1.5 pr-0.5">
                    {sortedNodeMsgs.map((m) => {
                      const selected = m.message_id === activeMsg?.message_id
                      return (
                        <button
                          key={m.message_id}
                          type="button"
                          onClick={() => handleSelectNodeMessage(m)}
                          className={cn(
                            "w-full text-left rounded-[var(--pm-r-sm)] px-2.5 py-2 transition-colors duration-150",
                            "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--pm-green-soft)]",
                            selected
                              ? "bg-[var(--pm-green-soft)] shadow-sm"
                              : "bg-white/80 hover:bg-white"
                          )}
                        >
                          {/* Render markdown + Tiptap HTML colors (not raw source) */}
                          <div className="min-h-[2.5rem]">
                            {(m.body || "").trim() ? (
                              <MessagePreview body={m.body || ""} lines={2} />
                            ) : (
                              <span className="pm-meta italic">(empty)</span>
                            )}
                          </div>
                          {m.created_at && (
                            <p className="pm-meta mt-1 tabular-nums">
                              {m.created_at.slice(0, 16).replace("T", " ")}
                              {m.edited_at ? " · edited" : ""}
                            </p>
                          )}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="h-[32%] min-h-[140px] shrink-0 overflow-hidden">
            <div className="px-3 pt-1.5 flex items-center justify-between">
              <p className="pm-label">Timeline</p>
              <p className="pm-meta">
                Click a node to open Timeline view
              </p>
            </div>
            {collectionId && ownerId && (
              <MiniChainGraph
                collectionId={collectionId}
                nodeId={ownerId}
                className="h-[calc(100%-22px)]"
                onNodeClick={(nid, cid) => {
                  // Parent closes dialog + delays timeline switch for exit motion
                  onNavigateToNode?.(nid, cid)
                }}
              />
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
