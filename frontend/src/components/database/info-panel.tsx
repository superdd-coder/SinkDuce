import { useState, useEffect, useCallback } from "react"
import { Loader2, RefreshCw } from "lucide-react"
import { toast } from "sonner"
import { TiptapEditor } from "@/components/ui/tiptap-editor"
import { cn } from "@/lib/utils"
import { useAppStore } from "@/stores/app-store"
import {
  getCollectionSummary,
  getProjectDescription,
  getCollectionConflicts,
  triggerConsolidation,
  getMeetingLog,
  getActiveCollectionTasks,
  type ConflictItem,
  type MeetingLogItem,
} from "@/api/client"
import { ConflictViewerDialog } from "./conflict-viewer-dialog"
import { NotesCard } from "./notes-card"

interface InfoPanelProps {
  collection: string
}

/* Editorial section header */
function SectionLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("text-[11px] font-normal uppercase tracking-[0.12em] mb-2.5 text-muted-foreground/80", className)}>
      {children}
    </div>
  )
}

export function InfoPanel({ collection }: InfoPanelProps) {
  const [summary, setSummary] = useState<string | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [consolidating, setConsolidating] = useState(false)
  const [consolidatingCollection, setConsolidatingCollection] = useState<string | null>(null)
  const [projectDescription, setProjectDescription] = useState<string | null>(null)
  const [conflicts, setConflicts] = useState<ConflictItem[]>([])
  const [conflictsLoading, setConflictsLoading] = useState(false)
  const [selectedConflict, setSelectedConflict] = useState<ConflictItem | null>(null)
  const [meetings, setMeetings] = useState<MeetingLogItem[]>([])
  const [meetingsLoading, setMeetingsLoading] = useState(false)
  const [notesCount, setNotesCount] = useState(0)
  const [ingestedNotesCount, setIngestedNotesCount] = useState(0)
  const [docCount, setDocCount] = useState(0)

  const { setSidebarView, setActiveMeeting, setPendingOpenFile, collections } = useAppStore()

  const collectionName = collections.find(c => c.id === collection)?.name || collection

  useEffect(() => {
    setSummary(null)
    setProjectDescription(null)
    setConflicts([])
    setMeetings([])
    setConsolidating(false)
    setConsolidatingCollection(null)
    setSelectedConflict(null)
    getActiveCollectionTasks(collection).then((res) => {
      if (res.consolidating) {
        setConsolidating(true)
        setConsolidatingCollection(collection)
      }
    }).catch(() => {})
  }, [collection])

  useEffect(() => {
    if (!consolidating || consolidatingCollection !== collection) return
    const poll = setInterval(async () => {
      try {
        const res = await getActiveCollectionTasks(collection)
        if (!res.consolidating) {
          clearInterval(poll)
          setConsolidating(false)
          setConsolidatingCollection(null)
          fetchSummary()
          fetchProjectDescription()
          fetchConflicts()
          fetchMeetings()
        }
      } catch { /* ignore */ }
    }, 2000)
    return () => clearInterval(poll)
  }, [consolidating, consolidatingCollection, collection])

  const fetchSummary = useCallback(async () => {
    if (!collection) return
    setSummaryLoading(true)
    try {
      const res = await getCollectionSummary(collection)
      setSummary(res?.content ?? null)
    } catch {
      setSummary(null)
    } finally {
      setSummaryLoading(false)
    }
  }, [collection])

  const fetchConflicts = useCallback(async () => {
    if (!collection) return
    setConflictsLoading(true)
    try {
      const res = await getCollectionConflicts(collection)
      setConflicts(res.conflicts ?? [])
    } catch {
      setConflicts([])
    } finally {
      setConflictsLoading(false)
    }
  }, [collection])

  const fetchMeetings = useCallback(async () => {
    if (!collection) return
    setMeetingsLoading(true)
    try {
      const res = await getMeetingLog(collection)
      setMeetings(res.meetings ?? [])
    } catch {
      setMeetings([])
    } finally {
      setMeetingsLoading(false)
    }
  }, [collection])

  useEffect(() => {
    fetchSummary()
    fetchProjectDescription()
    fetchConflicts()
    fetchMeetings()
    // Fetch stats
    if (collection) {
      import("@/api/client").then(({ getNotes, getFiles }) => {
        getNotes(collection).then(r => {
          setNotesCount(r.notes?.length ?? 0)
          setIngestedNotesCount(r.notes?.filter(n => n.is_ingested).length ?? 0)
        }).catch(() => { setNotesCount(0); setIngestedNotesCount(0) })
        getFiles(collection).then(r => setDocCount(r.files?.length ?? 0)).catch(() => setDocCount(0))
      })
    }
  }, [fetchSummary, fetchConflicts, fetchMeetings])

  const fetchProjectDescription = useCallback(async () => {
    if (!collection) return
    try {
      const res = await getProjectDescription(collection)
      setProjectDescription(res?.content ?? null)
    } catch {
      setProjectDescription(null)
    }
  }, [collection])

  const handleConsolidate = async () => {
    setConsolidating(true)
    setConsolidatingCollection(collection)
    try {
      const res = await triggerConsolidation(collection)
      toast.info(`Consolidation started for ${collectionName}...`)
      const taskId = res.task?.id
      const targetCollection = collection
      if (taskId) {
        const poll = setInterval(async () => {
          try {
            const { getTask } = await import("@/api/client")
            const task = await getTask(taskId)
            if (task.status === "completed" || task.status === "failed") {
              clearInterval(poll)
              setConsolidating(false)
              setConsolidatingCollection(null)
              if (task.status === "completed") {
                toast.success(`Consolidation complete for ${collectionName}`)
                if (collection === targetCollection) {
                  fetchSummary()
                  fetchProjectDescription()
                  fetchConflicts()
                  fetchMeetings()
                }
              } else {
                toast.error(`Consolidation failed: ${task.error || "unknown error"}`)
              }
            }
          } catch { /* ignore */ }
        }, 2000)
      } else {
        setConsolidating(false)
        setConsolidatingCollection(null)
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      toast.error(msg || "Consolidation failed")
      setConsolidating(false)
      setConsolidatingCollection(null)
    }
  }

  const handleMeetingClick = (meeting: MeetingLogItem) => {
    setActiveMeeting(meeting.id)
    setSidebarView("meeting")
  }

  const formatDate = (dateStr: string) => {
    try {
      return new Date(dateStr).toLocaleDateString(undefined, {
        year: "numeric", month: "short", day: "numeric",
      })
    } catch {
      return dateStr
    }
  }

  return (
    <div className="space-y-8">
      {/* Stats row + Consolidate action */}
      <div className="flex items-end justify-between pb-5 border-b border-dashed border-border">
        <div className="flex gap-10">
          <div className="flex flex-col">
            <span className="text-[28px] font-light leading-none text-foreground t-body-family">{docCount}</span>
            <span className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80 mt-1.5">Documents</span>
          </div>
          <div className="flex flex-col">
            <span className="text-[28px] font-light leading-none text-foreground t-body-family">{meetings.length > 0 || meetingsLoading ? meetings.length : "—"}</span>
            <span className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80 mt-1.5">Meetings</span>
          </div>
          <div className="flex flex-col">
            <span className="text-[28px] font-light leading-none text-foreground t-body-family">
              {notesCount > 0 ? `${ingestedNotesCount}/${notesCount}` : "—"}
            </span>
            <span className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80 mt-1.5">
              Notes{ingestedNotesCount > 0 ? ` · ${ingestedNotesCount} ingested` : ""}
            </span>
          </div>
          <div className="flex flex-col">
            <span className="text-[28px] font-light leading-none text-foreground t-body-family">{conflicts.length}</span>
            <span className="text-[11px] font-normal uppercase tracking-[0.12em] text-muted-foreground/80 mt-1.5">Conflicts</span>
          </div>
        </div>

        <button
          type="button"
          onClick={handleConsolidate}
          disabled={consolidating && consolidatingCollection === collection}
          className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.1em] cursor-pointer transition-opacity hover:opacity-80 bg-primary text-primary-foreground border-none"
          style={{
            padding: "4px 10px",
            borderRadius: "2px",
          }}
        >
          {consolidating ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
          Consolidate
        </button>
      </div>

      {/* Project Description */}
      {consolidating ? (
        <div className="flex items-center gap-2 text-sm italic text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Updating…
        </div>
      ) : projectDescription && (
        <div
          className="text-sm leading-[1.8] pl-4 border-l italic text-foreground border-border t-body-italic-family"
        >
          {projectDescription}
        </div>
      )}

      {/* Summary */}
      <div>
        <SectionLabel className="mb-2.5">Summary</SectionLabel>

        {consolidating ? (
          <div className="flex items-center justify-center py-8 gap-2 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span className="text-sm">Consolidating…</span>
          </div>
        ) : summaryLoading ? (
          <div className="flex items-center justify-center py-8 gap-2 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span className="text-sm">Loading…</span>
          </div>
        ) : summary ? (
          <div className="pl-4 border-l border-border">
            <TiptapEditor value={summary} readonly showToolbar={false} />
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">No summary yet. Upload files and consolidate.</p>
        )}
      </div>

      {/* Conflicts */}
      {conflicts.length > 0 && (
        <div>
          <SectionLabel className="!text-amber-600">
            ⚠ Conflicts · {conflicts.length}
          </SectionLabel>
          {conflictsLoading ? (
            <div className="flex items-center gap-2 py-4 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span className="text-xs">Loading…</span>
            </div>
          ) : (
            <div>
              {conflicts.map((conflict, i) => (
                <button
                  key={i}
                  type="button"
                  className="w-full text-left py-2.5 border-b cursor-pointer transition-opacity hover:opacity-80 border-b border-dashed border-border"
                  style={{ background: "none", borderLeft: "none", borderRight: "none", borderTop: "none" }}
                  onClick={() => setSelectedConflict(conflict)}
                >
                  <div className="text-xs leading-relaxed text-foreground">
                    <span style={{ color: "#B45309" }}>{conflict.content1}</span>
                    <span className="text-muted-foreground"> ({conflict.source1})</span>
                    <span className="text-muted-foreground" style={{ margin: "0 6px" }}>vs</span>
                    <span style={{ color: "#B45309" }}>{conflict.content2}</span>
                    <span className="text-muted-foreground"> ({conflict.source2})</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Notes */}
      <NotesCard collection={collection} />

      {/* Meeting Log */}
      {meetings.length > 0 && (
        <div>
          <SectionLabel>Meeting Log · {meetings.length}</SectionLabel>
          {meetingsLoading ? (
            <div className="flex items-center gap-2 py-4 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span className="text-xs">Loading…</span>
            </div>
          ) : (
            <div>
              {meetings.map((meeting) => (
                <div
                  key={meeting.id}
                  className="py-2.5 border-b border-b border-dashed border-border"
                >
                  <button
                    type="button"
                    className="w-full text-left flex items-center gap-3 cursor-pointer transition-opacity hover:opacity-80 text-foreground"
                    style={{ background: "none", border: "none" }}
                    onClick={() => handleMeetingClick(meeting)}
                  >
                    <span className="text-xs flex-1 truncate">{meeting.title}</span>
                    <span className="text-[10px] shrink-0 text-muted-foreground">
                      {formatDate(meeting.created_at)}
                    </span>
                  </button>
                  {meeting.file_ids && meeting.file_ids.length > 0 && (
                    <div className="ml-4 mt-1">
                      {meeting.file_ids.map((fid) => (
                        <button
                          key={fid}
                          type="button"
                          className="block text-[11px] truncate w-full text-left cursor-pointer transition-colors text-muted-foreground"
                          style={{ background: "none", border: "none" }}
                          onClick={() => setPendingOpenFile(fid)}
                        >
                          {meeting.file_labels?.[fid] || fid}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <ConflictViewerDialog
        conflict={selectedConflict}
        collection={collection}
        onOpenChange={(v) => !v && setSelectedConflict(null)}
      />
    </div>
  )
}
