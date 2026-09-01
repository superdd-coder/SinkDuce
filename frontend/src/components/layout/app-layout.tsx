import { useState, useEffect, useCallback, useMemo, memo, useRef } from "react"
import { useShallow } from "zustand/react/shallow"
import { useAppStore, type SidebarView } from "@/stores/app-store"
import { Header } from "./header"
import { cn } from "@/lib/utils"
import { Sidebar } from "./sidebar"
import { LogViewer } from "./log-viewer"
import { ChatView } from "@/components/chat/chat-view"
import { DatabaseView } from "@/components/database/database-view"
import { RecallView } from "@/components/recall/recall-view"
import { LLMProviderView } from "@/components/llm-provider/llm-provider-view"
import { MeetingView } from "@/components/meeting/meeting-view"
import { ModelDownloadDialog } from "@/components/model-download-dialog"
import { getConfig, getModelStatus, type ModelStatus } from "@/api/client"
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
  return <LLMProviderView />
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
  const { sidebarView, logPanelOpen, toggleLogPanel, hydrateLocale } = useAppStore(
    useShallow((s) => ({
      sidebarView: s.sidebarView,
      logPanelOpen: s.logPanelOpen,
      toggleLogPanel: s.toggleLogPanel,
      hydrateLocale: s.hydrateLocale,
    }))
  )

  useEffect(() => {
    getConfig()
      .then((c) => hydrateLocale(c.locale))
      .catch(() => {})
  }, [hydrateLocale])

  const [downloadDialogOpen, setDownloadDialogOpen] = useState(false)
  const [minimized, setMinimized] = useState(false)
  const [isDownloading, setIsDownloading] = useState(false)
  const [models, setModels] = useState<ModelStatus[]>([])
  /** First-visit lazy mount; after that keep mounted (state preserved). */
  const [visitedViews, setVisitedViews] = useState<Set<SidebarView>>(
    () => new Set([sidebarView])
  )
  /**
   * Sequential fade between Navigate views (Chat / Database / Meeting / …).
   * displayView lags sidebarView: fade out → swap → fade in (no hard cut).
   */
  const [displayView, setDisplayView] = useState<SidebarView>(sidebarView)
  const [viewPhase, setViewPhase] = useState<"shown" | "hiding">("shown")
  const viewMotionGenRef = useRef(0)
  const VIEW_OUT_MS = 100

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
    setViewPhase("hiding")
    const t = window.setTimeout(() => {
      if (viewMotionGenRef.current !== gen) return
      setDisplayView(sidebarView)
      // Paint next at opacity 0, then fade in
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          if (viewMotionGenRef.current !== gen) return
          setViewPhase("shown")
        })
      })
    }, VIEW_OUT_MS)
    return () => window.clearTimeout(t)
  }, [sidebarView, displayView])

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

  const viewEntries = useMemo(
    () => (Object.keys(views) as SidebarView[]).filter((k) => visitedViews.has(k)),
    [visitedViews]
  )

  return (
    <div className="pm-shell-root h-screen flex flex-col">
      <Header />
      <div className="flex flex-1 overflow-hidden min-h-0">
        <Sidebar />
        {/*
          Sage canvas behind float cards; padding = soft-shadow bleed.
        */}
        <main
          className={cn(
            "pm-shell-main flex-1 min-w-0 flex flex-col overflow-hidden p-3 pt-1",
            logPanelOpen && "has-backend-logs"
          )}
        >
          {/* Stacked keep-alive views + sequential fade on Navigate switch */}
          <div className="pm-shell-view-host">
            {viewEntries.map((key) => {
              const V = views[key]
              const isDisplay = key === displayView
              const isNavActive = key === sidebarView
              const phaseClass = isDisplay
                ? viewPhase === "hiding"
                  ? "is-exiting"
                  : "is-active"
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
                  aria-hidden={!isDisplay || viewPhase === "hiding"}
                >
                  <V active={isNavActive} />
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
