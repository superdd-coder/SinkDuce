import { useState, useEffect, useCallback, useMemo, memo, useRef, Profiler, type CSSProperties } from "react"
import { useShallow } from "zustand/react/shallow"
import { useAppStore, type SidebarView } from "@/stores/app-store"
import { Header } from "./header"
import { cn } from "@/lib/utils"
import { Sidebar } from "./sidebar"
import { LogViewer } from "./log-viewer"
import { PerfHud } from "./perf-hud"
import { recordRender } from "@/lib/perf-hud"
import { ChatView } from "@/components/chat/chat-view"
import { DatabaseView } from "@/components/database/database-view"
import { RecallView } from "@/components/recall/recall-view"
import { LLMProviderView } from "@/components/llm-provider/llm-provider-view"
import { RemoteSettingsView } from "@/components/llm-provider/remote-settings-view"
import { MeetingView } from "@/components/meeting/meeting-view"
import { ModelDownloadDialog } from "@/components/model-download-dialog"
import { getConfig, getModelStatus, type ModelStatus } from "@/api/client"
import { useAppEvents } from "@/hooks/use-app-events"
import { Loader2 } from "lucide-react"
import { toast } from "sonner"
import { useT } from "@/i18n/use-t"

type ViewProps = { active: boolean }

const MemoChatView = memo(function MemoChatView(_props: ViewProps) {
  return <ChatView />
})
const MemoDatabaseView = memo(function MemoDatabaseView({ active }: ViewProps) {
  return <DatabaseView active={active} />
})
const MemoRecallView = memo(function MemoRecallView(_props: ViewProps) {
  return <RecallView />
})
const MemoMeetingView = memo(function MemoMeetingView({ active }: ViewProps) {
  return <MeetingView active={active} />
})
const MemoLLMProviderView = memo(function MemoLLMProviderView(_props: ViewProps) {
  // 远程模式按角色分流：有 config.write（owner/admin，判定来自**当前所连
  // 盒子**的身份画像，与正常模式连远端同一闸门）→ 完整配置面（读写都走
  // 那台盒子，语言卡经 store 分支写壳）；其余角色 → 三件壳设置。
  const remoteShellMode = useAppStore((s) => s.remoteShellMode)
  const canWriteConfig = useAppStore(
    (s) => s.identity?.capabilities?.["config.write"] === true,
  )
  return remoteShellMode && !canWriteConfig ? <RemoteSettingsView /> : <LLMProviderView />
})

const views = {
  chat: MemoChatView,
  database: MemoDatabaseView,
  recall: MemoRecallView,
  meeting: MemoMeetingView,
  llm_provider: MemoLLMProviderView,
} as const

