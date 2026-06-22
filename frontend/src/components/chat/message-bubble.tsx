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
      {message.isStreaming && !message.content ? (
        <div className="flex items-center gap-2.5 text-xs" style={{ color: "oklch(0.38 0.08 160 / 0.7)" }}>
          <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeDasharray="31.4 31.4" className="opacity-25" />
            <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" className="opacity-75" />
          </svg>
          <span className="font-light uppercase tracking-[0.12em]" style={{ fontFamily: "var(--font-serif)", fontStyle: "italic" }}>Educing…</span>
        </div>
      ) : message.content ? (
        <div
          className="text-sm leading-[1.8] text-foreground"
          style={{ fontFamily: "var(--font-serif)" }}
        >
          <div className="prose prose-sm dark:prose-invert max-w-none prose-p:my-1 prose-pre:my-2 prose-ul:my-1 prose-ol:my-1 prose-li:my-0">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content}
            </ReactMarkdown>
          </div>
        </div>
      ) : null}

      {/* Sources */}
      {!message.isStreaming && message.sources && message.sources.length > 0 && (
        <SourcesCard
          sources={message.sources}
          onSelectSource={onSelectSource}
          selectedSourceId={selectedSourceId}
        />
      )}
    </div>
  )
})
