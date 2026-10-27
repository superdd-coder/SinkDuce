/**
 * Perf HUD — developerMode-only instrumentation that reports what the user
 * actually experiences inside the App's WKWebView, where remote profiling
 * can't reach: nav-switch timeline (click → first painted frame), every API
 * fetch duration, and main-thread long tasks.
 */

export interface FpsStats {
  fps: number
  worst: number
  janks: number
}

export interface PerfEvent {
  /** ms since HUD install */
  t: number
  kind: "switch" | "fetch" | "longtask" | "render" | "jump"
  /** switch: view name; fetch: url; longtask: duration summary */
  label: string
  ms?: number
}

const MAX_EVENTS = 120
let events: PerfEvent[] = []
let installed = false
let t0 = 0
let pendingSwitch: { view: string; t: number } | null = null
const listeners = new Set<() => void>()

const emit = (e: PerfEvent) => {
  events.push(e)
  if (events.length > MAX_EVENTS) events = events.slice(-MAX_EVENTS)
  for (const l of listeners) l()
}

export const getPerfEvents = () => events
export const clearPerfEvents = () => {
  events = []
  for (const l of listeners) l()
}
export const subscribePerf = (cb: () => void) => {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

let fpsStats: FpsStats = { fps: 0, worst: 0, janks: 0 }
export const getFpsStats = () => fpsStats

export function recordJump(id: string, targetIdx: number, landedIdx: number) {
  if (!installed) return
  emit({
    t: Math.round(performance.now() - t0),
    kind: "jump",
    label: `引用 ${id.replace(/^.*?stt_/, "stt_").replace(/^stt_0*/, "")} → 目标${targetIdx} 落点${landedIdx}`,
  })
}

export function recordRender(view: string, phase: string, ms: number) {
  if (!installed) return
  emit({
    t: Math.round(performance.now() - t0),
    kind: "render",
    label: `${view} (${phase})`,
    ms: Math.round(ms),
  })
}

export function installPerfHud() {
  if (installed || typeof window === "undefined") return
  installed = true
  t0 = performance.now()

  // Continuous frame sampler: 2s sliding window, worst frame + jank count.
  {
    const window: number[] = []
    let last = performance.now()
    const tick = (now: number) => {
      const d = now - last
      last = now
      if (d > 1 && d < 4000) {
        window.push(d)
        while (window.length > 240) window.shift()
        const recent = window.slice(-120)
        const avg = recent.reduce((a, b) => a + b, 0) / recent.length
        fpsStats = {
          fps: Math.round(1000 / avg),
          worst: Math.round(Math.max(...recent)),
          janks: recent.filter((x) => x > 34).length,
        }
        for (const l of listeners) l()
      }
      requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  }

  // Fetch durations (skip the log stream / websockets)
  const of = window.fetch
  window.fetch = function (input, _init) {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : (input?.url ?? "")
    const st = performance.now()
    return of.apply(this, arguments as unknown as Parameters<typeof of>).then(
      (res) => {
        if (url.startsWith("/api/") && !url.includes("/stream")) {
          emit({
            t: Math.round(st - t0),
            kind: "fetch",
            label: url.split("?")[0].replace("/api/", "").slice(0, 120),
            ms: Math.round(performance.now() - st),
          })
        }
        return res
      }
    )
  }

  // Main-thread long tasks
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        if (e.duration > 80)
          emit({
            t: Math.round(e.startTime - t0),
            kind: "longtask",
            label: "主线程阻塞",
            ms: Math.round(e.duration),
          })
      }
    }).observe({ type: "longtask" })
  } catch {
    /* older WebKit */
  }

  // Nav switch: click → first painted frame of the new active layer
  document.addEventListener(
    "click",
    (ev) => {
      const el = (ev.target as HTMLElement | null)?.closest?.(
        ".pm-shell-nav-item"
      )
      if (!el || pendingSwitch) return
      pendingSwitch = {
        view: (el.textContent || "").trim().slice(0, 10),
        t: performance.now(),
      }
    },
    true
  )
  const host = document.querySelector(".pm-shell-view-host")
  if (host) {
    new MutationObserver((muts) => {
      if (!pendingSwitch) return
      for (const m of muts) {
        const cls = (m.target as HTMLElement).className as string
        if (cls.includes("is-active")) {
          const clickT = pendingSwitch.t
          const view = pendingSwitch.view
          pendingSwitch = null
          // first presented frame after the commit
          requestAnimationFrame(() =>
            requestAnimationFrame(() => {
              emit({
                t: Math.round(clickT - t0),
                kind: "switch",
                label: view,
                ms: Math.round(performance.now() - clickT),
              })
            })
          )
          break
        }
      }
    }).observe(host, { subtree: true, attributes: true, attributeFilter: ["class"] })
  }
}

