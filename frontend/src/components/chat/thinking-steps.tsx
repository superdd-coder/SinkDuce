import { useState } from "react"
import {
  ChevronRight,
  ChevronDown,
  Check,
  Loader2,
  Sparkles,
  Layers,
  RotateCcw,
} from "lucide-react"
import type { ThinkingIteration, ThinkingSummary, TaskSummary, AqSummary, MetaInfo } from "@/stores/app-store"

interface ThinkingStepsProps {
  steps: ThinkingIteration[]
  summary?: ThinkingSummary
  metaInfo?: MetaInfo
  isStreaming: boolean
}

// ── Icons ──

function AqIcon({ aq }: { aq: AqSummary }) {
  if (aq.sufficient) return <Check className="h-3 w-3 shrink-0 text-emerald-500" />
  return <span className="text-[10px] shrink-0 text-amber-500">⚠</span>
}

// ── AQ row ──

function AqRow({ aq }: { aq: AqSummary }) {
  const [expanded, setExpanded] = useState(false)
  const hasRewrites = aq.rewritten.length > 0

  return (
    <div className="ml-5 text-[11px] leading-relaxed">
      <div
        className="flex items-start gap-1.5 py-0.5 cursor-pointer"
        onClick={() => hasRewrites && setExpanded(!expanded)}
      >
        {hasRewrites ? (
          expanded ? <ChevronDown className="h-2.5 w-2.5 mt-0.5 shrink-0 text-muted-foreground" /> :
          <ChevronRight className="h-2.5 w-2.5 mt-0.5 shrink-0 text-muted-foreground" />
        ) : (
          <span className="w-2.5 shrink-0" />
        )}
        <AqIcon aq={aq} />
        <span className="text-muted-foreground truncate">{aq.query}</span>
        <span className="text-muted-foreground/50 shrink-0">
          {aq.final_chunks > 0 ? (
            <>→ {aq.final_chunks} chunks</>
          ) : aq.current_chunks > 0 ? (
            <span className="text-muted-foreground/40">
              <Loader2 className="h-2.5 w-2.5 inline animate-spin mr-0.5" />
              {aq.current_chunks} chunks so far
            </span>
          ) : (
            <span className="text-muted-foreground/30 italic">searching…</span>
          )}
          {aq.iterations > 1 && ` (${aq.iterations} iters)`}
        </span>
      </div>

      {/* Rewrite details */}
      {expanded && hasRewrites && (
        <div className="ml-7 mb-1 space-y-0.5">
          {aq.rewritten.map((r, i) => (
            <div key={i} className="text-[10px] text-muted-foreground/50 flex items-center gap-1">
              <RotateCcw className="h-2.5 w-2.5 shrink-0" />
              <span>rewrote to: {r}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Task group ──

function TaskGroup({ task }: { task: TaskSummary }) {
  const [expanded, setExpanded] = useState(true)

  return (
    <div>
      <div
        className="flex items-center gap-1.5 py-1 cursor-pointer text-[12px]"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3 w-3 text-muted-foreground" />
        )}
        <Layers className="h-3 w-3 text-muted-foreground/50" />
        <span className="font-[400]">{task.task || "Task"}</span>
        <span className="text-muted-foreground/60">
          — {task.aq_count} AQ{task.aq_count > 1 ? "s" : ""}, {task.useful_chunks} useful chunks
        </span>
      </div>

      {expanded && (
        <div>
          {task.task_query && (
            <div className="ml-7 text-[10px] text-muted-foreground/40 mb-0.5 italic">
              {task.task_query}
            </div>
          )}
          {task.aqs.map((aq) => (
            <AqRow key={aq.aq_id} aq={aq} />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Main component ──

export function ThinkingSteps({ steps, summary, metaInfo, isStreaming }: ThinkingStepsProps) {
  const [topExpanded, setTopExpanded] = useState(true)

  // Waiting for first events — show spinner
  if (isStreaming && (!summary || summary.tasks.length === 0)) {
    return (
      <div className="mt-5 pt-3.5 border-t border-dashed border-border">
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground/60 italic">
          <Loader2 className="h-3 w-3 animate-spin text-primary" />
          <Sparkles className="h-3 w-3 text-amber-500/60" />
          Agentic RAG — searching…
        </div>
      </div>
    )
  }

  // Prefer clean summary over verbose step tree
  if (summary && summary.tasks.length > 0) {
    return (
      <div className="mt-5 pt-3.5 border-t border-dashed border-border">
        {/* Meta info */}
        {metaInfo && (metaInfo.provider || metaInfo.model) && (
          <div className="flex items-center gap-2 text-[10px] mb-2 flex-wrap text-muted-foreground">
            {metaInfo.provider && metaInfo.model && (
              <span>{metaInfo.provider} / {metaInfo.model}</span>
            )}
          </div>
        )}

        {/* Toggle */}
        <button
          type="button"
          onClick={() => setTopExpanded(!topExpanded)}
          className="flex items-center gap-1.5 mb-2 cursor-pointer"
        >
          {isStreaming ? (
            <span className="text-[10px] font-normal text-muted-foreground/50 w-3 text-center">
              {topExpanded ? "▼" : "▶"}
            </span>
          ) : topExpanded ? (
            <ChevronDown className="h-3 w-3 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3 w-3 text-muted-foreground" />
          )}
          <span className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80">
            <Sparkles className="h-3 w-3 inline mr-1 text-amber-500/60" />
            Agentic RAG · {summary.task_count} task{summary.task_count > 1 ? "s" : ""}, {summary.aq_count} AQ{summary.aq_count > 1 ? "s" : ""}
          </span>
        </button>

        {/* Live status */}
        {summary.status && isStreaming && (
          <div className="ml-1 text-[10px] text-muted-foreground/50 italic mb-1 truncate">
            {summary.status}
          </div>
        )}

        {/* Tasks */}
        {topExpanded && (
          <div className="space-y-1">
            {summary.tasks.map((task, i) => (
              <TaskGroup key={i} task={task} />
            ))}

            {/* Generating indicator */}
            {isStreaming && (
              <div className="flex items-center gap-2 ml-1 py-1 text-[11px] text-muted-foreground/60 italic">
                <Loader2 className="h-3 w-3 animate-spin text-primary" />
                Generating answer…
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  // Fallback: old verbose step tree (for messages without summary)
  if (steps.length === 0) return null

  const totalSteps = steps.reduce((acc, g) => acc + g.steps.length, 0)

  return (
    <div className="mt-5 pt-3.5 border-t border-dashed border-border">
      <button
        type="button"
        onClick={() => setTopExpanded(!topExpanded)}
        className="flex items-center gap-1.5 mb-2 cursor-pointer"
      >
        {topExpanded ? (
          <ChevronDown className="h-3 w-3 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3 w-3 text-muted-foreground" />
        )}
        <span className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80">
          Steps · {totalSteps}
        </span>
      </button>
    </div>
  )
}
