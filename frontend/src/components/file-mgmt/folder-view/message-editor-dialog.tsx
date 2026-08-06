import { useState, useCallback, useEffect, useMemo } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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

  /**
   * Silk open/close (same as todo / Note) — opacity + scale + overlay fade.
   * Parent must keep dialog mounted while open→false (no hard unmount).
   */
  const silkShell = cn(
    "pm-dialog pm-dialog--silk",
    "animate-none data-open:animate-none data-closed:animate-none"
  )

  const bodyClass =
    "prose prose-sm max-w-none leading-relaxed break-words [&_p]:my-2 font-[family-name:var(--pm-ff-prose)]"

  /** Chrome actions: Save when editing, Edit when viewing (non-system). */
  const chromeActions = (
    <div className="pm-msg-dialog-actions">
      {editing ? (
        <button
          type="button"
          className="pm-btn-pri pm-btn-xs"
          onClick={() => void handleSave()}
          disabled={!content.trim() || saving}
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
        !isSystem &&
        activeMsg && (
          <button
            type="button"
            className="pm-btn-ghost pm-btn-xs gap-1"
            onClick={() => setEditing(true)}
          >
            <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} />
            Edit
          </button>
        )
      )}
    </div>
  )

  const titleRow = (
    <DialogTitle className="flex items-center gap-2 min-w-0 text-left">
      <span className="shrink-0">{title}</span>
      {sourceTag && (
        <span
          className={cn(
            "pm-meta normal-case tracking-normal px-1.5 py-0.5 rounded truncate max-w-[12rem]",
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
  )

  /** Message body card — same surface for Add / preview / edit. */
  const messageCard = (
    <div
      key={activeMsg?.message_id ?? "new"}
      className="pm-msg-dialog-card flex-1 min-h-0 flex flex-col overflow-hidden"
    >
      {editing ? (
        <div className="flex-1 min-h-0 flex flex-col pm-msg-editor-host">
          <MarkdownEditor
            value={content}
            onChange={setContent}
            minHeight={isNodeMsg ? "140px" : "280px"}
            placeholder={MESSAGE_EDITOR_PLACEHOLDER}
            showToolbar
            flush
            className="flex-1 min-h-0"
          />
        </div>
      ) : (
        <div className="p-5 pm-prose max-w-none flex-1 overflow-auto">
          <MessageBody
            body={activeMsg?.body || initialContent}
            className={bodyClass}
          />
        </div>
      )}
    </div>
  )

  // ── Single column (Add Message + file/folder message preview/edit) ──
  if (!isNodeMsg) {
    return (
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent
          overlayClassName="pm-dialog-overlay--silk"
          className={cn(
            silkShell,
            "pm-msg-dialog w-[min(1200px,90vw)] max-w-[90vw] sm:max-w-[90vw] h-[85vh] flex flex-col overflow-hidden gap-0 p-0"
          )}
        >
          <DialogHeader className="pm-msg-dialog-chrome shrink-0">
            {titleRow}
            {chromeActions}
          </DialogHeader>
          <div className="pm-msg-dialog-stage flex-1 min-h-0 overflow-hidden flex flex-col">
            {messageCard}
          </div>
        </DialogContent>
      </Dialog>
    )
  }

  // ── Node message: same chrome + floating cards (message | detail | timeline)
  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        overlayClassName="pm-dialog-overlay--silk"
        className={cn(
          silkShell,
          "pm-msg-dialog w-[min(1200px,92vw)] max-w-[92vw] sm:max-w-[92vw] h-[88vh] flex flex-col overflow-hidden gap-0 p-0"
        )}
      >
        <DialogHeader className="pm-msg-dialog-chrome shrink-0">
          {titleRow}
          {chromeActions}
        </DialogHeader>

        <div className="pm-msg-dialog-stage flex-1 min-h-0 overflow-hidden flex flex-col gap-3">
          {/* Upper: message card + node meta card */}
          <div className="flex-1 min-h-0 flex gap-3 overflow-hidden">
            {/* Left: active message preview / edit */}
            <div className="flex-[1.4] min-w-0 min-h-0 flex flex-col overflow-hidden">
              {messageCard}
            </div>

            {/* Right: node meta + message list */}
            <div className="pm-msg-dialog-card flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden">
              <div className="shrink-0 p-4 space-y-3 border-b border-[color-mix(in_srgb,var(--pm-ink)_6%,transparent)]">
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
                              className="flex items-center gap-1.5 pm-meta px-2 py-1 rounded-[var(--pm-r-sm)] bg-[var(--pm-canvas)]"
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

              {/* Message list fills remaining height */}
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
                              ? "bg-[var(--pm-green-soft)]"
                              : "bg-[var(--pm-canvas)] hover:bg-[var(--pm-green-wash)]"
                          )}
                        >
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

          {/* Lower: timeline in floating card */}
          <div className="pm-msg-dialog-card h-[32%] min-h-[140px] shrink-0 overflow-hidden flex flex-col">
            <div className="px-3 pt-2 pb-1 flex items-center justify-between shrink-0">
              <p className="pm-label">Timeline</p>
              <p className="pm-meta">Click a node to open Timeline view</p>
            </div>
            {collectionId && ownerId && (
              <MiniChainGraph
                collectionId={collectionId}
                nodeId={ownerId}
                className="flex-1 min-h-0"
                onNodeClick={(nid, cid) => {
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
