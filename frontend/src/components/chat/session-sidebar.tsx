import { useEffect, useState, useCallback, useRef } from "react"
import { createPortal } from "react-dom"
import { useAppStore } from "@/stores/app-store"
import {
  listSessions, deleteSession,
} from "@/api/client"
import { Button } from "@/components/ui/button"
import { Plus, Trash2, MessageSquare } from "lucide-react"
import { cn } from "@/lib/utils"
import { toast } from "sonner"

export function SessionSidebar() {
  const {
    sessionId, sessions, setSessions,
    loadSessionMessages, initSession,
  } = useAppStore()
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  /* Sliding active-session indicator — a single 2px green bar that moves to
     the currently active session via transform. This replaces per-item
     `border-l-2` so the transition is a true slide, not per-row fade in/out. */
  const listRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const [indicator, setIndicator] = useState({ top: 0, height: 0 })

  useEffect(() => {
    if (!sessionId) return
    const activeEl = itemRefs.current.get(sessionId)
    const listEl = listRef.current
    if (!activeEl || !listEl) return
    const activeRect = activeEl.getBoundingClientRect()
    const listRect = listEl.getBoundingClientRect()
    setIndicator({
      top: activeRect.top - listRect.top + listEl.scrollTop,
      height: activeRect.height,
    })
  }, [sessionId, sessions])

  // Load session list on mount
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
      // Session deleted — auto-create a new one
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await deleteSession(id)
      if (id === sessionId) {
        // Don't auto-create — clear session; next message will create one
        useAppStore.getState().setSessionId(null)
        useAppStore.setState({ messages: [] })
      }
      await refreshList()
      toast.success("Session deleted")
    } catch (err) {
      toast.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`)
    }
    setConfirmDelete(null)
  }

  // Auto-refresh list after any session change
  useEffect(() => {
    if (sessionId) {
      const inList = sessions.some(s => s.id === sessionId)
      if (!inList) refreshList()
    }
  }, [sessionId, sessions, refreshList])

  // Sort: newest first
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

  const sidebar = (
    <aside
      className="flex flex-col h-full w-[200px] border-r border-border/50 bg-background/60 backdrop-blur-sm shrink-0"
    >
      {/* Header — fixed h-12 (48px) so its bottom border aligns with the
          Collections header in collection-list and the session title
          header in chat-view. */}
      <div className="flex items-center border-b border-border/50 px-3 h-12">
        <span
          className="uppercase"
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: "14px",
            fontWeight: 300,
            letterSpacing: "-0.015em",
            color: "var(--muted-foreground)",
          }}
        >
          Sessions
        </span>
      </div>

      {/* New Chat button — AI-COMP-001 btn-default (primary green) */}
      <div className="px-3 pt-3 pb-2">
        <button
          type="button"
          onClick={handleNew}
          className="w-full flex items-center justify-center gap-2 font-sans sk-send-btn"
          style={{
            fontSize: "10px",
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.12em",
            padding: "5px 14px",
            borderRadius: "2px",
          }}
        >
          <Plus className="h-3.5 w-3.5" />
          New Chat
        </button>
      </div>

      {/* Session list */}
      <div ref={listRef} className="flex-1 overflow-y-auto relative">
        {/* Sliding active indicator — 2px green bar that slides between sessions */}
        {sessionId && (
          <div
            className="absolute left-0 w-[2px] bg-primary pointer-events-none z-10 transition-all duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]"
            style={{ top: indicator.top, height: indicator.height }}
          />
        )}

        {sorted.length === 0 && (
          <div className="px-4 py-8 text-center">
            <MessageSquare className="h-5 w-5 mx-auto mb-2 text-muted-foreground/30" />
            <p className="text-[10px] text-muted-foreground/50">
              No sessions yet
            </p>
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
              onClick={() => handleSwitch(s.id)}
              className={cn(
                "group relative px-3 py-2.5 cursor-pointer transition-colors",
                isActive
                  ? "bg-primary/5"
                  : "hover:bg-accent/50",
              )}
            >
              {/* Title — gains right padding on hover so it never runs under the trash icon */}
              <div
                className={cn(
                  "text-[11px] font-[400] leading-snug truncate transition-[padding] duration-200 group-hover:pr-6",
                  isActive ? "text-foreground" : "text-muted-foreground",
                )}
              >
                {s.title || "New Chat"}
              </div>

              {/* Meta — fades out on hover to make room for the delete button */}
              <div className="flex items-center gap-2 mt-0.5 group-hover:opacity-0 transition-opacity duration-200">
                <span className="text-[9px] text-muted-foreground/50">
                  {s.message_count} msg{s.message_count !== 1 ? "s" : ""}
                </span>
                {s.last_message && (
                  <span className="text-[9px] text-muted-foreground/40 truncate flex-1">
                    {s.last_message.slice(0, 40)}
                  </span>
                )}
                <span className="text-[9px] text-muted-foreground/30 ml-auto shrink-0">
                  {formatTime(s.updated_at)}
                </span>
              </div>

              {/* Delete button — visible on hover, meta fades out so no overlap */}
              <button
                type="button"
                className={cn(
                  "absolute right-2 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity",
                  "hover:text-red-500 text-muted-foreground/60",
                )}
                onClick={(e) => { e.stopPropagation(); setConfirmDelete(s.id) }}
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          )
        })}
      </div>

      {/* Bottom spacer */}
      <div className="h-2 shrink-0" />
    </aside>
  )

  // Delete confirmation portal
  const confirmPortal = confirmDelete && createPortal(
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/20" onClick={() => setConfirmDelete(null)}>
      <div
        className="rounded border border-border bg-popover p-4 shadow-xl max-w-[240px]"
        onClick={(e) => e.stopPropagation()}
      >
        <p className="text-[12px] mb-3">Delete this session? Messages will be lost.</p>
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" className="text-[10px]" onClick={() => setConfirmDelete(null)}>
            Cancel
          </Button>
          <Button variant="default" size="sm" className="text-[10px]" onClick={() => handleDelete(confirmDelete)}>
            Delete
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  )

  return (
    <>
      {sidebar}
      {confirmPortal}
    </>
  )
}
