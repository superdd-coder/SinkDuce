import { useEffect, useState, useCallback, useRef } from "react"
import { useShallow } from "zustand/react/shallow"
import { useAppStore } from "@/stores/app-store"
import {
  listSessions, deleteSession,
} from "@/api/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Trash2, MessageSquare } from "lucide-react"
import { cn } from "@/lib/utils"
import { toast } from "sonner"
import { useScrollEdgeFade } from "@/hooks/use-scroll-edge-fade"

export function SessionSidebar() {
  const {
    sessionId, sessions, setSessions,
    loadSessionMessages, initSession,
  } = useAppStore(
    useShallow((s) => ({
      sessionId: s.sessionId,
      sessions: s.sessions,
      setSessions: s.setSessions,
      loadSessionMessages: s.loadSessionMessages,
      initSession: s.initSession,
    }))
  )
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  /* Sliding mint indicator — one pill moves to the active session (Collections language). */
  const listRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const [indicator, setIndicator] = useState({ top: 0, height: 0 })
  const [indicatorReady, setIndicatorReady] = useState(false)

  useEffect(() => {
    if (!sessionId) {
      setIndicatorReady(false)
      return
    }
    const activeEl = itemRefs.current.get(sessionId)
    const listEl = listRef.current
    if (!activeEl || !listEl) return
    const activeRect = activeEl.getBoundingClientRect()
    const listRect = listEl.getBoundingClientRect()
    setIndicator({
      top: activeRect.top - listRect.top + listEl.scrollTop,
      height: activeRect.height,
    })
    // First place hard; subsequent moves slide
    requestAnimationFrame(() => setIndicatorReady(true))
  }, [sessionId, sessions])

  useEffect(() => {
    listSessions()
      .then(setSessions)
      .catch(() => {})
  }, [setSessions])

  const refreshList = useCallback(async () => {
    try { setSessions(await listSessions()) } catch { /* ignore */ }
  }, [setSessions])

  const handleNew = async () => {
    try {
      await initSession()
      await refreshList()
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleSwitch = async (id: string) => {
    if (id === sessionId) return
    try {
      await loadSessionMessages(id)
    } catch {
      // Session deleted — leave empty; next send creates one
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await deleteSession(id)
      if (id === sessionId) {
        useAppStore.getState().setSessionId(null)
      }
      await refreshList()
      toast.success("Session deleted")
    } catch (err) {
      toast.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`)
    }
    setConfirmDelete(null)
  }

  useEffect(() => {
    if (sessionId) {
      const inList = sessions.some(s => s.id === sessionId)
      if (!inList) refreshList()
    }
  }, [sessionId, sessions, refreshList])

  // Latest chat activity first (session.updated_at bumps on each message)
  const sorted = [...sessions].sort(
    (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
  )

  const formatTime = (iso: string) => {
    const d = new Date(iso)
    const now = new Date()
    const diff = now.getTime() - d.getTime()
    if (diff < 60_000) return "just now"
    if (diff < 3600_000) return `${Math.floor(diff / 60_000)}m ago`
    if (diff < 86_400_000) return `${Math.floor(diff / 3600_000)}h ago`
    return d.toLocaleDateString()
  }

  const edgeFade = useScrollEdgeFade(listRef, sorted.length)

  return (
    <>
      <aside className="pm-chat-sessions" aria-label="Sessions">
        <div className="pm-chat-sessions-surface">
          <div className="pm-chat-sessions-head pm-rail-head">
            <h2 className="pm-chat-sessions-title pm-rail-title">Sessions</h2>
            <button
              type="button"
              className="pm-rail-new"
              onClick={handleNew}
              title="New chat"
            >
              New
            </button>
          </div>

          <div className="pm-rail-list-shell">
            <div ref={listRef} className="pm-chat-sessions-list">
              {sessionId && (
                <div
                  className={cn(
                    "pm-chat-sess-indicator",
                    indicatorReady && "is-ready",
                  )}
                  style={{
                    transform: `translateY(${indicator.top}px)`,
                    height: indicator.height,
                  }}
                  aria-hidden
                />
              )}

              {sorted.length === 0 && (
                <div className="pm-chat-sess-empty">
                  <MessageSquare className="size-5 pm-chat-sess-empty-icon" />
                  <p className="pm-meta">No sessions yet</p>
                </div>
              )}

              {sorted.map((s) => {
                const isActive = s.id === sessionId
                return (
                  <div
                    key={s.id}
                    ref={(el) => {
                      if (el) itemRefs.current.set(s.id, el)
                      else itemRefs.current.delete(s.id)
                    }}
                    role="button"
                    tabIndex={0}
                    onClick={() => handleSwitch(s.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault()
                        handleSwitch(s.id)
                      }
                    }}
                    className={cn("pm-chat-sess-row", isActive && "is-active")}
                  >
                    <div className="pm-chat-sess-name">
                      {s.title || "New Chat"}
                    </div>
                    <div className="pm-chat-sess-meta">
                      {s.last_message ? (
                        <span className="pm-chat-sess-meta-snip">
                          {s.last_message.slice(0, 48)}
                        </span>
                      ) : (
                        <span className="pm-chat-sess-meta-snip">New conversation</span>
                      )}
                      <span className="pm-chat-sess-meta-time">
                        {formatTime(s.updated_at)}
                      </span>
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      className="pm-chat-sess-del"
                      title="Delete session"
                      aria-label="Delete session"
                      onClick={(e) => {
                        e.stopPropagation()
                        setConfirmDelete(s.id)
                      }}
                    >
                      <Trash2 className="size-3" />
                    </Button>
                  </div>
                )
              })}
            </div>
            <div
              className={cn(
                "pm-rail-edge-fade pm-rail-edge-fade--top",
                edgeFade.top && "is-visible",
              )}
              aria-hidden
            />
            <div
              className={cn(
                "pm-rail-edge-fade pm-rail-edge-fade--bottom",
                edgeFade.bottom && "is-visible",
              )}
              aria-hidden
            />
          </div>
        </div>
      </aside>

      <Dialog
        open={!!confirmDelete}
        onOpenChange={(open) => {
          if (!open) setConfirmDelete(null)
        }}
      >
        <DialogContent
          className="pm-dialog sm:max-w-[280px]"
          showCloseButton={false}
          overlayClassName="pm-dialog-overlay--silk"
        >
          <DialogHeader>
            <DialogTitle>Delete session?</DialogTitle>
            <DialogDescription>
              Messages in this session will be lost.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setConfirmDelete(null)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive-solid"
              size="sm"
              onClick={() => confirmDelete && handleDelete(confirmDelete)}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
