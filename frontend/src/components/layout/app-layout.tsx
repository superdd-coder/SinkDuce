import { useState, useEffect, useCallback } from "react"
import { useAppStore } from "@/stores/app-store"
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
import { getModelStatus, getSetupStatus, markSetupComplete, type ModelStatus } from "@/api/client"
import { Loader2 } from "lucide-react"
import { toast } from "sonner"

const DISMISSED_KEY = "model-download-dismissed"

const views = {
  chat: ChatView,
  database: DatabaseView,
  recall: RecallView,
  meeting: MeetingView,
  llm_provider: LLMProviderView,
} as const

export function AppLayout() {
  const { sidebarView, logPanelOpen, toggleLogPanel } = useAppStore()

  const [downloadDialogOpen, setDownloadDialogOpen] = useState(false)
  const [minimized, setMinimized] = useState(false)
  const [isDownloading, setIsDownloading] = useState(false)
  const [models, setModels] = useState<ModelStatus[]>([])

  // Check on startup
  useEffect(() => {
    const dismissed = localStorage.getItem(DISMISSED_KEY)
    Promise.all([getModelStatus(), getSetupStatus().catch(() => ({ setup_completed: true, models: [], categories: [] }))])
      .then(([m, setup]) => {
        setModels(m)
        const hasMissing = m.some((x) => !x.downloaded)
        const hasActive = m.some((x) => x.status === "downloading")
        const shouldShowSetup = !setup.setup_completed && hasMissing
        if (hasActive) {
          setIsDownloading(true)
          setMinimized(true)
        } else if (shouldShowSetup || (hasMissing && !dismissed)) {
          setDownloadDialogOpen(true)
        }
      })
      .catch(() => {})
  }, [])

  // Poll while downloading
  useEffect(() => {
    if (!isDownloading) return
    const interval = setInterval(async () => {
      try {
        const m = await getModelStatus()
        setModels(m)
        const stillDownloading = m.some((x) => x.status === "downloading")
        if (!stillDownloading) {
          setIsDownloading(false)
          const allDone = m.every((x) => x.downloaded)
          if (allDone) {
            toast.success("All models downloaded!")
            setMinimized(false)
            setDownloadDialogOpen(true) // Show completion state
          } else {
            const errors = m.filter((x) => x.status === "error")
            if (errors.length > 0) {
              toast.error(`Download failed: ${errors.map(e => e.display_name).join(", ")}`)
            }
            setMinimized(false)
            setDownloadDialogOpen(true)
          }
        }
      } catch { /* ignore */ }
    }, 3000)
    return () => clearInterval(interval)
  }, [isDownloading])

  const handleDialogClose = useCallback((open: boolean) => {
    if (!open) {
      const hasActive = models.some((x) => x.status === "downloading")
      if (hasActive) {
        // Minimize instead of closing
        setMinimized(true)
        setIsDownloading(true)
        setDownloadDialogOpen(false)
        return
      }
      // Mark as dismissed so it doesn't re-appear on refresh
      localStorage.setItem(DISMISSED_KEY, "true")
    }
    setDownloadDialogOpen(open)
  }, [models])

  const handleComplete = useCallback(() => {
    localStorage.setItem(DISMISSED_KEY, "true")
    markSetupComplete().catch(() => {})
    setDownloadDialogOpen(false)
    setMinimized(false)
  }, [])

  // Reset dismissed flag when models change (new missing models)
  useEffect(() => {
    if (models.length > 0 && models.every((m) => m.downloaded)) {
      localStorage.removeItem(DISMISSED_KEY)
    }
  }, [models])

  return (
    <div className="h-screen flex flex-col">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-hidden flex flex-col">
          {Object.entries(views).map(([key, V]) => (
            <div
              key={key}
              className={cn(
                "flex-1 overflow-hidden",
                key === sidebarView ? "flex flex-col animate-tab-in" : "hidden",
              )}
            >
              <V />
            </div>
          ))}
          <div
            className="relative z-40 bg-background overflow-hidden"
            style={{
              height: logPanelOpen ? 280 : 0,
              transition: "height 350ms cubic-bezier(0.4,0,0.2,1)",
            }}
          >
            <div
              style={{
                height: 280,
                transform: `translateY(${logPanelOpen ? 0 : 10}px)`,
                opacity: logPanelOpen ? 1 : 0,
                transition: "transform 350ms cubic-bezier(0.4,0,0.2,1), opacity 300ms ease-out",
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
          <span className="text-sm">Downloading models...</span>
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
