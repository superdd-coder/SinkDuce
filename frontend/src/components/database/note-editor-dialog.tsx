import {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
} from "react"
import { Dialog, DialogContent } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { ChevronLeft, X } from "lucide-react"
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
  /** Second page after Split — wait for list pick; never clone the same note. */
  | { kind: "empty" }

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
  const panesRef = useRef(panes)
  panesRef.current = panes

  const [focusedPaneId, setFocusedPaneId] = useState<string>("")
  const focusedPaneIdRef = useRef(focusedPaneId)
  focusedPaneIdRef.current = focusedPaneId

  /** Entering / exiting group ids for CSS classes only */
  const [enteringPaneId, setEnteringPaneId] = useState<string | null>(null)
  const [exitingPaneId, setExitingPaneId] = useState<string | null>(null)

  /**
   * Single rail owner. Resting rule: focused note with distill, else null.
   * During merge, may stay on the exiting pane so rail collapses with it.
   */
  const [railOwnerId, setRailOwnerId] = useState<string | null>(null)

  /**
   * Motion mutex — only one phase at a time.
   * idle → rail_close | pane_split | pane_merge | rail_open → idle
   */
  type MotionPhase = "idle" | "rail_close" | "pane_split" | "pane_merge" | "rail_open"
  const phaseRef = useRef<MotionPhase>("idle")
  const [, setPhaseTick] = useState(0) // force re-render when phase changes (chrome lock)
  const setPhase = useCallback((p: MotionPhase) => {
    phaseRef.current = p
    setPhaseTick((n) => n + 1)
  }, [])

  const enteringPaneIdRef = useRef<string | null>(null)
  const exitingPaneIdRef = useRef<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const rafRef = useRef<number[]>([])
  const motionGenRef = useRef(0)

  /** Unified duration — keep in sync with CSS --pm-ws-pane-ms / --pm-ws-rail-r-ms */
  const MOTION_MS = 300

  const [noteMetaById, setNoteMetaById] = useState<Record<string, NoteDetail>>(
    {}
  )
  const noteMetaByIdRef = useRef(noteMetaById)
  noteMetaByIdRef.current = noteMetaById
  const [activeBlockId, setActiveBlockId] = useState<string | null>(null)

  const clearTimers = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    for (const id of rafRef.current) cancelAnimationFrame(id)
    rafRef.current = []
  }, [])

  const bumpGen = useCallback(() => {
    motionGenRef.current += 1
    clearTimers()
    return motionGenRef.current
  }, [clearTimers])

  const isBusy = useCallback(
    () => phaseRef.current !== "idle",
    []
  )

  const noteHasDistill = useCallback((nid: string) => {
    const n = noteMetaByIdRef.current[nid]
    if (!n) return false
    return (
      (n.references?.length ?? 0) > 0 || (n.extracted_into?.length ?? 0) > 0
    )
  }, [])

  const paneHasDistill = useCallback(
    (pane: EditorPane | null | undefined) => {
      if (!pane || pane.doc.kind !== "note") return false
      return noteHasDistill(pane.doc.noteId)
    },
    [noteHasDistill]
  )

  /** Resting rail owner from focus + meta */
  const computeRailOwner = useCallback(
    (focusId: string, list: EditorPane[]) => {
      const p = list.find((x) => x.id === focusId)
      if (!p || !paneHasDistill(p)) return null
      return p.id
    },
    [paneHasDistill]
  )

  const railOpenForPane = useCallback(
    (pane: EditorPane) => {
      if (pane.doc.kind !== "note") return false
      if (!noteHasDistill(pane.doc.noteId)) return false
      return railOwnerId === pane.id
    },
    [railOwnerId, noteHasDistill]
  )

  useEffect(() => {
    return () => {
      bumpGen()
      phaseRef.current = "idle"
    }
  }, [bumpGen])

  // Focus must always point at a live pane
  useEffect(() => {
    if (panes.length === 0) return
    if (!panes.some((p) => p.id === focusedPaneId)) {
      const id = panes[0].id
      setFocusedPaneId(id)
      if (phaseRef.current === "idle") {
        setRailOwnerId(computeRailOwner(id, panes))
      }
    }
  }, [panes, focusedPaneId, computeRailOwner])

  // When idle, keep rail owner in sync with focus + meta loads
  useEffect(() => {
    if (phaseRef.current !== "idle") return
    if (!focusedPaneId) return
    setRailOwnerId(computeRailOwner(focusedPaneId, panes))
  }, [focusedPaneId, panes, noteMetaById, computeRailOwner])

  const focusedPane = useMemo(
    () => panes.find((p) => p.id === focusedPaneId) ?? panes[0] ?? null,
    [panes, focusedPaneId]
  )

  const focusedNoteId =
    focusedPane?.doc.kind === "note" ? focusedPane.doc.noteId : null

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
          const d = p.doc as {
            kind: "meeting"
            meetingId: string
            tabId: string
          }
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
    bumpGen()
    phaseRef.current = "idle"
    enteringPaneIdRef.current = null
    exitingPaneIdRef.current = null
    setEnteringPaneId(null)
    setExitingPaneId(null)
    setPanes([{ id: pid, doc: { kind: "note", noteId } }])
    setFocusedPaneId(pid)
    setRailOwnerId(null) // meta may load later → idle effect recomputes
    setSidebarTab("notes")
    setNoteMetaById({})
    setActiveBlockId(null)
    void import("@/components/ui/tiptap-editor").then((m) => {
      m.invalidateMeetingSpeakerCache()
    })
  }, [open, noteId, bumpGen])

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

  /**
   * Load a document into the focused pane (or the empty sibling after Split).
   * If this note/meeting is already open in a pane, only focus that pane —
   * never mount two editors on the same document.
   */
  const setFocusedDoc = useCallback(
    (doc: PaneDoc) => {
      if (doc.kind === "empty") return
      if (isBusy()) return

      setPanes((prev) => {
        if (prev.length === 0) {
          const id = newPaneId()
          queueMicrotask(() => {
            setFocusedPaneId(id)
            setRailOwnerId(
              doc.kind === "note" && noteHasDistill(doc.noteId) ? id : null
            )
          })
          return [{ id, doc }]
        }

        if (doc.kind === "note") {
          const existing = prev.find(
            (p) => p.doc.kind === "note" && p.doc.noteId === doc.noteId
          )
          if (existing) {
            queueMicrotask(() => {
              setFocusedPaneId(existing.id)
              setRailOwnerId(computeRailOwner(existing.id, prev))
            })
            return prev
          }
        } else if (doc.kind === "meeting") {
          const existing = prev.find(
            (p) =>
              p.doc.kind === "meeting" &&
              p.doc.meetingId === doc.meetingId &&
              p.doc.tabId === doc.tabId
          )
          if (existing) {
            queueMicrotask(() => {
              setFocusedPaneId(existing.id)
              setRailOwnerId(null)
            })
            return prev
          }
        }

        const empty = prev.find((p) => p.doc.kind === "empty")
        if (empty) {
          const next = prev.map((p) =>
            p.id === empty.id ? { ...p, doc } : p
          )
          queueMicrotask(() => {
            setFocusedPaneId(empty.id)
            setRailOwnerId(computeRailOwner(empty.id, next))
          })
          return next
        }

        const fid =
          focusedPaneIdRef.current &&
          prev.some((p) => p.id === focusedPaneIdRef.current)
            ? focusedPaneIdRef.current
            : prev[0].id
        const next = prev.map((p) => (p.id === fid ? { ...p, doc } : p))
        queueMicrotask(() => {
          setRailOwnerId(computeRailOwner(fid, next))
        })
        return next
      })
    },
    [isBusy, noteHasDistill, computeRailOwner]
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

  /** User clicked a pane chrome / body — switch focus (+ rail at rest). */
  const focusPane = useCallback(
    (paneId: string) => {
      if (isBusy()) return
      if (!panesRef.current.some((p) => p.id === paneId)) return
      setFocusedPaneId(paneId)
      setRailOwnerId(computeRailOwner(paneId, panesRef.current))
    },
    [isBusy, computeRailOwner]
  )

  /**
   * pane_split: mount new pane collapsed, expand together with source narrowing.
   * (No pre-split empty half — that looked awkward.)
   */
  const runPaneSplit = useCallback(
    (newPane: EditorPane, opts: { focusAfter: boolean }) => {
      if (panesRef.current.length >= 2) return

      const gen = bumpGen()
      setPhase("pane_split")
      enteringPaneIdRef.current = newPane.id
      // Mount collapsed under is-entering; double-rAF then expand
      setEnteringPaneId(newPane.id)
      setPanes((prev) => {
        if (prev.length >= 2) return prev
        if (prev.some((p) => p.id === newPane.id)) return prev
        return [...prev, newPane]
      })

      const r1 = requestAnimationFrame(() => {
        if (motionGenRef.current !== gen) return
        const el = document.querySelector(
          ".pm-ws-pane-group.is-entering"
        ) as HTMLElement | null
        void el?.offsetWidth
        const r2 = requestAnimationFrame(() => {
          if (motionGenRef.current !== gen) return
          setEnteringPaneId(null)
          enteringPaneIdRef.current = null
          timerRef.current = setTimeout(() => {
            if (motionGenRef.current !== gen) return
            if (opts.focusAfter) {
              const list = panesRef.current
              if (list.some((p) => p.id === newPane.id)) {
                setFocusedPaneId(newPane.id)
                setRailOwnerId(computeRailOwner(newPane.id, list))
              }
            }
            setPhase("idle")
            timerRef.current = null
          }, MOTION_MS)
        })
        rafRef.current.push(r2)
      })
      rafRef.current.push(r1)
    },
    [bumpGen, setPhase, computeRailOwner]
  )

  /**
   * Distill sidebar / open-beside: keep focus + rail; only split widths.
   */
  const openDocBeside = useCallback(
    (doc: PaneDoc) => {
      if (doc.kind === "empty") return
      if (isBusy()) return

      const live = panesRef.current

      if (doc.kind === "note") {
        if (
          live.some(
            (p) => p.doc.kind === "note" && p.doc.noteId === doc.noteId
          )
        ) {
          return
        }
      } else if (doc.kind === "meeting") {
        if (
          live.some(
            (p) =>
              p.doc.kind === "meeting" &&
              p.doc.meetingId === doc.meetingId &&
              p.doc.tabId === doc.tabId
          )
        ) {
          return
        }
      }

      const empty = live.find((p) => p.doc.kind === "empty")
      if (empty) {
        setPanes((prev) =>
          prev.map((p) => (p.id === empty.id ? { ...p, doc } : p))
        )
        return
      }

      if (live.length >= 2) {
        const fid =
          focusedPaneIdRef.current &&
          live.some((p) => p.id === focusedPaneIdRef.current)
            ? focusedPaneIdRef.current
            : live[0]?.id
        if (!fid) return
        setPanes((prev) =>
          prev.map((p) => (p.id === fid ? p : { ...p, doc }))
        )
        return
      }

      if (live.length === 0) return
      // railOwnerId unchanged — source keeps distill
      runPaneSplit({ id: newPaneId(), doc }, { focusAfter: false })
    },
    [isBusy, runPaneSplit]
  )

  /**
   * Split button: empty second page.
   * If rail open → rail_close first, then pane_split, then focus empty.
   */
  const addPane = useCallback(() => {
    if (isBusy()) return
    if (panesRef.current.length >= 2) return

    const live = panesRef.current
    const focused =
      live.find((p) => p.id === focusedPaneIdRef.current) ?? live[0] ?? null
    const railIsOpen =
      !!focused && railOwnerId === focused.id && paneHasDistill(focused)

    const doSplit = () => {
      runPaneSplit(
        { id: newPaneId(), doc: { kind: "empty" } },
        { focusAfter: true }
      )
    }

    if (railIsOpen) {
      const gen = bumpGen()
      setPhase("rail_close")
      setRailOwnerId(null)
      timerRef.current = setTimeout(() => {
        if (motionGenRef.current !== gen) return
        doSplit()
      }, MOTION_MS)
      return
    }

    doSplit()
  }, [isBusy, railOwnerId, paneHasDistill, bumpGen, setPhase, runPaneSplit])

  /**
   * Close one pane (pane_merge).
   *
   * Case A — close unfocused (e.g. empty right split while left+distill focused):
   *   Keep focus + rail on survivor; empty collapses; left group (editor+rail)
   *   expands together so distill “slides” with the editor. Never close the rail.
   *
   * Case B — close focused pane that owns rail:
   *   Rail rides the exiting group, then opens on survivor after merge if needed.
   */
  const closePane = useCallback(
    (paneId: string) => {
      if (exitingPaneIdRef.current === paneId) return

      const live = panesRef.current
      if (live.length <= 1) return
      if (!live.some((p) => p.id === paneId)) return

      // Still fully collapsed under is-entering — drop without anim
      if (enteringPaneIdRef.current === paneId && enteringPaneId === paneId) {
        bumpGen()
        setPhase("idle")
        enteringPaneIdRef.current = null
        setEnteringPaneId(null)
        setPanes((cur) => {
          if (cur.length <= 1) return cur
          const next = cur.filter((p) => p.id !== paneId)
          if (next.length === 0) return cur
          const fid = next[0].id
          setFocusedPaneId(fid)
          setRailOwnerId(computeRailOwner(fid, next))
          return next
        })
        return
      }

      if (isBusy() && phaseRef.current !== "pane_split") return
      if (isBusy() && phaseRef.current === "pane_split") {
        clearTimers()
        enteringPaneIdRef.current = null
        setEnteringPaneId(null)
      }

      const survivor = live.find((p) => p.id !== paneId)
      if (!survivor) return

      const closingIsFocused = focusedPaneIdRef.current === paneId
      const closingOwnedRail = railOwnerId === paneId
      const survivorKeepsRail =
        railOwnerId === survivor.id ||
        (!closingOwnedRail && paneHasDistill(survivor))

      const gen = bumpGen()
      setPhase("pane_merge")
      exitingPaneIdRef.current = paneId
      // Apply is-exiting immediately so flex transition starts with the click
      setExitingPaneId(paneId)

      if (closingIsFocused) {
        // Focus moves to survivor; rail may still ride the exiting group
        setFocusedPaneId(survivor.id)
        if (closingOwnedRail) {
          // keep railOwnerId === paneId for the exit duration
        } else {
          setRailOwnerId(
            paneHasDistill(survivor) ? survivor.id : null
          )
        }
      } else {
        /*
         * Closing empty / unfocused right pane while left+distill is focused:
         * pin focus + rail on survivor for the whole merge — distill stays open
         * and slides as the left group flex-grows.
         */
        setFocusedPaneId(survivor.id)
        if (survivorKeepsRail || paneHasDistill(survivor)) {
          setRailOwnerId(survivor.id)
        }
        // else leave railOwner as-is / null
      }

      const r1 = requestAnimationFrame(() => {
        if (motionGenRef.current !== gen) return
        const el = document.querySelector(
          ".pm-ws-pane-group.is-exiting"
        ) as HTMLElement | null
        void el?.offsetWidth
      })
      rafRef.current.push(r1)

      timerRef.current = setTimeout(() => {
        if (motionGenRef.current !== gen) return
        let nextList: EditorPane[] | null = null
        setPanes((cur) => {
          if (cur.length <= 1) return cur
          const next = cur.filter((p) => p.id !== paneId)
          if (next.length === 0) return cur
          nextList = next
          return next
        })
        if (exitingPaneIdRef.current === paneId) {
          exitingPaneIdRef.current = null
        }
        setExitingPaneId(null)

        const list = nextList ?? panesRef.current
        const fid = list.some((p) => p.id === survivor.id)
          ? survivor.id
          : list[0]?.id
        if (!fid) {
          setPhase("idle")
          timerRef.current = null
          return
        }
        setFocusedPaneId(fid)

        if (closingOwnedRail) {
          // Rail rode exit — open on survivor if it has distill (after width settles)
          const nextRail = computeRailOwner(fid, list)
          setRailOwnerId(null)
          if (nextRail) {
            setPhase("rail_open")
            timerRef.current = setTimeout(() => {
              if (motionGenRef.current !== gen) return
              setRailOwnerId(nextRail)
              timerRef.current = setTimeout(() => {
                if (motionGenRef.current !== gen) return
                setPhase("idle")
                timerRef.current = null
              }, MOTION_MS)
            }, 16)
          } else {
            setPhase("idle")
            timerRef.current = null
          }
        } else {
          // Rail stayed on survivor (e.g. closed empty right) — recompute only, no close/reopen
          setRailOwnerId(computeRailOwner(fid, list))
          setPhase("idle")
          timerRef.current = null
        }
      }, MOTION_MS)
    },
    [
      bumpGen,
      setPhase,
      isBusy,
      railOwnerId,
      computeRailOwner,
      paneHasDistill,
      enteringPaneId,
      clearTimers,
    ]
  )

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
              const busy = phaseRef.current !== "idle"
              const docKey =
                pane.doc.kind === "note"
                  ? `n-${pane.doc.noteId}`
                  : pane.doc.kind === "meeting"
                    ? `m-${pane.doc.meetingId}-${pane.doc.tabId}`
                    : `empty-${pane.id}`
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
              const paneFocused =
                pane.id === focusedPaneId && !isExiting
              /*
               * Keep Split/Close chrome stable during width motion.
               * Hiding both while busy made the title row jump mid-animation.
               */
              const paneChromeClose =
                panes.length > 1 && !isExiting
              const paneChromeSplit =
                panes.length < 2 && !isEntering && !busy

              return (
                /*
                 * Pane group = editor + optional distill rail.
                 * Groups always equal flex; rail width is internal only.
                 */
                <div
                  key={pane.id}
                  className={cn(
                    "pm-ws-pane-group",
                    isEntering && "is-entering",
                    isExiting && "is-exiting",
                    showDistillRail && "has-rail",
                    panes.length > 1 && paneFocused && "is-focused"
                  )}
                >
                  <div className="pm-ws-pane-slot">
                    <div className="pm-ws-card pm-ws-card--pane">
                      {pane.doc.kind === "note" ? (
                        <NotePane
                          key={pane.id}
                          collection={collection}
                          noteId={pane.doc.noteId}
                          focused={paneFocused}
                          showFocusChrome={
                            panes.length > 1 && paneFocused
                          }
                          onFocus={() => {
                            if (!isExiting) focusPane(pane.id)
                          }}
                          showClose={paneChromeClose}
                          onClosePane={() => closePane(pane.id)}
                          showSplit={paneChromeSplit}
                          onSplit={addPane}
                          onCloseDialog={() => onOpenChange(false)}
                          onTitleChange={handleTitleChange}
                          onDeleted={(nid) => void handleNoteDeleted(nid)}
                          onNoteMeta={rememberNoteMeta}
                          onNavigateSource={navigateSource}
                          className="pm-ws-doc-surface"
                          docSwapKey={docKey}
                        />
                      ) : pane.doc.kind === "meeting" ? (
                        (() => {
                          const meetingDoc = pane.doc
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
                                const finish = () => {
                                  window.removeEventListener("mouseup", finish, true)
                                  focusPane(pane.id)
                                }
                                window.addEventListener("mouseup", finish, true)
                              }}
                            >
                              <MeetingSummaryPanel
                                meetingId={meetingDoc.meetingId}
                                tabId={meetingDoc.tabId}
                                paneChrome={{
                                  focused:
                                    paneFocused && panes.length > 1,
                                  onFocus: () => {
                                    if (!isExiting) focusPane(pane.id)
                                  },
                                  showSplit: paneChromeSplit,
                                  onSplit: addPane,
                                  showClose: paneChromeClose,
                                  onClose: () => closePane(pane.id),
                                }}
                              />
                            </div>
                          )
                        })()
                      ) : (
                        /* Empty second page after Split — pick hint only */
                        <div
                          className="relative flex-1 flex flex-col min-h-0 min-w-0 pm-ws-doc-surface"
                          onMouseDown={() => {
                            if (!isExiting) focusPane(pane.id)
                          }}
                        >
                          {paneChromeClose && (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="pm-ws-icon-btn !h-6 !w-6 absolute top-3 right-3 z-10"
                              onClick={(e) => {
                                e.stopPropagation()
                                closePane(pane.id)
                              }}
                              title="Close page"
                            >
                              <X className="h-3.5 w-3.5" />
                            </Button>
                          )}
                          <div className="flex-1 flex flex-col items-center justify-center px-8 text-center select-none">
                            <p className="text-[13px] font-normal text-[var(--pm-muted)]">
                              Select a note or meeting
                            </p>
                          </div>
                        </div>
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
