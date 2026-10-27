import { HelpHint } from "@/components/shared/help-hint"
/**
 * Ingest history dialog — persistent upload records for a collection.
 *
 * House premium recipe (definitive-suggestions language): kicker → serif
 * display title → muted description; tabs filter 全部/进行中/已成功/已失败.
 * Each row: status chip, file name, failing stage + error for failures,
 * finish time, and Re-ingest on failed rows. Failed rows retry in place
 * (same record: queued again, attempt+1). Polls while open and stops on
 * idle — the backend store is the source of truth, so a refresh never
 * loses history.
 */

import { useCallback, useEffect, useState } from "react"
import {
  CheckCircle2,
  Clock,
  Loader2,
  RotateCcw,
  XCircle,
} from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsIndicator,
} from "@/components/ui/tabs"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  getIngestHistory,
  retryIngest,
  type IngestHistoryRecord,
} from "@/api/collections"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"

const POLL_MS = 3000
const IDLE_POLL_MS = 15000

type HistoryTab = "all" | "active" | "success" | "failed"

const STAGE_KEYS: Record<string, string> = {
  queued: "stageQueued",
  parsing: "stageParsing",
  images: "stageImages",
  embedding: "stageEmbedding",
  storing: "stageStoring",
  summary: "stageSummary",
  interrupted: "stageInterrupted",
}

function stageLabel(stage: string, t: (k: string) => string): string {
  return t(`library.${STAGE_KEYS[stage] ?? "stageUnknown"}`)
}

function StatusChip({ status }: { status: IngestHistoryRecord["status"] }) {
  if (status === "success") {
    return (
      <span className="pm-ingest-chip is-success" aria-hidden>
        <CheckCircle2 className="h-3.5 w-3.5" />
      </span>
    )
  }
  if (status === "failed") {
    return (
      <span className="pm-ingest-chip is-failed" aria-hidden>
        <XCircle className="h-3.5 w-3.5" />
      </span>
    )
  }
  if (status === "processing") {
    return (
      <span className="pm-ingest-chip is-active" aria-hidden>
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      </span>
    )
  }
  return (
    <span className="pm-ingest-chip is-queued" aria-hidden>
      <Clock className="h-3.5 w-3.5" />
    </span>
  )
}

