import { useEffect, useRef, useState } from "react"
import { ArrowDownToLine, Terminal, Trash2, X } from "lucide-react"
import { cn } from "@/lib/utils"

interface LogEntry {
  time: number
  level: string
  logger: string
  message: string
  exception?: string
}

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const

export function LogViewer({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [autoScroll, setAutoScroll] = useState(true)
  const [live, setLive] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!open) return

    let retryDelay = 1000
    const maxRetryDelay = 30000
    let disposed = false

    const onMessage = (e: MessageEvent) => {
      try {
        const entry = JSON.parse(e.data) as LogEntry
        setLogs((prev) => [...prev.slice(-499), entry])
      } catch {
        /* ignore */
      }
    }

    const connect = () => {
      if (disposed) return
      const es = new EventSource("/api/logs/stream")
      esRef.current = es
      es.onmessage = onMessage
      es.onopen = () => {
        retryDelay = 1000
        setLive(true)
      }
      es.onerror = () => {
        setLive(false)
        es.close()
        if (disposed) return
        retryDelay = Math.min(retryDelay * 2, maxRetryDelay)
        setTimeout(connect, retryDelay)
      }
    }

    fetch("/api/logs?limit=200")
      .then((r) => r.json())
      .then((d) => setLogs(d.logs || []))
      .catch(() => {})

    connect()

    return () => {
      disposed = true
      setLive(false)
      esRef.current?.close()
      esRef.current = null
    }
  }, [open])

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [logs, autoScroll])

  const formatTime = (t: number) => {
    const d = new Date(t * 1000)
    return d.toLocaleTimeString("en-GB", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })
  }

  const shortLogger = (name: string) => {
    if (!name) return "app"
    const parts = name.split(".")
    return parts[parts.length - 1] || name
  }

  const levelClass = (level: string) => {
    const L = (level || "INFO").toUpperCase()
    if (L === "DEBUG") return "is-debug"
    if (L === "WARNING" || L === "WARN") return "is-warn"
    if (L === "ERROR") return "is-error"
    if (L === "CRITICAL") return "is-critical"
    return "is-info"
  }

  return (
    <div className="pm-backend-logs">
      {/* Header chrome */}
      <header className="pm-backend-logs-head">
        <div className="pm-backend-logs-head-left">
          <span className="pm-backend-logs-gem" aria-hidden>
            <Terminal className="h-3.5 w-3.5" strokeWidth={1.75} />
          </span>
          <div className="pm-backend-logs-titles">
            <span className="pm-backend-logs-kicker">Developer</span>
            <h2 className="pm-backend-logs-title">Backend Logs</h2>
          </div>
          <span className="pm-backend-logs-count" title="Buffered lines">
            {logs.length}
          </span>
          <span
            className={cn(
              "pm-backend-logs-live",
              live ? "is-on" : "is-off"
            )}
            title={live ? "SSE connected" : "Reconnecting…"}
          >
            <span className="pm-backend-logs-live-dot" aria-hidden />
            {live ? "Live" : "Idle"}
          </span>
        </div>

        <div className="pm-backend-logs-head-actions">
          <button
            type="button"
            className={cn(
              "pm-backend-logs-tool",
              autoScroll && "is-active"
            )}
            onClick={() => setAutoScroll((v) => !v)}
            title={
              autoScroll
                ? "Auto-scroll on — click to pause"
                : "Auto-scroll off — click to resume"
            }
          >
            <ArrowDownToLine className="h-3.5 w-3.5" strokeWidth={1.75} />
            <span>Follow</span>
          </button>
          <button
            type="button"
            className="pm-backend-logs-tool"
            onClick={() => setLogs([])}
            title="Clear buffer"
          >
            <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} />
            <span>Clear</span>
          </button>
          <button
            type="button"
            className="pm-backend-logs-tool is-close"
            onClick={onClose}
            title="Close"
          >
            <X className="h-3.5 w-3.5" strokeWidth={1.75} />
          </button>
        </div>
      </header>

      {/* Stream body */}
      <div
        ref={scrollRef}
        className="pm-backend-logs-stream"
        onScroll={(e) => {
          const el = e.currentTarget
          setAutoScroll(
            el.scrollHeight - el.scrollTop - el.clientHeight < 48
          )
        }}
      >
        {logs.length === 0 ? (
          <div className="pm-backend-logs-empty">
            <Terminal className="h-5 w-5 opacity-40" strokeWidth={1.5} />
            <p>Waiting for backend output…</p>
            <span>Stream opens when the panel is visible</span>
          </div>
        ) : (
          <div className="pm-backend-logs-lines">
            {logs.map((log, i) => {
              const level = (log.level || "INFO").toUpperCase()
              return (
                <div
                  key={`${log.time}-${i}`}
                  className={cn(
                    "pm-backend-logs-row",
                    levelClass(level)
                  )}
                >
                  <span className="pm-backend-logs-time">
                    {formatTime(log.time)}
                  </span>
                  <span
                    className={cn(
                      "pm-backend-logs-level",
                      levelClass(level)
                    )}
                  >
                    {LEVELS.includes(level as (typeof LEVELS)[number])
                      ? level === "WARNING"
                        ? "WARN"
                        : level.slice(0, 5)
                      : level.slice(0, 5)}
                  </span>
                  <span
                    className="pm-backend-logs-logger"
                    title={log.logger}
                  >
                    {shortLogger(log.logger)}
                  </span>
                  <span className="pm-backend-logs-msg">
                    {log.message}
                    {log.exception ? (
                      <pre className="pm-backend-logs-exc">
                        {log.exception}
                      </pre>
                    ) : null}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
