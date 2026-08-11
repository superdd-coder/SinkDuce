import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Star, Plug, Loader2, Power, Download } from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"

export type LoadState = "unloaded" | "loading" | "loaded" | "error"

export interface LoadDetail {
  state?: LoadState
  message?: string
  error?: string
  started_at?: number
  load_s?: number
}

interface LocalModelCardProps {
  id: string
  name: string
  model: string
  isDefault: boolean
  loadState: LoadState
  loadDetail?: LoadDetail
  isDownloaded: boolean
  onTest: () => Promise<{
    success: boolean
    message?: string
    error?: string
    code?: string
  }>
  onSetDefault: () => Promise<void>
  /** Parent starts load/unload; must return API payload with real status */
  onToggleLoad: (action: "load" | "unload") => Promise<{
    success: boolean
    status?: string
    message?: string
    error?: string
  }>
  onDownload: () => void
}

export function LocalModelCard({
  name,
  model,
  isDefault,
  loadState,
  loadDetail,
  isDownloaded,
  onTest,
  onSetDefault,
  onToggleLoad,
  onDownload,
}: LocalModelCardProps) {
  const [testing, setTesting] = useState(false)
  const [testNote, setTestNote] = useState<string | null>(null)
  const [testOk, setTestOk] = useState<boolean | null>(null)
  /** User clicked Load and we have not yet seen a terminal server state */
  const [awaitingLoad, setAwaitingLoad] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [loadStartedAt, setLoadStartedAt] = useState<number | null>(null)

  // Effective UI state: never show "loaded" while still awaiting a load.
  // Only server loadState === "loaded" ends awaitingLoad.
  const displayState: LoadState =
    loadState === "loaded"
      ? "loaded"
      : loadState === "error"
        ? "error"
        : loadState === "loading" || awaitingLoad
          ? "loading"
          : "unloaded"

  const isLoaded = displayState === "loaded"
  const isLoading = displayState === "loading"
  const isError = displayState === "error"

  // Sync awaitingLoad with server: clear only on terminal states after a load attempt
  useEffect(() => {
    if (loadState === "loaded" || loadState === "error") {
      setAwaitingLoad(false)
    }
    if (loadState === "loading") {
      setAwaitingLoad(true)
    }
  }, [loadState])

  // Live elapsed timer while loading
  useEffect(() => {
    if (!isLoading) {
      setElapsed(0)
      return
    }
    const startedMs =
      typeof loadDetail?.started_at === "number"
        ? loadDetail.started_at * 1000
        : loadStartedAt ?? Date.now()
    const tick = () =>
      setElapsed(Math.max(0, Math.floor((Date.now() - startedMs) / 1000)))
    tick()
    const id = window.setInterval(tick, 500)
    return () => window.clearInterval(id)
  }, [isLoading, loadDetail?.started_at, loadStartedAt])

  const handleTest = async () => {
    if (!isLoaded) {
      toast.message("Load the model first", {
        description: "Test only checks that the model is in memory.",
      })
      return
    }
    setTesting(true)
    setTestNote(null)
    setTestOk(null)
    try {
      const res = await onTest()
      setTestOk(res.success)
      if (res.success) {
        // No long adapter/model dump on the card — keep UI quiet
        setTestNote(null)
        toast.success("Test passed")
      } else {
        setTestNote(res.error || "Test failed")
        if (res.code === "not_loaded") {
          toast.message("Not loaded", { description: res.error })
        } else {
          toast.error("Test failed", { description: res.error })
        }
      }
    } catch (e) {
      setTestOk(false)
      setTestNote(String(e))
      toast.error("Test failed")
    } finally {
      setTesting(false)
    }
  }

  const handleToggle = async () => {
    if (isLoading) return
    setTestOk(null)
    setTestNote(null)

    const action: "load" | "unload" = isLoaded ? "unload" : "load"

    if (action === "load") {
      setAwaitingLoad(true)
      setLoadStartedAt(Date.now())
    }

    try {
      const res = await onToggleLoad(action)
      if (!res.success) {
        setAwaitingLoad(false)
        toast.error(res.error || "Request failed")
        return
      }

      // Trust server status only
      if (res.status === "loading") {
        setAwaitingLoad(true)
        toast.message("Loading into memory…", {
          description:
            res.message ||
            "CPU load often takes 10–60s. This card updates when ready.",
        })
        return
      }
      if (res.status === "loaded") {
        setAwaitingLoad(false)
        // Already in memory (e.g. second click) — only then show ready
        toast.success("Already in memory", { description: res.message })
        return
      }
      if (res.status === "unloaded") {
        setAwaitingLoad(false)
        toast.success("Unloaded", {
          description: res.message || "Freed from memory. Files stay on disk.",
        })
        return
      }
      // Unknown status: keep awaiting if we started a load
      if (action === "load") {
        setAwaitingLoad(true)
        toast.message("Loading…", {
          description: "Waiting for server status updates.",
        })
      } else {
        setAwaitingLoad(false)
      }
    } catch {
      setAwaitingLoad(false)
      toast.error("Load/unload failed")
    }
  }

  const handleSetDefault = async () => {
    try {
      await onSetDefault()
      // Parent shows which adapter became active
    } catch {
      toast.error("Failed to set default")
    }
  }

  const statusDot = isError || testOk === false
    ? "is-error"
    : isLoading
      ? "is-loading"
      : isLoaded
        ? "is-ready"
        : ""

  const badge = () => {
    if (isLoading) {
      return (
        <Badge variant="secondary">
          <Loader2 className="h-3 w-3 animate-spin" />
          Loading{elapsed > 0 ? ` ${elapsed}s` : "…"}
        </Badge>
      )
    }
    if (isLoaded) {
      return <Badge variant="default">In memory</Badge>
    }
    if (isError) {
      return <Badge variant="destructive">Error</Badge>
    }
    if (!isDownloaded) {
      return <Badge variant="outline">Not downloaded</Badge>
    }
    return <Badge variant="outline">On disk only</Badge>
  }

  const statusLine = (() => {
    if (isLoading) {
      return (
        loadDetail?.message ||
        `Loading into memory on CPU… ${elapsed > 0 ? `${elapsed}s elapsed` : "please wait"}`
      )
    }
    if (isError) {
      return loadDetail?.error || loadDetail?.message || "Load failed — try again"
    }
    if (isLoaded) {
      const took =
        typeof loadDetail?.load_s === "number" ? ` (loaded in ${loadDetail.load_s}s)` : ""
      return (loadDetail?.message || "Ready in memory — meetings can use it") + took
    }
    if (!isDownloaded) {
      return "Download model files first, then Load into memory"
    }
    return "Downloaded but not loaded — click Load before using or Test"
  })()

  return (
    <div className="pm-settings-provider-card">
      <div className="pm-settings-provider-top">
        <div className="pm-settings-provider-name-row">
          <span className="pm-settings-provider-name">{name}</span>
          <span className={cn("pm-settings-status-dot", statusDot)} aria-hidden />
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {isDefault && <Badge variant="default">Default</Badge>}
          {badge()}
        </div>
      </div>

      <p className="pm-settings-provider-meta">{model}</p>
      <p
        className={cn(
          "pm-settings-local-status-line",
          isError && "is-error",
          isLoading && "is-loading",
          isLoaded && "is-ok",
        )}
      >
        {statusLine}
      </p>
      {testNote && (
        <p
          className={cn(
            "pm-settings-local-status-line",
            testOk === false ? "is-error" : "is-ok",
          )}
        >
          Test: {testNote}
        </p>
      )}

      <div className="pm-settings-provider-actions">
        <Button
          variant="ghost"
          size="sm"
          onClick={handleTest}
          disabled={testing || !isLoaded || isLoading}
          title={!isLoaded ? "Load model first" : "Check that the model is in memory"}
        >
          {testing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plug className="h-3 w-3" />}
          Test
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleSetDefault}
          disabled={isDefault || !isLoaded || !isDownloaded || isLoading}
          title={!isLoaded ? "Load model before setting default" : undefined}
        >
          <Star className="h-3 w-3" />
          Default
        </Button>
        {isDownloaded ? (
          <Button
            variant={isLoaded ? "ghost" : "default"}
            size="sm"
            onClick={handleToggle}
            disabled={isLoading}
          >
            {isLoading ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Power className="h-3 w-3" />
            )}
            {isLoading ? "Loading…" : isLoaded ? "Unload" : "Load"}
          </Button>
        ) : (
          <Button variant="secondary" size="sm" onClick={onDownload}>
            <Download className="h-3 w-3" />
            Download
          </Button>
        )}
      </div>
    </div>
  )
}
