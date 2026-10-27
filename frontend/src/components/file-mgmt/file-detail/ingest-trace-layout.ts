import type { IngestTrace, IngestTraceStep } from "@/api/file-mgmt"

export type LaidIngestStep = {
  step: IngestTraceStep
  start: number
  end: number
  lane: number
}

export type IngestTimelineLayout = {
  items: LaidIngestStep[]
  laneCount: number
  origin: number
  span: number
}

function parseIsoMs(value?: string | null): number | null {
  if (!value) return null
  const t = Date.parse(value)
  return Number.isFinite(t) ? t : null
}

function relFromIso(iso: string | undefined, runStartMs: number): number | null {
  const abs = parseIsoMs(iso)
  if (abs == null) return null
  return Math.max(0, abs - runStartMs)
}

function naiveWindow(step: IngestTraceStep, runStartMs: number | null): { start: number; end: number } {
  if (typeof step.started_ms === "number") {
    const start = step.started_ms
    const end =
      typeof step.ended_ms === "number"
        ? step.ended_ms
        : typeof step.ms === "number"
          ? start + step.ms
          : start
    return { start, end: Math.max(start, end) }
  }
  if (runStartMs == null) {
    if (typeof step.ms === "number") return { start: 0, end: step.ms }
    return { start: 0, end: 0 }
  }
  const started = relFromIso(step.started_at, runStartMs)
  const ended = relFromIso(step.ended_at || step.at, runStartMs)
  if (started != null) {
    const end =
      ended != null ? ended : typeof step.ms === "number" ? started + step.ms : started
    return { start: started, end: Math.max(started, end) }
  }
  if (ended != null) {
    const start = typeof step.ms === "number" ? Math.max(0, ended - step.ms) : ended
    return { start, end: ended }
  }
  if (typeof step.ms === "number") return { start: 0, end: step.ms }
  return { start: 0, end: 0 }
}

function reconstructPipeline(
  rows: { step: IngestTraceStep; start: number; end: number }[],
  allSteps: IngestTraceStep[],
  runStartMs: number | null,
  schema: number,
): void {
  const byId = new Map(rows.map((r) => [r.step.id, r]))
  const ocr = byId.get("ocr")
  const vision = byId.get("vision")
  const legacyOcrVis =
    ocr &&
    vision &&
    ocr.step.started_ms == null &&
    vision.step.started_ms == null &&
    !ocr.step.started_at &&
    !vision.step.started_at
  if (legacyOcrVis && ocr && vision && runStartMs != null) {
    const ocrAt = parseIsoMs(ocr.step.at)
    const visAt = parseIsoMs(vision.step.at)
    const ocrMs = typeof ocr.step.ms === "number" ? ocr.step.ms : 0
    const visMs = typeof vision.step.ms === "number" ? vision.step.ms : 0
    if (ocrAt != null && visAt != null && Math.abs(ocrAt - visAt) <= 1500 && ocrMs > 0 && visMs > 0) {
      const end = visAt - runStartMs
      vision.start = Math.max(0, end - visMs)
      vision.end = Math.max(vision.start, end)
      ocr.end = vision.start
      ocr.start = Math.max(0, ocr.end - ocrMs)
    }
  }

  const summary = byId.get("summary")
  const wait = byId.get("cache_wait")
  const context = byId.get("context")
  const legacyEnrich = Boolean(summary && summary.step.started_ms == null && !summary.step.started_at)
  if (!legacyEnrich || !summary || runStartMs == null) return

  const sumMs = typeof summary.step.ms === "number" ? summary.step.ms : Math.max(0, summary.end - summary.start)
  let waitMs =
    wait && wait.step.status !== "skip" && typeof wait.step.ms === "number" ? wait.step.ms : 0
  let ctxMs =
    context && context.step.status !== "skip" && typeof context.step.ms === "number"
      ? context.step.ms
      : 0
  const ats = [summary, wait, context]
    .filter(Boolean)
    .map((r) => parseIsoMs(r!.step.at))
    .filter((n): n is number => n != null)
  const sameFlush = ats.length >= 2 && Math.max(...ats) - Math.min(...ats) <= 2000
  if (schema < 2 && sameFlush && ctxMs >= waitMs && waitMs > 0) {
    ctxMs -= waitMs
  }

  const marker = allSteps.find((s) => s.id === "summary_start")
  const chainStart = relFromIso(marker?.started_at || marker?.at, runStartMs)

  if (chainStart != null) {
    let t = chainStart
    summary.start = t
    summary.end = t + sumMs
    t = summary.end
    if (wait) {
      wait.start = t
      wait.end = t + waitMs
      t = wait.end
    }
    if (context && context.step.status !== "skip") {
      context.start = t
      context.end = t + ctxMs
    }
    return
  }

  if (sameFlush && context) {
    const endAbs = parseIsoMs(context.step.at || summary.step.at)
    if (endAbs == null) return
    let t = endAbs - runStartMs
    if (context.step.status !== "skip") {
      context.end = t
      context.start = Math.max(0, t - ctxMs)
      t = context.start
    }
    if (wait) {
      wait.end = t
      wait.start = Math.max(0, t - waitMs)
      t = wait.start
    }
    summary.end = t
    summary.start = Math.max(0, t - sumMs)
  }
}

