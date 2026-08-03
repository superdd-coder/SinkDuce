import { useState, useEffect, useCallback, useRef } from "react"
import { Button } from "@/components/ui/button"
import {
  X,
  Calendar,
  Trash2,
  Edit3,
  Check,
  Paperclip,
  XCircle,
  Plus,
  Star,
} from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import type { NodeDetail, NodeGroup, Message } from "@/types/file-mgmt"
import {
  getNodeDetail,
  updateNode,
  deleteNode,
  getNodeMessages,
  getFileMessages,
  createNodeMessage,
  updateMessage,
  deleteMessage,
  detachFileFromNode,
} from "@/api/file-mgmt"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { NodeFileAttach } from "./node-file-attach"
import { MessageCard } from "../message-card"
import { MessageEditorDialog } from "../folder-view/message-editor-dialog"
import { FileMgmtDetailDialog } from "@/components/file-mgmt/file-detail"

/** Format ISO timestamp as yyyy/mm/dd HH:mm:ss (24h local). */
function formatCreatedAt(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  const h = String(d.getHours()).padStart(2, "0")
  const min = String(d.getMinutes()).padStart(2, "0")
  const s = String(d.getSeconds()).padStart(2, "0")
  return `${y}/${m}/${day} ${h}:${min}:${s}`
}

