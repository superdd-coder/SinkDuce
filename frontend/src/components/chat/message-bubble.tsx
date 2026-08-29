import { memo, useEffect, useState } from "react"
import { ChevronRight, Globe, Loader2, Wrench } from "lucide-react"
import { SourcesCard } from "./sources-card"
import { ThinkingSteps } from "./thinking-steps"
import { StreamingAnswerBody } from "./streaming-answer-body"
import type { Message, Source, TimelineBlock, ThinkingSummary } from "@/stores/app-store"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"
import { isToolBlockRunning, isWaitingForNextStep } from "@/lib/chat-next-step"

function ThinkingContent({ text, isStreaming }: { text: string; isStreaming: boolean }) {
  const t = useT()
  const [expanded, setExpanded] = useState(true)
  useEffect(() => { if (!isStreaming) setExpanded(false) }, [isStreaming])
  if (!text) return null
  return (
    <div className="pm-chat-trail">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="pm-chat-trail-toggle"
        aria-expanded={expanded}
      >
        <span
          className={cn("pm-chat-trail-chev", expanded && "is-open")}
          aria-hidden
        >
          <ChevronRight className="size-3 opacity-40" />
        </span>
        <span className={cn("sk-diamond", isStreaming ? "breathing" : "on sk-diamond-static")} aria-hidden />
        {t("chat.thinking")}{isStreaming ? "…" : ""}
      </button>
      <div className={cn("pm-chat-trail-body", expanded && "is-open")}>
        <div>
          <p className="pm-chat-reasoning-text">{text}</p>
        </div>
      </div>
    </div>
  )
}

interface MessageBubbleProps {
  message: Message
  onSelectSource?: (source: Source) => void
  selectedSourceId?: string | null
}

