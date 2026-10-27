import { useState, useCallback, useEffect, useRef, useMemo } from "react"
import { useFileMgmtStore } from "@/stores/file-mgmt-store"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Loader2,
  Plus,
  PanelRightClose,
  FolderTree,
  FileText,
  GitBranch,
} from "lucide-react"
import type { FileVersion, FolderTreeNode, Message } from "@/types/file-mgmt"
import { MessageEditorDialog } from "./message-editor-dialog"
import { MessageCard } from "../message-card"
import { LogMessageDialog } from "../file-detail/log-message-dialog"
import { getFileDetail } from "@/api/file-mgmt"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"

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
  const t = useT()
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
  /** Version-update message → same dual-pane Log as file-detail */
  const [logOpen, setLogOpen] = useState<{
    message: Message
    version: FileVersion | null
    isCurrent: boolean
    docSource: string | null
  } | null>(null)

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
    setDialogOpen(false)
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        setDialogOpen(true)
      })
    })
  }, [])

  const handleOpenForView = useCallback(
    async (msg: Message) => {
      const isVer = (msg.owner_type || "").toLowerCase() === "system_version"
      // Match file-detail Log: version updates open dual-pane LogMessageDialog
      if (isVer && msg.owner_id) {
        try {
          const detail = await getFileDetail(collectionId, msg.owner_id)
          const versions = detail.versions ?? []
          const versAsc = [...versions].sort(
            (a, b) => a.version_no - b.version_no
          )
          const body = (msg.body || "").trim()
          let version =
            versAsc.find(
              (v) =>
                v.created_at &&
                msg.created_at &&
                v.created_at === msg.created_at
            ) ||
            (body
              ? versAsc.find(
                  (v) => (v.commit_message || "").trim() === body
                )
              : undefined)
          if (!version) {
            // Chronological index: nth system_version ≈ version_no n
            const verMsgs = currentFolderMessages
              .filter(
                (m) =>
                  (m.owner_type || "").toLowerCase() === "system_version" &&
                  m.owner_id === msg.owner_id
              )
              .slice()
              .sort(
                (a, b) =>
                  (new Date(a.created_at).getTime() || 0) -
                  (new Date(b.created_at).getTime() || 0)
              )
            const idx = verMsgs.findIndex(
              (m) => m.message_id === msg.message_id
            )
            if (idx >= 0 && versAsc[idx]) version = versAsc[idx]
          }
          const isCurrent = !!(
            version &&
            detail.current_version_id &&
            version.version_id === detail.current_version_id
          )
          setLogOpen({
            message: msg,
            version: version ?? null,
            isCurrent,
            docSource:
              detail.source ||
              (msg.owner_id ? `__file__:${msg.owner_id}` : null),
          })
          return
        } catch {
          /* fall through to plain message dialog */
        }
      }
      // Enter path: set payload first, open=false, then open=true next frames
      // so Base UI applies data-starting-style → silk fade-in (Note/Todo pattern).
      setEditingMsg(msg)
      setDialogReadonly(true)
      setDialogOpen(false)
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          setDialogOpen(true)
        })
      })
    },
    [collectionId, currentFolderMessages]
  )

  const dialogCloseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const DIALOG_CLOSE_MS = 320

  const handleCloseDialog = useCallback((next: boolean) => {
    if (dialogCloseTimerRef.current) {
      clearTimeout(dialogCloseTimerRef.current)
      dialogCloseTimerRef.current = null
    }
    setDialogOpen(next)
    if (!next) {
      // Keep message for exit anim (silk ~280ms) — same as Note / Todo
      dialogCloseTimerRef.current = setTimeout(() => {
        setEditingMsg(null)
        setDialogReadonly(false)
        dialogCloseTimerRef.current = null
      }, DIALOG_CLOSE_MS)
    }
  }, [])

  const atRoot = !focusFolderId && !currentFolderId
  const folderScopeIsRoot = focusFolderId
    ? false
    : !currentFolderId

  const fileLabel =
    focusFile?.display_name?.trim() || focusFile?.filename?.trim() || ""

  const scopeHint = (() => {
    if (focusFileId) {
      return fileLabel
        ? t("fileMgmt.fileColon", { name: fileLabel })
        : t("fileMgmt.selectedFileMessages")
    }
    if (focusFolderId) {
      const name = focusFolder?.name || t("fileMgmt.selectedFolder")
      const bits: string[] = [name]
      if (messageRecursive) bits.push(t("common.nested"))
      if (messageIncludeFiles) bits.push(t("common.files"))
      if (messageIncludeNodes) bits.push(t("common.nodes"))
      return bits.join(" · ")
    }
    const bits: string[] = [
      folderScopeIsRoot ? t("common.root") : t("fileMgmt.thisFolder"),
    ]
    if (messageRecursive)
      bits.push(
        folderScopeIsRoot
          ? t("fileMgmt.allFoldersBit")
          : t("fileMgmt.nestedFoldersBit")
      )
    if (messageIncludeFiles) {
      bits.push(
        messageRecursive
          ? t("fileMgmt.allFilesBit")
          : folderScopeIsRoot
            ? t("fileMgmt.orphanFilesBit")
            : t("fileMgmt.filesHereBit")
      )
    }
    if (messageIncludeNodes) {
      if (folderScopeIsRoot && !messageRecursive) {
        bits.push(t("fileMgmt.nodesNeedNested"))
      } else {
        bits.push(
          messageRecursive
            ? t("fileMgmt.allLinkedNodes")
            : t("fileMgmt.groupBranchNodes")
        )
      }
    }
    if (bits.length === 1)
      bits[0] = folderScopeIsRoot
        ? t("fileMgmt.rootMessagesOnly")
        : t("fileMgmt.thisFolderOnly")
    return bits.join(" + ")
  })()

  const contextLabel = focusFileId
    ? t("common.file")
    : focusFolderId
      ? t("common.folder")
      : t("common.messages")

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

  /*
   * Soft rail panel (ENGINEERING §4.5) — curtain clip.
   * Fixed open width + pin-right; parent track shrinks with overflow:hidden
   * so content never reflows to “half size”.
   */
  return (
    <div
      className={cn(
        "pm-files-msg-panel",
        !messageSidebarOpen && "is-collapsed"
      )}
      aria-hidden={!messageSidebarOpen}
      inert={!messageSidebarOpen ? true : undefined}
    >
        <div className="pm-files-msg-head">
          <span className="pm-files-msg-title">{contextLabel}</span>
          <span className="pm-files-msg-count">
            {currentFolderMessages.length}
          </span>
          {softRefreshing && (
            <Loader2 className="h-3 w-3 animate-spin text-[var(--pm-faint)]" />
          )}
          <div className="flex gap-0.5 ml-auto">
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={handleOpenForAdd}
              title={
                focusFileId
                  ? t("fileMgmt.addMessageToFile")
                  : focusFolderId
                    ? t("fileMgmt.addMessageToFolder")
                    : t("fileMgmt.addMessage")
              }
              className="text-[var(--pm-muted)] hover:text-[var(--pm-ink)]"
              tabIndex={messageSidebarOpen ? 0 : -1}
            >
              <Plus className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={toggleMessageSidebar}
              title={t("fileMgmt.hideMessages")}
              className="text-[var(--pm-muted)] hover:text-[var(--pm-ink)]"
              tabIndex={messageSidebarOpen ? 0 : -1}
            >
              <PanelRightClose className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>

        {/* Scope toggles — only for folder / root context, not single file */}
        {showScopeToggles && (
          <div className="shrink-0">
            <div className="pm-files-scope-row">
              <button
                type="button"
                className={cn(
                  "pm-files-scope-btn",
                  messageRecursive && "is-on"
                )}
                onClick={() => setMessageRecursive(!messageRecursive)}
                title={
                  messageRecursive
                    ? t("fileMgmt.stopNested")
                    : atRoot || folderScopeIsRoot
                      ? t("fileMgmt.includeAllFolders")
                      : t("fileMgmt.includeNested")
                }
              >
                <FolderTree className="h-3 w-3 shrink-0" />
                {t("common.nested")}
              </button>
              <button
                type="button"
                className={cn(
                  "pm-files-scope-btn",
                  messageIncludeFiles && "is-on"
                )}
                onClick={() => setMessageIncludeFiles(!messageIncludeFiles)}
                title={
                  messageIncludeFiles
                    ? t("fileMgmt.hideFileMessages")
                    : t("fileMgmt.includeFileMessagesScope")
                }
              >
                <FileText className="h-3 w-3 shrink-0" />
                {t("common.files")}
              </button>
              <button
                type="button"
                className={cn(
                  "pm-files-scope-btn",
                  messageIncludeNodes && "is-on"
                )}
                onClick={() => setMessageIncludeNodes(!messageIncludeNodes)}
                title={
                  messageIncludeNodes
                    ? t("fileMgmt.hideNodeMessages")
                    : t("fileMgmt.includeNodeMessagesScope")
                }
              >
                <GitBranch className="h-3 w-3 shrink-0" />
                {t("common.nodes")}
              </button>
            </div>
            <p className="pm-files-scope-hint" title={scopeHint}>
              {scopeHint}
            </p>
          </div>
        )}

        {/* File context: compact label instead of toggles */}
        {focusFileId && (
          <div className="shrink-0">
            <p className="pm-files-scope-hint" title={scopeHint}>
              {scopeHint}
            </p>
          </div>
        )}

        <ScrollArea className="flex-1 min-h-0 overflow-x-hidden overscroll-x-none [&>[data-slot=scroll-area-viewport]]:overflow-x-hidden! [&>[data-slot=scroll-area-viewport]]:overscroll-x-none">
          {messagesLoading && currentFolderMessages.length === 0 ? (
            <div className="flex items-center justify-center h-12">
              <Loader2 className="h-4 w-4 animate-spin text-[var(--pm-faint)]" />
            </div>
          ) : currentFolderMessages.length === 0 ? (
            <div className="pm-files-empty py-8">
              {t("fileMgmt.noMessagesClick")}
            </div>
          ) : (
            <div
              className={cn(
                "pm-files-msg-list min-w-0 max-w-full overflow-x-hidden transition-opacity duration-200",
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
                  previewSide="left"
                />
              ))}
            </div>
          )}
        </ScrollArea>

      <MessageEditorDialog
        // Stable key — remount on message_id kills silk enter/exit
        key="folder-message-editor"
        open={dialogOpen}
        onOpenChange={handleCloseDialog}
        title={editingMsg ? t("common.message") : t("fileMgmt.addMessage")}
        kicker={
          editingMsg
            ? undefined
            : focusFileId
              ? t("common.file")
              : focusFolderId
                ? t("common.folder")
                : folderScopeIsRoot
                  ? t("common.root")
                  : t("common.folder")
        }
        description={
          editingMsg
            ? undefined
            : focusFileId
              ? fileLabel
                ? t("fileMgmt.newMessageOnFile", { name: fileLabel })
                : t("fileMgmt.newMessageOnSelectedFile")
              : focusFolderId
                ? focusFolder?.name
                  ? t("fileMgmt.newMessageOnFolderNamed", {
                      name: focusFolder.name,
                    })
                  : t("fileMgmt.newMessageOnSelectedFolder")
                : folderScopeIsRoot
                  ? t("fileMgmt.newMessageAtRoot")
                  : t("fileMgmt.newMessageOnThisFolder")
        }
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
          // Silk exit first — keep payload until animation ends
          handleCloseDialog(false)
          window.setTimeout(() => {
            requestTimelineFocus(nodeId, chainId)
          }, 320)
        }}
      />

      <LogMessageDialog
        open={!!logOpen}
        onOpenChange={(v) => {
          if (!v) setLogOpen(null)
        }}
        collectionId={collectionId}
        docSource={logOpen?.docSource ?? null}
        message={logOpen?.message ?? null}
        version={logOpen?.version ?? null}
        isCurrentVersion={!!logOpen?.isCurrent}
        onSaved={(updated) => {
          setLogOpen((prev) =>
            prev && prev.message.message_id === updated.message_id
              ? { ...prev, message: updated }
              : prev
          )
          void refreshMessages(collectionId, { silent: true })
        }}
        onVersionDeleted={() => {
          setLogOpen(null)
          void refreshMessages(collectionId, { silent: true })
        }}
      />
    </div>
  )
}