export function IngestHistoryDialog({
  collection,
  open,
  onOpenChange,
  onCountsChange,
}: {
  collection: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onCountsChange?: (counts: { active: number; failed: number }) => void
}) {
  const t = useT()
  const [tab, setTab] = useState<HistoryTab>("all")
  const [records, setRecords] = useState<IngestHistoryRecord[]>([])
  const [counts, setCounts] = useState({
    queued: 0,
    processing: 0,
    success: 0,
    failed: 0,
  })
  const [busyId, setBusyId] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const data = await getIngestHistory(collection)
      setRecords(data.records)
      setCounts(data.counts)
      onCountsChange?.({
        active: data.counts.queued + data.counts.processing,
        failed: data.counts.failed,
      })
      return data.counts.queued + data.counts.processing > 0
    } catch {
      return false
    }
  }, [collection, onCountsChange])

  // Poll while open — faster while something is in flight. Unconditional
  // (no idle stop) on purpose: a stopped chain must never show stale rows.
  useEffect(() => {
    if (!open) return
    let alive = true
    let timer: number | null = null
    const tick = async () => {
      let hasActive = true
      try {
        hasActive = await refresh()
      } catch {
        // keep the in-flight cadence on transient errors
      }
      if (alive) {
        timer = window.setTimeout(tick, hasActive ? POLL_MS : IDLE_POLL_MS)
      }
    }
    void tick()
    return () => {
      alive = false
      if (timer) window.clearTimeout(timer)
    }
  }, [open, refresh])

  const handleRetry = async (record: IngestHistoryRecord) => {
    setBusyId(record.record_id)
    try {
      await retryIngest(record.record_id, collection)
      toast.info(t("library.ingestRetried", { name: record.filename }))
      await refresh()
    } catch (err: unknown) {
      toast.error(t("common.failedWithError", { error: formatApiError(err, t) }))
    } finally {
      setBusyId(null)
    }
  }

  // In-flight records lead the list in true queue order (oldest enqueue
  // first); finished ones follow, most recent result on top.
  const inFlight = (r: IngestHistoryRecord) =>
    r.status === "queued" || r.status === "processing"

  const filtered = records
    .filter((r) => {
      if (tab === "all") return true
      if (tab === "active") return inFlight(r)
      return r.status === tab
    })
    .sort((a, b) => {
      const ra = inFlight(a) ? 0 : 1
      const rb = inFlight(b) ? 0 : 1
      if (ra !== rb) return ra - rb
      if (ra === 0) return a.created_at.localeCompare(b.created_at)
      return b.created_at.localeCompare(a.created_at)
    })

  const tabDef: { id: HistoryTab; label: string; n?: number }[] = [
    { id: "all", label: t("library.ingestTabAll"), n: records.length },
    {
      id: "active",
      label: t("library.ingestTabActive"),
      n: counts.queued + counts.processing,
    },
    { id: "success", label: t("library.ingestTabSuccess"), n: counts.success },
    { id: "failed", label: t("library.ingestTabFailed"), n: counts.failed },
  ]

  const fmtTime = (iso: string) =>
    iso
      ? new Date(iso).toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })
      : ""

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="pm-dialog pm-suggest-dialog">
        <DialogHeader className="shrink-0">
          <DialogKicker>{t("library.ingestKicker")}</DialogKicker>
          <DialogTitle>{t("library.ingestTitle")}<HelpHint label={t("library.ingestTitle")}>{t("library.ingestBody")}</HelpHint></DialogTitle>
        </DialogHeader>

        {/* House tab language: white pill tray + sliding green indicator.
            .pm-ingest-dialog-tabs adds a hairline ring + soft shadow so the
            white tray keeps its card edge on the dialog's white canvas
            (WKWebView renders the dialog canvas flat white). */}
        <Tabs
          value={tab}
          onValueChange={(v) => setTab(v as HistoryTab)}
          className="shrink-0"
        >
          <TabsList className="pm-pill-tabs pm-ingest-dialog-tabs">
            {tabDef.map((tb) => (
              <TabsTrigger key={tb.id} value={tb.id} className="pm-ingest-tab">
                {tb.label}
                {tb.n ? <span className="pm-ingest-tab-n">{tb.n}</span> : null}
              </TabsTrigger>
            ))}
            <TabsIndicator />
          </TabsList>
        </Tabs>

        {filtered.length === 0 ? (
          <div className="pm-suggest-empty">
            <span className="pm-suggest-empty-badge" aria-hidden>
              <Clock className="h-4.5 w-4.5" />
            </span>
            <p className="pm-suggest-empty-title">
              {t("library.ingestEmptyTitle")}
            </p>
            <p className="pm-suggest-empty-hint">
              {t("library.ingestEmptyHint")}
            </p>
          </div>
        ) : (
          <div className="pm-ingest-list">
            {filtered.map((r) => (
              <div
                key={r.record_id}
                className={
                  "pm-ingest-row" +
                  (inFlight(r) ? " is-active" : "") +
                  (r.status === "queued" ? " is-queued-row" : "") +
                  (r.status === "failed" ? " is-failed" : "")
                }
              >
                <StatusChip status={r.status} />
                <div className="pm-ingest-row-body">
                  <span className="pm-ingest-row-name">
                    <span className="pm-ingest-row-file">{r.filename}</span>
                    {r.attempt > 0 && (
                      <span
                        className="pm-ingest-attempt"
                        title={t("library.ingestAttempt", { n: r.attempt + 1 })}
                      >
                        {"×" + (r.attempt + 1)}
                      </span>
                    )}
                  </span>
                  <span className="pm-ingest-row-meta">
                    {r.status === "failed" ? (
                      <>
                        <span className="pm-ingest-stage-tag">
                          {stageLabel(r.stage, t)}
                        </span>
                        <span className="pm-ingest-error-text">
                          {r.error || ""}
                        </span>
                      </>
                    ) : inFlight(r) ? (
                      stageLabel(
                        r.status === "queued" ? "queued" : r.stage,
                        t,
                      )
                    ) : null}
                  </span>
                </div>
                {(r.status === "success" || r.status === "failed") && (
                  <span className="pm-ingest-row-time">
                    {fmtTime(r.finished_at)}
                  </span>
                )}
                {r.status === "failed" && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    disabled={busyId === r.record_id}
                    onClick={() => void handleRetry(r)}
                    title={t("library.ingestRetry")}
                  >
                    {busyId === r.record_id ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <RotateCcw className="h-3 w-3" />
                    )}
                    {t("library.ingestRetry")}
                  </Button>
                )}
              </div>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
