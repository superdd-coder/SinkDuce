import { useEffect, useState } from "react"
import {
  ChevronRight,
  Loader2,
  Sparkles,
  Layers,
} from "lucide-react"
import type { ThinkingIteration, ThinkingSummary, TaskSummary, AqSummary, MetaInfo } from "@/stores/app-store"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"

interface ThinkingStepsProps {
  steps: ThinkingIteration[]
  summary?: ThinkingSummary
  metaInfo?: MetaInfo
  isStreaming: boolean
}

// ── Icons ──

function AqIcon({ aq, isStreaming }: { aq: AqSummary; isStreaming: boolean }) {
  const isSearching =
    isStreaming && (aq.final_chunks ?? 0) === 0 && aq.has_gaps !== false
  if (aq.has_gaps === false) {
    return <span className="sk-diamond on sk-diamond-static shrink-0" aria-hidden />
  }
  if (isSearching) {
    return <span className="sk-diamond breathing shrink-0" aria-hidden />
  }
  return <span className="sk-diamond shrink-0" aria-hidden />
}

/** Symmetric height fold — always mounted, open via .is-open (ENGINEERING §4). */
function TrailFold({
  open,
  children,
  className,
}: {
  open: boolean
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn("pm-chat-trail-body", open && "is-open", className)}>
      <div>{children}</div>
    </div>
  )
}

// ── AQ row ──

function AqRow({ aq, isStreaming }: { aq: AqSummary; isStreaming: boolean }) {
  const t = useT()
  const [expanded, setExpanded] = useState(false)
  const variants = aq.variants ?? []
  const totalQueries = 1 + (aq.variant_count ?? 0)
  const canExpand = variants.length > 0

  return (
    <div className="ml-5 pm-meta leading-relaxed">
      <button
        type="button"
        className={cn(
          "flex items-start gap-1.5 py-0.5 w-full text-left border-none bg-transparent p-0",
          canExpand ? "cursor-pointer" : "cursor-default",
        )}
        onClick={() => canExpand && setExpanded((v) => !v)}
      >
        {canExpand ? (
          <span
            className={cn(
              "pm-chat-trail-chev shrink-0 mt-0.5",
              expanded && "is-open",
            )}
            aria-hidden
          >
            <ChevronRight className="size-2.5 text-[var(--pm-faint)]" />
          </span>
        ) : (
          <span className="w-2.5 shrink-0" />
        )}
        <span className="inline-flex items-center mt-1.5">
          <AqIcon aq={aq} isStreaming={isStreaming} />
        </span>
        <span className="text-[var(--pm-muted)] truncate">{aq.query}</span>
        <span className="text-[var(--pm-faint)] shrink-0">
          {(aq.final_chunks ?? 0) > 0 ? (
            <>→ {t("chat.nChunks", { n: aq.final_chunks ?? 0 })}</>
          ) : (aq.current_chunks ?? 0) > 0 ? (
            <span>
              <Loader2 className="size-2.5 inline animate-spin mr-0.5" />
              {t("chat.chunksSoFar", { n: aq.current_chunks ?? 0 })}
            </span>
          ) : (
            <span className="italic">{t("chat.searching")}</span>
          )}
          {totalQueries > 1 && (
            <span className="ml-1">{t("chat.nQueries", { n: totalQueries })}</span>
          )}
        </span>
      </button>

      <TrailFold open={expanded && canExpand}>
        <div className="ml-7 mb-1 space-y-0.5 pt-0.5">
          <div className="pm-meta flex items-center gap-1 opacity-80">
            <Sparkles className="size-2.5 shrink-0" />
            <span>{t("chat.original")}</span>
          </div>
          {variants.map((v, i) => (
            <div key={i} className="pm-meta flex items-center gap-1 opacity-70">
              <Sparkles className="size-2.5 shrink-0 opacity-50" />
              <span>{t("chat.variantN", { n: i + 1, v })}</span>
            </div>
          ))}
        </div>
      </TrailFold>
    </div>
  )
}

// ── Task group ──

function TaskGroup({ task, isStreaming }: { task: TaskSummary; isStreaming: boolean }) {
  const t = useT()
  const [expanded, setExpanded] = useState(isStreaming)
  useEffect(() => {
    if (!isStreaming) setExpanded(false)
  }, [isStreaming])

  return (
    <div>
      <button
        type="button"
        className="flex items-center gap-1.5 py-1 cursor-pointer pm-meta w-full text-left border-none bg-transparent p-0"
        onClick={() => setExpanded((v) => !v)}
      >
        <span
          className={cn("pm-chat-trail-chev shrink-0", expanded && "is-open")}
          aria-hidden
        >
          <ChevronRight className="size-3 text-[var(--pm-faint)]" />
        </span>
        <Layers className="size-3 text-[var(--pm-faint)]" />
        <span className="text-[var(--pm-text)]">{task.task || t("chat.task")}</span>
        <span className="text-[var(--pm-faint)]">
          {t("chat.aqUseful", { aqCount: task.aq_count, chunks: task.useful_chunks })}
        </span>
      </button>

      <TrailFold open={expanded}>
        <div className="pt-0.5">
          {task.task_query && (
            <div className="ml-7 pm-meta mb-0.5 italic opacity-70">
              {task.task_query}
            </div>
          )}
          {(task.aqs ?? []).map((aq) => (
            <AqRow key={aq.aq_id} aq={aq} isStreaming={isStreaming} />
          ))}
        </div>
      </TrailFold>
    </div>
  )
}