const SKIP_MERGE_MS = 800

function mergeNearbySkips(
  rows: { step: IngestTraceStep; start: number; end: number }[],
): { step: IngestTraceStep; start: number; end: number }[] {
  const briefSkip = (r: { step: IngestTraceStep; start: number; end: number }) =>
    r.step.status === "skip" && r.end - r.start < 200
  const skips = rows.filter(briefSkip).sort((a, b) => a.start - b.start)
  const rest = rows.filter((r) => !briefSkip(r))
  if (skips.length <= 1) return rows

  const groups: (typeof skips)[] = []
  for (const row of skips) {
    const last = groups[groups.length - 1]
    if (last && row.start - last[0].start <= SKIP_MERGE_MS) last.push(row)
    else groups.push([row])
  }

  const merged = groups.map((group) => {
    if (group.length === 1) return group[0]
    const titles = group.map((g) => g.step.title)
    const details = group.map((g) => g.step.detail).filter(Boolean)
    const last = group[group.length - 1]
    return {
      start: group[0].start,
      end: group[0].start,
      step: {
        id: group.map((g) => g.step.id).join("+"),
        title: titles.join(" · "),
        status: "skip",
        detail: details.join("\n"),
        at: group[0].step.at,
        started_at: group[0].step.started_at || group[0].step.at,
        ended_at: last.step.ended_at || last.step.at,
        ms: 0,
      },
    }
  })
  return [...rest, ...merged]
}

export function layoutIngestTimeline(trace: IngestTrace): IngestTimelineLayout {
  const runStartMs = parseIsoMs(trace.started_at)
  const allSteps = trace.steps || []
  const steps = allSteps.filter((s) => s.id !== "summary_start")
  const raw = steps.map((step) => {
    const win = naiveWindow(step, runStartMs)
    let end = win.end
    if (step.status === "running" && end <= win.start) {
      const hinted =
        typeof trace.duration_ms === "number" ? Math.max(trace.duration_ms, win.start + 400) : win.start + 400
      end = hinted
    }
    return { step, start: win.start, end }
  })
  reconstructPipeline(raw, allSteps, runStartMs, Number(trace.schema || 1))
  const packed = mergeNearbySkips(raw)

  const origin = packed.reduce((m, r) => Math.min(m, r.start), packed[0]?.start ?? 0)
  const extent = packed.reduce((m, r) => Math.max(m, r.end), origin)
  const runMs = typeof trace.duration_ms === "number" ? trace.duration_ms : 0
  const span = Math.max(1, extent - origin, runMs - origin)

  const sorted = [...packed].sort((a, b) => a.start - b.start || a.end - b.end)
  const laneEnds: number[] = []
  const items: LaidIngestStep[] = sorted.map((row) => {
    // Instant markers occupy a slice so two 0ms skips 12ms apart do not stack.
    const occupiedEnd = row.end > row.start + 50 ? row.end : row.start + 400
    let lane = -1
    for (let i = 0; i < laneEnds.length; i++) {
      if (laneEnds[i] <= row.start) {
        lane = i
        break
      }
    }
    if (lane < 0) {
      lane = laneEnds.length
      laneEnds.push(occupiedEnd)
    } else {
      laneEnds[lane] = occupiedEnd
    }
    return { ...row, lane }
  })

  return {
    items,
    laneCount: Math.max(1, laneEnds.length),
    origin,
    span,
  }
}

export function tickMarks(origin: number, span: number): number[] {
  const totalSec = span / 1000
  const step =
    totalSec <= 12 ? 2 : totalSec <= 30 ? 5 : totalSec <= 90 ? 10 : totalSec <= 180 ? 15 : 30
  const ticks: number[] = [origin]
  const startSec = Math.ceil(origin / 1000 / step) * step
  const endSec = (origin + span) / 1000
  for (let s = startSec; s < endSec - step * 0.15; s += step) {
    const ms = s * 1000
    if (ms - origin > 200) ticks.push(ms)
  }
  ticks.push(origin + span)
  return ticks
}

export function formatClock(iso?: string | null): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  })
}
