import { memo, useEffect, useState } from "react"
import { Brain, ChevronDown, ChevronRight, Wrench } from "lucide-react"
import { SourcesCard } from "./sources-card"
import { ThinkingSteps } from "./thinking-steps"
import { StreamingAnswerBody } from "./streaming-answer-body"
import type { Message, Source, TimelineBlock, ThinkingSummary } from "@/stores/app-store"
import { cn } from "@/lib/utils"

function ThinkingContent({ text, isStreaming }: { text: string; isStreaming: boolean }) {
  const [expanded, setExpanded] = useState(true)
  useEffect(() => { if (!isStreaming) setExpanded(false) }, [isStreaming])
  if (!text) return null
  return (
    <div className="mt-4 mb-3">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 text-[10px] text-muted-foreground/50 uppercase tracking-[0.1em] cursor-pointer hover:text-muted-foreground/70 transition-colors"
      >
        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <Brain className="h-3 w-3 text-[var(--ze-green)]" />
        Reasoning{isStreaming ? "…" : ""}
      </button>
      {expanded && (
        <div
          className="mt-1.5 pl-5 text-[11px] leading-relaxed border-l border-[var(--ze-green)]/20 t-body-italic-family"
          style={{ color: "oklch(0.38 0.07 160 / 0.65)" }}
        >
          {text}
        </div>
      )}
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
  const result = (block.toolResult || "").trim()
  const hasResult = result.length > 0
  const [open, setOpen] = useState(false)

  // Hard rules for diamond state (ignore message-level streaming):
  // 1) has result text → done (solid, no animation)
  // 2) explicit error/declined → empty diamond
  // 3) only pure running / awaiting_confirm without result → breathing
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
    <div className="mt-3 pt-2.5 border-t border-dashed border-border">
      <button
        type="button"
        onClick={() => hasResult && setOpen(!open)}
        className={`flex items-center gap-1.5 w-full text-left min-w-0 ${hasResult ? "cursor-pointer" : "cursor-default"}`}
      >
        {hasResult ? (
          open ? (
            <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
          )
        ) : (
          <span className="w-3 shrink-0" />
        )}
        {/* Tool icon — breathe only while in progress */}
        <Wrench
          className={cn(
            "h-3 w-3 shrink-0",
            running
              ? "text-[var(--ze-green)] sk-tool-icon-breathe"
              : isError || isDeclined
                ? "text-muted-foreground/40"
                : "text-muted-foreground/70",
          )}
          aria-hidden
        />
        <span className="text-[11px] font-mono text-muted-foreground/80 truncate">{name}</span>
        {running && (
          <span className="text-[10px] text-muted-foreground/50 italic shrink-0">
            {statusRaw === "awaiting_confirm" ? "waiting…" : "running…"}
          </span>
        )}
        {isDeclined && (
          <span className="text-[10px] text-muted-foreground/50 italic shrink-0">declined</span>
        )}
        {isError && (
          <span className="text-[10px] text-muted-foreground/50 italic shrink-0">error</span>
        )}
      </button>
      {open && hasResult && (
        <div
          className="mt-1.5 ml-6 max-h-48 overflow-auto text-[10px] leading-relaxed text-muted-foreground/50 italic whitespace-pre-wrap break-words font-light t-body-italic-family"
        >
          {result}
        </div>
      )}
    </div>
  )
}

