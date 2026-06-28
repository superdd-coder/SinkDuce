import { useEffect, useState, useCallback } from "react"
import { createPortal } from "react-dom"
import { useAppStore } from "@/stores/app-store"
import {
  listSessions, deleteSession,
} from "@/api/client"
import { Button } from "@/components/ui/button"
import { Plus, Trash2, PanelLeftClose, PanelLeft, MessageSquare } from "lucide-react"
import { cn } from "@/lib/utils"
import { toast } from "sonner"

export function SessionSidebar() {
  const {
    sessionId, sessions, setSessions,
    loadSessionMessages, initSession,
  } = useAppStore()
  const [collapsed, setCollapsed] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

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
      className={cn(
        "flex flex-col h-full border-r border-border/50 bg-background/60 backdrop-blur-sm shrink-0 transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] overflow-hidden",
        collapsed ? "w-10" : "w-[200px]",
      )}
    >
      {/* Header */}
      <div className={cn(
        "flex items-center border-b border-border/50 px-3 transition-all",
        collapsed ? "justify-center py-3" : "justify-between py-2.5",
      )}>
        {!collapsed && (
          <span className="text-[11px] font-[350] uppercase tracking-[0.15em] text-muted-foreground">
            Sessions
          </span>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={() => setCollapsed(!collapsed)}
        >
          {collapsed ? (
            <PanelLeft className="h-3.5 w-3.5 text-muted-foreground" />
          ) : (
            <PanelLeftClose className="h-3.5 w-3.5 text-muted-foreground" />
          )}
        </Button>
      </div>

      {/* New Session button */}
      {!collapsed && (
        <div className="px-3 pt-3 pb-2">
          <Button
            variant="outline"
            size="sm"
            className="w-full justify-start gap-2 text-[11px] font-[350] uppercase tracking-[0.1em]"
            onClick={handleNew}
          >
            <Plus className="h-3.5 w-3.5" />
            New Session
          </Button>
        </div>
      )}

      {/* Session list */}
      <div className="flex-1 overflow-y-auto">
        {sorted.length === 0 && !collapsed && (
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
              onClick={() => handleSwitch(s.id)}
              className={cn(
                "group relative px-3 py-2.5 cursor-pointer transition-colors border-l-2",
                isActive
                  ? "border-primary bg-primary/5"
                  : "border-transparent hover:bg-accent/50",
                collapsed ? "px-1.5 py-2" : "",
              )}
            >
              {/* Title */}
              <div className={cn(
                "text-[11px] font-[400] leading-snug truncate",
                isActive ? "text-foreground" : "text-muted-foreground",
                collapsed ? "text-center text-[10px]" : "",
              )}>
                {collapsed ? (
                  <MessageSquare className="h-3.5 w-3.5 mx-auto" />
                ) : (
                  s.title || "New Chat"
                )}
              </div>

              {/* Meta */}
              {!collapsed && (
                <div className="flex items-center gap-2 mt-0.5">
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
              )}

              {/* Delete button — visible on hover */}
              {!collapsed && (
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
              )}
            </div>
          )
        })}
      </div>

      {/* Bottom spacer */}
      {!collapsed && <div className="h-2 shrink-0" />}
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
