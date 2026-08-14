import { useEffect, useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogKicker,
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
        overlayClassName="pm-node-preview-overlay"
        className={cn(
          "pm-node-preview-sheet",
          "fixed inset-0 top-0 left-0 z-50",
          "w-screen max-w-none h-screen max-h-none",
          "translate-x-0 translate-y-0",
          "rounded-none border-0 p-0 shadow-none gap-0",
          "bg-transparent",
          "flex flex-col sm:max-w-none",
        )}
      >
        <button
          type="button"
          aria-label="Close node preview"
          className="pm-node-preview-hit"
          onClick={() => onOpenChange(false)}
        />

        <div className="pm-node-preview-stage">
          <div className="pm-node-preview-graph-wrap">
            {nodeId && (
              <div
                className="pm-node-preview-graph"
                onClick={(e) => e.stopPropagation()}
              >
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

          <aside
            className="pm-node-preview-rail"
            onClick={(e) => e.stopPropagation()}
          >
            <DialogHeader className="pm-node-preview-rail-head">
              <DialogKicker>Node</DialogKicker>
              <DialogTitle className="pm-node-preview-title">
                {loading ? "Loading…" : detail?.title || "Untitled"}
              </DialogTitle>
              <DialogDescription className="pm-node-preview-lede">
                Preview only — stays on this file.
              </DialogDescription>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className="pm-node-preview-close"
                onClick={() => onOpenChange(false)}
              >
                <X className="size-3.5" />
                <span className="sr-only">Close</span>
              </Button>
            </DialogHeader>

            {loading ? (
              <div className="pm-node-preview-empty">
                <Loader2 className="size-4 animate-spin" />
              </div>
            ) : !detail ? (
              <div className="pm-node-preview-empty">
                <p className="pm-meta">Node not found</p>
              </div>
            ) : (
              <div className="pm-node-preview-rail-body">
                <dl className="pm-ws-meta-grid pm-node-preview-meta">
                  <dt>Group</dt>
                  <dd title={groupName}>{groupName}</dd>
                  <dt>Type</dt>
                  <dd className="capitalize">{detail.node_type}</dd>
                  {detail.event_time ? (
                    <>
                      <dt>Date</dt>
                      <dd>
                        <span className="pm-node-preview-date">
                          <Calendar className="size-3" aria-hidden />
                          {detail.event_time.slice(0, 10)}
                        </span>
                      </dd>
                    </>
                  ) : null}
                </dl>

                <section className="pm-node-preview-block">
                  <header className="pm-node-preview-block-h">
                    <span className="pm-label">Attachments</span>
                    <span className="pm-count-pill">
                      {detail.attachments?.length ?? 0}
                    </span>
                  </header>
                  {(detail.attachments?.length ?? 0) === 0 ? (
                    <p className="pm-meta">None</p>
                  ) : (
                    <ul className="pm-node-preview-files">
                      {detail.attachments.map((a) => {
                        const clickable = !!onOpenAttachment
                        const label =
                          a.display_name || a.filename || a.file_id
                        const inner = (
                          <>
                            <Paperclip className="size-3 shrink-0" aria-hidden />
                            <span className="truncate">{label}</span>
                            {a.archived ? (
                              <span className="pm-meta uppercase shrink-0">
                                archived
                              </span>
                            ) : null}
                          </>
                        )
                        return (
                          <li key={a.file_id}>
                            {clickable ? (
                              <button
                                type="button"
                                title="Open file detail"
                                onClick={() => onOpenAttachment(a.file_id)}
                                className="pm-node-preview-file"
                              >
                                {inner}
                              </button>
                            ) : (
                              <div className="pm-node-preview-file is-static">
                                {inner}
                              </div>
                            )}
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </section>

                <section className="pm-node-preview-block">
                  <header className="pm-node-preview-block-h">
                    <span className="pm-label">Messages</span>
                    <span className="pm-count-pill">{messages.length}</span>
                  </header>
                  {messages.length === 0 ? (
                    <p className="pm-meta">No messages</p>
                  ) : (
                    <ol className="pm-node-preview-msgs">
                      {messages.map((m) => (
                        <li key={m.message_id} className="pm-node-preview-msg">
                          {m.created_at ? (
                            <time className="pm-meta" dateTime={m.created_at}>
                              {new Date(m.created_at).toLocaleString()}
                            </time>
                          ) : null}
                          <div className="pm-node-preview-msg-body">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                              {m.body || ""}
                            </ReactMarkdown>
                          </div>
                        </li>
                      ))}
                    </ol>
                  )}
                </section>
              </div>
            )}
          </aside>
        </div>
      </DialogContent>
    </Dialog>
  )
}
