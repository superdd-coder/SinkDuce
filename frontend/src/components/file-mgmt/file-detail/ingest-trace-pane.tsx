import { useEffect, useMemo, useState } from "react"
import { Loader2, RefreshCw } from "lucide-react"
import {
  getIngestTrace,
  type IngestTrace,
  type IngestTraceStep,
} from "@/api/file-mgmt"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import {
  formatClock,
  layoutIngestTimeline,
  tickMarks,
} from "./ingest-trace-layout"

function formatDuration(ms: number) {
  if (!Number.isFinite(ms) || ms < 0) return ""
  if (ms < 1000) return `${Math.round(ms)} ms`
  const sec = ms / 1000
  if (sec < 60) return `${sec < 10 ? sec.toFixed(1) : Math.round(sec)}s`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}m ${s}s`
}

function statusClass(status: string) {
  if (status === "ok") return "text-[var(--pm-green)]"
  if (status === "skip") return "text-[var(--pm-faint)]"
  if (status === "running") return "text-[var(--pm-muted)]"
  if (status === "error" || status === "failed") return "text-red-600"
  return "text-[var(--pm-muted)]"
}

function StepBody({ step }: { step: IngestTraceStep }) {
  const data = step.data || {}
  if (step.id === "summary") {
    const short = String(data.short_summary || "")
    const structured = String(data.structured_summary || "")
    if (!short && !structured) return null
    return (
      <div className="mt-2 space-y-2 text-[13px] leading-relaxed text-[var(--pm-ink)]">
        {short ? (
          <p>
            <span className="pm-meta">short_summary · </span>
            {short}
          </p>
        ) : null}
        {structured ? (
          <pre className="pm-meta whitespace-pre-wrap font-mono text-[12px] max-h-56 overflow-auto">
            {structured}
          </pre>
        ) : null}
      </div>
    )
  }
  if (step.id === "context") {
    const contexts = Array.isArray(data.contexts) ? data.contexts : []
    if (!contexts.length) return null
    return (
      <div className="mt-2 space-y-3">
        {contexts.map((row) => {
          const rec = row as {
            index?: number
            context?: string
            chunk_preview?: string
          }
          return (
            <div
              key={rec.index}
              className="border-t border-[color-mix(in_srgb,var(--pm-ink)_8%,transparent)] pt-2"
            >
              <div className="pm-meta mb-1">chunk {rec.index}</div>
              {rec.context ? (
                <p className="text-[13px] leading-relaxed text-[var(--pm-ink)]">
                  {rec.context}
                </p>
              ) : (
                <p className="pm-meta">no context</p>
              )}
              {rec.chunk_preview ? (
                <p className="pm-meta mt-1 line-clamp-2">{rec.chunk_preview}</p>
              ) : null}
            </div>
          )
        })}
      </div>
    )
  }
  if (step.id === "vision") {
    const described = Array.isArray(data.described) ? data.described : []
    if (!described.length) return null
    return (
      <div className="mt-2 space-y-3">
        {described.map((row) => {
          const rec = row as {
            image_id?: string
            description?: string
            ocr_text?: string
            is_table_source?: boolean
          }
          return (
            <div key={rec.image_id} className="text-[13px] leading-relaxed">
              <div className="pm-meta mb-0.5 font-mono">
                {rec.image_id?.slice(0, 12)}
                {rec.is_table_source ? " · table-source" : ""}
              </div>
              {rec.ocr_text ? (
                <p className="text-[var(--pm-ink)]">
                  <span className="pm-meta">OCR · </span>
                  {rec.ocr_text}
                </p>
              ) : null}
              {rec.description ? (
                <p className="text-[var(--pm-ink)]">
                  <span className="pm-meta">Description · </span>
                  {rec.description}
                </p>
              ) : null}
            </div>
          )
        })}
      </div>
    )
  }
  return null
}

export function IngestTracePane({
  collectionId,
  fileId,
  versionId,
}: {
  collectionId: string
  fileId: string
  versionId?: string | null
}) {
  const [trace, setTrace] = useState<IngestTrace | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    getIngestTrace(collectionId, fileId, versionId)
      .then((data) => {
        setTrace(data)
        const prefer = (data.steps || []).find(
          (s) => s.id === "summary" || s.id === "context" || s.id === "vision",
        )
        setSelectedId((prev) => prev || prefer?.id || null)
      })
      .catch((err) => {
        setTrace(null)
        setError(err instanceof Error ? err.message : "No ingest trace")
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collectionId, fileId, versionId])

  const layout = useMemo(() => (trace ? layoutIngestTimeline(trace) : null), [trace])
  const selected =
    layout?.items.find((item) => item.step.id === selectedId)?.step ||
    trace?.steps?.find((s) => s.id === selectedId) ||
    null

  if (loading) {
    return (
      <div className="pm-ws-loading py-12">
        <Loader2 className="h-5 w-5 animate-spin" />
        Loading ingest steps…
      </div>
    )
  }

  if (error || !trace || !layout) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 p-8 text-center">
        <p className="pm-meta">No ingest trace for this version.</p>
        <p className="pm-meta max-w-sm leading-relaxed">
          Re-ingest the file after this build to record parse → vision →
          summary → context → store on this tab. Older uploads have no sidecar.
        </p>
        <Button variant="outline" size="sm" onClick={load} className="mt-2">
          <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
          Retry
        </Button>
      </div>
    )
  }

  const cfg = trace.config || {}
  const { items, laneCount, origin, span } = layout
  const height = Math.min(880, Math.max(280, span / 90))
  const pxPerMs = height / span
  const ticks = tickMarks(origin, span)
  const laneW = `calc((100% - ${(laneCount - 1) * 8}px) / ${laneCount})`

  return (
    <ScrollArea className="h-full">
      <div className="p-4 space-y-4">
        <header className="space-y-1">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-medium">Ingest run</h3>
            <button
              type="button"
              className="pm-ws-link inline-flex items-center gap-1"
              onClick={load}
            >
              <RefreshCw className="h-3 w-3" />
              Refresh
            </button>
          </div>
          <p className={cn("pm-meta", statusClass(trace.status))}>
            {trace.status}
            {typeof trace.duration_ms === "number"
              ? ` · ${formatDuration(trace.duration_ms)} total`
              : ""}
          </p>
          <p className="pm-meta tabular-nums">
            {trace.started_at ? formatClock(trace.started_at) : ""}
            {trace.finished_at ? ` → ${formatClock(trace.finished_at)}` : ""}
          </p>
          {trace.error ? (
            <p className="text-[13px] text-red-600">{trace.error}</p>
          ) : null}
          <p className="pm-meta leading-relaxed">
            Contextual {cfg.contextual_enabled === false ? "off" : "on"}
            {" · "}
            model {String(cfg.enrich_model || "default")}
            {cfg.is_visual ? " (visual)" : ""}
            {cfg.tabular ? " · tabular (no Context LLM)" : ""}
            {cfg.vision_model
              ? ` · vision ${String(cfg.vision_model)}`
              : " · no Visual model"}
          </p>
          <p className="pm-meta">
            Time flows down. Overlapping steps sit side by side.
          </p>
        </header>

        <div className="pm-ingest-tl" style={{ minHeight: height + 8 }}>
          <div className="pm-ingest-tl-axis" aria-hidden>
            {ticks.map((t) => (
              <div
                key={t}
                className="pm-ingest-tl-tick"
                style={{ top: (t - origin) * pxPerMs }}
              >
                {t === origin ? "0s" : formatDuration(t - origin)}
              </div>
            ))}
          </div>
          <div className="pm-ingest-tl-canvas" style={{ height }}>
            {items.map((item) => {
              const dur = Math.max(0, item.end - item.start)
              const top = (item.start - origin) * pxPerMs
              const spanH = Math.max(3, dur * pxPerMs)
              const compact = spanH < 36
              const left = `calc(${item.lane} * (${laneW} + 8px))`
              const open = selectedId === item.step.id
              const clock =
                !compact && item.step.started_at
                  ? `${formatClock(item.step.started_at)}${
                      item.step.ended_at ? `–${formatClock(item.step.ended_at)}` : ""
                    }`
                  : ""
              return (
                <button
                  key={`${item.step.id}-${item.start}`}
                  type="button"
                  title={`${item.step.title} · ${dur > 0 ? formatDuration(dur) : item.step.status}`}
                  className={cn(
                    "pm-ingest-tl-item",
                    compact && "is-compact",
                    `is-${item.step.status || "ok"}`,
                    open && "is-open",
                  )}
                  style={{
                    top,
                    left,
                    width: laneW,
                    height: compact ? undefined : spanH,
                    minHeight: compact ? 32 : undefined,
                  }}
                  onClick={() =>
                    setSelectedId((prev) =>
                      prev === item.step.id ? null : item.step.id,
                    )
                  }
                >
                  <span
                    className="pm-ingest-tl-span"
                    style={compact ? undefined : { height: spanH }}
                  />
                  <span className="pm-ingest-tl-copy">
                    <span className="pm-ingest-tl-bar-title">{item.step.title}</span>
                    <span className="pm-ingest-tl-bar-meta">
                      {dur > 0 ? formatDuration(dur) : item.step.status}
                      {clock ? ` · ${clock}` : ""}
                    </span>
                  </span>
                </button>
              )
            })}
          </div>
        </div>

        {selected ? (
          <div className="pm-ws-tile !p-3">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[13px] font-medium">{selected.title}</span>
              <span className={cn("pm-meta uppercase", statusClass(selected.status))}>
                {selected.status}
              </span>
              {typeof selected.ms === "number" ? (
                <span className="pm-meta tabular-nums">{formatDuration(selected.ms)}</span>
              ) : null}
            </div>
            {(selected.started_at || selected.ended_at) && (
              <p className="pm-meta tabular-nums mt-0.5">
                {formatClock(selected.started_at || selected.at)}
                {selected.ended_at ? ` → ${formatClock(selected.ended_at)}` : ""}
              </p>
            )}
            {selected.detail ? (
              <p className="pm-meta mt-1 leading-relaxed">{selected.detail}</p>
            ) : null}
            <StepBody step={selected} />
          </div>
        ) : null}
      </div>
    </ScrollArea>
  )
}
