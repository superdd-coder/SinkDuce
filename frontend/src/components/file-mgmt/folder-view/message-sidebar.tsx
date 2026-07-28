import { useState, useCallback } from "react"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Loader2, Plus, Pencil, Trash2, Bot, Clock, PanelRightClose, PanelRight } from "lucide-react"
import { cn } from "@/lib/utils"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import type { Message } from "@/types/file-mgmt"
import { MessageEditorDialog } from "./message-editor-dialog"

export function MessageSidebar({ collectionId }: { collectionId: string }) {
  const {
    currentFolderMessages,
    messagesLoading,
    addMessage,
    editMessage,
    removeMessage,
    messageSidebarOpen,
    toggleMessageSidebar,
  } = useFileMgmtStore()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingMsg, setEditingMsg] = useState<Message | null>(null)
  const [dialogReadonly, setDialogReadonly] = useState(false)

  const handleAdd = useCallback(
    (content: string) => {
      addMessage(collectionId, content)
    },
    [collectionId, addMessage],
  )

  const handleEdit = useCallback(
    (content: string) => {
      if (!editingMsg) return
      editMessage(collectionId, editingMsg.message_id, content, editingMsg.version)
      setEditingMsg(null)
    },
    [collectionId, editingMsg, editMessage],
  )

  const handleOpenForAdd = useCallback(() => {
    setEditingMsg(null)
    setDialogReadonly(false)
    setDialogOpen(true)
  }, [])

  const handleOpenForView = useCallback((msg: Message) => {
    setEditingMsg(msg)
    setDialogReadonly(true)
    setDialogOpen(true)
  }, [])

  const handleStartEdit = useCallback((msg: Message) => {
    setEditingMsg(msg)
    setDialogReadonly(false)
    setDialogOpen(true)
  }, [])

  const handleCloseDialog = useCallback((open: boolean) => {
    if (!open) setEditingMsg(null)
    setDialogOpen(open)
  }, [])

  if (!messageSidebarOpen) {
    return (
      <div className="w-8 shrink-0 flex flex-col items-center pt-2">
        <Button variant="ghost" size="icon-xs" onClick={toggleMessageSidebar} title="Show messages">
          <PanelRight className="h-3.5 w-3.5" />
        </Button>
      </div>
    )
  }

  return (
    <div className="w-72 shrink-0 flex flex-col h-full min-h-0 overflow-hidden rounded-lg border border-border/40 bg-background/50">
      {/* Header */}
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border/40">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Messages</span>
        <span className="text-[10px] text-muted-foreground/50">{currentFolderMessages.length}</span>
        <div className="flex gap-0.5 ml-auto">
          <Button variant="ghost" size="icon-xs" onClick={handleOpenForAdd} title="Add message">
            <Plus className="h-3.5 w-3.5" />
          </Button>
          <Button variant="ghost" size="icon-xs" onClick={toggleMessageSidebar} title="Hide messages">
            <PanelRightClose className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Messages list */}
      <ScrollArea className="flex-1 min-h-0">
        {messagesLoading ? (
          <div className="flex items-center justify-center h-12">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          </div>
        ) : currentFolderMessages.length === 0 ? (
          <div className="text-center text-xs text-muted-foreground/50 py-8 px-3">
            {"No messages yet. Click + to add one."}
          </div>
        ) : (
          <div className="flex flex-col gap-1 p-2">
            {currentFolderMessages.map((msg) => (
              <MessageCard
                key={msg.message_id}
                msg={msg}
                onView={handleOpenForView}
                onEdit={handleStartEdit}
                onDelete={() => removeMessage(collectionId, msg.message_id)}
              />
            ))}
          </div>
        )}
      </ScrollArea>

      {/* MD Editor Dialog */}
      <MessageEditorDialog
        key={editingMsg?.message_id || "new"}
        open={dialogOpen}
        onOpenChange={handleCloseDialog}
        title={dialogReadonly ? "Message" : editingMsg ? "Edit Message" : "Add Message"}
        initialContent={editingMsg?.body || ""}
        onSave={!editingMsg ? handleAdd : !dialogReadonly ? handleEdit : (() => {})}
        readonly={dialogReadonly}
      />
    </div>
  )
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function MessagePreview({ body }: { body: string }) {
  return (
    <div className="text-foreground/90 leading-relaxed line-clamp-3 break-words text-[11px] prose prose-xs dark:prose-invert max-w-none [&_p]:my-0 [&_ul]:my-0 [&_ol]:my-0 [&_li]:my-0 [&_h1]:text-xs [&_h2]:text-xs [&_h3]:text-xs [&_h1]:my-0 [&_h2]:my-0 [&_h3]:my-0 [&_code]:text-[10px] [&_pre]:hidden [&_blockquote]:my-0 [&_blockquote]:text-xs">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
    </div>
  )
}

// ─── Message Card ───────────────────────────────────────────────────────────

function MessageCard({
  msg,
  onView,
  onEdit,
  onDelete,
}: {
  msg: Message
  onView: (msg: Message) => void
  onEdit: (msg: Message) => void
  onDelete: () => void
}) {
  const isSystem = msg.author_type === "system"
  const time = msg.created_at
    ? new Date(msg.created_at).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : ""

  return (
    <div
      className={cn(
        "rounded-md p-2 text-xs group cursor-pointer hover:bg-accent/60 transition-colors relative",
        isSystem ? "bg-muted/30 border border-border/30" : "bg-background",
      )}
      onClick={() => onView(msg)}
    >
      {/* Hover preview card — shows full rendered markdown */}
      <div className="absolute left-[calc(100%+6px)] bottom-0 z-[9999] hidden group-hover:block pointer-events-none">
        <div className="w-[320px] max-h-[360px] overflow-y-auto rounded-lg border border-border bg-popover shadow-2xl p-3">
          <div className="prose prose-xs dark:prose-invert max-w-none text-xs leading-relaxed break-words">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.body || ""}</ReactMarkdown>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-1 mb-0.5">
        {isSystem ? (
          <Bot className="h-3 w-3 text-muted-foreground/60" />
        ) : (
          <Clock className="h-3 w-3 text-muted-foreground/40" />
        )}
        <span className="text-[10px] text-muted-foreground/50">{time}</span>
        {msg.edited_at && (
          <span className="text-[9px] text-muted-foreground/40 italic">edited</span>
        )}
        {msg.source_node_id && (
          <span className="text-[9px] text-blue-400/70 bg-blue-400/10 px-1 rounded">
            from node
          </span>
        )}
        {!isSystem && (
          <div className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity flex gap-0.5">
            <Button
              size="icon-xs"
              variant="ghost"
              onClick={(e) => {
                e.stopPropagation()
                onEdit(msg)
              }}
              className="h-5 w-5"
            >
              <Pencil className="h-2.5 w-2.5" />
            </Button>
            <Button
              size="icon-xs"
              variant="ghost"
              onClick={(e) => {
                e.stopPropagation()
                onDelete()
              }}
              className="h-5 w-5 text-destructive"
            >
              <Trash2 className="h-2.5 w-2.5" />
            </Button>
          </div>
        )}
      </div>
      <MessagePreview body={msg.body || ""} />
    </div>
  )
}
