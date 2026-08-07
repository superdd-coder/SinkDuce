import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { MESSAGE_EDITOR_PLACEHOLDER } from "@/components/ui/tiptap-editor"
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
  enrichMessageSourceNames,
  formatMessageSourceTag,
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

/** Compact list time — same as MessageEditorDialog node rail. */
function formatMsgListTime(iso: string | null | undefined): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ""
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

/** Plain excerpt for stream rows (matches message-editor-dialog). */
function messagePlainExcerpt(body: string): string {
  return (body || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`[^`]*`/g, " ")
    .replace(/!\[.*?\]\(.*?\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/[*_~>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
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

  /**
   * Sliding focus wash (same as node-message dialog Messages card).
   * listFocusId moves immediately on click; detailMsg may lag after openDetail.
   */
  const [listFocusId, setListFocusId] = useState<string | null>(null)
  const msgListRef = useRef<HTMLDivElement>(null)
  const msgItemRefs = useRef(new Map<string, HTMLDivElement>())
  const focusIndRef = useRef({ top: 0, height: 0, ready: false })
  const [focusInd, setFocusInd] = useState({
    top: 0,
    height: 0,
    ready: false,
  })
  const focusCanTweenRef = useRef(false)

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
    setListFocusId(null)
    onDetailChange?.({ open: false, sourceNodeIds: [], messageId: null })
    if (wasEditing) loadRef.current()
  }, [onDetailChange, persistEditIfNeeded])

  const openDetail = useCallback(
    async (msg: Message, editing: boolean) => {
      // Focus wash slides immediately (do not wait for async open)
      setListFocusId(msg.message_id)

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

  // ── Sliding focus indicator — same algorithm as MessageEditorDialog ──
  const measureFocusInd = useCallback(() => {
    const id = listFocusId
    const list = msgListRef.current
    const el = id ? msgItemRefs.current.get(id) : null
    if (!list || !el) {
      if (focusIndRef.current.ready) {
        const next = { top: 0, height: 0, ready: false }
        focusIndRef.current = next
        setFocusInd(next)
      }
      return
    }

    // Prefer geometry relative to list (scroll-safe); fall back to offsetTop
    const listRect = list.getBoundingClientRect()
    const elRect = el.getBoundingClientRect()
    const top = elRect.top - listRect.top + list.scrollTop
    const height = elRect.height
    if (height < 1) return

    const prev = focusIndRef.current
    if (
      prev.ready &&
      Math.abs(prev.top - top) < 0.5 &&
      Math.abs(prev.height - height) < 0.5
    ) {
      return
    }
    const next = { top, height, ready: true }
    focusIndRef.current = next
    setFocusInd(next)
    // First place is hard; subsequent moves slide (is-focus-tween)
    if (!focusCanTweenRef.current) {
      requestAnimationFrame(() => {
        focusCanTweenRef.current = true
        list.classList.add("is-focus-tween")
      })
    }
  }, [listFocusId])

  useLayoutEffect(() => {
    measureFocusInd()
  }, [measureFocusInd, messages.length, loading, listFocusId])

  useEffect(() => {
    const list = msgListRef.current
    if (!list) return
    const onScroll = () => measureFocusInd()
    list.addEventListener("scroll", onScroll, { passive: true })
    const ro = new ResizeObserver(() => measureFocusInd())
    ro.observe(list)
    for (const el of msgItemRefs.current.values()) ro.observe(el)
    return () => {
      list.removeEventListener("scroll", onScroll)
      ro.disconnect()
    }
  }, [measureFocusInd, loading, messages.length])

  // Reset tween lock when list reloads (new focus scope / filters)
  useEffect(() => {
    focusCanTweenRef.current = false
    msgListRef.current?.classList.remove("is-focus-tween")
    // Keep ready false until next measure — do not leave a stale pill
    focusIndRef.current = { top: 0, height: 0, ready: false }
    setFocusInd({ top: 0, height: 0, ready: false })
    setListFocusId(null)
  }, [focus, includeBranches, includeFiles])

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
      setListFocusId(null)
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
      // Resolve Folder/File/Node names from ids (never show raw ids in tags)
      const nodeNameById = new Map<string, string>()
      for (const nodes of chainNodes.values()) {
        for (const n of nodes) {
          if (n.title) nodeNameById.set(n.node_id, n.title)
        }
      }
      const prefilled = sorted.map((m) => {
        if (m.source_name?.trim()) return m
        const ot = (m.owner_type || "").toLowerCase()
        if (ot === "node" && nodeNameById.has(m.owner_id)) {
          return { ...m, source_name: nodeNameById.get(m.owner_id)! }
        }
        return m
      })
      const enriched = await enrichMessageSourceNames(collectionId, prefilled)
      setMessages(enriched)

      // Keep detail in sync if still present; refresh multi-node highlight
      const cur = detailMsgRef.current
      if (cur) {
        const next =
          enriched.find((x) => x.message_id === cur.message_id) ??
          map.get(cur.message_id)
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
      className="h-full w-full min-h-0 flex gap-3"
    >
      {/* Detail panel — equal width, left of stream, squeezes canvas via parent shell */}
      {detailMsg && (
        <div
          data-message-detail-panel
          className="pm-timeline-panel pm-timeline-msg-detail h-full min-w-0 flex-1"
        >
          <div className="pm-timeline-panel-head justify-between gap-2">
            <div className="min-w-0">
              <h3 className="pm-timeline-panel-title">
                {detailEditing ? "Edit message" : "Message"}
              </h3>
              <div className="flex items-center gap-1.5 mt-0.5 min-w-0">
                {detailMsg.author_type === "system" ? (
                  <Bot className="h-3 w-3 text-[var(--pm-faint)] shrink-0" />
                ) : (
                  <Clock className="h-3 w-3 text-[var(--pm-faint)] shrink-0" />
                )}
                <span className="pm-timeline-panel-meta truncate">
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
                  className="p-1 text-[var(--pm-faint)] hover:text-[var(--pm-ink)] rounded-[var(--pm-r-sm)] transition-colors"
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
                className="p-1 text-[var(--pm-faint)] hover:text-[var(--pm-ink)] rounded-[var(--pm-r-sm)] transition-colors"
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
                <div className="flex-1 min-h-0 overflow-auto px-2">
                  <MarkdownEditor
                    value={detailDraft}
                    onChange={setDetailDraft}
                    minHeight="100%"
                    placeholder={MESSAGE_EDITOR_PLACEHOLDER}
                    showToolbar={false}
                  />
                </div>
                <div className="pm-timeline-panel-foot flex items-center justify-end gap-2">
                  <Button
                    variant="ghost"
                    size="xs"
                    className="pm-btn-ghost"
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
                    className="pm-btn-pri"
                    disabled={!detailDraft.trim()}
                    onClick={() => void finishEditing()}
                  >
                    Done
                  </Button>
                </div>
              </>
            ) : (
              <ScrollArea className="flex-1 min-h-0">
                <div className="px-4 pb-4">
                  <MessageBody
                    body={detailMsg.body || ""}
                    className="pm-prose max-w-none break-words [&_p]:my-2"
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
          "pm-timeline-panel h-full min-h-0",
          detailOpen ? "flex-1 min-w-0" : "w-full"
        )}
      >
        <div className="pm-timeline-panel-head justify-between">
          <div className="min-w-0">
            <h3 className="pm-timeline-panel-title">Message stream</h3>
            <p className="pm-timeline-panel-meta truncate mt-0.5">
              Focus: {focusLabel}
            </p>
          </div>
          <button
            type="button"
            className="text-[var(--pm-faint)] hover:text-[var(--pm-ink)] transition-colors p-1 rounded-[var(--pm-r-sm)]"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Scope chips + add — one row: short filters left, + pinned right */}
        <div className="pm-timeline-scope-row">
          <div className="pm-timeline-scope-filters">
            {focus.kind === "main" && (
              <button
                type="button"
                className={cn(
                  "pm-timeline-scope-btn",
                  includeBranches && "is-on"
                )}
                onClick={() => setIncludeBranches(!includeBranches)}
                title="Include branch messages"
              >
                Branches
              </button>
            )}
            <button
              type="button"
              className={cn(
                "pm-timeline-scope-btn",
                includeFiles && "is-on"
              )}
              onClick={() => setIncludeFiles(!includeFiles)}
              title="Include file messages"
            >
              Files
            </button>
          </div>
          <button
            type="button"
            className="pm-timeline-scope-add"
            title={addHint}
            onClick={() => {
              setAddDialogOpen(false)
              requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                  setAddDialogOpen(true)
                })
              })
            }}
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>

        {loading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="h-4 w-4 animate-spin text-[var(--pm-faint)]" />
          </div>
        ) : messages.length === 0 ? (
          <p className="text-center pm-meta text-[var(--pm-faint)] py-8 px-3">
            No messages in this scope. Click + to add at focus layer.
          </p>
        ) : (
          /*
           * Sliding focus wash — identical structure to MessageEditorDialog
           * node Messages card: row IS the measured element (no wrapper).
           */
          <div
            ref={msgListRef}
            className="pm-node-msg-list flex-1 min-h-0 overflow-y-auto"
          >
            <div
              className={cn(
                "pm-node-msg-focus",
                focusInd.ready && !!listFocusId && "is-ready"
              )}
              style={{
                top: focusInd.top,
                height: focusInd.height,
              }}
              aria-hidden
            />
            {messages.map((msg) => {
              const focused = msg.message_id === listFocusId
              const excerpt = messagePlainExcerpt(msg.body || "")
              const time = formatMsgListTime(msg.created_at)
              const sourceTag = formatMessageSourceTag(msg)
              return (
                <div
                  key={msg.message_id}
                  ref={(el) => {
                    if (el) msgItemRefs.current.set(msg.message_id, el)
                    else msgItemRefs.current.delete(msg.message_id)
                  }}
                  role="button"
                  tabIndex={0}
                  className={cn(
                    "pm-msg-card pm-node-msg-row group",
                    focused && "is-focused"
                  )}
                  onClick={() => {
                    if (
                      detailMsg?.message_id === msg.message_id &&
                      !detailEditing
                    ) {
                      void closeDetail()
                      return
                    }
                    void openDetail(msg, false)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      if (
                        detailMsg?.message_id === msg.message_id &&
                        !detailEditing
                      ) {
                        void closeDetail()
                        return
                      }
                      void openDetail(msg, false)
                    }
                  }}
                >
                  <div className="flex items-center gap-1.5 mb-0.5 min-w-0">
                    <Clock
                      className="h-3 w-3 shrink-0 text-[var(--pm-faint)]"
                      strokeWidth={1.75}
                    />
                    {time ? (
                      <span className="pm-meta shrink-0 tabular-nums">
                        {time}
                      </span>
                    ) : null}
                    {msg.edited_at ? (
                      <span className="pm-meta italic shrink-0">edited</span>
                    ) : null}
                    {sourceTag ? (
                      <span
                        className={cn(
                          "pm-meta shrink-0 truncate max-w-[8rem] px-1.5 py-0.5 rounded",
                          sourceTag.kind === "Node" ||
                            (msg.owner_type || "").toLowerCase() === "node"
                            ? "text-[var(--pm-green)] bg-[var(--pm-green-wash)]"
                            : "text-[var(--pm-muted)] bg-[rgba(18,20,16,0.05)]"
                        )}
                        title={sourceTag.full}
                      >
                        {sourceTag.label}
                      </span>
                    ) : null}
                    {msg.author_type !== "system" ? (
                      <button
                        type="button"
                        className="ml-auto shrink-0 pm-meta text-[var(--pm-faint)] hover:text-[var(--pm-danger)] opacity-0 group-hover:opacity-100 transition-opacity px-1"
                        title="Delete message"
                        onClick={(e) => {
                          e.stopPropagation()
                          void handleDelete(msg.message_id)
                        }}
                      >
                        ×
                      </button>
                    ) : null}
                  </div>
                  <p className="pm-node-msg-excerpt">
                    {excerpt || "Empty message"}
                  </p>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Add-only dialog — silk enter/exit via stable mount + open flip */}
      <MessageEditorDialog
        key="stream-message-editor"
        open={addDialogOpen}
        onOpenChange={(next) => {
          setAddDialogOpen(next)
        }}
        title={addHint}
        initialContent=""
        onSave={handleAdd}
        readonly={false}
      />
    </div>
  )
}
