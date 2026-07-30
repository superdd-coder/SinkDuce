import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { Bot, Clock, Loader2, Pencil, Plus, X } from "lucide-react"
import { toast } from "sonner"
import type { Chain, Message, Node } from "@/types/file-mgmt"
import {
  createCollectionMessage,
  createFolderMessage,
  createNodeMessage,
  getCollectionMessages,
  getFileMessages,
  getFolderMessages,
  getNodeDetail,
  getNodeMessages,
  updateMessage,
  deleteMessage,
} from "@/api/file-mgmt"
import {
  MessageBody,
  MessageCard,
  resolveMessageHighlightNodeIds,
} from "../message-card"
import { MessageEditorDialog } from "../folder-view/message-editor-dialog"
import { cn } from "@/lib/utils"

export type MessageFocus =
  | { kind: "main" }
  | { kind: "chain"; chainId: string }
  | { kind: "node"; nodeId: string; chainId: string }

export type MessageDetailState = {
  open: boolean
  /** Timeline nodes to highlight while detail is open (all mounts for file msgs) */
  sourceNodeIds: string[]
  /** Active message id — used so switching messages always re-fits the canvas */
  messageId?: string | null
}

interface MessageStreamSidebarProps {
  collectionId: string
  chains: Chain[]
  /** chainId → nodes */
  chainNodes: Map<string, Node[]>
  focus: MessageFocus
  onClose: () => void
  onFocusChange: (focus: MessageFocus) => void
  /** Notify parent so shell width / node highlight can update */
  onDetailChange?: (state: MessageDetailState) => void
  /**
   * Parent-controlled open flag (e.g. empty-canvas click closes detail).
   * When false, local detail panel is cleared.
   */
  detailOpen?: boolean
}

/**
 * Collect chain id + descendant branch chain ids (extensible for nested V2).
 */
export function collectChainSubtree(
  chains: Chain[],
  rootChainId: string
): string[] {
  const byParent = new Map<string, string[]>()
  for (const c of chains) {
    if (!c.parent_chain_id) continue
    const list = byParent.get(c.parent_chain_id) ?? []
    list.push(c.chain_id)
    byParent.set(c.parent_chain_id, list)
  }
  const out: string[] = []
  const stack = [rootChainId]
  while (stack.length) {
    const id = stack.pop()!
    out.push(id)
    for (const child of byParent.get(id) ?? []) stack.push(child)
  }
  return out
}

/** Node messages + optional messages on attached files. */
async function loadMessagesForNode(
  collectionId: string,
  nodeId: string,
  includeFiles: boolean
): Promise<Message[]> {
  const out: Message[] = []
  const nm = await getNodeMessages(collectionId, nodeId).catch(() => [])
  out.push(...nm)
  if (includeFiles) {
    try {
      const d = await getNodeDetail(collectionId, nodeId)
      for (const a of d.attachments ?? []) {
        const fm = await getFileMessages(collectionId, a.file_id).catch(() => [])
        out.push(...fm)
      }
    } catch {
      /* ignore detail failures */
    }
  }
  return out
}

/**
 * Branch/folder layer messages.
 * Node messages always come from chain nodes (folder API only returns nodes in
 * groups bound to the folder — timeline branch nodes are usually unbound).
 * File messages: folder path mounts when includeFiles, plus node attachments.
 */
async function loadMessagesForChain(
  collectionId: string,
  chain: Chain,
  nodes: Node[],
  includeFiles: boolean
): Promise<Message[]> {
  const out: Message[] = []
  if (chain.folder_id) {
    // Folder-level + optional path-mounted file msgs; skip group-bound nodes.
    const fm = await getFolderMessages(
      collectionId,
      chain.folder_id,
      false,
      includeFiles
    ).catch(() => [])
    out.push(...fm)
  }
  for (const n of nodes) {
    out.push(...(await loadMessagesForNode(collectionId, n.node_id, includeFiles)))
  }
  return out
}