function isRetrievalTool(block: TimelineBlock): boolean {
  const name = block.tool || ""
  // Main agentic search — keep the classic Agentic RAG step tree
  if (name === "search_knowledge_base") return true
  // Legacy blocks without tool name but with agentic summary
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
    // Retrieval: original detailed Agentic RAG UI (unchanged)
    if (isRetrievalTool(block)) {
      const running =
        messageStreaming &&
        (block.isStreaming ||
          block.toolStatus === "running" ||
          block.toolStatus === "awaiting_confirm")
      return (
        <ThinkingSteps
          steps={[]}
          summary={block.summary}
          metaInfo={metaInfo}
          isStreaming={!!running}
        />
      )
    }
    // Structure / web / full-text: simple name + collapsible result
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
  /** True once final-answer tokens exist — fold trail so MD can paint. */
  answerStarted: boolean
  toolCount: number
  thinkingCount: number
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(true)
  useEffect(() => {
    // Expanded while tools/reasoning run; fold when final answer starts or stream ends
    // (keeps DOM light during the answer phase — main freeze hotspot).
    if (!isStreaming) {
      setOpen(false)
      return
    }
    if (answerStarted) {
      setOpen(false)
      return
    }
    setOpen(true)
  }, [isStreaming, answerStarted])

  // "reasoning" = LLM Think mode blocks; "tools" = function calls / search tree
  const parts: string[] = []
  if (toolCount > 0) parts.push(`${toolCount} tool${toolCount === 1 ? "" : "s"}`)
  if (thinkingCount > 0) parts.push("reasoning")
  const summary = parts.length > 0 ? parts.join(" · ") : "steps"

  return (
    <div className="mt-3 mb-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-[10px] text-muted-foreground/55 uppercase tracking-[0.1em] cursor-pointer hover:text-muted-foreground/75 transition-colors"
      >
        {open ? (
          <ChevronDown className="h-3 w-3 shrink-0" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0" />
        )}
        <Wrench className="h-3 w-3 shrink-0 text-[var(--ze-green)]/80" />
        <span>
          {isStreaming ? "Working" : "Steps"}
          {isStreaming ? "…" : ""}
          <span className="normal-case tracking-normal font-normal text-muted-foreground/45 ml-1.5">
            · {summary}
          </span>
        </span>
      </button>
      {open && <div className="mt-0.5">{children}</div>}
    </div>
  )
}

/**
 * Process trail isolated from answer tokens. appendToLastMessage keeps the same
 * `timeline` array reference, so this memo skips re-render while only content grows
 * — same isolation Meeting gets by only subscribing to streamingMd.
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
        timeline!.map((block, i) => (
          <TimelineBlockView
            key={i}
            block={block}
            metaInfo={metaInfo}
            messageStreaming={isStreaming}
          />
        ))
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
  const isUser = message.role === "user"
  const streaming = !!message.isStreaming

  if (isUser) {
    return (
      <div className="flex flex-col items-end mb-8">
        <div
          className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80 mb-1.5 text-primary"
        >
          You
        </div>
        <div
          className="max-w-[60%] text-sm leading-[1.7] pb-3 border-b text-right text-foreground border-border t-body-family"
        >
          <p>{message.content}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="mb-8 pl-5 border-l max-w-[72%] border-border">
      <div
        className="text-[11px] font-normal uppercase tracking-[0.12em] mb-2.5 text-muted-foreground/80"
      >
        Assistant
      </div>

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

      {/* Live answer: MD prefix (throttled) + plain tail (always current) */}
      {streaming && !message.content ? (
        <div className="flex items-center gap-2.5 text-xs" style={{ color: "oklch(0.38 0.08 160 / 0.7)" }}>
          <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeDasharray="31.4 31.4" className="opacity-25" />
            <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" className="opacity-75" />
          </svg>
          <span className="font-light uppercase tracking-[0.12em] t-body-italic-family">Educing…</span>
        </div>
      ) : message.content ? (
        <div className="text-sm leading-[1.8] text-foreground t-body-family">
          <StreamingAnswerBody content={message.content} isStreaming={streaming} />
        </div>
      ) : null}

      {!streaming &&
        Array.isArray(message.sources) &&
        message.sources.some((s) => s?.metadata?.source_type === "web") && (
        <div className="mt-4 rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-900 dark:text-amber-200">
          <span className="font-bold uppercase tracking-wider">Internet sources used</span>
          {" — "}
          Some evidence below is from the public web (Tavily), not your private knowledge base.
          Treat WEB badges as external data.
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
