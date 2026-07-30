import { useState, useCallback } from "react"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Loader2, Plus, PanelRightClose, PanelRight } from "lucide-react"
import type { Message } from "@/types/file-mgmt"
import { MessageEditorDialog } from "./message-editor-dialog"
import { MessageCard } from "../message-card"
import {
  NodePreviewSheet,
  resolveMessageSourceNodeId,
} from "../node-preview-sheet"

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
  const [previewNodeId, setPreviewNodeId] = useState<string | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)

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
                showFromNode={!!resolveMessageSourceNodeId(msg)}
                onSourceNodeClick={(nodeId) => {
                  setPreviewNodeId(nodeId)
                  setPreviewOpen(true)
                }}
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

      <NodePreviewSheet
        collectionId={collectionId}
        nodeId={previewNodeId}
        open={previewOpen}
        onOpenChange={setPreviewOpen}
      />
    </div>
  )
}
