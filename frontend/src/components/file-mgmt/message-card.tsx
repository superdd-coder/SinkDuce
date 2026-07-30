import { Bot, Clock, Pencil, Trash2 } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import rehypeRaw from "rehype-raw"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { Message } from "@/types/file-mgmt"

/**
 * Tiptap (Color / Highlight) serializes styled text as HTML spans inside
 * markdown. Default ReactMarkdown escapes those tags as text — enable
 * rehype-raw so stream + hover previews match the editor.
 */
function MessageBody({
  body,
  className,
}: {
  body: string
  className?: string
}) {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw]}
        components={{
          // Preserve inline color styles from Tiptap
          span: ({ node: _n, ...props }) => <span {...props} />,
        }}
      >
        {body}
      </ReactMarkdown>
    </div>
  )
}

function MessagePreview({ body }: { body: string }) {
  return (
    <MessageBody
      body={body}
      className="text-foreground/90 leading-relaxed line-clamp-3 break-words text-[11px] prose prose-xs dark:prose-invert max-w-none [&_p]:my-0 [&_ul]:my-0 [&_ol]:my-0 [&_li]:my-0 [&_h1]:text-xs [&_h2]:text-xs [&_h3]:text-xs [&_h1]:my-0 [&_h2]:my-0 [&_h3]:my-0 [&_code]:text-[10px] [&_pre]:hidden [&_blockquote]:my-0 [&_blockquote]:text-xs"
    />
  )
}

export function MessageCard({
  msg,
  onView,
  onEdit,
  onDelete,
  /** When true, hover preview opens to the left (for right-side sidebars). */
  previewSide = "right",
  /** Open node preview for source node (folder stream / aggregate). */
  onSourceNodeClick,
  /** Force show from-node badge even when source_node_id empty (owner_type=node). */
  showFromNode,
}: {
  msg: Message
  onView: (msg: Message) => void
  onEdit: (msg: Message) => void
  onDelete: () => void
  previewSide?: "left" | "right"
  onSourceNodeClick?: (nodeId: string) => void
  showFromNode?: boolean
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
  const sourceNodeId =
    msg.source_node_id || (msg.owner_type === "node" ? msg.owner_id : null)
  const fromNode = showFromNode || !!sourceNodeId

  return (
    <div
      className={cn(
        "rounded-md p-2 text-xs group cursor-pointer hover:bg-accent/60 transition-colors relative",
        isSystem ? "bg-muted/30 border border-border/30" : "bg-background",
      )}
      onClick={() => onView(msg)}
    >
      {/* Hover preview card — shows full rendered markdown */}
      <div
        className={cn(
          "absolute bottom-0 z-[9999] hidden group-hover:block pointer-events-none",
          previewSide === "right"
            ? "left-[calc(100%+6px)]"
            : "right-[calc(100%+6px)]"
        )}
      >
        <div className="w-[320px] max-h-[360px] overflow-y-auto rounded-lg border border-border bg-popover shadow-2xl p-3">
          <MessageBody
            body={msg.body || ""}
            className="prose prose-xs dark:prose-invert max-w-none text-xs leading-relaxed break-words"
          />
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
        {fromNode && sourceNodeId && (
          <button
            type="button"
            className="text-[9px] text-blue-400/90 bg-blue-400/10 px-1 rounded hover:bg-blue-400/20 transition-colors"
            title="Preview source node"
            onClick={(e) => {
              e.stopPropagation()
              onSourceNodeClick?.(sourceNodeId)
            }}
          >
            from node
          </button>
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
