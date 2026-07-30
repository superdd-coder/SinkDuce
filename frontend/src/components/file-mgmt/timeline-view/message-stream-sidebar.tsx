import { useCallback, useEffect, useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Loader2, Plus, X } from "lucide-react"
import { toast } from "sonner"
import type { Chain, Message, Node } from "@/types/file-mgmt"
import {
  createCollectionMessage,
  createFolderMessage,
  createNodeMessage,
  getCollectionMessages,
  getFileMessages,
  getFolderMessages,
  getNodeMessages,
  updateMessage,
  deleteMessage,
} from "@/api/file-mgmt"
import { MessageCard } from "../message-card"
import { MessageEditorDialog } from "../folder-view/message-editor-dialog"

export type MessageFocus =
  | { kind: "main" }
  | { kind: "chain"; chainId: string }
  | { kind: "node"; nodeId: string; chainId: string }

interface MessageStreamSidebarProps {
  collectionId: string
  chains: Chain[]
  /** chainId → nodes */
  chainNodes: Map<string, Node[]>
  focus: MessageFocus
  onClose: () => void
  onFocusChange: (focus: MessageFocus) => void
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

export function MessageStreamSidebar({
  collectionId,
  chains,
  chainNodes,
  focus,
  onClose,
}: MessageStreamSidebarProps) {
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(false)
  const [includeBranches, setIncludeBranches] = useState(false)
  const [includeFiles, setIncludeFiles] = useState(false)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingMsg, setEditingMsg] = useState<Message | null>(null)
  const [dialogReadonly, setDialogReadonly] = useState(false)

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
            if (n.node_type !== "event" && n.node_type !== "end") continue
            const nm = await getNodeMessages(collectionId, n.node_id).catch(() => [])
            collected.push(...nm)
            if (includeFiles) {
              // file msgs loaded via node attachments would need detail; skip bulk for perf
              // optional light path: only when includeFiles and we have few nodes
            }
          }
          if (includeBranches) {
            const branchIds = chains
              .filter((c) => !c.is_main && c.parent_chain_id)
              .map((c) => c.chain_id)
            for (const cid of branchIds) {
              const ch = chains.find((c) => c.chain_id === cid)
              if (ch?.folder_id) {
                const fm = await getFolderMessages(
                  collectionId,
                  ch.folder_id,
                  true,
                  includeFiles
                ).catch(() => [])
                collected.push(...fm)
              } else {
                for (const n of chainNodes.get(cid) ?? []) {
                  if (n.node_type !== "event") continue
                  const nm = await getNodeMessages(collectionId, n.node_id).catch(
                    () => []
                  )
                  collected.push(...nm)
                }
              }
            }
          }
        }
      } else if (focus.kind === "chain") {
        const subtree = collectChainSubtree(chains, focus.chainId)
        // Main-chain start / merge anchors for this branch (and nested branches)
        const anchorIds = new Set<string>()
        for (const cid of subtree) {
          const ch = chains.find((c) => c.chain_id === cid)
          if (ch?.parent_node_id) anchorIds.add(ch.parent_node_id)
          if (ch?.merge_node_id) anchorIds.add(ch.merge_node_id)
          if (ch?.folder_id) {
            const fm = await getFolderMessages(
              collectionId,
              ch.folder_id,
              true,
              includeFiles
            ).catch(() => [])
            collected.push(...fm)
          } else {
            // Include every node on the branch (start/event/end)
            for (const n of chainNodes.get(cid) ?? []) {
              const nm = await getNodeMessages(collectionId, n.node_id).catch(() => [])
              collected.push(...nm)
            }
          }
        }
        // Always pull messages on start/merge anchors (live on main chain)
        for (const nid of anchorIds) {
          const nm = await getNodeMessages(collectionId, nid).catch(() => [])
          collected.push(...nm)
        }
      } else {
        const nm = await getNodeMessages(collectionId, focus.nodeId).catch(() => [])
        collected.push(...nm)
        if (includeFiles) {
          // Load via getNodeDetail attachments
          const { getNodeDetail } = await import("@/api/file-mgmt")
          try {
            const d = await getNodeDetail(collectionId, focus.nodeId)
            for (const a of d.attachments ?? []) {
              const fm = await getFileMessages(collectionId, a.file_id).catch(() => [])
              collected.push(...fm)
            }
          } catch {
            /* ignore */
          }
        }
      }

      // Dedupe by message_id
      const map = new Map<string, Message>()
      for (const m of collected) map.set(m.message_id, m)
      const sorted = [...map.values()].sort((a, b) =>
        (b.created_at || "").localeCompare(a.created_at || "")
      )
      setMessages(sorted)
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
  ])

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

  const handleEdit = async (content: string) => {
    if (!editingMsg) return
    try {
      await updateMessage(collectionId, editingMsg.message_id, {
        body: content,
        version: editingMsg.version,
      })
      toast.success("Message updated")
      setEditingMsg(null)
      void load()
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleDelete = async (messageId: string) => {
    try {
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

  return (
    <div
      data-message-stream-sidebar
      className="h-full w-full min-h-0 border border-border rounded-xl bg-background shadow-lg flex flex-col overflow-hidden"
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
            onClick={() => {
              setEditingMsg(null)
              setDialogReadonly(false)
              setDialogOpen(true)
            }}
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
                onView={(m) => {
                  setEditingMsg(m)
                  setDialogReadonly(true)
                  setDialogOpen(true)
                }}
                onEdit={(m) => {
                  setEditingMsg(m)
                  setDialogReadonly(false)
                  setDialogOpen(true)
                }}
                onDelete={() => void handleDelete(msg.message_id)}
              />
            ))}
          </div>
        )}
      </ScrollArea>

      <MessageEditorDialog
        key={editingMsg?.message_id || "new-stream"}
        open={dialogOpen}
        onOpenChange={(o) => {
          if (!o) setEditingMsg(null)
          setDialogOpen(o)
        }}
        title={
          dialogReadonly
            ? "Message"
            : editingMsg
              ? "Edit Message"
              : addHint
        }
        initialContent={editingMsg?.body || ""}
        onSave={
          !editingMsg
            ? handleAdd
            : !dialogReadonly
              ? handleEdit
              : () => {}
        }
        readonly={dialogReadonly}
      />
    </div>
  )
}
