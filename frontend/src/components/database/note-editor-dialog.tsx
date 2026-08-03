import { useState, useEffect, useCallback, useMemo } from "react"
import { Dialog, DialogContent } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { ChevronLeft, Columns2, X } from "lucide-react"
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

  // Focused note meta — for Distill In/Out right sidebar only
  const [currentNote, setCurrentNote] = useState<NoteDetail | null>(null)
  const [activeBlockId, setActiveBlockId] = useState<string | null>(null)

  useEffect(() => {
    if (!panes.find((p) => p.id === focusedPaneId) && panes[0]) {
      setFocusedPaneId(panes[0].id)
    }
  }, [panes, focusedPaneId])

  const focusedPane = useMemo(
    () => panes.find((p) => p.id === focusedPaneId) ?? panes[0] ?? null,
    [panes, focusedPaneId]
  )

  const focusedIsNote = focusedPane?.doc.kind === "note"
  const focusedNoteId =
    focusedPane?.doc.kind === "note" ? focusedPane.doc.noteId : null

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
    setPanes([{ id: pid, doc: { kind: "note", noteId } }])
    setFocusedPaneId(pid)
    setSidebarTab("notes")
    setCurrentNote(null)
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

  const addPane = useCallback(() => {
    setPanes((prev) => {
      if (prev.length >= 2) return prev
      const seed = prev[0]?.doc ?? { kind: "note" as const, noteId }
      const id = newPaneId()
      setFocusedPaneId(id)
      return [...prev, { id, doc: seed }]
    })
  }, [noteId])

  const closePane = useCallback(
    (paneId: string) => {
      setPanes((prev) => {
        if (prev.length <= 1) return prev
        const next = prev.filter((p) => p.id !== paneId)
        if (focusedPaneId === paneId && next[0]) {
          setFocusedPaneId(next[0].id)
        }
        return next
      })
    },
    [focusedPaneId]
  )

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
        const next = prev
          .map((p) => {
            if (p.doc.kind === "note" && p.doc.noteId === deletedId) {
              const fallback = updatedList.find((n) => n.id !== deletedId) ?? updatedList[0]
              return fallback
                ? { ...p, doc: { kind: "note" as const, noteId: fallback.id } }
                : p
            }
            return p
          })
          .filter((p) => {
            if (p.doc.kind === "note" && p.doc.noteId === deletedId) {
              return false
            }
            return true
          })
        if (next.length === 0 && updatedList[0]) {
          return [
            {
              id: newPaneId(),
              doc: { kind: "note", noteId: updatedList[0].id },
            },
          ]
        }
        return next.length ? next : prev
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
    setCurrentNote((prev) =>
      prev && prev.id === nid ? { ...prev, title } : prev
    )
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

  const paneTitle = (doc: PaneDoc): string => {
    if (doc.kind === "note") {
      return notesList.find((n) => n.id === doc.noteId)?.title || "Note"
    }
    const m = meetingsList.find((x) => x.id === doc.meetingId)
    const mt = m?.title || "Meeting"
    if (doc.tabId === "tab_general") return mt
    const sec = (m?.tabs ?? []).find((t) => t.tab_id === doc.tabId)
    return `${mt} · ${sec?.name || doc.tabId}`
  }

  // ── Render ────────────────────────────────────────────

  if (!open || !noteId) {
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
        className="!max-w-[92vw] !w-[92vw] h-[85vh] p-0 !gap-0 flex flex-col"
      >
        {/* Global chrome — sidebar toggle only */}
        <div className="flex items-center gap-2 px-3 py-2 border-b shrink-0">
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={() => setLeftSidebarCollapsed(!leftSidebarCollapsed)}
          >
            <ChevronLeft
              className={`h-4 w-4 transition-transform ${leftSidebarCollapsed ? "rotate-180" : ""}`}
            />
          </Button>
          <span className="flex-1" />
          {/* room for dialog close button */}
          <div className="w-8" />
        </div>

        <div className="flex flex-1 min-h-0">
          {!leftSidebarCollapsed && (
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
          )}

          {/* Center: 1 or 2 panes — each with own action bar */}
          <div className="flex-1 flex min-w-0 min-h-0">
            {panes.map((pane, idx) => (
              <div
                key={pane.id}
                className={cn(
                  "flex flex-col min-w-0 min-h-0 flex-1",
                  panes.length === 2 && idx === 0 && "border-r border-border"
                )}
              >
                {pane.doc.kind === "note" ? (
                  <NotePane
                    collection={collection}
                    noteId={pane.doc.noteId}
                    focused={pane.id === focusedPaneId}
                    onFocus={() => setFocusedPaneId(pane.id)}
                    showClose={panes.length > 1}
                    onClosePane={() => closePane(pane.id)}
                    showSplit={panes.length < 2}
                    onSplit={addPane}
                    onCloseDialog={() => onOpenChange(false)}
                    onTitleChange={handleTitleChange}
                    onDeleted={(nid) => void handleNoteDeleted(nid)}
                    onNoteMeta={(note) => {
                      if (pane.id === focusedPaneId || focusedNoteId === note.id) {
                        setCurrentNote(note)
                      }
                    }}
                    onNavigateSource={navigateSource}
                  />
                ) : (
                  (() => {
                    const meetingDoc = pane.doc as Extract<
                      PaneDoc,
                      { kind: "meeting" }
                    >
                    const meetingFocused = pane.id === focusedPaneId
                    return (
                      <div
                        className="flex-1 flex flex-col min-w-0 min-h-0"
                        onMouseDown={() => setFocusedPaneId(pane.id)}
                      >
                        {/* Meeting pane header — focus = deeper tab only */}
                        <div
                          className={cn(
                            "flex items-center gap-2 px-2 py-1.5 border-b shrink-0 min-h-9 transition-colors",
                            meetingFocused
                              ? "border-primary/30 bg-primary/10"
                              : "border-border bg-muted/10"
                          )}
                        >
                          <span
                            className={cn(
                              "font-light text-sm truncate flex-1 min-w-0",
                              meetingFocused
                                ? "text-foreground font-medium"
                                : "text-muted-foreground"
                            )}
                          >
                            {paneTitle(meetingDoc)}
                          </span>
                          <span className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground shrink-0">
                            Meeting
                          </span>
                          {panes.length < 2 && (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 px-1.5 text-[10px] gap-0.5 shrink-0 font-light uppercase tracking-[0.08em] text-muted-foreground hover:text-primary"
                              onClick={addPane}
                              title="Split into second page"
                            >
                              <Columns2 className="h-3.5 w-3.5" />
                              Split
                            </Button>
                          )}
                          {panes.length > 1 && (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 w-6 p-0 shrink-0"
                              onClick={() => closePane(pane.id)}
                              title="Close page"
                            >
                              <X className="h-3.5 w-3.5" />
                            </Button>
                          )}
                        </div>
                        <MeetingSummaryPanel
                          meetingId={meetingDoc.meetingId}
                          tabId={meetingDoc.tabId}
                        />
                      </div>
                    )
                  })()
                )}
              </div>
            ))}
          </div>

          {focusedIsNote && currentNote && (
            <NoteSidebarRight
              references={currentNote.references ?? []}
              injectedInto={currentNote.extracted_into ?? []}
              injectedIntoTitles={new Map(notesList.map((n) => [n.id, n.title]))}
              activeBlockId={activeBlockId}
              onSelectBlock={handleSelectBlock}
              onNavigateToNote={navigateSource}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