// ── Main component ──

export function ThinkingSteps({ steps, summary, metaInfo, isStreaming }: ThinkingStepsProps) {
  const t = useT()
  const [topExpanded, setTopExpanded] = useState(true)
  useEffect(() => {
    if (!isStreaming) setTopExpanded(false)
  }, [isStreaming])

  // Waiting for first events
  if (isStreaming && (!summary || (summary.tasks?.length ?? 0) === 0)) {
    return (
      <div className="pm-chat-trail">
        <div className="pm-chat-trail-toggle" style={{ cursor: "default" }}>
          <span className="sk-diamond breathing" aria-hidden />
          <span>{t("chat.agenticRag")}</span>
          <span className="pm-chat-trail-sum">· {t("chat.searching")}</span>
        </div>
      </div>
    )
  }

  // Prefer clean summary over verbose step tree
  if (summary && (summary.tasks?.length ?? 0) > 0) {
    return (
      <div className="pm-chat-trail">
        {metaInfo && (metaInfo.provider || metaInfo.model) && (
          <div className="pm-meta mb-1.5">
            {metaInfo.provider && metaInfo.model && (
              <span>{metaInfo.provider} / {metaInfo.model}</span>
            )}
          </div>
        )}

        <button
          type="button"
          onClick={() => setTopExpanded((v) => !v)}
          className="pm-chat-trail-toggle"
          aria-expanded={topExpanded}
        >
          <span
            className={cn("pm-chat-trail-chev", topExpanded && "is-open")}
            aria-hidden
          >
            <ChevronRight className="size-3 opacity-40" />
          </span>
          <span
            className={`sk-diamond ${isStreaming ? "breathing" : "on sk-diamond-static"}`}
            aria-hidden
          />
          <span>
            {t("chat.agenticRag")}
            <span className="pm-chat-trail-sum">
              {t("chat.taskAqCount", { tasks: summary.task_count, aq: summary.aq_count })}
            </span>
          </span>
        </button>

        {summary.status && isStreaming && (
          <div className="pm-meta italic mt-1 ml-5 truncate">
            {summary.status}
          </div>
        )}

        {/* Always mounted — grid 0fr↔1fr + opacity (symmetric open/close) */}
        <TrailFold open={topExpanded}>
          <div className="mt-1.5 space-y-0.5 pl-0.5">
            {(summary.tasks ?? []).map((task, i) => (
              <TaskGroup key={i} task={task} isStreaming={isStreaming} />
            ))}

            {isStreaming && (
              <div className="flex items-center gap-2 py-1 pm-meta italic">
                <Loader2 className="size-3 animate-spin text-[var(--pm-faint)]" />
                {t("chat.generating")}
              </div>
            )}
          </div>
        </TrailFold>
      </div>
    )
  }

  // Tool was used but no detailed task breakdown
  if (summary && !isStreaming) {
    return (
      <div className="pm-chat-trail">
        <div className="pm-chat-trail-toggle" style={{ cursor: "default" }}>
          <span className="sk-diamond on sk-diamond-static" aria-hidden />
          <span>{t("chat.agenticRag")}</span>
          <span className="pm-chat-trail-sum">
            · {summary.aq_count > 0
              ? t("chat.aqSearched", { n: summary.aq_count })
              : t("chat.searchCompleted")}
          </span>
        </div>
      </div>
    )
  }

  // Fallback: old verbose step tree (for messages without summary)
  if (steps.length === 0) return null

  const totalSteps = steps.reduce((acc, g) => acc + g.steps.length, 0)

  return (
    <div className="pm-chat-trail">
      <button
        type="button"
        onClick={() => setTopExpanded((v) => !v)}
        className="pm-chat-trail-toggle"
        aria-expanded={topExpanded}
      >
        <span
          className={cn("pm-chat-trail-chev", topExpanded && "is-open")}
          aria-hidden
        >
          <ChevronRight className="size-3 opacity-40" />
        </span>
        <span className="sk-diamond on sk-diamond-static" aria-hidden />
        <span>
          {t("chat.steps")}
          <span className="pm-chat-trail-sum">· {totalSteps}</span>
        </span>
      </button>
    </div>
  )
}