/** Simple tool row: name + collapsible result body (default collapsed) */
function ToolCallBlock({
  block,
}: {
  block: TimelineBlock
  messageStreaming?: boolean
}) {
  const t = useT()
  const result = (block.toolResult || "").trim()
  const hasResult = result.length > 0
  const [open, setOpen] = useState(false)

  const statusRaw = block.toolStatus
  const isError = statusRaw === "error"
  const isDeclined = statusRaw === "declined"
  const running =
    !hasResult &&
    !isError &&
    !isDeclined &&
    (statusRaw === "running" ||
      statusRaw === "awaiting_confirm" ||
      block.isStreaming === true)
  const name = block.tool || "tool"

  return (
    <div className="pm-chat-tool-row">
      <button
        type="button"
        onClick={() => hasResult && setOpen((v) => !v)}
        className={cn(
          "pm-chat-trail-toggle w-full text-left min-w-0 normal-case tracking-normal",
          hasResult ? "cursor-pointer" : "cursor-default",
        )}
        aria-expanded={hasResult ? open : undefined}
      >
        {hasResult ? (
          <span
            className={cn("pm-chat-trail-chev shrink-0", open && "is-open")}
            aria-hidden
          >
            <ChevronRight className="size-3 opacity-40" />
          </span>
        ) : (
          <span className="w-3 shrink-0" />
        )}
        <span
          className={cn(
            "sk-diamond",
            running && "breathing",
            hasResult && !isError && !isDeclined && "on sk-diamond-static",
          )}
          aria-hidden
        />
        <Wrench
          className={cn(
            "size-3 shrink-0",
            running
              ? "text-[var(--pm-green)] sk-tool-icon-breathe"
              : isError || isDeclined
                ? "text-[var(--pm-faint)]"
                : "text-[var(--pm-muted)]",
          )}
          aria-hidden
        />
        <span className="pm-meta font-mono truncate normal-case tracking-normal">{name}</span>
        {running && (
          <span className="pm-meta italic shrink-0 normal-case tracking-normal">
            {statusRaw === "awaiting_confirm" ? t("chat.waiting") : t("chat.running")}
          </span>
        )}
        {isDeclined && (
          <span className="pm-meta italic shrink-0 normal-case tracking-normal">{t("chat.declined")}</span>
        )}
        {isError && (
          <span className="pm-meta italic shrink-0 normal-case tracking-normal">{t("chat.error")}</span>
        )}
      </button>
      <div className={cn("pm-chat-trail-body", open && hasResult && "is-open")}>
        <div>
          {hasResult && (
            <p className="pm-chat-reasoning-text max-h-48 overflow-auto">
              {result}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function isRetrievalTool(block: TimelineBlock): boolean {
  const name = block.tool || ""
  if (name === "search_knowledge_base") return true
  if (!name && (block.summary?.tasks?.length ?? 0) > 0) return true
  if (!name && block.summary && (block.summary.aq_count ?? 0) > 0) return true
  return false
}

function TimelineBlockView({
  block,
  metaInfo,
  messageStreaming,
}: {
  block: TimelineBlock
  metaInfo?: Message["metaInfo"]
  messageStreaming: boolean
}) {
  if (block.type === "thinking") {
    return (
      <ThinkingContent
        text={block.content || ""}
        isStreaming={!!block.isStreaming && messageStreaming}
      />
    )
  }
  if (block.type === "tool") {
    if (isRetrievalTool(block)) {
      const running = messageStreaming && isToolBlockRunning(block)
      return (
        <ThinkingSteps
          steps={[]}
          summary={block.summary}
          metaInfo={metaInfo}
          isStreaming={!!running}
        />
      )
    }
    return <ToolCallBlock block={block} />
  }
  return null
}

/** Outer shell: collapse entire process trail once the answer finishes. */
function ProcessTrail({
  isStreaming,
  answerStarted,
  toolCount,
  thinkingCount,
  children,
}: {
  isStreaming: boolean
  answerStarted: boolean
  toolCount: number
  thinkingCount: number
  children: React.ReactNode
}) {
  const t = useT()
  const [open, setOpen] = useState(true)
  useEffect(() => {
    const next = isStreaming && !answerStarted
    setOpen((prev) => (prev === next ? prev : next))
  }, [isStreaming, answerStarted])

  const parts: string[] = []
  if (toolCount > 0) {
    parts.push(
      toolCount === 1
        ? t("chat.nTool", { n: toolCount })
        : t("chat.nTools", { n: toolCount }),
    )
  }
  if (thinkingCount > 0) parts.push(t("chat.reasoning"))
  const summary = parts.length > 0 ? parts.join(" · ") : t("chat.steps")

  return (
    <div className="pm-chat-trail">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="pm-chat-trail-toggle"
        aria-expanded={open}
      >
        <span
          className={cn("pm-chat-trail-chev shrink-0", open && "is-open")}
          aria-hidden
        >
          <ChevronRight className="size-3 opacity-40" />
        </span>
        <span
          className={cn("sk-diamond", isStreaming ? "breathing" : "on sk-diamond-static")}
          aria-hidden
        />
        <span>
          {isStreaming ? t("chat.working") : t("chat.steps")}
          {isStreaming ? "…" : ""}
          <span className="pm-chat-trail-sum">· {summary}</span>
        </span>
      </button>
      <div className={cn("pm-chat-trail-body", open && "is-open")}>
        <div className="pt-0.5">{children}</div>
      </div>
    </div>
  )
}

/**
 * Process trail isolated from answer tokens. appendToLastMessage keeps the same
 * `timeline` array reference, so this memo skips re-render while only content grows.
 */
const AssistantProcessTrail = memo(function AssistantProcessTrail({
  timeline,
  thinkingContent,
  hasToolCall,
  thinkingSummary,
  thinkingSteps,
  metaInfo,
  isStreaming,
  answerStarted,
}: {
  timeline?: TimelineBlock[]
  thinkingContent?: string
  hasToolCall?: boolean
  thinkingSummary?: ThinkingSummary
  thinkingSteps?: Message["thinkingSteps"]
  metaInfo?: Message["metaInfo"]
  isStreaming: boolean
  answerStarted: boolean
}) {
  const t = useT()
  const hasTimeline = !!(timeline && timeline.length > 0)
  const hasLegacyTrail =
    !hasTimeline &&
    !!(
      thinkingContent ||
      hasToolCall ||
      thinkingSummary ||
      (thinkingSteps?.length ?? 0) > 0
    )
  if (!hasTimeline && !hasLegacyTrail) return null

  const toolCount = hasTimeline
    ? timeline!.filter((b) => b.type === "tool").length
    : hasToolCall || thinkingSummary
      ? 1
      : 0
  const thinkingCount = hasTimeline
    ? timeline!.filter((b) => b.type === "thinking" && (b.content || "").trim()).length
    : thinkingContent
      ? 1
      : 0

  return (
    <ProcessTrail
      isStreaming={isStreaming}
      answerStarted={answerStarted}
      toolCount={toolCount}
      thinkingCount={thinkingCount}
    >
      {hasTimeline ? (
        <>
          {timeline!.map((block, i) => (
            <TimelineBlockView
              key={i}
              block={block}
              metaInfo={metaInfo}
              messageStreaming={isStreaming}
            />
          ))}
          {isWaitingForNextStep(timeline, isStreaming, answerStarted) && (
            <div className="flex items-center gap-2 py-1 pm-meta italic">
              <Loader2 className="size-3 animate-spin text-[var(--pm-faint)]" />
              {t("chat.nextStep")}
            </div>
          )}
        </>
      ) : (
        <>
          {thinkingContent && (
            <ThinkingContent text={thinkingContent} isStreaming={isStreaming} />
          )}
          {(hasToolCall || thinkingSummary || thinkingSteps?.length) && (
            <ThinkingSteps
              steps={thinkingSteps || []}
              summary={thinkingSummary}
              metaInfo={metaInfo}
              isStreaming={isStreaming}
            />
          )}
        </>
      )}
    </ProcessTrail>
  )
})

export const MessageBubble = memo(function MessageBubble({ message, onSelectSource, selectedSourceId }: MessageBubbleProps) {
  const t = useT()
  const isUser = message.role === "user"
  const streaming = !!message.isStreaming

  if (isUser) {
    return (
      <div className="pm-chat-msg pm-chat-msg--user">
        <span className="pm-chat-msg-role">{t("chat.you")}</span>
        <div className="pm-chat-msg-user-body">
          <p>{message.content}</p>
        </div>
      </div>
    )
  }

  return (
    <div
      className={cn(
        "pm-chat-msg pm-chat-msg--assistant",
        streaming && "is-streaming",
      )}
    >
      <span className="pm-chat-msg-role pm-chat-msg-role--ai">{t("chat.assistant")}</span>

      <AssistantProcessTrail
        timeline={message.timeline}
        thinkingContent={message.thinkingContent}
        hasToolCall={message.hasToolCall}
        thinkingSummary={message.thinkingSummary}
        thinkingSteps={message.thinkingSteps}
        metaInfo={message.metaInfo}
        isStreaming={streaming}
        answerStarted={!!(message.content && message.content.length > 0)}
      />

      {streaming && !message.content ? (
        <div className="pm-chat-typing">
          <span className="sk-diamond breathing" aria-hidden />
          <span>{t("chat.educing")}</span>
        </div>
      ) : message.content ? (
        <div className="pm-chat-msg-answer">
          <StreamingAnswerBody
            content={message.content}
            isStreaming={streaming}
            className="pm-chat-prose"
          />
        </div>
      ) : null}

      {!streaming &&
        Array.isArray(message.sources) &&
        message.sources.some(
          (s) =>
            s?.metadata?.source_type === "web" ||
            s?.metadata?.provider === "tavily" ||
            s?.metadata?.provider === "web",
        ) && (
        <div className="pm-chat-web-banner" role="note">
          <Globe className="size-3.5 pm-chat-web-banner-icon" strokeWidth={1.75} aria-hidden />
          <p className="m-0 min-w-0">
            <strong>{t("chat.internetSources")}</strong>
            {" — "}
            {t("chat.internetSourcesHint")}
          </p>
        </div>
      )}

      {!streaming && message.sources && message.sources.length > 0 && (
        <SourcesCard
          sources={message.sources}
          onSelectSource={onSelectSource}
          selectedSourceId={selectedSourceId}
        />
      )}
    </div>
  )
})
