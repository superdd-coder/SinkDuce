import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { X, Trash2, Terminal } from "lucide-react"

interface LogEntry {
  time: number
  level: string
  logger: string
  message: string
  exception?: string
}

const levelColor: Record<string, string> = {
  DEBUG: "text-muted-foreground",
  INFO: "text-foreground",
  WARNING: "text-yellow-500",
  ERROR: "text-red-500",
  CRITICAL: "text-red-400 font-bold",
}

export function LogViewer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [autoScroll, setAutoScroll] = useState(true)
  const scrollRef = useRef<HTMLDivElement>(null)
  const esRef = useRef<EventSource | null>(null)

  // Load recent logs + start SSE stream with reconnect
  useEffect(() => {
    if (!open) return

    let retryDelay = 1000
    const maxRetryDelay = 30000
    let disposed = false

    const onMessage = (e: MessageEvent) => {
      try {
        const entry = JSON.parse(e.data) as LogEntry
        setLogs((prev) => [...prev.slice(-499), entry])
      } catch { /* ignore */ }
    }

    const connect = () => {
      if (disposed) return
      const es = new EventSource("/api/logs/stream")
      esRef.current = es
      es.onmessage = onMessage
      es.onopen = () => { retryDelay = 1000 }
      es.onerror = () => {
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
      esRef.current?.close()
      esRef.current = null
    }
  }, [open])

  // Auto-scroll
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [logs, autoScroll])

  if (!open) return null

  const formatTime = (t: number) => {
    const d = new Date(t * 1000)
    return d.toLocaleTimeString("en-GB", { hour12: false })
  }

  return (
    <div className="border-t border-border bg-background flex flex-col" style={{ height: 280 }}>
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border bg-muted/50 shrink-0">
        <Terminal className="h-4 w-4 text-muted-foreground" />
        <span className="text-xs font-medium flex-1">Backend Logs ({logs.length})</span>
        <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setLogs([])}>
          <Trash2 className="h-3 w-3" />
        </Button>
        <Button variant="ghost" size="icon" className="h-6 w-6" onClick={onClose}>
          <X className="h-3 w-3" />
        </Button>
      </div>
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto overflow-x-hidden t-mono-family text-xs leading-relaxed p-2 space-y-0.5"
        onScroll={(e) => {
          const el = e.currentTarget
          setAutoScroll(el.scrollHeight - el.scrollTop - el.clientHeight < 40)
        }}
      >
        {logs.map((log, i) => (
          <div key={i} className="flex gap-2 whitespace-pre-wrap break-all">
            <span className="text-muted-foreground shrink-0 w-20">{formatTime(log.time)}</span>
            <span className={`shrink-0 w-16 ${levelColor[log.level] || "text-foreground"}`}>
              {log.level}
            </span>
            <span className="text-foreground flex-1 min-w-0">{log.message}</span>
          </div>
        ))}
        {logs.length === 0 && (
          <div className="text-muted-foreground text-center py-8">Waiting for logs...</div>
        )}
      </div>
    </div>
  )
}
