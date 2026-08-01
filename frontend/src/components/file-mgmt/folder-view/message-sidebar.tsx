import { useState, useCallback, useEffect, useRef, useMemo } from "react"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Loader2,
  Plus,
  PanelRightClose,
  PanelRight,
  FolderTree,
  FileText,
  GitBranch,
} from "lucide-react"
import type { FolderTreeNode, Message } from "@/types/file-mgmt"
import { MessageEditorDialog } from "./message-editor-dialog"
import { MessageCard } from "../message-card"
import { cn } from "@/lib/utils"

const SIDEBAR_W = 340
const COLLAPSED_W = 32
const EASE = "cubic-bezier(0.16, 1, 0.3, 1)"

function findFolderInTree(
  tree: FolderTreeNode[],
  fid: string
): FolderTreeNode | null {
  for (const n of tree) {
    if (n.folder_id === fid) return n
    const found = findFolderInTree(n.children ?? [], fid)
    if (found) return found
  }
  return null
}

export function MessageSidebar({ collectionId }: { collectionId: string }) {
  const {
    currentFolderId,
    currentFolderMessages,
    currentFolderFiles,
    folderTree,
    messagesLoading,
    addMessage,
    editMessage,
    removeMessage,
    messageSidebarOpen,
    toggleMessageSidebar,
    messageIncludeFiles,
    messageRecursive,
    messageIncludeNodes,
    setMessageIncludeFiles,
    setMessageRecursive,
    setMessageIncludeNodes,
    refreshMessages,
    requestTimelineFocus,
    selectedFileIds,
    selectedFolderIds,
  } = useFileMgmtStore()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingMsg, setEditingMsg] = useState<Message | null>(null)
  const [dialogReadonly, setDialogReadonly] = useState(false)
  const [softRefreshing, setSoftRefreshing] = useState(false)
  const softRefreshGen = useRef(0)

  const selFiles = useMemo(
    () => Array.from(selectedFileIds),
    [selectedFileIds]
  )
  const selFolders = useMemo(
    () => Array.from(selectedFolderIds),
    [selectedFolderIds]
  )

  /** Exactly one file selected → file messages (no Nested/Files/Nodes). */
  const focusFileId =
    selFiles.length === 1 && selFolders.length === 0 ? selFiles[0] : null
  /** Exactly one folder selected → that folder's messages (+ toggles). */
  const focusFolderId =
    selFolders.length === 1 && selFiles.length === 0 ? selFolders[0] : null

  const focusFile = focusFileId
    ? currentFolderFiles.find((f) => f.file_id === focusFileId) ?? null
    : null
  const focusFolder = focusFolderId
    ? findFolderInTree(folderTree, focusFolderId)
    : null

  const showScopeToggles = !focusFileId

  // Soft refresh when scope toggles or selection changes
  useEffect(() => {
    if (!messageSidebarOpen) return
    const gen = ++softRefreshGen.current
    setSoftRefreshing(true)
    void refreshMessages(collectionId, { silent: true }).finally(() => {
      if (softRefreshGen.current === gen) setSoftRefreshing(false)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    messageIncludeFiles,
    messageRecursive,
    messageIncludeNodes,
    focusFileId,
    focusFolderId,
    currentFolderId,
    messageSidebarOpen,
  ])

  const handleAdd = useCallback(
    (content: string) => {
      addMessage(collectionId, content)
    },
    [collectionId, addMessage]
  )

  const handleEdit = useCallback(
    (content: string) => {
      if (!editingMsg) return
      editMessage(
        collectionId,
        editingMsg.message_id,
        content,
        editingMsg.version
      )
      setEditingMsg(null)
    },
    [collectionId, editingMsg, editMessage]
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

  const handleCloseDialog = useCallback((open: boolean) => {
    if (!open) setEditingMsg(null)
    setDialogOpen(open)
  }, [])

  const atRoot = !focusFolderId && !currentFolderId
  const folderScopeIsRoot = focusFolderId
    ? false
    : !currentFolderId

  const scopeHint = (() => {
    if (focusFileId) {
      return focusFile?.filename
        ? `File: ${focusFile.filename}`
        : "Selected file messages"
    }
    if (focusFolderId) {
      const name = focusFolder?.name || "Selected folder"
      const bits: string[] = [name]
      if (messageRecursive) bits.push("nested")
      if (messageIncludeFiles) bits.push("files")
      if (messageIncludeNodes) bits.push("nodes")
      return bits.join(" · ")
    }
    const bits: string[] = [folderScopeIsRoot ? "Root" : "This folder"]
    if (messageRecursive)
      bits.push(folderScopeIsRoot ? "all folders" : "nested folders")
    if (messageIncludeFiles) {
      bits.push(
        messageRecursive
          ? "all files"
          : folderScopeIsRoot
            ? "orphan files"
            : "files here"
      )
    }
    if (messageIncludeNodes) {
      if (folderScopeIsRoot && !messageRecursive) {
        bits.push("nodes (need Nested)")
      } else {
        bits.push(
          messageRecursive ? "all linked nodes" : "group/branch nodes"
        )
      }
    }
    if (bits.length === 1)
      bits[0] = folderScopeIsRoot ? "Root messages only" : "This folder only"
    return bits.join(" + ")
  })()

  const contextLabel = focusFileId
    ? "File"
    : focusFolderId
      ? "Folder"
      : "Messages"

  /** Folder id used for "Current folder" tag highlighting */
  const highlightFolderId = focusFileId
    ? null
    : focusFolderId ?? currentFolderId
  /**
   * Nested off → list is only this folder's own messages; every folder-level
   * message is "Current folder". Nested on → only owner_id match.
   */
  const folderMsgsAreCurrentScope =
    !focusFileId && !messageRecursive && !!highlightFolderId

  return (
    <div
      className={cn(
        "h-full min-h-0 shrink-0 overflow-visible",
        // Gutter so soft shadow stays inside ancestor overflow clips
        messageSidebarOpen && "py-1.5 pr-1"
      )}
      style={{
        width: messageSidebarOpen ? SIDEBAR_W + 4 : COLLAPSED_W,
        transition: `width 320ms ${EASE}`,
      }}
    >
      <div className="relative h-full w-full min-h-0">
        <div
          className={cn(
            "absolute inset-y-0 left-0 w-8 flex flex-col items-center pt-1.5 z-[1]",
            "transition-opacity duration-200",
            messageSidebarOpen
              ? "opacity-0 pointer-events-none"
              : "opacity-100"
          )}
        >
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={toggleMessageSidebar}
            title="Show messages"
            className={cn(
              "rounded-lg border border-border/40 bg-background/80 backdrop-blur-sm",
              "shadow-sm hover:shadow-md hover:bg-background transition-shadow"
            )}
          >
            <PanelRight className="h-3.5 w-3.5" />
          </Button>
        </div>

        {/*
          Shadow lives on this wrapper (overflow visible).
          Inner panel keeps overflow-hidden for scroll — same-element
          overflow+radius was clipping the floating shadow.
        */}
        <div
          className={cn(
            "absolute inset-0 min-h-0",
            "rounded-xl",
            // Softer, shorter falloff so gutter can fully contain the shadow
            "shadow-[0_2px_8px_rgba(0,0,0,0.06),0_8px_20px_-4px_rgba(0,0,0,0.12)]",
            "dark:shadow-[0_2px_8px_rgba(0,0,0,0.25),0_10px_24px_-4px_rgba(0,0,0,0.4)]",
            "transition-[opacity,transform] duration-300",
            messageSidebarOpen
              ? "opacity-100 translate-x-0 pointer-events-auto"
              : "opacity-0 translate-x-3 pointer-events-none"
          )}
          style={{ transitionTimingFunction: EASE }}
        >
          <div
            className={cn(
              "flex h-full min-h-0 flex-col overflow-hidden overflow-x-hidden overscroll-x-none touch-pan-y",
              "rounded-xl border border-border/50",
              "bg-background/85 backdrop-blur-md",
              "ring-1 ring-black/[0.03] dark:ring-white/[0.06]"
            )}
          >
          <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border/30 shrink-0 bg-muted/15">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {contextLabel}
            </span>
            <span className="text-[10px] text-muted-foreground/50 tabular-nums">
              {currentFolderMessages.length}
            </span>
            {softRefreshing && (
              <Loader2 className="h-3 w-3 animate-spin text-muted-foreground/50" />
            )}
            <div className="flex gap-0.5 ml-auto">
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={handleOpenForAdd}
                title={
                  focusFileId
                    ? "Add message to file"
                    : focusFolderId
                      ? "Add message to folder"
                      : "Add message"
                }
              >
                <Plus className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={toggleMessageSidebar}
                title="Hide messages"
              >
                <PanelRightClose className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>

          {/* Scope toggles — only for folder / root context, not single file */}
          {showScopeToggles && (
            <div className="px-2 py-1.5 border-b border-border/40 space-y-1 shrink-0">
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  className={cn(
                    "flex-1 flex items-center justify-center gap-1 rounded px-1 py-1 text-[10px] border transition-colors",
                    messageRecursive
                      ? "border-primary/40 bg-primary/10 text-primary"
                      : "border-border/50 text-muted-foreground hover:bg-muted/40"
                  )}
                  onClick={() => setMessageRecursive(!messageRecursive)}
                  title={
                    messageRecursive
                      ? "Stop including nested folders"
                      : atRoot || folderScopeIsRoot
                        ? "Include messages from all folders"
                        : "Include messages from nested folders (recursive)"
                  }
                >
                  <FolderTree className="h-3 w-3 shrink-0" />
                  Nested
                </button>
                <button
                  type="button"
                  className={cn(
                    "flex-1 flex items-center justify-center gap-1 rounded px-1 py-1 text-[10px] border transition-colors",
                    messageIncludeFiles
                      ? "border-primary/40 bg-primary/10 text-primary"
                      : "border-border/50 text-muted-foreground hover:bg-muted/40"
                  )}
                  onClick={() => setMessageIncludeFiles(!messageIncludeFiles)}
                  title={
                    messageIncludeFiles
                      ? "Hide file messages"
                      : "Include file messages in scope"
                  }
                >
                  <FileText className="h-3 w-3 shrink-0" />
                  Files
                </button>
                <button
                  type="button"
                  className={cn(
                    "flex-1 flex items-center justify-center gap-1 rounded px-1 py-1 text-[10px] border transition-colors",
                    messageIncludeNodes
                      ? "border-primary/40 bg-primary/10 text-primary"
                      : "border-border/50 text-muted-foreground hover:bg-muted/40"
                  )}
                  onClick={() => setMessageIncludeNodes(!messageIncludeNodes)}
                  title={
                    messageIncludeNodes
                      ? "Hide node messages"
                      : "Include node messages in scope"
                  }
                >
                  <GitBranch className="h-3 w-3 shrink-0" />
                  Nodes
                </button>
              </div>
              <p className="text-[9px] text-muted-foreground/60 px-0.5 leading-snug truncate" title={scopeHint}>
                {scopeHint}
              </p>
            </div>
          )}

          {/* File context: compact label instead of toggles */}
          {focusFileId && (
            <div className="px-3 py-1.5 border-b border-border/40 shrink-0">
              <p
                className="text-[9px] text-muted-foreground/70 truncate"
                title={scopeHint}
              >
                {scopeHint}
              </p>
            </div>
          )}

          <ScrollArea className="flex-1 min-h-0 overflow-x-hidden overscroll-x-none [&>[data-slot=scroll-area-viewport]]:overflow-x-hidden! [&>[data-slot=scroll-area-viewport]]:overscroll-x-none">
            {messagesLoading && currentFolderMessages.length === 0 ? (
              <div className="flex items-center justify-center h-12">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              </div>
            ) : currentFolderMessages.length === 0 ? (
              <div className="text-center text-xs text-muted-foreground/50 py-8 px-3">
                {"No messages yet. Click + to add one."}
              </div>
            ) : (
              <div
                className={cn(
                  "flex flex-col gap-1 p-2 min-w-0 max-w-full overflow-x-hidden transition-opacity duration-200",
                  softRefreshing && "opacity-70"
                )}
              >
                {currentFolderMessages.map((msg) => (
                  <MessageCard
                    key={msg.message_id}
                    msg={msg}
                    onView={handleOpenForView}
                    onDelete={() =>
                      removeMessage(collectionId, msg.message_id)
                    }
                    onSourceTagClick={handleOpenForView}
                    highlightFolderId={highlightFolderId}
                    folderMsgsAreCurrentScope={folderMsgsAreCurrentScope}
                  />
                ))}
              </div>
            )}
          </ScrollArea>

          <MessageEditorDialog
            key={dialogOpen ? (editingMsg?.message_id ?? "new") : "closed"}
            open={dialogOpen}
            onOpenChange={handleCloseDialog}
            title={editingMsg ? "Message" : "Add Message"}
            initialContent={editingMsg?.body || ""}
            onSave={!editingMsg ? handleAdd : handleEdit}
            readonly={dialogReadonly || !!editingMsg}
            message={editingMsg}
            collectionId={collectionId}
            highlightFolderId={highlightFolderId}
            folderMsgsAreCurrentScope={folderMsgsAreCurrentScope}
            onSelectNodeMessage={(msg) => {
              setEditingMsg(msg)
              setDialogReadonly(true)
            }}
            onNavigateToNode={(nodeId, chainId) => {
              setDialogOpen(false)
              setEditingMsg(null)
              window.setTimeout(() => {
                requestTimelineFocus(nodeId, chainId)
              }, 280)
            }}
          />
          </div>
        </div>
      </div>
    </div>
  )
}