function formatMsgTime(iso: string | null | undefined): string {
  if (!iso) return ""
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function ownerLabel(msg: Message): string {
  switch (msg.owner_type) {
    case "node":
      return "Node"
    case "file":
      return "File"
    case "folder":
      return "Folder"
    case "system_version":
      return "System"
    default:
      return msg.owner_type || "Message"
  }
}

export function MessageStreamSidebar({
  collectionId,
  chains,
  chainNodes,
  focus,
  onClose,
  onFocusChange: _onFocusChange,
  onDetailChange,
  detailOpen: externalDetailOpen,
}: MessageStreamSidebarProps) {
  void _onFocusChange
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(false)
  const [includeBranches, setIncludeBranches] = useState(false)
  const [includeFiles, setIncludeFiles] = useState(false)
  const [addDialogOpen, setAddDialogOpen] = useState(false)

  /** Detail panel (view / edit) — no modal dialog for view */
  const [detailMsg, setDetailMsg] = useState<Message | null>(null)
  const detailMsgRef = useRef<Message | null>(null)
  detailMsgRef.current = detailMsg
  const [detailEditing, setDetailEditing] = useState(false)
  const [detailDraft, setDetailDraft] = useState("")
  const detailEditingRef = useRef(false)
  const detailDraftRef = useRef("")
  detailEditingRef.current = detailEditing
  detailDraftRef.current = detailDraft
  /** Latest load() for post-save list refresh (avoids TDZ with closeDetail). */
  const loadRef = useRef<() => void>(() => {})

  const mainChain = useMemo(
    () => chains.find((c) => c.is_main) ?? chains.find((c) => !c.parent_chain_id),
    [chains]
  )

  const focusLabel = useMemo(() => {
    if (focus.kind === "main") return "Main chain"
    if (focus.kind === "chain") {
      const c = chains.find((x) => x.chain_id === focus.chainId)
      return c?.title || "Branch"
    }
    for (const nodes of chainNodes.values()) {
      const n = nodes.find((x) => x.node_id === focus.nodeId)
      if (n) return n.title || "Node"
    }
    return "Node"
  }, [focus, chains, chainNodes])

  /** Persist draft when leaving edit (close panel / switch message). Empty draft is skipped. */
  const persistEditIfNeeded = useCallback(async (): Promise<boolean> => {
    const msg = detailMsgRef.current
    if (!detailEditingRef.current || !msg) return true
    const trimmed = detailDraftRef.current.trim()
    if (!trimmed) return true
    if (trimmed === (msg.body || "").trim()) return true
    try {
      const updated = await updateMessage(collectionId, msg.message_id, {
        body: trimmed,
        version: msg.version,
      })
      // Keep ref/state version in sync for subsequent edits
      detailMsgRef.current = updated
      setDetailMsg(updated)
      setDetailDraft(updated.body || trimmed)
      return true
    } catch (err) {
      toast.error(
        `Failed to save: ${err instanceof Error ? err.message : String(err)}`
      )
      return false
    }
  }, [collectionId])

  const closeDetail = useCallback(async () => {
    const wasEditing = detailEditingRef.current
    const ok = await persistEditIfNeeded()
    if (!ok) return
    setDetailMsg(null)
    setDetailEditing(false)
    setDetailDraft("")
    onDetailChange?.({ open: false, sourceNodeIds: [], messageId: null })
    if (wasEditing) loadRef.current()
  }, [onDetailChange, persistEditIfNeeded])

  const openDetail = useCallback(
    async (msg: Message, editing: boolean) => {
      // Auto-save current edit before switching to another message
      if (
        detailMsgRef.current &&
        detailMsgRef.current.message_id !== msg.message_id &&
        detailEditingRef.current
      ) {
        await persistEditIfNeeded()
        loadRef.current()
      }
      setDetailMsg(msg)
      setDetailEditing(editing)
      setDetailDraft(msg.body || "")
      // Immediate highlight for node / source_node_id so we never fall back to a
      // stale msgFocus node while resolving multi-mount file messages.
      const immediate: string[] = []
      if (msg.owner_type === "node" && msg.owner_id) {
        immediate.push(msg.owner_id)
      } else if (msg.source_node_id) {
        immediate.push(msg.source_node_id)
      }
      onDetailChange?.({
        open: true,
        sourceNodeIds: immediate,
        messageId: msg.message_id,
      })

      const sourceNodeIds = await resolveMessageHighlightNodeIds(
        collectionId,
        msg
      )
      // Drop if user already switched away
      if (detailMsgRef.current?.message_id !== msg.message_id) return
      onDetailChange?.({
        open: true,
        sourceNodeIds,
        messageId: msg.message_id,
      })
    },
    [collectionId, onDetailChange, persistEditIfNeeded]
  )

  // Parent force-close (empty canvas click / focus change from timeline)
  useEffect(() => {
    if (externalDetailOpen !== false) return
    if (!detailMsgRef.current) return
    let cancelled = false
    const wasEditing = detailEditingRef.current
    void (async () => {
      await persistEditIfNeeded()
      if (cancelled) return
      setDetailMsg(null)
      setDetailEditing(false)
      setDetailDraft("")
      if (wasEditing) loadRef.current()
    })()
    return () => {
      cancelled = true
    }
  }, [externalDetailOpen, persistEditIfNeeded])

  // Unmount cleanup — best-effort save (fire-and-forget)
  useEffect(() => {
    return () => {
      void persistEditIfNeeded()
      onDetailChange?.({ open: false, sourceNodeIds: [], messageId: null })
    }
  }, [onDetailChange, persistEditIfNeeded])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const collected: Message[] = []

      if (focus.kind === "main") {
        const collMsgs = await getCollectionMessages(collectionId).catch(() => [])
        collected.push(...collMsgs)
        if (mainChain) {
          const mainNodes = chainNodes.get(mainChain.chain_id) ?? []
          for (const n of mainNodes) {
            // Main timeline cards: event + merge (end). Start anchors stay off base stream.
            if (n.node_type !== "event" && n.node_type !== "end") continue
            collected.push(
              ...(await loadMessagesForNode(
                collectionId,
                n.node_id,
                includeFiles
              ))
            )
          }
          if (includeBranches) {
            for (const ch of chains) {
              if (ch.is_main || !ch.parent_chain_id) continue
              collected.push(
                ...(await loadMessagesForChain(
                  collectionId,
                  ch,
                  chainNodes.get(ch.chain_id) ?? [],
                  includeFiles
                ))
              )
            }
          }
        }
      } else if (focus.kind === "chain") {
        const subtree = collectChainSubtree(chains, focus.chainId)
        const anchorIds = new Set<string>()
        for (const cid of subtree) {
          const ch = chains.find((c) => c.chain_id === cid)
          if (!ch) continue
          if (ch.parent_node_id) anchorIds.add(ch.parent_node_id)
          if (ch.merge_node_id) anchorIds.add(ch.merge_node_id)
          collected.push(
            ...(await loadMessagesForChain(
              collectionId,
              ch,
              chainNodes.get(cid) ?? [],
              includeFiles
            ))
          )
        }
        // Start/merge anchors live on the parent (main) chain
        for (const nid of anchorIds) {
          collected.push(
            ...(await loadMessagesForNode(collectionId, nid, includeFiles))
          )
        }
      } else {
        collected.push(
          ...(await loadMessagesForNode(
            collectionId,
            focus.nodeId,
            includeFiles
          ))
        )
      }

      // Dedupe by message_id
      const map = new Map<string, Message>()
      for (const m of collected) map.set(m.message_id, m)
      const sorted = [...map.values()].sort((a, b) =>
        (b.created_at || "").localeCompare(a.created_at || "")
      )
      setMessages(sorted)

      // Keep detail in sync if still present; refresh multi-node highlight
      const cur = detailMsgRef.current
      if (cur) {
        const next = map.get(cur.message_id)
        if (!next) {
          setDetailMsg(null)
          onDetailChange?.({ open: false, sourceNodeIds: [], messageId: null })
        } else {
          setDetailMsg(next)
          const sourceNodeIds = await resolveMessageHighlightNodeIds(
            collectionId,
            next
          )
          // Only apply if still viewing the same message
          if (detailMsgRef.current?.message_id === next.message_id) {
            onDetailChange?.({
              open: true,
              sourceNodeIds,
              messageId: next.message_id,
            })
          }
        }
      }
    } catch (err) {
      toast.error(`Failed to load messages: ${err instanceof Error ? err.message : String(err)}`)
      setMessages([])
    } finally {
      setLoading(false)
    }
  }, [
    collectionId,
    focus,
    chains,
    chainNodes,
    mainChain,
    includeBranches,
    includeFiles,
    onDetailChange,
  ])

  loadRef.current = () => {
    void load()
  }

  useEffect(() => {
    void load()
  }, [load])

  const handleAdd = async (content: string) => {
    try {
      if (focus.kind === "main") {
        await createCollectionMessage(collectionId, {
          owner_type: "collection",
          owner_id: collectionId,
          body: content,
          author_type: "user",
        })
      } else if (focus.kind === "chain") {
        const ch = chains.find((c) => c.chain_id === focus.chainId)
        if (!ch?.folder_id) {
          toast.error("This chain has no folder to attach messages")
          return
        }
        await createFolderMessage(collectionId, ch.folder_id, {
          owner_type: "folder",
          owner_id: ch.folder_id,
          body: content,
          author_type: "user",
        })
      } else {
        await createNodeMessage(collectionId, focus.nodeId, {
          owner_type: "node",
          owner_id: focus.nodeId,
          body: content,
          author_type: "user",
        })
      }
      toast.success("Message added")
      void load()
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  /** Exit edit mode with auto-save (same as closing the editor chrome). */
  const finishEditing = async () => {
    const ok = await persistEditIfNeeded()
    if (!ok) return
    setDetailEditing(false)
    // Refresh body from draft so view mode shows latest without waiting for load
    const trimmed = detailDraftRef.current.trim()
    if (trimmed && detailMsgRef.current) {
      setDetailMsg({
        ...detailMsgRef.current,
        body: trimmed,
      })
    }
    void load()
  }

  const handleDelete = async (messageId: string) => {
    try {
      if (detailMsg?.message_id === messageId) {
        // Skip auto-save when deleting the open message
        setDetailEditing(false)
        setDetailMsg(null)
        setDetailDraft("")
        onDetailChange?.({ open: false, sourceNodeIds: [], messageId: null })
      }
      await deleteMessage(collectionId, messageId)
      void load()
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const addHint =
    focus.kind === "main"
      ? "Add main-chain (collection) message"
      : focus.kind === "chain"
        ? "Add branch folder message"
        : "Add node message"

  const detailOpen = !!detailMsg

  return (
    <div
      data-message-stream-sidebar
      className={cn(
        "h-full w-full min-h-0 flex gap-3",
        detailOpen ? "" : ""
      )}
    >
      {/* Detail panel — equal width, left of stream, squeezes canvas via parent shell */}
      {detailMsg && (
        <div
          data-message-detail-panel
          className="h-full min-w-0 flex-1 border border-border rounded-xl bg-background shadow-lg flex flex-col overflow-hidden"
        >
          <div className="flex items-center justify-between gap-2 px-3 py-3 border-b border-border shrink-0">
            <div className="min-w-0">
              <h3 className="text-xs font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                {detailEditing ? "Edit message" : "Message"}
              </h3>
              <div className="flex items-center gap-1.5 mt-0.5 min-w-0">
                {detailMsg.author_type === "system" ? (
                  <Bot className="h-3 w-3 text-muted-foreground/60 shrink-0" />
                ) : (
                  <Clock className="h-3 w-3 text-muted-foreground/40 shrink-0" />
                )}
                <span className="text-[10px] text-muted-foreground/60 truncate">
                  {formatMsgTime(detailMsg.created_at)}
                  {detailMsg.edited_at ? " · edited" : ""}
                  {" · "}
                  {ownerLabel(detailMsg)}
                </span>
              </div>
            </div>
            <div className="flex items-center gap-0.5 shrink-0">
              {!detailEditing && detailMsg.author_type !== "system" && (
                <button
                  type="button"
                  className="p-1 text-muted-foreground hover:text-foreground rounded"
                  title="Edit"
                  onClick={() => {
                    setDetailEditing(true)
                    setDetailDraft(detailMsg.body || "")
                  }}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
              )}
              <button
                type="button"
                className="p-1 text-muted-foreground hover:text-foreground rounded"
                title={detailEditing ? "Save & close" : "Close"}
                onClick={() => void closeDetail()}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>

          <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
            {detailEditing ? (
              <>
                <div className="flex-1 min-h-0 overflow-auto">
                  <MarkdownEditor
                    value={detailDraft}
                    onChange={setDetailDraft}
                    minHeight="100%"
                    placeholder="Write a message… type / for commands"
                    showToolbar={false}
                  />
                </div>
                <div className="flex items-center justify-end gap-2 px-3 py-2 border-t border-border shrink-0">
                  <Button
                    variant="outline"
                    size="xs"
                    onClick={() => {
                      // Discard local edits, stay on message in view mode
                      setDetailEditing(false)
                      setDetailDraft(detailMsg.body || "")
                    }}
                  >
                    Discard
                  </Button>
                  <Button
                    size="xs"
                    disabled={!detailDraft.trim()}
                    onClick={() => void finishEditing()}
                  >
                    Done
                  </Button>
                </div>
              </>
            ) : (
              <ScrollArea className="flex-1 min-h-0">
                <div className="p-4">
                  <MessageBody
                    body={detailMsg.body || ""}
                    className="prose prose-sm dark:prose-invert max-w-none text-sm leading-relaxed break-words [&_p]:my-2"
                  />
                </div>
              </ScrollArea>
            )}
          </div>
        </div>
      )}

      {/* Message list stream */}
      <div
        className={cn(
          "h-full min-h-0 border border-border rounded-xl bg-background shadow-lg flex flex-col overflow-hidden",
          detailOpen ? "flex-1 min-w-0" : "w-full"
        )}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
          <div className="min-w-0">
            <h3 className="text-xs font-semibold uppercase tracking-[0.1em] text-muted-foreground">
              Message stream
            </h3>
            <p className="text-[10px] text-muted-foreground/70 truncate mt-0.5">
              Focus: {focusLabel}
            </p>
          </div>
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Toggles */}
        <div className="px-3 py-2 border-b border-border/40 space-y-1.5 shrink-0">
          {focus.kind === "main" && (
            <label className="flex items-center gap-2 text-[10px] text-muted-foreground cursor-pointer">
              <input
                type="checkbox"
                className="rounded border-border"
                checked={includeBranches}
                onChange={(e) => setIncludeBranches(e.target.checked)}
              />
              Include branches
            </label>
          )}
          <label className="flex items-center gap-2 text-[10px] text-muted-foreground cursor-pointer">
            <input
              type="checkbox"
              className="rounded border-border"
              checked={includeFiles}
              onChange={(e) => setIncludeFiles(e.target.checked)}
            />
            Include file messages
          </label>
        </div>

        <div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-border/40 shrink-0">
          <span className="text-[10px] text-muted-foreground/50">{messages.length}</span>
          <div className="ml-auto">
            <Button
              variant="ghost"
              size="icon-xs"
              title={addHint}
              onClick={() => setAddDialogOpen(true)}
            >
              <Plus className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>

        <ScrollArea className="flex-1 min-h-0">
          {loading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          ) : messages.length === 0 ? (
            <p className="text-center text-xs text-muted-foreground/50 py-8 px-3">
              No messages in this scope. Click + to add at focus layer.
            </p>
          ) : (
            <div className="flex flex-col gap-1 p-2">
              {messages.map((msg) => (
                <MessageCard
                  key={msg.message_id}
                  msg={msg}
                  previewSide="left"
                  isActive={detailMsg?.message_id === msg.message_id}
                  onView={(m) => {
                    // Toggle close if same message already open in view mode
                    if (
                      detailMsg?.message_id === m.message_id &&
                      !detailEditing
                    ) {
                      closeDetail()
                      return
                    }
                    void openDetail(m, false)
                  }}
                  onEdit={(m) => void openDetail(m, true)}
                  onDelete={() => void handleDelete(msg.message_id)}
                  onSourceNodeClick={() => {
                    // Open detail + highlight all mounts of the source file/node
                    void openDetail(msg, false)
                  }}
                />
              ))}
            </div>
          )}
        </ScrollArea>
      </div>

      {/* Add-only dialog (new message has no detail to show) */}
      <MessageEditorDialog
        key="new-stream-msg"
        open={addDialogOpen}
        onOpenChange={setAddDialogOpen}
        title={addHint}
        initialContent=""
        onSave={handleAdd}
      />
    </div>
  )
}
