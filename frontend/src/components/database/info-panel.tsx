import { useState, useEffect, useCallback, useRef, type ReactNode } from "react"
import { ChevronRight, Loader2, RefreshCw, Star } from "lucide-react"
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
import { NotesCard, type NotesCardHandle } from "./notes-card"
import { TodoCard } from "./todo-card"

interface InfoPanelProps {
  collection: string
  /** Premium: tabs live in left column so right rail top-aligns with tab bar. */
  tabsSlot?: ReactNode
  /**
   * Quick Chat float covering the right rail — cards fade out in place while
   * the panel slides in; reverse when Quick Chat closes.
   */
  railCovered?: boolean
}

type RailPanel = "notes" | "meetings" | null

export function InfoPanel({ collection, tabsSlot, railCovered = false }: InfoPanelProps) {
  const [summary, setSummary] = useState<string | null>(null)
  /** Only true on first load / collection switch — never on silent hot-refresh. */
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [consolidating, setConsolidating] = useState(false)
  const [projectDescription, setProjectDescription] = useState<string | null>(null)
  const [conflicts, setConflicts] = useState<ConflictItem[]>([])
  const [meetings, setMeetings] = useState<MeetingLogItem[]>([])
  const [notesCount, setNotesCount] = useState(0)
  const [docCount, setDocCount] = useState(0)
  const [definitiveFiles, setDefinitiveFiles] = useState<FileSummary[]>([])
  const [definitiveLoading, setDefinitiveLoading] = useState(false)
  /** Collapsed by default — sources used for Collection Summary. */
  const [definitiveOpen, setDefinitiveOpen] = useState(false)
  const [clearingDefinitiveId, setClearingDefinitiveId] = useState<string | null>(null)
  const [selectedConflict, setSelectedConflict] = useState<ConflictItem | null>(null)
  /**
   * Accordion: at most one of Notes / Meetings open.
   * Drives both .is-expanded (instant flex fill) and .pm-rail-expand.is-open
   * (grid 0fr↔1fr — the only height animation). See index.css rail motion notes.
   */
  const [openRail, setOpenRail] = useState<RailPanel>(null)
  /**
   * To-do fixed height = left chrome (tabs → Consolidate bottom).
   * Measured so top aligns with tab bar, bottom with Consolidate.
   */
  const chromeRef = useRef<HTMLDivElement>(null)
  const notesCardRef = useRef<NotesCardHandle>(null)
  const [todoHeight, setTodoHeight] = useState<number | null>(null)

  const { setSidebarView, setActiveMeeting, setPendingOpenFile, collections } = useAppStore()
  const collectionName = collections.find(c => c.id === collection)?.name || collection

  // Keep To-do height locked to left chrome (tabs through Consolidate)
  useEffect(() => {
    const el = chromeRef.current
    if (!el || typeof ResizeObserver === "undefined") return
    const measure = () => {
      // Round up slightly so To-do bottom doesn't fall short of Consolidate
      const h = Math.ceil(el.getBoundingClientRect().height)
      if (h > 0) setTodoHeight(h)
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    // Tabs fonts / layout settle after paint
    const raf = requestAnimationFrame(measure)
    return () => {
      ro.disconnect()
      cancelAnimationFrame(raf)
    }
  }, [collection, projectDescription, consolidating, tabsSlot, docCount, notesCount, meetings.length, conflicts.length])

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
    setDocCount(0)
    setOpenRail(null)

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

  /** Open linked section/file source from meeting log. */
  const handleMeetingSectionClick = (sourceOrFileId: string) => {
    const id = sourceOrFileId.trim()
    if (!id) return
    // Already a typed source (__meeting__:… / __file__:…) — pass through
    if (id.startsWith("__")) {
      setPendingOpenFile(id)
      return
    }
    setPendingOpenFile(`__file__:${id}`)
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

  const toggleRail = (panel: Exclude<RailPanel, null>) => {
    setOpenRail((prev) => (prev === panel ? null : panel))
  }

  const meetingsMeta =
    meetings.length === 0 ? "None linked" : `${meetings.length} linked`

  const ensureNotesOpen = () => {
    if (openRail !== "notes") setOpenRail("notes")
  }

  return (
    <div
      className={cn(
        "pm-overview h-full min-h-0",
        openRail && "is-rail-expanded"
      )}
    >
      {/*
        LEFT column:
        - Chrome (tabs → Consolidate) is sticky / non-scrolling
        - Only body (summary text, definitive, conflicts) scrolls
      */}
      <div className="pm-overview-left">
        <div ref={chromeRef} className="pm-overview-chrome">
          {tabsSlot && (
            <div className="pm-overview-tabs min-w-0">{tabsSlot}</div>
          )}

          <div className="pm-health">
            <div className="pm-h-cell">
              <span className="pm-h-num">{docCount}</span>
              <span className="pm-label" style={{ textTransform: "none", letterSpacing: "0.02em" }}>
                Docs
              </span>
            </div>
            <div className="pm-h-cell">
              <span className="pm-h-num">{meetings.length > 0 ? meetings.length : "—"}</span>
              <span className="pm-label" style={{ textTransform: "none", letterSpacing: "0.02em" }}>
                Meetings
              </span>
            </div>
            <div className="pm-h-cell">
              <span className="pm-h-num">{notesCount > 0 ? notesCount : "—"}</span>
              <span className="pm-label" style={{ textTransform: "none", letterSpacing: "0.02em" }}>
                Notes
              </span>
            </div>
            <div className={cn("pm-h-cell", conflicts.length === 0 && "ok")}>
              <span className="pm-h-num">{conflicts.length}</span>
              <span className="pm-label" style={{ textTransform: "none", letterSpacing: "0.02em" }}>
                Conflicts
              </span>
            </div>
          </div>

          <div className="pm-blurb-row">
            <p className="pm-blurb pm-read-text">
              {projectDescription?.trim()
                ? projectDescription
                : "No project description yet. Upload sources and consolidate to build context."}
            </p>
          </div>

          <div className="pm-summary-h">
            <span className="pm-summary-title">Summary</span>
            <div className="flex items-center gap-2 shrink-0">
              {consolidating && (
                <span className="pm-meta inline-flex items-center gap-1.5">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Updating
                </span>
              )}
              <button
                type="button"
                onClick={handleConsolidate}
                disabled={consolidating}
                className="pm-btn-pri"
              >
                {consolidating ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <RefreshCw className="h-3 w-3" />
                )}
                Consolidate
              </button>
            </div>
          </div>
        </div>

        <div className="pm-overview-left-scroll">
        <div className="pm-read-column pm-overview-left-body">
          {summaryLoading && !summary ? (
            <div className="flex items-center justify-center py-8 gap-2">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--pm-faint)]" />
              <span className="pm-meta">Loading…</span>
            </div>
          ) : summary ? (
            <div className="pm-summary-body pm-read-text mb-6">
              <TiptapEditor
                value={summary}
                readonly
                showToolbar={false}
                flush
                className="pm-summary-editor"
              />
            </div>
          ) : (
            <p className="pm-meta mb-6">No summary yet. Upload files and consolidate.</p>
          )}

          <div className="mb-6">
            <button
              type="button"
              className="pm-collapse-trigger inline-flex items-center gap-1.5 text-left group border-none bg-transparent p-0 cursor-pointer"
              aria-expanded={definitiveOpen}
              onClick={() => setDefinitiveOpen((o) => !o)}
            >
              <span className="pm-collapse-chev" aria-hidden>
                <ChevronRight className="h-3.5 w-3.5" />
              </span>
              <span className="pm-meta" style={{ color: "var(--pm-green)" }}>
                Definitive sources ·{" "}
                {definitiveLoading && definitiveFiles.length === 0
                  ? "…"
                  : definitiveFiles.length}
              </span>
            </button>
            <div
              className={cn("pm-collapse", definitiveOpen && "is-open")}
              role="region"
              aria-hidden={!definitiveOpen}
            >
              <div className="pm-collapse-panel">
                <div className="pm-collapse-panel-inner mt-2 pl-1">
                  {definitiveLoading && definitiveFiles.length === 0 ? (
                    <div className="flex items-center gap-2 py-3">
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--pm-faint)]" />
                      <span className="pm-meta">Loading…</span>
                    </div>
                  ) : definitiveFiles.length === 0 ? (
                    <p className="pm-meta py-1">
                      No definitive files yet. Mark files as definitive to include them in
                      Collection Summary.
                    </p>
                  ) : (
                    <ul className="space-y-0">
                      {definitiveFiles.map((f) => (
                        <li
                          key={f.file_id}
                          className="flex items-center gap-1 border-b border-dashed border-border/50 group/def"
                        >
                          <button
                            type="button"
                            className="flex-1 min-w-0 text-left flex items-center gap-2 py-2 cursor-pointer transition-opacity hover:opacity-80 border-none bg-transparent"
                            title={f.filename || f.file_id}
                            onClick={() => {
                              setPendingOpenFile(`__file__:${f.file_id}`)
                            }}
                          >
                            <Star className="h-3 w-3 shrink-0 text-[var(--pm-green)] fill-[var(--pm-green)]" />
                            <span className="text-xs flex-1 truncate text-[var(--pm-text)]">
                              {f.filename || f.file_id.slice(0, 8)}
                            </span>
                            {f.original_ext && (
                              <span className="pm-meta shrink-0 uppercase">
                                {f.original_ext}
                              </span>
                            )}
                          </button>
                          <button
                            type="button"
                            className="shrink-0 px-1.5 py-1 pm-meta hover:text-[var(--pm-green)] transition-colors disabled:opacity-50 border-none bg-transparent cursor-pointer"
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
              </div>
            </div>
          </div>

          {conflicts.length > 0 && (
            <div className="mb-4">
              <span className="pm-label mb-2 block" style={{ color: "#B45309" }}>
                ⚠ Conflicts · {conflicts.length}
              </span>
              <div>
                {conflicts.map((conflict, i) => (
                  <button
                    key={i}
                    type="button"
                    className="w-full text-left py-2.5 border-b border-dashed border-border/50 cursor-pointer transition-opacity hover:opacity-80 bg-transparent border-x-0 border-t-0"
                    onClick={() => setSelectedConflict(conflict)}
                  >
                    <div className="text-xs leading-relaxed text-[var(--pm-text)]">
                      <span style={{ color: "#B45309" }}>{conflict.content1}</span>
                      <span className="text-[var(--pm-faint)]">
                        {" "}
                        ({conflict.source1_label ?? conflict.source1})
                      </span>
                      <span className="text-[var(--pm-faint)]" style={{ margin: "0 6px" }}>
                        vs
                      </span>
                      <span style={{ color: "#B45309" }}>{conflict.content2}</span>
                      <span className="text-[var(--pm-faint)]">
                        {" "}
                        ({conflict.source2_label ?? conflict.source2})
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
        </div>
      </div>

      {/*
        RIGHT: pinned to stage height (same bottom edge as left content area).
        To-do = chrome height; Notes/Meetings expand into remaining space.
        data-pm-rail-anchor: Quick Chat float card matches this box and covers it.
      */}
      <aside
        className={cn(
          "pm-overview-right relative",
          railCovered && "is-qc-covered"
        )}
        data-pm-rail-anchor
      >
        {/* Stack fades in place when Quick Chat covers the rail */}
        <div className="pm-rail-stack">
        <div
          className="pm-todo-fixed"
          style={todoHeight != null ? { height: todoHeight } : undefined}
        >
          <TodoCard collection={collection} variant="card" />
        </div>

        <div className="pm-rail-lower">
          <section
            className={cn(
              "pm-rail-card !p-0",
              openRail === "notes" && "is-expanded"
            )}
          >
            <div className="pm-collapse-h shrink-0">
              <button
                type="button"
                className="pm-collapse-h-main"
                aria-expanded={openRail === "notes"}
                aria-label="Toggle Notes"
                onClick={() => toggleRail("notes")}
              >
                <span
                  className={cn(
                    "pm-rail-chev",
                    openRail === "notes" && "is-open"
                  )}
                  aria-hidden
                >
                  <ChevronRight className="size-3.5" strokeWidth={2} />
                </span>
                <span
                  className="pm-label"
                  style={{ textTransform: "none", letterSpacing: "0.02em" }}
                >
                  Notes
                </span>
                <span className="pm-count-pill">{notesCount}</span>
              </button>
              <div className="pm-collapse-h-actions">
                <button
                  type="button"
                  className="pm-btn-ghost pm-btn-xs"
                  onClick={() => {
                    ensureNotesOpen()
                    requestAnimationFrame(() => notesCardRef.current?.openImport())
                  }}
                >
                  Import
                </button>
                <button
                  type="button"
                  className="pm-btn-pri pm-btn-xs"
                  onClick={() => {
                    ensureNotesOpen()
                    requestAnimationFrame(() => notesCardRef.current?.create())
                  }}
                >
                  New
                </button>
              </div>
            </div>
            {/* Always mounted for smooth height + imperative Import/New */}
            <div
              className={cn(
                "pm-rail-expand",
                openRail === "notes" && "is-open"
              )}
            >
              <div className="pm-rail-expand-panel">
                <div className="pm-rail-expand-inner pm-rail-body px-3 pb-3">
                  <NotesCard
                    ref={notesCardRef}
                    collection={collection}
                    variant="rail"
                    hideToolbar
                  />
                </div>
              </div>
            </div>
          </section>

          <section
            className={cn(
              "pm-rail-card !p-0",
              openRail === "meetings" && "is-expanded"
            )}
          >
            <div className="pm-collapse-h shrink-0">
              <button
                type="button"
                className="pm-collapse-h-main"
                aria-expanded={openRail === "meetings"}
                aria-label="Toggle Meetings"
                onClick={() => toggleRail("meetings")}
              >
                <span
                  className={cn(
                    "pm-rail-chev",
                    openRail === "meetings" && "is-open"
                  )}
                  aria-hidden
                >
                  <ChevronRight className="size-3.5" strokeWidth={2} />
                </span>
                <span
                  className="pm-label"
                  style={{ textTransform: "none", letterSpacing: "0.02em" }}
                >
                  Meetings
                </span>
                <span className="pm-count-pill">{meetings.length}</span>
                <span className="pm-meta ml-auto shrink-0 pl-2">{meetingsMeta}</span>
              </button>
            </div>
            <div
              className={cn(
                "pm-rail-expand",
                openRail === "meetings" && "is-open"
              )}
            >
              <div className="pm-rail-expand-panel">
                <div className="pm-rail-expand-inner pm-rail-body px-3 pb-3">
                  {meetings.length === 0 ? (
                    <p className="pm-meta py-1">
                      No meetings linked. Attach from Meeting when ready.
                    </p>
                  ) : (
                    <div className="flex flex-col gap-0.5">
                      {meetings.map((meeting) => {
                        // Sections ingested into this collection (from meeting-log file_ids)
                        const sections = meeting.file_ids ?? []
                        return (
                          <div
                            key={meeting.id}
                            className="py-1 border-b border-dashed border-border/40 last:border-0"
                          >
                            {/* Meeting title row */}
                            <button
                              type="button"
                              className="pm-rail-row"
                              onClick={() => handleMeetingClick(meeting)}
                              title={meeting.title || "Open meeting"}
                            >
                              <span className="pm-rail-row-title">
                                {meeting.title || meeting.id}
                              </span>
                              {meeting.created_at && (
                                <span className="pm-meta shrink-0">
                                  {formatDate(meeting.created_at)}
                                </span>
                              )}
                            </button>
                            {/*
                              Any ingested section: indent under meeting title.
                              (Previously hidden when length === 1 — that dropped all single-section rows.)
                            */}
                            {sections.length > 0 && (
                              <div className="flex flex-col gap-0 -mt-0.5">
                                {sections.map((fid) => (
                                  <button
                                    key={fid}
                                    type="button"
                                    className="pm-rail-row pm-rail-row--indent"
                                    onClick={() => handleMeetingSectionClick(fid)}
                                    title={
                                      meeting.file_labels?.[fid] ||
                                      "Open section"
                                    }
                                  >
                                    <span className="pm-rail-row-title">
                                      {meeting.file_labels?.[fid] || fid}
                                    </span>
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </section>
        </div>
        </div>{/* /.pm-rail-stack */}
      </aside>

      <ConflictViewerDialog
        conflict={selectedConflict}
        collection={collection}
        onOpenChange={(v) => !v && setSelectedConflict(null)}
      />
    </div>
  )
}