export function AppLayout() {
  const t = useT()
  const { sidebarView, logPanelOpen, toggleLogPanel, hydrateLocale, developerMode, viewRailWidth } =
    useAppStore(
      useShallow((s) => ({
        sidebarView: s.sidebarView,
        logPanelOpen: s.logPanelOpen,
        toggleLogPanel: s.toggleLogPanel,
        hydrateLocale: s.hydrateLocale,
        developerMode: s.developerMode,
        viewRailWidth: s.viewRailWidth,
      }))
    )

  useEffect(() => {
    getConfig()
      .then((c) => hydrateLocale(c.locale))
      .catch(() => {})
  }, [hydrateLocale])

  // App-wide change stream: peers' actions invalidate this client's stale
  // surfaces (multi-device sync). Mounted once, here at the layout root.
  useAppEvents()

  // 来源切换遮罩清理：切换 = 设 data-switching + reload；新页面挂载即清除，
  // 遮罩只在「旧页面 → reload 完成」的窗口可见。
  useEffect(() => {
    delete document.documentElement.dataset.switching
  }, [])

  const [downloadDialogOpen, setDownloadDialogOpen] = useState(false)
  const [minimized, setMinimized] = useState(false)
  const [isDownloading, setIsDownloading] = useState(false)
  const [models, setModels] = useState<ModelStatus[]>([])
  /** First-visit lazy mount; after that keep mounted (state preserved). */
  const [visitedViews, setVisitedViews] = useState<Set<SidebarView>>(
    () => new Set([sidebarView])
  )
  /** Views whose first mount already ran. First-visit views render a quiet
   * skeleton for one beat so their mount+raster never collides with the
   * view fade and the QuickChat diamond settle animation. */
  const [settledViews, setSettledViews] = useState<Set<SidebarView>>(
    () => new Set([sidebarView])
  )

  /**
   * Crossfade between Navigate views (Chat / Database / Meeting / …).
   * The outgoing layer stays mounted on top and fades out while the incoming
   * layer is already painted beneath it — no empty-gap stage between the two
   * fades, and the new view's first-paint spike lands under the cover of the
   * old layer instead of mid-fade.
   */
  const [displayView, setDisplayView] = useState<SidebarView>(sidebarView)
  const [leavingView, setLeavingView] = useState<SidebarView | null>(null)
  /** The view we just left stays promoted (is-warm) so alternating switches
   * hit its cached raster instead of re-rastering the whole subtree. */
  const [warmView, setWarmView] = useState<SidebarView | null>(null)
  const viewMotionGenRef = useRef(0)
  const lastSwitchAtRef = useRef(0)
  /** Leaving fade is 120ms; +20ms buffer before dropping the layer. */
  const VIEW_CROSS_MS = 140
  /** Within this window consecutive switches jump-cut (no fade work at all). */
  const RAPID_SWITCH_MS = 350

  useEffect(() => {
    setVisitedViews((prev) => {
      if (prev.has(sidebarView)) return prev
      const next = new Set(prev)
      next.add(sidebarView)
      return next
    })
  }, [sidebarView])

  useEffect(() => {
    if (sidebarView === displayView) return
    const gen = ++viewMotionGenRef.current
    const rapid = performance.now() - lastSwitchAtRef.current < RAPID_SWITCH_MS
    lastSwitchAtRef.current = performance.now()
    setWarmView(displayView)
    setLeavingView(rapid ? null : displayView)
    setDisplayView(sidebarView)
    // Plain timer on purpose (not the double-rAF arming this used to have):
    // it must fire even when the main thread stalls right after the switch,
    // and the gen guard drops stale timers from rapid multi-switches.
    window.setTimeout(() => {
      if (viewMotionGenRef.current !== gen) return
      setLeavingView(null)
    }, VIEW_CROSS_MS)
  }, [sidebarView, displayView])
  useEffect(() => {
    if (settledViews.has(displayView)) return
    const t = window.setTimeout(() => {
      setSettledViews((prev) => {
        if (prev.has(displayView)) return prev
        const next = new Set(prev)
        next.add(displayView)
        return next
      })
    }, 300)
    return () => window.clearTimeout(t)
  }, [displayView, settledViews])

  // Never auto-open download UI on first deploy. Only resume a minimized chip
  // if a download is already running (started from Settings).
  useEffect(() => {
    getModelStatus()
      .then((m) => {
        setModels(m)
        if (
          m.some(
            (x) => x.status === "downloading" || x.status === "extracting"
          )
        ) {
          setIsDownloading(true)
          setMinimized(true)
        }
      })
      .catch(() => {})
  }, [])

  // Poll while a background download is minimized
  useEffect(() => {
    if (!isDownloading) return
    const interval = setInterval(async () => {
      try {
        const m = await getModelStatus()
        setModels(m)
        const stillDownloading = m.some(
          (x) => x.status === "downloading" || x.status === "extracting"
        )
        if (!stillDownloading) {
          setIsDownloading(false)
          setMinimized(false)
          const allDone = m.every((x) => x.downloaded)
          if (allDone) {
            toast.success(t("shell.allModelsDownloaded"))
          } else {
            const errors = m.filter((x) => x.status === "error")
            if (errors.length > 0) {
              toast.error(
                `Download failed: ${errors.map((e) => e.display_name).join(", ")}`
              )
            }
          }
        }
      } catch {
        /* ignore */
      }
    }, 3000)
    return () => clearInterval(interval)
  }, [isDownloading])

  const handleDialogClose = useCallback(
    (open: boolean) => {
      if (!open) {
        const hasActive = models.some(
          (x) => x.status === "downloading" || x.status === "extracting"
        )
        if (hasActive) {
          setMinimized(true)
          setIsDownloading(true)
          setDownloadDialogOpen(false)
          return
        }
      }
      setDownloadDialogOpen(open)
    },
    [models]
  )

  const handleComplete = useCallback(() => {
    setDownloadDialogOpen(false)
    setMinimized(false)
  }, [])

  /** Recall unmounts with developer mode off — no nav entry, no background fetches */
  const viewEntries = useMemo(
    () =>
      (Object.keys(views) as SidebarView[]).filter(
        (k) => visitedViews.has(k) && (developerMode || k !== "recall")
      ),
    [visitedViews, developerMode]
  )

  return (
    <div className="pm-shell-root h-screen flex flex-col">
      <Header />
      <div className="flex flex-1 overflow-hidden min-h-0">
        <Sidebar />
        {developerMode && <PerfHud />}
        {/*
          Sage canvas behind float cards; padding = soft-shadow bleed.
        */}
        <main
          className={cn(
            "pm-shell-main flex-1 min-w-0 flex flex-col overflow-hidden p-3 pt-1",
            logPanelOpen && "has-backend-logs"
          )}
        >
          {/* Stacked keep-alive views + crossfade on Navigate switch */}
          <div className="pm-shell-view-host" style={{ "--pm-view-rail-w": `${viewRailWidth}px` } as CSSProperties}>
            {viewEntries.map((key) => {
              const V = views[key]
              const isDisplay = key === displayView
              const isNavActive = key === sidebarView
              const isLeaving = !isDisplay && key === leavingView
              const phaseClass = isDisplay
                ? "is-active"
                : isLeaving
                  ? "is-leaving"
                  : key === warmView
                    ? "is-idle is-warm"
                    : "is-idle"
              const floatChrome =
                key === "database" || key === "chat" || key === "meeting"
              return (
                <div
                  key={key}
                  className={cn(
                    "pm-shell-view-layer",
                    phaseClass,
                    floatChrome && "pm-shell-view-layer--float",
                  )}
                  aria-hidden={!isDisplay}
                >
                  {settledViews.has(key) ? (
                    <Profiler
                      id={`view:${key}`}
                      onRender={(_id, phase, actualDuration) => {
                        if (actualDuration > 12) recordRender(key, phase, actualDuration)
                      }}
                    >
                      <V active={isNavActive} />
                    </Profiler>
                  ) : (
                    <div className="pm-shell-view-skeleton" aria-hidden />
                  )}
                </div>
              )
            })}
          </div>
          {/*
            Height + shadow gutter live in CSS (.pm-backend-logs-slot[data-open]).
            Negative margin into main p-3 keeps card L/R flush with Chat stage
            while soft-float shadow is not clipped by overflow:hidden.
          */}
          <div
            className="pm-backend-logs-slot"
            data-open={logPanelOpen ? "true" : "false"}
          >
            <div
              className="pm-backend-logs-slot-pad"
              style={{
                transform: `translateY(${logPanelOpen ? 0 : 12}px)`,
                opacity: logPanelOpen ? 1 : 0,
              }}
            >
              <LogViewer open={logPanelOpen} onClose={toggleLogPanel} />
            </div>
          </div>
        </main>
      </div>

      {/* Minimized download indicator */}
      {minimized && !downloadDialogOpen && (
        <button
          onClick={() => { setDownloadDialogOpen(true); setMinimized(false) }}
          className="fixed top-14 right-4 z-50 flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-card shadow-lg hover:bg-accent transition-colors"
        >
          <Loader2 className="h-4 w-4 animate-spin text-primary" />
          <span className="text-sm">{t("shell.downloadingModels")}</span>
        </button>
      )}

      <ModelDownloadDialog
        open={downloadDialogOpen}
        onOpenChange={handleDialogClose}
        onComplete={handleComplete}
      />

    </div>
  )
}
