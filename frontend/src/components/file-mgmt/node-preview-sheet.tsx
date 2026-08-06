import { useEffect, useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ArrowUpRight, Calendar, Loader2, Paperclip, X } from "lucide-react"
import { toast } from "sonner"
import type { Message, NodeDetail, NodeGroup } from "@/types/file-mgmt"
import { getNodeDetail, getNodeMessages, listGroups } from "@/api/file-mgmt"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { MiniChainGraph } from "@/components/file-mgmt/mini-chain-graph"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

/** Resolve node id from a message for "from node" preview. */
export function resolveMessageSourceNodeId(msg: Message): string | null {
  if (msg.source_node_id) return msg.source_node_id
  if (msg.owner_type === "node") return msg.owner_id
  return null
}

interface NodePreviewSheetProps {
  collectionId: string
  nodeId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
  /**
   * Open a file from the attachments list (e.g. nested file detail).
   * Parent should handle stacking / switching.
   */
  onOpenAttachment?: (fileId: string) => void
  /**
   * When user clicks another node on the mini timeline graph.
   * Defaults to no-op if omitted (chips still highlight active only).
   */
  onSelectNode?: (nodeId: string, chainId: string) => void
  /**
   * Jump to Timeline view focused on the current (or selected) node.
   * Parent should close dialogs then call requestTimelineFocus.
   */
  onGoToNode?: (nodeId: string, chainId: string | null) => void
}

/**
 * Node preview from file detail:
 * - Right sidebar: title / group / attachments / messages
 * - Center: MiniChainGraph (same as folder message dialog), transparent bg
 */
