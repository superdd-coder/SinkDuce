import { useEffect, useState } from "react"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import { Calendar, Loader2, Paperclip } from "lucide-react"
import { toast } from "sonner"
import type { Message, NodeDetail, NodeGroup } from "@/types/file-mgmt"
import { getNodeDetail, getNodeMessages, listGroups } from "@/api/file-mgmt"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

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
}

export function NodePreviewSheet({
  collectionId,
  nodeId,
  open,
  onOpenChange,
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
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-md flex flex-col gap-0 p-0">
        <SheetHeader className="px-4 py-3 border-b border-border shrink-0">
          <SheetTitle className="text-sm">Node preview</SheetTitle>
          <SheetDescription className="text-[11px]">
            Read-only preview from folder messages. Does not switch to Timeline.
          </SheetDescription>
        </SheetHeader>

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
              <p className="text-sm font-medium">{detail.title || "Untitled"}</p>
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
                <p className="text-xs uppercase text-muted-foreground">{detail.node_type}</p>
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
                  {detail.attachments.map((a) => (
                    <li
                      key={a.file_id}
                      className="flex items-center gap-1.5 text-xs px-2 py-1 rounded bg-muted/30"
                    >
                      <Paperclip className="h-3 w-3 text-muted-foreground shrink-0" />
                      <span className="truncate">{a.filename || a.file_id}</span>
                      {a.archived && (
                        <span className="text-[9px] text-amber-500">archived</span>
                      )}
                    </li>
                  ))}
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
      </SheetContent>
    </Sheet>
  )
}