/** Normalize stored event_time to yyyy-mm-dd for <input type="date">. */
function toDateInputValue(raw: string | null | undefined): string {
  if (!raw) return ""
  // Accept "2024-01-15", "2024-01-15T10:00:00", ISO, etc.
  const m = raw.match(/^(\d{4}-\d{2}-\d{2})/)
  if (m) return m[1]
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) return ""
  const y = d.getFullYear()
  const mo = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${y}-${mo}-${day}`
}

interface NodeDetailSidebarProps {
  collectionId: string
  nodeId: string | null
  onClose: () => void
  onNodeUpdated: () => void
  getGroupName: (groupId: string | null) => string
  groups: NodeGroup[]
}

export function NodeDetailSidebar({
  collectionId,
  nodeId,
  onClose,
  onNodeUpdated,
  getGroupName,
  groups,
}: NodeDetailSidebarProps) {
  const [detail, setDetail] = useState<NodeDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [editingTitle, setEditingTitle] = useState(false)
  const [editTitle, setEditTitle] = useState("")
  const [editingGroupId, setEditingGroupId] = useState(false)
  const [newGroupId, setNewGroupId] = useState<string | null>(null)
  const [eventTime, setEventTime] = useState("")
  const [messages, setMessages] = useState<Message[]>([])
  const [msgTab, setMsgTab] = useState<"all" | "node" | "files">("all")
  const [attachOpen, setAttachOpen] = useState(true)
  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const [msgDialogOpen, setMsgDialogOpen] = useState(false)
  const [editingMsg, setEditingMsg] = useState<Message | null>(null)
  const [msgDialogReadonly, setMsgDialogReadonly] = useState(false)
  /** Open unified file detail from attachment list. */
  const [detailFileId, setDetailFileId] = useState<string | null>(null)
  const dateInputRef = useRef<HTMLInputElement>(null)

  const fetchDetail = useCallback(async () => {
    if (!nodeId) {
      setDetail(null)
      setMessages([])
      setEventTime("")
      return
    }
    setLoading(true)
    try {
      const d = await getNodeDetail(collectionId, nodeId)
      const nodeMsgs = await getNodeMessages(collectionId, nodeId)
      const fileMsgLists = await Promise.all(
        (d.attachments ?? []).map((a) =>
          getFileMessages(collectionId, a.file_id).catch(() => [] as Message[])
        )
      )
      const fileMsgs = fileMsgLists.flat()
      const merged = [...nodeMsgs, ...fileMsgs].sort((a, b) =>
        (b.created_at || "").localeCompare(a.created_at || "")
      )
      setDetail(d)
      setMessages(merged)
      setEventTime(toDateInputValue(d.event_time))
    } catch (err) {
      toast.error(`Failed to load node: ${err instanceof Error ? err.message : String(err)}`)
      setDetail(null)
      setEventTime("")
    } finally {
      setLoading(false)
    }
  }, [collectionId, nodeId])

  useEffect(() => {
    fetchDetail()
    setAttachOpen(true)
    setDeleteConfirm(false)
    setEditingTitle(false)
    setEditingGroupId(false)
  }, [fetchDetail])

  const handleSaveTitle = async () => {
    if (!detail || !editTitle.trim()) return
    try {
      await updateNode(collectionId, detail.node_id, {
        title: editTitle.trim(),
        version: detail.version,
      })
      setEditingTitle(false)
      fetchDetail()
      onNodeUpdated()
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleSaveGroup = async () => {
    if (!detail) return
    try {
      await updateNode(collectionId, detail.node_id, {
        group_id: newGroupId,
        version: detail.version,
      })
      setEditingGroupId(false)
      fetchDetail()
      onNodeUpdated()
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const openDatePicker = () => {
    const el = dateInputRef.current
    if (!el) return
    try {
      el.showPicker?.()
    } catch {
      el.focus()
      el.click()
    }
  }

  const handleSaveEventTime = async (value: string) => {
    if (!detail) return
    const next = value || null
    const prev = toDateInputValue(detail.event_time) || null
    if (next === prev) return
    try {
      await updateNode(collectionId, detail.node_id, {
        event_time: next,
        version: detail.version,
      })
      setEventTime(value)
      fetchDetail()
      onNodeUpdated()
      toast.success("Event time updated")
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
      setEventTime(toDateInputValue(detail.event_time))
    }
  }

  const handleDeleteNode = async () => {
    if (!detail) return
    try {
      await deleteNode(collectionId, detail.node_id)
      onClose()
      onNodeUpdated()
      toast.success("Node deleted")
    } catch (err) {
      toast.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleToggleDefinitive = async (
    fileId: string,
    currentDefinitive: boolean,
    version: number
  ) => {
    // Same path as folder view: SQLite flag + doc_summary include + debounce consolidate
    await useFileMgmtStore
      .getState()
      .toggleDefinitive(collectionId, fileId, !currentDefinitive, version)
    fetchDetail()
    onNodeUpdated()
  }

  const handleDetachFile = async (fileId: string) => {
    if (!detail) return
    try {
      await detachFileFromNode(collectionId, detail.node_id, fileId)
      fetchDetail()
      onNodeUpdated()
      toast.success("File detached")
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const refreshMessages = useCallback(async () => {
    if (!detail) return
    try {
      const msgs = await getNodeMessages(collectionId, detail.node_id)
      setMessages(msgs)
    } catch {
      /* ignore */
    }
  }, [collectionId, detail])

  const handleAddMessage = useCallback(
    async (content: string) => {
      if (!detail || !content.trim()) return
      try {
        await createNodeMessage(collectionId, detail.node_id, {
          owner_type: "node",
          owner_id: detail.node_id,
          body: content.trim(),
          author_type: "user",
        })
        await refreshMessages()
        toast.success("Message added")
      } catch (err) {
        toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
      }
    },
    [collectionId, detail, refreshMessages]
  )

  const handleEditMessage = useCallback(
    async (content: string) => {
      if (!editingMsg || !content.trim()) return
      try {
        await updateMessage(collectionId, editingMsg.message_id, {
          body: content.trim(),
          version: editingMsg.version,
        })
        setEditingMsg(null)
        await refreshMessages()
        toast.success("Message updated")
      } catch (err) {
        toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
      }
    },
    [collectionId, editingMsg, refreshMessages]
  )

  const handleDeleteMessage = useCallback(
    async (messageId: string) => {
      if (!detail) return
      try {
        await deleteMessage(collectionId, messageId)
        await refreshMessages()
        toast.success("Message deleted")
      } catch (err) {
        toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
      }
    },
    [collectionId, detail, refreshMessages]
  )

  const handleOpenForAdd = useCallback(() => {
    setEditingMsg(null)
    setMsgDialogReadonly(false)
    setMsgDialogOpen(true)
  }, [])

  const handleOpenForView = useCallback((msg: Message) => {
    setEditingMsg(msg)
    setMsgDialogReadonly(true)
    setMsgDialogOpen(true)
  }, [])

  const handleStartEdit = useCallback((msg: Message) => {
    setEditingMsg(msg)
    setMsgDialogReadonly(false)
    setMsgDialogOpen(true)
  }, [])

  const handleCloseMsgDialog = useCallback((open: boolean) => {
    if (!open) setEditingMsg(null)
    setMsgDialogOpen(open)
  }, [])

  if (!nodeId) return null

  return (
    <div
      data-node-detail-sidebar
      className="h-full w-full min-h-0 border border-border rounded-xl bg-background shadow-lg flex flex-col overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <h3 className="text-xs font-semibold uppercase tracking-[0.1em] text-muted-foreground">
          Node Detail
        </h3>
        <button
          className="text-muted-foreground hover:text-foreground transition-colors"
          onClick={onClose}
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {loading ? (
        <div className="flex-1 min-h-0 flex items-center justify-center text-muted-foreground">
          <p className="text-sm">Loading...</p>
        </div>
      ) : !detail ? (
        <div className="flex-1 min-h-0 flex items-center justify-center text-muted-foreground">
          <p className="text-sm">Node not found</p>
        </div>
      ) : (
        <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain">
          <div className="p-4 pb-8 space-y-4">
            {/* Basic info — Title left; Group + Type on the right */}
            <div className="space-y-3">
              <div className="flex items-start gap-3">
                {/* Title */}
                <div className="flex-1 min-w-0">
                  <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 block mb-1">
                    Title
                  </label>
                  {editingTitle ? (
                    <div className="flex items-center gap-1">
                      <input
                        className="flex-1 min-w-0 text-xs border rounded px-2 py-1 bg-background"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && handleSaveTitle()}
                        autoFocus
                      />
                      <button onClick={handleSaveTitle}>
                        <Check className="h-3.5 w-3.5 text-emerald-500" />
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 group/title min-w-0">
                      <span className="text-sm font-medium truncate">
                        {detail.title || "Untitled"}
                      </span>
                      <button
                        className="opacity-0 group-hover/title:opacity-100 transition-opacity text-muted-foreground shrink-0"
                        onClick={() => {
                          setEditTitle(detail.title ?? "")
                          setEditingTitle(true)
                        }}
                      >
                        <Edit3 className="h-3 w-3" />
                      </button>
                    </div>
                  )}
                </div>

                {/* Group */}
                <div className="shrink-0 max-w-[120px]">
                  <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 block mb-1">
                    Group
                  </label>
                  {editingGroupId ? (
                    <div className="flex items-center gap-1">
                      <select
                        className="w-full max-w-[110px] text-xs border rounded px-1.5 py-1 bg-background"
                        value={newGroupId ?? detail.group_id ?? ""}
                        onChange={(e) =>
                          setNewGroupId(e.target.value || null)
                        }
                      >
                        <option value="">No Group</option>
                        {groups.map((g) => (
                          <option key={g.group_id} value={g.group_id}>
                            {g.name}
                          </option>
                        ))}
                      </select>
                      <button onClick={handleSaveGroup}>
                        <Check className="h-3.5 w-3.5 text-emerald-500" />
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1 group/group">
                      <span className="text-xs text-muted-foreground truncate max-w-[90px]">
                        {getGroupName(detail.group_id)}
                      </span>
                      <button
                        className="opacity-0 group-hover/group:opacity-100 transition-opacity text-muted-foreground shrink-0"
                        onClick={() => {
                          setNewGroupId(detail.group_id)
                          setEditingGroupId(true)
                        }}
                      >
                        <Edit3 className="h-3 w-3" />
                      </button>
                    </div>
                  )}
                </div>

                {/* Type */}
                <div className="shrink-0">
                  <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 block mb-1">
                    Type
                  </label>
                  <span className="text-xs uppercase font-medium text-muted-foreground">
                    {detail.node_type}
                  </span>
                </div>
              </div>

              {/* Event time — date only; empty when unset */}
              <div>
                <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 block mb-1">
                  Event Time
                </label>
                <div className="flex items-center gap-2">
                  <Calendar className="h-3 w-3 text-muted-foreground shrink-0" />
                  <input
                    ref={dateInputRef}
                    type="date"
                    value={eventTime}
                    onChange={(e) => {
                      setEventTime(e.target.value)
                      void handleSaveEventTime(e.target.value)
                    }}
                    onClick={openDatePicker}
                    className={cn(
                      "text-xs border rounded px-2 py-1 bg-background flex-1 cursor-pointer",
                      !eventTime &&
                        "[&::-webkit-datetime-edit]:text-transparent [&::-webkit-datetime-edit-fields-wrapper]:opacity-0"
                    )}
                  />
                  {eventTime && (
                    <button
                      type="button"
                      className="text-muted-foreground hover:text-foreground shrink-0 p-0.5"
                      title="Clear date"
                      onClick={() => {
                        setEventTime("")
                        void handleSaveEventTime("")
                      }}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  )}
                </div>
              </div>
            </div>

            {/* Attachments — drop/search first (fixed size); list scrolls below */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60">
                  Attachments ({detail.attachments?.length ?? 0})
                </label>
                <button
                  className="text-[10px] font-medium text-primary hover:underline flex items-center gap-1"
                  onClick={() => setAttachOpen(!attachOpen)}
                >
                  <Paperclip className="h-3 w-3" />
                  {attachOpen ? "Hide" : "Add"}
                </button>
              </div>

              {/* Drop / Select zone always above the list so it is never pushed away */}
              {attachOpen && (
                <div className="shrink-0">
                  <NodeFileAttach
                    collectionId={collectionId}
                    nodeId={detail.node_id}
                    attachedIds={(detail.attachments ?? []).map((a) => a.file_id)}
                    onAttached={() => {
                      fetchDetail()
                      onNodeUpdated()
                    }}
                  />
                </div>
              )}

              {detail.attachments && detail.attachments.length > 0 ? (
                <div className="max-h-[88px] overflow-y-auto space-y-1.5 pr-0.5">
                  {detail.attachments.map((att) => (
                    <div
                      key={att.file_id}
                      className="flex items-center gap-2 px-2 py-1.5 rounded bg-muted/30 text-xs group/file"
                    >
                      <Paperclip className="h-3 w-3 text-muted-foreground shrink-0" />
                      <button
                        type="button"
                        className="flex-1 min-w-0 text-left truncate hover:text-primary transition-colors"
                        title="Open file detail"
                        onClick={() => setDetailFileId(att.file_id)}
                      >
                        {att.filename}
                      </button>
                      {att.archived && (
                        <span className="text-[9px] text-amber-500 font-medium">
                          archived
                        </span>
                      )}
                      <button
                        className={`opacity-0 group-hover/file:opacity-100 transition-opacity ${
                          att.is_definitive
                            ? "text-emerald-500"
                            : "text-muted-foreground"
                        }`}
                        onClick={() =>
                          handleToggleDefinitive(
                            att.file_id,
                            att.is_definitive,
                            att.version ?? 1
                          )
                        }
                        title={
                          att.is_definitive
                            ? "Remove definitive"
                            : "Mark as definitive"
                        }
                      >
                        <Star
                          className={`h-3 w-3 ${
                            att.is_definitive ? "fill-emerald-500" : ""
                          }`}
                        />
                      </button>
                      <button
                        className="opacity-0 group-hover/file:opacity-100 transition-opacity text-muted-foreground hover:text-red-500"
                        onClick={() => handleDetachFile(att.file_id)}
                        title="Remove attachment"
                      >
                        <XCircle className="h-3 w-3" />
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                !attachOpen && (
                  <p className="text-xs text-muted-foreground/50">No files attached</p>
                )
              )}
            </div>

            {/* Messages — node + file with tabs */}
            <div className="rounded-lg border border-border/40 bg-background/50 overflow-hidden">
              <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border/40">
                <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Messages
                </span>
                <span className="text-[10px] text-muted-foreground/50">
                  {
                    (msgTab === "all"
                      ? messages
                      : msgTab === "node"
                        ? messages.filter((m) => m.owner_type === "node")
                        : messages.filter((m) => m.owner_type === "file")
                    ).length
                  }
                </span>
                <div className="flex gap-0.5 ml-auto">
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    onClick={handleOpenForAdd}
                    title="Add node message"
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
              <div className="flex gap-1 px-2 pt-1.5 border-b border-border/30">
                {(["all", "node", "files"] as const).map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    className={cn(
                      "text-[10px] px-2 py-0.5 rounded capitalize",
                      msgTab === tab
                        ? "bg-primary/10 text-primary"
                        : "text-muted-foreground hover:bg-muted/40"
                    )}
                    onClick={() => setMsgTab(tab)}
                  >
                    {tab}
                  </button>
                ))}
              </div>

              <div className="max-h-56 overflow-y-auto">
                {(() => {
                  const filtered =
                    msgTab === "all"
                      ? messages
                      : msgTab === "node"
                        ? messages.filter((m) => m.owner_type === "node")
                        : messages.filter((m) => m.owner_type === "file")
                  if (filtered.length === 0) {
                    return (
                      <div className="text-center text-xs text-muted-foreground/50 py-8 px-3">
                        No messages yet. Click + to add a node message.
                      </div>
                    )
                  }
                  return (
                    <div className="flex flex-col gap-1 p-2">
                      {filtered.map((msg) => (
                        <MessageCard
                          key={msg.message_id}
                          msg={msg}
                          previewSide="left"
                          onView={handleOpenForView}
                          onEdit={handleStartEdit}
                          onDelete={() => handleDeleteMessage(msg.message_id)}
                          onSourceTagClick={handleOpenForView}
                        />
                      ))}
                    </div>
                  )
                })()}
              </div>
            </div>

            <MessageEditorDialog
              key={editingMsg?.message_id || "new"}
              open={msgDialogOpen}
              onOpenChange={handleCloseMsgDialog}
              title={
                msgDialogReadonly
                  ? "Message"
                  : editingMsg
                    ? "Edit Message"
                    : "Add Message"
              }
              initialContent={editingMsg?.body || ""}
              onSave={
                !editingMsg
                  ? handleAddMessage
                  : !msgDialogReadonly
                    ? handleEditMessage
                    : () => {}
              }
              readonly={msgDialogReadonly}
            />

            {/* Footer: Delete left · Created right */}
            <div className="pt-2 border-t">
              {deleteConfirm ? (
                <div className="space-y-2">
                  <p className="text-xs text-muted-foreground">
                    Delete this node? Derived file paths will be removed.
                  </p>
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        className="text-[10px] h-7"
                        onClick={() => setDeleteConfirm(false)}
                      >
                        Cancel
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        className="text-[10px] h-7"
                        onClick={handleDeleteNode}
                      >
                        Delete Node
                      </Button>
                    </div>
                    <span className="text-[10px] text-muted-foreground/70 tabular-nums shrink-0">
                      {formatCreatedAt(detail.created_at)}
                    </span>
                  </div>
                </div>
              ) : (
                <div className="flex items-center justify-between gap-3">
                  <button
                    className="text-[10px] font-medium text-red-500/70 hover:text-red-500 flex items-center gap-1 shrink-0"
                    onClick={() => setDeleteConfirm(true)}
                  >
                    <Trash2 className="h-3 w-3" />
                    Delete Node
                  </button>
                  <span
                    className="text-[10px] text-muted-foreground/70 tabular-nums text-right"
                    title="Created"
                  >
                    {formatCreatedAt(detail.created_at)}
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <FileMgmtDetailDialog
        collectionId={collectionId}
        fileId={detailFileId}
        open={!!detailFileId}
        onOpenChange={(v) => {
          if (!v) setDetailFileId(null)
        }}
        onDeleted={() => {
          setDetailFileId(null)
          fetchDetail()
          onNodeUpdated()
        }}
        contextNodeId={nodeId}
      />
    </div>
  )
}
