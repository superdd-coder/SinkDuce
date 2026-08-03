import { useState, useEffect, useCallback, useRef } from "react"
import { ChevronDown, ChevronRight, Loader2, RefreshCw, Star } from "lucide-react"
import { toast } from "sonner"
import { TiptapEditor } from "@/components/ui/tiptap-editor"
import { cn } from "@/lib/utils"
import { onInfoRefresh, triggerInfoRefresh } from "@/lib/info-refresh"
import { useAppStore } from "@/stores/app-store"
import {
  getCollectionSummary,
  getProjectDescription,
  getCollectionConflicts,
  triggerConsolidation,
  getMeetingLog,
  getActiveCollectionTasks,
  getNotes,
  getFiles,
  type ConflictItem,
  type MeetingLogItem,
} from "@/api/client"
import { getDefinitiveFiles, updateFile } from "@/api/file-mgmt"
import type { FileSummary } from "@/types/file-mgmt"
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
  /** Only true on first load / collection switch — never on silent hot-refresh. */
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [consolidating, setConsolidating] = useState(false)
  const [projectDescription, setProjectDescription] = useState<string | null>(null)
  const [conflicts, setConflicts] = useState<ConflictItem[]>([])
  const [meetings, setMeetings] = useState<MeetingLogItem[]>([])
  const [notesCount, setNotesCount] = useState(0)
  const [ingestedNotesCount, setIngestedNotesCount] = useState(0)
  const [docCount, setDocCount] = useState(0)
  const [definitiveFiles, setDefinitiveFiles] = useState<FileSummary[]>([])
  const [definitiveLoading, setDefinitiveLoading] = useState(false)
  /** Collapsed by default — sources used for Collection Summary. */
  const [definitiveOpen, setDefinitiveOpen] = useState(false)
  const [clearingDefinitiveId, setClearingDefinitiveId] = useState<string | null>(null)
  const [selectedConflict, setSelectedConflict] = useState<ConflictItem | null>(null)

  const { setSidebarView, setActiveMeeting, setPendingOpenFile, collections } = useAppStore()
  const collectionName = collections.find(c => c.id === collection)?.name || collection

  // Track activity edges so we only silent-refresh when work finishes
  const wasConsolidatingRef = useRef(false)
  const wasBusyRef = useRef(false)
  const hasLoadedRef = useRef(false)
  const collectionRef = useRef(collection)
  collectionRef.current = collection

  // ── Silent / loud fetchers ─────────────────────────────────

  const fetchDefinitiveFiles = useCallback(async (opts?: { silent?: boolean }) => {
    if (!collection) return
    const silent = opts?.silent && hasLoadedRef.current
    if (!silent) setDefinitiveLoading(true)
    try {
      const files = await getDefinitiveFiles(collection)
      if (collectionRef.current !== collection) return
      setDefinitiveFiles(files ?? [])
    } catch {
      if (collectionRef.current !== collection) return
      setDefinitiveFiles([])
    } finally {
      if (!silent) setDefinitiveLoading(false)
    }
  }, [collection])

  const fetchSummary = useCallback(async (opts?: { silent?: boolean }) => {
    if (!collection) return
    const silent = opts?.silent && hasLoadedRef.current
    if (!silent) setSummaryLoading(true)
    try {
      const res = await getCollectionSummary(collection)
      if (collectionRef.current !== collection) return
      setSummary(res?.content ?? null)
    } catch {
      if (collectionRef.current !== collection) return
      setSummary(null)
    } finally {
      if (!silent) setSummaryLoading(false)
    }
  }, [collection])

  const fetchConflicts = useCallback(async () => {
    if (!collection) return
    try {
      const res = await getCollectionConflicts(collection)
      if (collectionRef.current !== collection) return
      setConflicts(res.conflicts ?? [])
    } catch {
      if (collectionRef.current !== collection) return
      setConflicts([])
    }
  }, [collection])

  const fetchMeetings = useCallback(async () => {
    if (!collection) return
    try {
      const res = await getMeetingLog(collection)
      if (collectionRef.current !== collection) return
      setMeetings(res.meetings ?? [])
    } catch {
      if (collectionRef.current !== collection) return
      setMeetings([])
    }
  }, [collection])

  const fetchProjectDescription = useCallback(async () => {
    if (!collection) return
    try {
      const res = await getProjectDescription(collection)
      if (collectionRef.current !== collection) return
      setProjectDescription(res?.content ?? null)
    } catch {
      if (collectionRef.current !== collection) return
      setProjectDescription(null)
    }
  }, [collection])

  const fetchStats = useCallback(async () => {
    if (!collection) return
    try {
      const [notesRes, filesRes] = await Promise.all([
        getNotes(collection).catch(() => ({ notes: [] as { is_ingested?: boolean }[] })),
        getFiles(collection).catch(() => ({ files: [] as unknown[] })),
      ])
      if (collectionRef.current !== collection) return
      const notes = notesRes.notes ?? []
      setNotesCount(notes.length)
      setIngestedNotesCount(notes.filter((n) => n.is_ingested).length)
      setDocCount(filesRes.files?.length ?? 0)
    } catch {
      /* ignore */
    }
  }, [collection])

  /** Full panel refresh — silent keeps existing UI (no skeleton flash). */
  const refreshAll = useCallback(async (opts?: { silent?: boolean }) => {
    const silent = opts?.silent ?? false
    await Promise.all([
      fetchSummary({ silent }),
      fetchProjectDescription(),
      fetchConflicts(),
      fetchMeetings(),
      fetchDefinitiveFiles({ silent }),
      fetchStats(),
    ])
    hasLoadedRef.current = true
  }, [
    fetchSummary,
    fetchProjectDescription,
    fetchConflicts,
    fetchMeetings,
    fetchDefinitiveFiles,
    fetchStats,
  ])

  // ── Collection switch: reset + initial load ────────────────

  useEffect(() => {
    hasLoadedRef.current = false
    wasConsolidatingRef.current = false
    wasBusyRef.current = false
    setSummary(null)
    setProjectDescription(null)
    setConflicts([])
    setMeetings([])
    setDefinitiveFiles([])
    setDefinitiveOpen(false)
    setConsolidating(false)
    setSelectedConflict(null)
    setNotesCount(0)
    setIngestedNotesCount(0)
    setDocCount(0)

    let cancelled = false
    ;(async () => {
      try {
        const res = await getActiveCollectionTasks(collection)
        if (cancelled) return
        if (res.consolidating) {
          setConsolidating(true)
          wasConsolidatingRef.current = true
        }
      } catch { /* ignore */ }
      if (!cancelled) await refreshAll({ silent: false })
    })()

    return () => { cancelled = true }
    // Only re-run on collection change — refreshAll is stable enough via deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collection])

  // ── Background poll: active tasks → silent refresh on edges ──

  useEffect(() => {
    if (!collection) return

    let cancelled = false

    const tick = async () => {
      try {
        const res = await getActiveCollectionTasks(collection)
        if (cancelled || collectionRef.current !== collection) return

        const consolidatingNow = !!res.consolidating
        const busyNow =
          consolidatingNow ||
          !!res.uploading ||
          (res.active_tasks ?? []).some(
            (t) =>
              t.task_type === "doc_summary" ||
              t.task_type === "upload" ||
              t.task_type === "consolidate"
          )

        // Enter consolidating: soft badge only (keep old summary visible)
        if (consolidatingNow && !wasConsolidatingRef.current) {
          setConsolidating(true)
        }

        // Consolidate finished → silent pull new summary/conflicts
        if (!consolidatingNow && wasConsolidatingRef.current) {
          setConsolidating(false)
          await refreshAll({ silent: true })
        }

        // Any busy work finished (upload / note ingest / doc_summary)
        if (!busyNow && wasBusyRef.current) {
          await refreshAll({ silent: true })
        }

        wasConsolidatingRef.current = consolidatingNow
        wasBusyRef.current = busyNow
        if (consolidatingNow) setConsolidating(true)
        else if (!consolidatingNow) setConsolidating(false)
      } catch {
        /* ignore poll errors */
      }
    }

    void tick()
    // 2s while busy feels live; still fine idle (cheap endpoint)
    const id = setInterval(tick, 2000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [collection, refreshAll])

  // ── External triggers (note ingest, definitive from other views) ──

  useEffect(() => {
    return onInfoRefresh((detail) => {
      if (detail.collectionId && detail.collectionId !== collection) return
      void refreshAll({ silent: true })
    })
  }, [collection, refreshAll])

  // ── Manual consolidate ─────────────────────────────────────

  const handleConsolidate = async () => {
    setConsolidating(true)
    wasConsolidatingRef.current = true
    wasBusyRef.current = true
    try {
      await triggerConsolidation(collection)
      toast.info(`Consolidation started for ${collectionName}...`)
      // Poll loop above will silent-refresh when consolidating ends
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      toast.error(msg || "Consolidation failed")
      setConsolidating(false)
      wasConsolidatingRef.current = false
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
            <span className="text-[28px] font-light leading-none text-foreground t-body-family">{meetings.length > 0 ? meetings.length : "—"}</span>
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

        <div className="flex items-center gap-2">
          {consolidating && (
            <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              Updating
            </span>
          )}
          <button
            type="button"
            onClick={handleConsolidate}
            disabled={consolidating}
            className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.1em] cursor-pointer transition-opacity hover:opacity-80 bg-primary text-primary-foreground border-none disabled:opacity-60"
            style={{
              padding: "4px 10px",
              borderRadius: "2px",
            }}
          >
            {consolidating ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
            Consolidate
          </button>
        </div>
      </div>

      {/* Project Description — keep previous text while consolidating (无感) */}
      {projectDescription && (
        <div
          className="text-sm leading-[1.8] pl-4 border-l italic text-foreground border-border t-body-italic-family"
        >
          {projectDescription}
        </div>
      )}

      {/* Summary — never blank out on consolidate; only first load shows spinner */}
      <div>
        <SectionLabel className="mb-2.5">Summary</SectionLabel>

        {summaryLoading && !summary ? (
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

      {/* Definitive sources — feed the Collection Summary above; collapsed by default */}
      <div>
        <button
          type="button"
          className="w-full flex items-center gap-1.5 text-left group"
          style={{ background: "none", border: "none", padding: 0 }}
          onClick={() => setDefinitiveOpen((o) => !o)}
        >
          {definitiveOpen ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" />
          )}
          <SectionLabel className="mb-0 flex-1">
            Definitive sources
            <span className="ml-1.5 normal-case tracking-normal text-muted-foreground/60">
              · {definitiveLoading && definitiveFiles.length === 0 ? "…" : definitiveFiles.length}
            </span>
          </SectionLabel>
        </button>
        {definitiveOpen && (
          <div className="mt-2 pl-1">
            {definitiveLoading && definitiveFiles.length === 0 ? (
              <div className="flex items-center gap-2 py-3 text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                <span className="text-xs">Loading…</span>
              </div>
            ) : definitiveFiles.length === 0 ? (
              <p className="text-xs text-muted-foreground py-1">
                No definitive files yet. Mark files as definitive to include them in
                Collection Summary.
              </p>
            ) : (
              <ul className="space-y-0">
                {definitiveFiles.map((f) => (
                  <li
                    key={f.file_id}
                    className="flex items-center gap-1 border-b border-dashed border-border group/def"
                  >
                    <button
                      type="button"
                      className="flex-1 min-w-0 text-left flex items-center gap-2 py-2 cursor-pointer transition-opacity hover:opacity-80"
                      style={{ background: "none", border: "none" }}
                      title={f.filename || f.file_id}
                      onClick={() => {
                        setPendingOpenFile(`__file__:${f.file_id}`)
                      }}
                    >
                      <Star className="h-3 w-3 shrink-0 text-[var(--ze-green,#1A5E3D)] fill-[var(--ze-green,#1A5E3D)]" />
                      <span className="text-xs flex-1 truncate text-foreground">
                        {f.filename || f.file_id.slice(0, 8)}
                      </span>
                      {f.original_ext && (
                        <span className="text-[10px] shrink-0 uppercase text-muted-foreground">
                          {f.original_ext}
                        </span>
                      )}
                    </button>
                    <button
                      type="button"
                      className="shrink-0 px-1.5 py-1 text-[11px] font-normal uppercase tracking-[0.1em] text-muted-foreground hover:text-[var(--ze-green,#1A5E3D)] transition-colors disabled:opacity-50"
                      style={{ background: "none", border: "none" }}
                      title="Exclude from Collection Summary sources"
                      disabled={clearingDefinitiveId === f.file_id}
                      onClick={async (e) => {
                        e.stopPropagation()
                        setClearingDefinitiveId(f.file_id)
                        try {
                          await updateFile(collection, f.file_id, {
                            is_definitive: false,
                            version: f.version,
                          })
                          toast.success("Excluded from Collection Summary")
                          // Optimistic list update + silent full refresh
                          setDefinitiveFiles((prev) =>
                            prev.filter((x) => x.file_id !== f.file_id)
                          )
                          triggerInfoRefresh({
                            collectionId: collection,
                            reason: "definitive",
                          })
                        } catch (err) {
                          toast.error(
                            `Failed: ${err instanceof Error ? err.message : String(err)}`
                          )
                        } finally {
                          setClearingDefinitiveId(null)
                        }
                      }}
                    >
                      {clearingDefinitiveId === f.file_id ? (
                        <Loader2 className="h-3 w-3 animate-spin inline" />
                      ) : (
                        "Exclude"
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {/* Conflicts */}
      {conflicts.length > 0 && (
        <div>
          <SectionLabel className="!text-amber-600">
            ⚠ Conflicts · {conflicts.length}
          </SectionLabel>
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
                  <span className="text-muted-foreground"> ({conflict.source1_label ?? conflict.source1})</span>
                  <span className="text-muted-foreground" style={{ margin: "0 6px" }}>vs</span>
                  <span style={{ color: "#B45309" }}>{conflict.content2}</span>
                  <span className="text-muted-foreground"> ({conflict.source2_label ?? conflict.source2})</span>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Notes */}
      <NotesCard collection={collection} />

      {/* Meeting Log */}
      {meetings.length > 0 && (
        <div>
          <SectionLabel>Meeting Log · {meetings.length}</SectionLabel>
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
                        onClick={() =>
                          setPendingOpenFile(`__file__:${fid}`)
                        }
                      >
                        {meeting.file_labels?.[fid] || fid}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
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