export function NodePreviewSheet({
  collectionId,
  nodeId,
  open,
  onOpenChange,
  onOpenAttachment,
  onSelectNode,
  onGoToNode,
}: NodePreviewSheetProps) {
  const [detail, setDetail] = useState<NodeDetail | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [groups, setGroups] = useState<NodeGroup[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || !nodeId) {
      setDetail(null)
      setMessages([])
      return
    }
    let cancelled = false
    setLoading(true)
    Promise.all([
      getNodeDetail(collectionId, nodeId),
      getNodeMessages(collectionId, nodeId),
      listGroups(collectionId),
    ])
      .then(([d, msgs, gs]) => {
        if (cancelled) return
        setDetail(d)
        setMessages(msgs)
        setGroups(gs)
      })
      .catch((err) => {
        if (!cancelled) {
          toast.error(
            `Failed to load node: ${err instanceof Error ? err.message : String(err)}`
          )
          setDetail(null)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, nodeId, collectionId])

  const groupName = detail?.group_id
    ? groups.find((g) => g.group_id === detail.group_id)?.name ?? "—"
    : "未分类"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className={cn(
          // Full-viewport shell: transparent so file detail peeks through
          "fixed inset-0 top-0 left-0 z-50",
          "w-screen max-w-none h-screen max-h-none",
          "translate-x-0 translate-y-0",
          "rounded-none border-0 p-0 shadow-none gap-0",
          "bg-transparent",
          "flex flex-col sm:max-w-none",
          // Override default dialog zoom centering animation origin
          "data-open:zoom-in-100 data-closed:zoom-out-100"
        )}
      >
        {/* Dim + light blur — glass card does the heavy lifting */}
        <button
          type="button"
          aria-label="Close node preview"
          className="absolute inset-0 z-0 bg-black/30 dark:bg-black/45 supports-backdrop-filter:backdrop-blur-sm cursor-default"
          onClick={() => onOpenChange(false)}
        />

        <div className="relative z-10 flex h-full min-h-0 w-full pointer-events-none">
          {/* Center: frosted-glass timeline card */}
          <div className="flex-1 min-w-0 flex items-center justify-center p-4 sm:p-6 pr-2">
            {nodeId && (
              <div
                className={cn(
                  "pointer-events-auto relative w-full max-w-4xl",
                  "h-[min(320px,46vh)] min-h-[170px]",
                  "rounded-xl overflow-hidden flex flex-col",
                  // Frosted glass: semi-solid + strong blur, not fully transparent
                  "border border-white/25 dark:border-white/10",
                  "bg-background/75 dark:bg-background/65",
                  "supports-backdrop-filter:backdrop-blur-xl",
                  "shadow-2xl shadow-black/25",
                  "ring-1 ring-inset ring-white/15 dark:ring-white/5",
                  "animate-in fade-in-0 zoom-in-95 duration-200"
                )}
                onClick={(e) => e.stopPropagation()}
              >
                {/* Soft scrim so document text under the glass stays muted */}
                <div
                  className="pointer-events-none absolute inset-0 z-0 bg-gradient-to-b from-background/35 via-background/25 to-background/45"
                  aria-hidden
                />
                <div className="relative z-10 px-3 pt-1.5 pb-0.5 flex items-center justify-between shrink-0 gap-2">
                  <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-medium">
                    Timeline
                  </p>
                  {onGoToNode && nodeId ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="h-6 px-2 text-[10px] gap-0.5 text-primary hover:text-primary"
                      title="Open this node in Timeline view"
                      disabled={loading || !detail}
                      onClick={() => {
                        onGoToNode(nodeId, detail?.chain_id ?? null)
                      }}
                    >
                      Go To
                      <ArrowUpRight className="h-3 w-3" />
                    </Button>
                  ) : (
                    <p className="text-[9px] text-muted-foreground/70">
                      Node context graph
                    </p>
                  )}
                </div>
                <MiniChainGraph
                  collectionId={collectionId}
                  nodeId={nodeId}
                  variant="glass"
                  className="relative z-10 flex-1 min-h-0 bg-transparent"
                  onNodeClick={(nid, cid) => {
                    if (nid === nodeId) return
                    onSelectNode?.(nid, cid)
                  }}
                />
              </div>
            )}
          </div>

          {/* Right: node detail sidebar */}
          <aside
            className={cn(
              "pointer-events-auto shrink-0",
              "w-full sm:w-[min(28rem,100%)] h-full",
              "bg-popover text-popover-foreground",
              "border-l border-border shadow-xl",
              "flex flex-col",
              "animate-in slide-in-from-right-4 duration-200"
            )}
            onClick={(e) => e.stopPropagation()}
          >
            <DialogHeader className="px-4 py-3 shrink-0 space-y-1 relative pr-12">
              <DialogTitle className="text-sm">Node preview</DialogTitle>
              <DialogDescription className="text-[11px]">
                Read-only preview from file detail. Does not switch to Timeline.
              </DialogDescription>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className="absolute top-2.5 right-2"
                onClick={() => onOpenChange(false)}
              >
                <X className="h-4 w-4" />
                <span className="sr-only">Close</span>
              </Button>
            </DialogHeader>

            {loading ? (
              <div className="flex-1 flex items-center justify-center">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : !detail ? (
              <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
                Node not found
              </div>
            ) : (
              <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4">
                <div>
                  <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60 mb-0.5">
                    Title
                  </p>
                  <p className="text-sm font-medium">
                    {detail.title || "Untitled"}
                  </p>
                </div>
                <div className="flex gap-4">
                  <div>
                    <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60 mb-0.5">
                      Group
                    </p>
                    <p className="text-xs text-muted-foreground">{groupName}</p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60 mb-0.5">
                      Type
                    </p>
                    <p className="text-xs uppercase text-muted-foreground">
                      {detail.node_type}
                    </p>
                  </div>
                </div>
                {detail.event_time && (
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Calendar className="h-3 w-3" />
                    {detail.event_time.slice(0, 10)}
                  </div>
                )}

                <div>
                  <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60 mb-1.5">
                    Attachments ({detail.attachments?.length ?? 0})
                  </p>
                  {(detail.attachments?.length ?? 0) === 0 ? (
                    <p className="text-xs text-muted-foreground/50">None</p>
                  ) : (
                    <ul className="space-y-1">
                      {detail.attachments.map((a) => {
                        const clickable = !!onOpenAttachment
                        const inner = (
                          <>
                            <Paperclip className="h-3 w-3 text-muted-foreground shrink-0" />
                            <span className="truncate flex-1 min-w-0">
                              {a.filename || a.file_id}
                            </span>
                            {a.archived && (
                              <span className="text-[9px] text-amber-500 shrink-0">
                                archived
                              </span>
                            )}
                          </>
                        )
                        return (
                          <li key={a.file_id}>
                            {clickable ? (
                              <button
                                type="button"
                                title="Open file detail"
                                onClick={() => onOpenAttachment(a.file_id)}
                                className={cn(
                                  "w-full flex items-center gap-1.5 text-xs px-2 py-1.5 rounded",
                                  "bg-muted/30 hover:bg-muted/60 hover:text-primary",
                                  "text-left transition-colors"
                                )}
                              >
                                {inner}
                              </button>
                            ) : (
                              <div className="flex items-center gap-1.5 text-xs px-2 py-1 rounded bg-muted/30">
                                {inner}
                              </div>
                            )}
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </div>

                <div>
                  <p className="text-[10px] uppercase tracking-wide text-muted-foreground/60 mb-1.5">
                    Messages ({messages.length})
                  </p>
                  {messages.length === 0 ? (
                    <p className="text-xs text-muted-foreground/50">No messages</p>
                  ) : (
                    <div className="space-y-2">
                      {messages.map((m) => (
                        <div
                          key={m.message_id}
                          className="rounded-md border border-border/40 p-2 text-xs"
                        >
                          <p className="text-[10px] text-muted-foreground/50 mb-1">
                            {m.created_at
                              ? new Date(m.created_at).toLocaleString()
                              : ""}
                          </p>
                          <div className="prose prose-xs dark:prose-invert max-w-none">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                              {m.body || ""}
                            </ReactMarkdown>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </aside>
        </div>
      </DialogContent>
    </Dialog>
  )
}
