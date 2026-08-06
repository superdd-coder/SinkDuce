import { useState, useEffect, useCallback, useMemo, useRef } from "react"
import { Dialog, DialogContent } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { ChevronLeft } from "lucide-react"
import { toast } from "sonner"
import {
  getNotes,
  getMeetings,
  createNote,
  parseMeetingDistillSource,
  type NoteDetail,
  type NoteListItem,
  type Meeting,
} from "@/api/client"
import { NoteSidebarLeft, type SidebarTab } from "./note-sidebar-left"
import { NoteSidebarRight } from "./note-sidebar-right"
import { MeetingSummaryPanel } from "./meeting-summary-panel"
import { NotePane } from "./note-pane"
import { cn } from "@/lib/utils"

interface NoteEditorDialogProps {
  collection: string
  noteId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

// ── Dual-pane document model ─────────────────────────────────────

type PaneDoc =
  | { kind: "note"; noteId: string }
  | { kind: "meeting"; meetingId: string; tabId: string }

type EditorPane = {
  id: string
  doc: PaneDoc
}

let _paneSeq = 0
const newPaneId = () => `pane-${++_paneSeq}`

export function NoteEditorDialog({ collection, noteId, open, onOpenChange }: NoteEditorDialogProps) {
  const [notesList, setNotesList] = useState<NoteListItem[]>([])
  const [meetingsList, setMeetingsList] = useState<Meeting[]>([])
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("notes")
  const [leftSidebarCollapsed, setLeftSidebarCollapsed] = useState(false)

  const [panes, setPanes] = useState<EditorPane[]>(() => [
    { id: newPaneId(), doc: { kind: "note", noteId } },
  ])
  const [focusedPaneId, setFocusedPaneId] = useState<string>("")
  /** Soft enter/exit for split panes (width + fade) */
  const [enteringPaneId, setEnteringPaneId] = useState<string | null>(null)
  /** First paint only — apply collapsed styles without transition */
  const [enteringPrepId, setEnteringPrepId] = useState<string | null>(null)
  const [exitingPaneId, setExitingPaneId] = useState<string | null>(null)
  /** Refs lock concurrent open/close — React state is too slow for double-clicks */
  const enteringPaneIdRef = useRef<string | null>(null)
  const exitingPaneIdRef = useRef<string | null>(null)
  const exitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const enterRafRef = useRef<number | null>(null)
  const enterDoneTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const PANE_MOTION_MS = 420

  /**
   * Per-note meta cache — so each pane can open its own distill rail
   * without waiting on a single global currentNote.
   */
  const [noteMetaById, setNoteMetaById] = useState<Record<string, NoteDetail>>(
    {}
  )
  const [activeBlockId, setActiveBlockId] = useState<string | null>(null)

  useEffect(() => {
    return () => {
      if (exitTimerRef.current) clearTimeout(exitTimerRef.current)
      if (enterDoneTimerRef.current) clearTimeout(enterDoneTimerRef.current)
      if (enterRafRef.current != null) cancelAnimationFrame(enterRafRef.current)
    }
  }, [])

  useEffect(() => {
    if (!panes.find((p) => p.id === focusedPaneId) && panes[0]) {
      setFocusedPaneId(panes[0].id)
    }
  }, [panes, focusedPaneId])

  const focusedPane = useMemo(
    () => panes.find((p) => p.id === focusedPaneId) ?? panes[0] ?? null,
    [panes, focusedPaneId]
  )

  const focusedNoteId =
    focusedPane?.doc.kind === "note" ? focusedPane.doc.noteId : null

  const noteHasDistill = useCallback((nid: string) => {
    const n = noteMetaById[nid]
    if (!n) return false
    return (
      (n.references?.length ?? 0) > 0 || (n.extracted_into?.length ?? 0) > 0
    )
  }, [noteMetaById])

  /**
   * Distill rail lives *inside* the pane group, snug after that pane.
   * Open when focused + has distill. While that group is exiting, stay open so
   * the rail collapses with the group as one unit (not a separate close).
   */
  const railOpenForPane = useCallback(
    (pane: EditorPane) => {
      if (pane.id !== focusedPaneId) return false
      if (pane.doc.kind !== "note") return false
      return noteHasDistill(pane.doc.noteId)
    },
    [focusedPaneId, noteHasDistill]
  )

  const rememberNoteMeta = useCallback((note: NoteDetail) => {
    setNoteMetaById((prev) => {
      const prevN = prev[note.id]
      if (
        prevN &&
        prevN.references === note.references &&
        prevN.extracted_into === note.extracted_into &&
        prevN.title === note.title &&
        prevN.content === note.content
      ) {
        return prev
      }
      return { ...prev, [note.id]: note }
    })
  }, [])

  /** All open notes in panes (dual-pane: both highlighted in sidebar) */
  const openNoteIds = useMemo(
    () =>
      panes
        .filter((p) => p.doc.kind === "note")
        .map((p) => (p.doc as { kind: "note"; noteId: string }).noteId),
    [panes]
  )

  /** All open meeting tabs in panes */
  const openMeetings = useMemo(
    () =>
      panes
        .filter((p) => p.doc.kind === "meeting")
        .map((p) => {
          const d = p.doc as { kind: "meeting"; meetingId: string; tabId: string }
          return { meetingId: d.meetingId, tabId: d.tabId }
        }),
    [panes]
  )

  const focusedMeeting = useMemo(() => {
    if (focusedPane?.doc.kind !== "meeting") return null
    const d = focusedPane.doc
    return { meetingId: d.meetingId, tabId: d.tabId }
  }, [focusedPane])

  // ── Open / lists ──────────────────────────────────────

  useEffect(() => {
    if (!open) return
    const pid = newPaneId()
    // Reset pane motion locks when dialog (re)opens
    enteringPaneIdRef.current = null
    exitingPaneIdRef.current = null
    setEnteringPaneId(null)
    setEnteringPrepId(null)
    setExitingPaneId(null)
    if (exitTimerRef.current) {
      clearTimeout(exitTimerRef.current)
      exitTimerRef.current = null
    }
    if (enterDoneTimerRef.current) {
      clearTimeout(enterDoneTimerRef.current)
      enterDoneTimerRef.current = null
    }
    setPanes([{ id: pid, doc: { kind: "note", noteId } }])
    setFocusedPaneId(pid)
    setSidebarTab("notes")
    setNoteMetaById({})
    setActiveBlockId(null)
    // Refresh [spk:…] display names from latest meeting speaker maps
    void import("@/components/ui/tiptap-editor").then((m) => {
      m.invalidateMeetingSpeakerCache()
    })
  }, [open, noteId])

  useEffect(() => {
    if (!open) return
    getNotes(collection)
      .then((res) => setNotesList(res.notes ?? []))
      .catch(() => setNotesList([]))
    getMeetings()
      .then((list) => setMeetingsList(Array.isArray(list) ? list : []))
      .catch(() => setMeetingsList([]))
  }, [open, collection])

  // ── Pane helpers ──────────────────────────────────────

  const setFocusedDoc = useCallback(
    (doc: PaneDoc) => {
      setPanes((prev) => {
        if (prev.length === 0) {
          const id = newPaneId()
          setFocusedPaneId(id)
          return [{ id, doc }]
        }
        const fid =
          focusedPaneId && prev.some((p) => p.id === focusedPaneId)
            ? focusedPaneId
            : prev[0].id
        return prev.map((p) => (p.id === fid ? { ...p, doc } : p))
      })
    },
    [focusedPaneId]
  )

  const openNoteInFocused = useCallback(
    (id: string) => {
      setFocusedDoc({ kind: "note", noteId: id })
      setSidebarTab("notes")
    },
    [setFocusedDoc]
  )

  const openMeetingTab = useCallback(
    (meetingId: string, tabId: string) => {
      setFocusedDoc({
        kind: "meeting",
        meetingId,
        tabId: tabId || "tab_general",
      })
      setSidebarTab("meetings")
    },
    [setFocusedDoc]
  )

  /** Soft enter animation for a newly added pane id (keep focus on source). */
  const beginEnterMotion = useCallback((id: string) => {
    enteringPaneIdRef.current = id
    setEnteringPaneId(id)
    setEnteringPrepId(id)
    if (enterRafRef.current != null) cancelAnimationFrame(enterRafRef.current)
    if (enterDoneTimerRef.current) clearTimeout(enterDoneTimerRef.current)
    /*
     * Frame 0: group is-entering + is-prep (collapsed, no transition)
     * Frame 1: drop prep → transitions armed
     * Frame 2: drop entering → group flex-grows; lock for full motion ms
     */
    enterRafRef.current = requestAnimationFrame(() => {
      setEnteringPrepId(null)
      enterRafRef.current = requestAnimationFrame(() => {
        setEnteringPaneId(null)
        enterRafRef.current = null
        enterDoneTimerRef.current = setTimeout(() => {
          if (enteringPaneIdRef.current === id) {
            enteringPaneIdRef.current = null
          }
          enterDoneTimerRef.current = null
        }, PANE_MOTION_MS)
      })
    })
  }, [])

  /**
   * Open a document in the *other* pane without stealing focus.
   * - Dual pane: load into non-focused pane
   * - Single pane: soft-split and put doc in the new pane (rail stays on source)
   */
  const openDocBeside = useCallback(
    (doc: PaneDoc) => {
      setPanes((prev) => {
        if (prev.length === 0) return prev

        const fid =
          focusedPaneId && prev.some((p) => p.id === focusedPaneId)
            ? focusedPaneId
            : prev[0].id

        // Already dual — update the other pane only
        if (prev.length >= 2) {
          return prev.map((p) => (p.id === fid ? p : { ...p, doc }))
        }

        // Single — split; new pane gets the reference (do NOT focus it)
        if (enteringPaneIdRef.current || exitingPaneIdRef.current) {
          return prev
        }
        const id = newPaneId()
        beginEnterMotion(id)
        return [...prev, { id, doc }]
      })
    },
    [focusedPaneId, beginEnterMotion]
  )

  const addPane = useCallback(() => {
    // Ref lock — ignore double Split while animating
    if (enteringPaneIdRef.current || exitingPaneIdRef.current) {
      return
    }
    setPanes((prev) => {
      if (prev.length >= 2) return prev
      /*
       * Principal: keep focus on the original pane so its distill rail stays put.
       * Original group narrows left (rail rides with it); new group emerges right.
       */
      const source =
        prev.find((p) => p.id === focusedPaneId) ?? prev[0] ?? null
      const seed = source?.doc ?? { kind: "note" as const, noteId }
      const id = newPaneId()
      beginEnterMotion(id)
      return [...prev, { id, doc: seed }]
    })
  }, [noteId, focusedPaneId, beginEnterMotion])

  const closePane = useCallback((paneId: string) => {
    // Synchronous lock — two quick X clicks must not remove both panes
    if (exitingPaneIdRef.current || enteringPaneIdRef.current) return

    setPanes((prev) => {
      if (prev.length <= 1) return prev
      if (!prev.some((p) => p.id === paneId)) return prev

      exitingPaneIdRef.current = paneId
      setExitingPaneId(paneId)
      if (exitTimerRef.current) clearTimeout(exitTimerRef.current)
      exitTimerRef.current = setTimeout(() => {
        setPanes((cur) => {
          // Never empty the deck — always keep at least one pane
          if (cur.length <= 1) return cur
          const next = cur.filter((p) => p.id !== paneId)
          if (next.length === 0) return cur
          setFocusedPaneId((fid) =>
            fid === paneId ? next[0].id : fid
          )
          return next
        })
        if (exitingPaneIdRef.current === paneId) {
          exitingPaneIdRef.current = null
        }
        setExitingPaneId(null)
        exitTimerRef.current = null
      }, PANE_MOTION_MS)
      return prev
    })
  }, [])

  /** In-editor distill source click — replace focused pane (legacy). */
  const navigateSource = useCallback(
    (sourceId: string) => {
      const parsed = parseMeetingDistillSource(sourceId)
      if (parsed) {
        openMeetingTab(parsed.meetingId, parsed.tabId)
        return
      }
      openNoteInFocused(sourceId)
    },
    [openMeetingTab, openNoteInFocused]
  )

  /**
   * Distill In/Out rail click: open reference beside current view, never steal focus.
   */
  const openReferenceBeside = useCallback(
    (sourceId: string) => {
      const parsed = parseMeetingDistillSource(sourceId)
      const doc: PaneDoc = parsed
        ? {
            kind: "meeting",
            meetingId: parsed.meetingId,
            tabId: parsed.tabId || "tab_general",
          }
        : { kind: "note", noteId: sourceId }
      openDocBeside(doc)
    },
    [openDocBeside]
  )

  const handleCreateNote = useCallback(async () => {
    const title = new Date().toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    })
    try {
      const res = await createNote(collection, title)
      const notesRes = await getNotes(collection)
      const notes = notesRes.notes ?? []
      setNotesList(notes)
      openNoteInFocused(res.id)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create note")
    }
  }, [collection, openNoteInFocused])

  const handleNoteDeleted = useCallback(
    async (deletedId: string) => {
      const notesRes = await getNotes(collection).catch(() => ({ notes: [] as NoteListItem[] }))
      const updatedList = notesRes.notes ?? []
      setNotesList(updatedList)

      setPanes((prev) => {
        // Only replace the pane(s) that showed the deleted note — keep the other pane
        const next = prev
          .map((p) => {
            if (p.doc.kind === "note" && p.doc.noteId === deletedId) {
              // Prefer a note not already open in another pane
              const openIds = new Set(
                prev
                  .filter((x) => x.doc.kind === "note" && x.id !== p.id)
                  .map((x) => (x.doc as { noteId: string }).noteId)
              )
              const fallback =
                updatedList.find(
                  (n) => n.id !== deletedId && !openIds.has(n.id)
                ) ??
                updatedList.find((n) => n.id !== deletedId) ??
                null
              return fallback
                ? { ...p, doc: { kind: "note" as const, noteId: fallback.id } }
                : null
            }
            return p
          })
          .filter((p): p is EditorPane => p != null)

        if (next.length === 0) {
          if (updatedList[0]) {
            return [
              {
                id: newPaneId(),
                doc: { kind: "note", noteId: updatedList[0].id },
              },
            ]
          }
          return prev
        }
        return next
      })

      if (updatedList.length === 0) {
        onOpenChange(false)
      }
    },
    [collection, onOpenChange]
  )

  const handleTitleChange = useCallback((nid: string, title: string) => {
    setNotesList((prev) =>
      prev.map((n) => (n.id === nid ? { ...n, title } : n))
    )
    setNoteMetaById((prev) => {
      const cur = prev[nid]
      if (!cur) return prev
      return { ...prev, [nid]: { ...cur, title } }
    })
  }, [])

  const handleSelectBlock = useCallback((blockId: string) => {
    setActiveBlockId(blockId)
    setTimeout(() => {
      const blockEl = document.querySelector(
        `.distill-block[data-block-id="${blockId}"]`
      )
      if (blockEl) {
        blockEl.scrollIntoView({ behavior: "smooth", block: "center" })
        const htmlEl = blockEl as HTMLElement
        htmlEl.style.transition = "outline 0.3s"
        htmlEl.style.outline = "2px solid hsl(var(--primary))"
        htmlEl.style.outlineOffset = "2px"
        setTimeout(() => {
          htmlEl.style.outline = ""
        }, 1500)
      }
    }, 100)
  }, [])

  // ── Render ────────────────────────────────────────────

  // Keep Dialog mounted while open toggles so exit animation can play.
  // (Returning null on !open hard-cuts close.)
  if (!noteId) {
    return null
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(newOpen, eventDetails) => {
        if (!newOpen && eventDetails.reason === "escape-key") return
        onOpenChange(newOpen)
      }}
    >
      <DialogContent
        showCloseButton
        className={cn(
          "pm-dialog pm-workspace pm-ws-dialog",
          "!max-w-[92vw] !w-[92vw] h-[85vh] p-0 !gap-0 flex flex-col overflow-hidden",
          // Stronger enter/exit than default dialog (large workspace stage)
          "duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]",
          "data-open:animate-in data-open:fade-in-0 data-open:zoom-in-[0.97] data-open:slide-in-from-bottom-2",
          "data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-[0.97] data-closed:slide-out-to-bottom-2"
        )}
      >
        {/* Global chrome — sidebar toggle only */}
        <div className="pm-ws-chrome">
          <Button
            variant="ghost"
            size="sm"
            className="pm-ws-icon-btn"
            onClick={() => setLeftSidebarCollapsed(!leftSidebarCollapsed)}
            title={leftSidebarCollapsed ? "Show sidebar" : "Hide sidebar"}
          >
            <ChevronLeft
              className={cn(
                "h-4 w-4 transition-transform duration-200",
                leftSidebarCollapsed && "rotate-180"
              )}
            />
          </Button>
          <span className="flex-1 pm-label text-[var(--pm-faint)]">Notes</span>
          {/* room for dialog close button */}
          <div className="w-8" />
        </div>

        {/*
          Card deck: left list · panes with distill rail snug after focused pane
          (not pinned to stage right). White nested cards on beige stage.
        */}
        <div className="pm-ws-body">
          {!leftSidebarCollapsed && (
            <aside className="pm-ws-card pm-ws-card--rail">
              <NoteSidebarLeft
                notes={notesList}
                meetings={meetingsList}
                activeNoteIds={openNoteIds}
                focusedNoteId={focusedNoteId}
                activeMeetings={openMeetings}
                focusedMeeting={focusedMeeting}
                sidebarTab={sidebarTab}
                onSidebarTabChange={setSidebarTab}
                onSwitchNote={openNoteInFocused}
                onOpenMeetingTab={openMeetingTab}
                onCreateNote={handleCreateNote}
              />
            </aside>
          )}

          <div
            className={cn(
              "pm-ws-panes",
              // Keep split layout while a pane is exiting so sibling can ease wider
              (panes.length === 2 || !!exitingPaneId) && "is-split"
            )}
          >
            {panes.map((pane) => {
              const isExiting = exitingPaneId === pane.id
              const isEntering = enteringPaneId === pane.id
              const isPrep = enteringPrepId === pane.id
              const docKey =
                pane.doc.kind === "note"
                  ? `n-${pane.doc.noteId}`
                  : `m-${pane.doc.meetingId}-${pane.doc.tabId}`
              const paneNoteId =
                pane.doc.kind === "note" ? pane.doc.noteId : null
              const paneMeta = paneNoteId
                ? noteMetaById[paneNoteId] ?? null
                : null
              const showDistillRail = railOpenForPane(pane)
              const railHasContent =
                !!paneMeta &&
                paneNoteId != null &&
                noteHasDistill(paneNoteId) &&
                showDistillRail

              return (
                /*
                 * Pane group = editor card + optional distill rail as ONE flex unit.
                 * Split: original group narrows (rail rides left), new group emerges.
                 * Close: whole group collapses together (rail doesn't orphan).
                 * Focus switch: rail open state transfers between groups.
                 */
                <div
                  key={pane.id}
                  className={cn(
                    "pm-ws-pane-group",
                    isEntering && "is-entering",
                    isPrep && "is-prep",
                    isExiting && "is-exiting",
                    showDistillRail && "has-rail",
                    // Focus ring only when dual-pane (single window needs no selection chrome)
                    panes.length > 1 &&
                      pane.id === focusedPaneId &&
                      !isExiting &&
                      "is-focused"
                  )}
                >
                  <div className="pm-ws-pane-slot">
                    <div className="pm-ws-card pm-ws-card--pane">
                      {pane.doc.kind === "note" ? (
                        <NotePane
                          key={pane.id}
                          collection={collection}
                          noteId={pane.doc.noteId}
                          focused={
                            // Always track active pane for tools/meta; visual is-focus only in multi
                            pane.id === focusedPaneId && !isExiting
                          }
                          showFocusChrome={
                            panes.length > 1 &&
                            pane.id === focusedPaneId &&
                            !isExiting
                          }
                          onFocus={() => {
                            if (!isExiting) setFocusedPaneId(pane.id)
                          }}
                          showClose={
                            panes.length > 1 &&
                            !isExiting &&
                            !exitingPaneId &&
                            !enteringPaneId
                          }
                          onClosePane={() => closePane(pane.id)}
                          showSplit={
                            panes.length < 2 &&
                            !exitingPaneId &&
                            !enteringPaneId
                          }
                          onSplit={addPane}
                          onCloseDialog={() => onOpenChange(false)}
                          onTitleChange={handleTitleChange}
                          onDeleted={(nid) => void handleNoteDeleted(nid)}
                          onNoteMeta={rememberNoteMeta}
                          onNavigateSource={navigateSource}
                          className="pm-ws-doc-surface"
                          docSwapKey={docKey}
                        />
                      ) : (
                        (() => {
                          const meetingDoc = pane.doc as Extract<
                            PaneDoc,
                            { kind: "meeting" }
                          >
                          const meetingFocused =
                            pane.id === focusedPaneId && !isExiting
                          return (
                            <div
                              key={docKey}
                              className="flex-1 flex flex-col min-w-0 min-h-0 pm-ws-doc-surface is-doc-swap"
                              onMouseDown={(e) => {
                                if (isExiting) return
                                if (pane.id === focusedPaneId) return
                                if (e.button !== 0) return
                                if (
                                  (e.target as HTMLElement).closest?.(
                                    ".pm-ws-pane-h, [data-slot='menu']"
                                  )
                                ) {
                                  return
                                }
                                // Defer focus so body click-selection isn't interrupted
                                const finish = () => {
                                  window.removeEventListener("mouseup", finish, true)
                                  setFocusedPaneId(pane.id)
                                }
                                window.addEventListener("mouseup", finish, true)
                              }}
                            >
                              <MeetingSummaryPanel
                                meetingId={meetingDoc.meetingId}
                                tabId={meetingDoc.tabId}
                                paneChrome={{
                                  focused:
                                    meetingFocused && panes.length > 1,
                                  onFocus: () => {
                                    if (!isExiting) setFocusedPaneId(pane.id)
                                  },
                                  showSplit:
                                    panes.length < 2 &&
                                    !exitingPaneId &&
                                    !enteringPaneId,
                                  onSplit: addPane,
                                  showClose:
                                    panes.length > 1 &&
                                    !isExiting &&
                                    !exitingPaneId &&
                                    !enteringPaneId,
                                  onClose: () => closePane(pane.id),
                                }}
                              />
                            </div>
                          )
                        })()
                      )}
                    </div>
                  </div>

                  <div
                    className={cn(
                      "pm-ws-rail-r-host",
                      showDistillRail && "is-open"
                    )}
                    aria-hidden={!showDistillRail}
                  >
                    {railHasContent && paneMeta && (
                      <aside className="pm-ws-card pm-ws-card--rail-r">
                        <NoteSidebarRight
                          references={paneMeta.references ?? []}
                          injectedInto={paneMeta.extracted_into ?? []}
                          injectedIntoTitles={new Map(
                            notesList.map((n) => [n.id, n.title])
                          )}
                          activeBlockId={
                            showDistillRail ? activeBlockId : null
                          }
                          onSelectBlock={handleSelectBlock}
                          onNavigateToNote={openReferenceBeside}
                        />
                      </aside>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
