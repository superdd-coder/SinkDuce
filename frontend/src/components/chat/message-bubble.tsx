import { memo } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { SourcesCard } from "./sources-card"
import { ThinkingSteps } from "./thinking-steps"
import type { Message, Source } from "@/stores/app-store"

interface MessageBubbleProps {
  message: Message
  onSelectSource?: (source: Source) => void
  selectedSourceId?: string | null
}

export const MessageBubble = memo(function MessageBubble({ message, onSelectSource, selectedSourceId }: MessageBubbleProps) {
  const isUser = message.role === "user"

  if (isUser) {
    return (
      <div className="flex flex-col items-end mb-8">
        <div
          className="text-[9px] font-semibold uppercase tracking-[0.2em] mb-1.5 text-primary"
        >
          You
        </div>
        <div
          className="max-w-[60%] text-sm leading-[1.7] pb-3 border-b text-right text-foreground border-border"
          style={{ fontFamily: "var(--font-serif)" }}
        >
          <p>{message.content}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="mb-8 pl-5 border-l max-w-[72%] border-border">
      <div
        className="text-[9px] font-semibold uppercase tracking-[0.25em] mb-2.5 text-muted-foreground"
      >
        Assistant
      </div>

      {/* Thinking steps */}
      {(message.thinkingSteps?.length || message.metaInfo) && (
        <ThinkingSteps
          steps={message.thinkingSteps || []}
          metaInfo={message.metaInfo}
          isStreaming={!!message.isStreaming}
        />
      )}

      {/* Answer content */}
      {!message.content && message.thinkingSteps?.length ? null : (
        <div
          className="text-sm leading-[1.8] text-foreground"
          style={{ fontFamily: "var(--font-serif)" }}
        >
          <div className="prose prose-sm dark:prose-invert max-w-none prose-p:my-1 prose-pre:my-2 prose-ul:my-1 prose-ol:my-1 prose-li:my-0">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content || (message.isStreaming ? "..." : "")}
            </ReactMarkdown>
          </div>
        </div>
      )}

      {/* Sources */}
      {message.sources && message.sources.length > 0 && (
        <SourcesCard
          sources={message.sources}
          onSelectSource={onSelectSource}
          selectedSourceId={selectedSourceId}
        />
      )}
    </div>
  )
})
