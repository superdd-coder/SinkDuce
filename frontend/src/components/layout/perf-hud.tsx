import { useEffect, useState } from "react"
import { Activity, Gauge, Trash2, X } from "lucide-react"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"
import {
  clearPerfEvents,
  getFpsStats,
  getPerfEvents,
  installPerfHud,
  subscribePerf,
  type PerfEvent,
} from "@/lib/perf-hud"

const HUD_BUILD = "b10-profiling"

const switchTone = (ms: number | undefined) =>
  ms == null ? "is-idle" : ms < 250 ? "is-green" : ms < 600 ? "is-amber" : "is-red"

const fpsTone = (v: number) => (v >= 50 ? "is-green" : v >= 30 ? "is-amber" : "is-red")

const frameTone = (ms: number) => (ms < 34 ? "is-green" : ms < 68 ? "is-amber" : "is-red")

/** DeveloperMode-only floating readout of real in-App switch/fetch timings. */
export function PerfHud() {
  const t = useT()
  const [events, setEvents] = useState<PerfEvent[]>([])
  const [open, setOpen] = useState(false)
  const [fps, setFps] = useState({ fps: 0, worst: 0, janks: 0 })

  useEffect(() => {
    installPerfHud()
    setEvents([...getPerfEvents()])
    setFps(getFpsStats())
    return subscribePerf(() => {
      const next = getPerfEvents()
      setEvents((prev) => (prev.length !== next.length ? [...next] : prev))
      setFps(getFpsStats())
    })
  }, [])

  const lastSwitch = [...events].reverse().find((e) => e.kind === "switch")
  const lastLongtask = [...events].reverse().find((e) => e.kind === "longtask")
  const hot =
    lastLongtask &&
    (lastLongtask.ms ?? 0) > 200 &&
    lastLongtask.t > (lastSwitch?.t ?? -1)

  const tone = !lastSwitch ? "is-idle" : switchTone(lastSwitch.ms)
  const recent = events.slice(-60).reverse()

  const kindLabel = (e: PerfEvent) =>
    e.kind === "switch"
      ? t("perfHud.kindSwitch")
      : e.kind === "fetch"
        ? t("perfHud.kindFetch")
        : e.kind === "render"
          ? t("perfHud.kindRender")
          : e.kind === "jump"
            ? t("perfHud.kindJump")
            : t("perfHud.kindLongtask")

  return (
    <div className="pm-perf-hud">
      {open && (
        <div className="pm-perf-hud-panel" aria-label={t("perfHud.title")}>
          <header className="pm-perf-hud-head">
            <div className="pm-perf-hud-head-left">
              <span className="pm-perf-hud-gem" aria-hidden>
                <Gauge className="h-3.5 w-3.5" strokeWidth={1.75} />
              </span>
              <div className="pm-perf-hud-titles">
                <span className="pm-perf-hud-kicker">{t("perfHud.kicker")}</span>
                <h2 className="pm-perf-hud-title">{t("perfHud.title")}</h2>
              </div>
              <span className={cn("pm-perf-hud-live", fps.fps > 0 ? "is-on" : "is-off")}>
                <span className="pm-perf-hud-live-dot" aria-hidden />
                {fps.fps > 0 ? t("perfHud.live") : t("perfHud.idle")}
              </span>
            </div>
            <div className="pm-perf-hud-actions">
              <button
                type="button"
                className="pm-perf-hud-tool"
                onClick={() => {
                  clearPerfEvents()
                  setEvents([])
                }}
                title={t("perfHud.clear")}
              >
                <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} />
              </button>
              <button
                type="button"
                className="pm-perf-hud-tool is-close"
                onClick={() => setOpen(false)}
                title={t("perfHud.close")}
              >
                <X className="h-3.5 w-3.5" strokeWidth={1.75} />
              </button>
            </div>
          </header>

          <div className="pm-perf-hud-stats">
            <div className="pm-perf-hud-stat" title={t("perfHud.legend")}>
              <span className="pm-perf-hud-stat-label">
                <Activity className="h-3 w-3" strokeWidth={1.75} />
                {t("perfHud.fps")}
              </span>
              <span
                className={cn(
                  "pm-perf-hud-stat-value",
                  fps.fps > 0 ? fpsTone(fps.fps) : "is-idle"
                )}
              >
                {fps.fps > 0 ? fps.fps : "–"}
              </span>
            </div>
            <div className="pm-perf-hud-stat">
              <span className="pm-perf-hud-stat-label">{t("perfHud.worstFrame")}</span>
              <span
                className={cn(
                  "pm-perf-hud-stat-value",
                  fps.worst > 0 ? frameTone(fps.worst) : "is-idle"
                )}
              >
                {fps.worst > 0 ? `${fps.worst}ms` : "–"}
              </span>
            </div>
            <div className="pm-perf-hud-stat">
              <span className="pm-perf-hud-stat-label">{t("perfHud.janks")}</span>
              <span className={cn("pm-perf-hud-stat-value", fps.janks > 4 ? "is-red" : fps.janks > 0 ? "is-amber" : "is-green")}>
                {fps.janks}
              </span>
            </div>
            <div
              className="pm-perf-hud-stat"
              title={lastSwitch ? `${lastSwitch.label} · ${lastSwitch.ms}ms` : undefined}
            >
              <span className="pm-perf-hud-stat-label">{t("perfHud.lastSwitch")}</span>
              <span className={cn("pm-perf-hud-stat-value", switchTone(lastSwitch?.ms))}>
                {lastSwitch ? `${lastSwitch.ms}ms` : "–"}
              </span>
            </div>
          </div>

          <div className="pm-perf-hud-stream">
            {recent.length === 0 ? (
              <div className="pm-perf-hud-empty">
                <Gauge className="h-5 w-5 opacity-40" strokeWidth={1.5} />
                <p>{t("perfHud.empty")}</p>
                <span>{t("perfHud.emptyHint")}</span>
              </div>
            ) : (
              <div className="pm-perf-hud-rows">
                {recent.map((e, i) => (
                  <PerfRow key={`${e.t}-${e.kind}-${i}`} e={e} kindText={kindLabel(e)} />
                ))}
              </div>
            )}
          </div>

          <footer className="pm-perf-hud-foot">
            {HUD_BUILD} · {t("perfHud.legend")}
          </footer>
        </div>
      )}

      <button
        type="button"
        className="pm-perf-hud-pill"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        title={t("perfHud.title")}
      >
        <span
          className={cn("pm-perf-hud-pill-dot", tone, hot && "is-hot")}
          aria-hidden
        />
        {fps.fps > 0 && (
          <span className={cn("pm-perf-hud-pill-num", fpsTone(fps.fps))}>{fps.fps}fps</span>
        )}
        {fps.fps > 0 && <span className="pm-perf-hud-pill-sep" aria-hidden />}
        {lastSwitch ? (
          <span className="pm-perf-hud-pill-name">
            {lastSwitch.label}
            <span className={cn("pm-perf-hud-pill-ms", switchTone(lastSwitch.ms))}>
              {" "}
              {lastSwitch.ms}ms
            </span>
          </span>
        ) : (
          <span className="pm-perf-hud-pill-name">{t("perfHud.idle")}</span>
        )}
        {hot && lastLongtask && (
          <span className="pm-perf-hud-pill-hot">⚠{lastLongtask.ms}ms</span>
        )}
      </button>
    </div>
  )
}

function PerfRow({ e, kindText }: { e: PerfEvent; kindText: string }) {
  const tone =
    e.kind === "longtask"
      ? "is-red"
      : (e.ms ?? 0) > 100
        ? "is-amber"
        : "is-green"
  return (
    <div className={cn("pm-perf-hud-row", e.kind === "longtask" && "is-hot")}>
      <span className="pm-perf-hud-time">{e.t >= 0 ? `${(e.t / 1000).toFixed(1)}s` : "–"}</span>
      <span className={cn("pm-perf-hud-ms", tone)}>{e.ms != null ? `${e.ms}ms` : ""}</span>
      <span className={cn("pm-perf-hud-kind", e.kind === "longtask" ? "is-longtask" : "is-kind")}>
        {kindText}
      </span>
      <span className="pm-perf-hud-label">{e.kind === "longtask" ? "" : e.label}</span>
    </div>
  )
}
