import { useEffect, useRef, useState } from "react"
import { Loader2, Pencil, Eye, Columns2, X, Download } from "lucide-react"
import { toast } from "sonner"
import {
  getMeeting,
  getSectionMd,
  saveSectionMd,
  type Meeting,
} from "@/api/client"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { Button } from "@/components/ui/button"
import { SummaryMarkdownViewer } from "@/components/meeting/summary-markdown-viewer"
import { cn } from "@/lib/utils"

const SAVE_DELAY = 800

/** Fullwidth-ish brackets that Tiptap will not escape as markdown link syntax. */
const CITE_OPEN = "\u27E6" // ⟦
const CITE_CLOSE = "\u27E7" // ⟧

/**
 * Undo Tiptap/markdown over-escaping that corrupts meeting summary source.
 * - \[ \]  → citation / link brackets
 * - \~     → tilde (approx. numbers, paths); md uses ~~ for strike
 * - \_     → underscore in IDs/names when not emphasis
 */
function unescapeMarkdownOverEscapes(md: string): string {
  return (md || "")
    .replace(/\\\[/g, "[")
    .replace(/\\\]/g, "]")
    .replace(/\\~/g, "~")
    .replace(/\\_/g, "_")
}

/**
 * Protect citation / speaker / priority markers while inside Tiptap so they are
 * not serialized as \[stt_…\]. Editor shows ⟦stt_…⟧; disk always stores [stt_…].
 */
function protectCitationsForTiptap(md: string): string {
  let s = unescapeMarkdownOverEscapes(md)
  // [stt_…], [ref: stt_…], ranges/lists inside
  s = s.replace(
    /\[((?:ref:\s*)?stt_[^\]]+)\]/gi,
    `${CITE_OPEN}$1${CITE_CLOSE}`,
  )
  // [ref:67] / [ref:67,70] / [ref:67-70] (pre-persist / streaming)
  s = s.replace(
    /\[(ref:\s*\d+(?:\s*[-–,]\s*\d+)*(?:\s*,\s*\d+(?:\s*[-–,]\s*\d+)*)*)\]/gi,
    `${CITE_OPEN}$1${CITE_CLOSE}`,
  )
  // [priority: high|medium|low]
  s = s.replace(
    /\[(\s*priority\s*:\s*(?:high|medium|low)\s*)\]/gi,
    `${CITE_OPEN}$1${CITE_CLOSE}`,
  )
  // [spk:ID]
  s = s.replace(/\[(spk:[^\]]+)\]/gi, `${CITE_OPEN}$1${CITE_CLOSE}`)
  return s
}

/** Restore protected citations + strip Tiptap over-escapes before writing to disk. */
function restoreCitationsFromTiptap(md: string): string {
  let s = md || ""
  s = s.split(CITE_OPEN).join("[").split(CITE_CLOSE).join("]")
  // If Tiptap still escaped the fullwidth forms
  s = s.replace(/\\⟦/g, "[").replace(/\\⟧/g, "]")
  s = unescapeMarkdownOverEscapes(s)
  return s
}

interface MeetingSummaryPanelProps {
  meetingId: string
  /** ``tab_general`` or a section tab id — one file at a time */
  tabId: string
  onMeetingLoaded?: (meeting: Meeting, sectionTitle: string) => void
  /**
   * Note-editor dual-pane chrome — same two-row header as NotePane:
   * row1 meeting title · Split · close; row2 section name only · Edit.
   */
  paneChrome?: {
    focused: boolean
    /** Promote this meeting pane without requiring a prior body click */
    onFocus?: () => void
    showSplit?: boolean
    onSplit?: () => void
    showClose?: boolean
    onClose?: () => void
  }
}

/**
 * Single meeting summary file in the note editor.
 *
 * Display: Meeting-style viewer (speakers/refs/priority as UI only).
 * Edit: Tiptap WYSIWYG; citations protected in-editor so they are not escaped.
 * Disk always stores real [stt_…] / [spk:…] / [priority:…] markers.
 */
export function MeetingSummaryPanel({
  meetingId,
  tabId,
  onMeetingLoaded,
  paneChrome,
}: MeetingSummaryPanelProps) {
  const [loading, setLoading] = useState(true)
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false)
  type DocSwapPhase = "idle" | "out" | "in"
  const [docSwapPhase, setDocSwapPhase] = useState<DocSwapPhase>("idle")
  /**
   * Canonical raw markdown with real [ ] — used for viewer + disk.
   * Edit mode uses a protected copy for Tiptap only.
   */
  const [rawContent, setRawContent] = useState("")
  /** Tiptap-bound value with protected citations (⟦…⟧). */
  const [editContent, setEditContent] = useState("")
  const [speakerNames, setSpeakerNames] = useState<Record<string, string>>({})
  const [sectionTitle, setSectionTitle] = useState("General Summary")
  const [meetingTitle, setMeetingTitle] = useState("")
  const [mode, setMode] = useState<"view" | "edit">("view")
  const baselineRef = useRef("")
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const loadKeyRef = useRef("")
  const SWAP_OUT_MS = 280
  const SWAP_IN_MS = 420

  useEffect(() => {
    let cancelled = false
    const loadKey = `${meetingId}:${tabId}`
    loadKeyRef.current = loadKey
    const timers: ReturnType<typeof setTimeout>[] = []
    const wait = (ms: number) =>
      new Promise<void>((resolve) => {
        timers.push(setTimeout(resolve, ms))
      })

    setLoading(true)
    setMode("view")
    const isSoft = hasLoadedOnce
    const outStartedAt = performance.now()

    if (!hasLoadedOnce) {
      setRawContent("")
      setEditContent("")
      baselineRef.current = ""
      setDocSwapPhase("idle")
    } else {
      setDocSwapPhase("out")
    }

    ;(async () => {
      try {
        const m = await getMeeting(meetingId)
        if (cancelled || loadKeyRef.current !== loadKey) return

        setMeetingTitle(m.title || "")
        setSpeakerNames(m.speaker_names ?? {})

        let name = "General Summary"
        if (tabId !== "tab_general") {
          const tab = (m.tabs ?? []).find((t) => t.tab_id === tabId)
          name = tab?.name || tabId
        }
        setSectionTitle(name)
        onMeetingLoaded?.(m, name)

        let raw: string | null = null
        try {
          raw = await getSectionMd(meetingId, tabId)
        } catch {
          raw = null
        }
        if (
          (raw === null || !raw.trim()) &&
          tabId === "tab_general" &&
          m.detail
        ) {
          raw = m.detail
        }

        if (cancelled || loadKeyRef.current !== loadKey) return

        const disk = raw ?? ""
        const text = unescapeMarkdownOverEscapes(disk)

        if (isSoft) {
          const elapsed = performance.now() - outStartedAt
          if (elapsed < SWAP_OUT_MS) await wait(SWAP_OUT_MS - elapsed)
          await wait(40)
          if (cancelled || loadKeyRef.current !== loadKey) return
        }

        setRawContent(text)
        baselineRef.current = text
        setLoading(false)
        setHasLoadedOnce(true)

        if (isSoft) {
          await new Promise<void>((r) => {
            requestAnimationFrame(() => requestAnimationFrame(() => r()))
          })
        }
        if (cancelled || loadKeyRef.current !== loadKey) return
        setDocSwapPhase("in")
        await wait(SWAP_IN_MS)
        if (cancelled || loadKeyRef.current !== loadKey) return
        setDocSwapPhase("idle")

        if (text !== disk && text) {
          saveSectionMd(meetingId, tabId, text).catch(() => {})
        }
      } catch {
        if (!cancelled) {
          toast.error("Failed to load meeting summary")
          setLoading(false)
          setDocSwapPhase("idle")
        }
      }
    })()

    return () => {
      cancelled = true
      for (const t of timers) clearTimeout(t)
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current)
        saveTimerRef.current = null
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meetingId, tabId])

  const scheduleSaveRaw = (canonical: string) => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(async () => {
      try {
        const toSave = restoreCitationsFromTiptap(canonical)
        await saveSectionMd(meetingId, tabId, toSave)
        baselineRef.current = toSave
        setRawContent(toSave)
      } catch {
        toast.error("Failed to save summary")
      }
    }, SAVE_DELAY)
  }

  const enterEdit = () => {
    // Protect citations so Tiptap does not turn [stt_…] into \[stt_…\]
    setEditContent(protectCitationsForTiptap(rawContent))
    setMode("edit")
  }

  const exitEdit = () => {
    // Flush pending save
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }
    const canonical = restoreCitationsFromTiptap(editContent)
    setRawContent(canonical)
    if (canonical !== baselineRef.current) {
      void saveSectionMd(meetingId, tabId, canonical)
        .then(() => {
          baselineRef.current = canonical
        })
        .catch(() => toast.error("Failed to save summary"))
    }
    setMode("view")
  }

  const handleEditChange = (value: string) => {
    setEditContent(value)
    const canonical = restoreCitationsFromTiptap(value)
    // Keep rawContent in sync for quick preview switch
    setRawContent(canonical)
    if (canonical !== baselineRef.current) {
      scheduleSaveRaw(canonical)
    }
  }

  const softLoading = loading && hasLoadedOnce
  const hardLoading = loading && !hasLoadedOnce
  const swapBusy =
    docSwapPhase === "out" || docSwapPhase === "in" || softLoading

  if (hardLoading) {
    return (
      <div className="pm-ws-loading flex-1 is-doc-in min-h-0">
        <Loader2 className="h-5 w-5 animate-spin" />
        Loading…
      </div>
    )
  }

  const claimFocus = () => {
    if (paneChrome && !paneChrome.focused) paneChrome.onFocus?.()
  }

  const handleDownload = () => {
    claimFocus()
    const body =
      mode === "edit"
        ? restoreCitationsFromTiptap(editContent)
        : rawContent
    const blob = new Blob([body || ""], {
      type: "text/markdown;charset=utf-8",
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    const safeMeet = (meetingTitle || "meeting").replace(/[\\/:*?"<>|]+/g, "-")
    const safeSec = (sectionTitle || "summary").replace(/[\\/:*?"<>|]+/g, "-")
    a.download = `${safeMeet} - ${safeSec}.md`
    a.click()
    URL.revokeObjectURL(url)
    toast.success("Downloaded")
  }

  const downloadBtn = (
    <Button
      variant="ghost"
      size="sm"
      className="pm-ws-icon-btn"
      onClick={handleDownload}
      title="Download"
      aria-label="Download"
    >
      <Download className="h-3.5 w-3.5" />
    </Button>
  )

  const editToggle = (
    <Button
      variant="ghost"
      size="sm"
      className="pm-ws-action shrink-0"
      onClick={() => {
        claimFocus()
        if (mode === "view") enterEdit()
        else exitEdit()
      }}
      title={
        mode === "view"
          ? "Edit with Tiptap (citations protected)"
          : "Back to Meeting-style preview"
      }
    >
      {mode === "view" ? (
        <>
          <Pencil className="h-3.5 w-3.5" />
          Edit
        </>
      ) : (
        <>
          <Eye className="h-3.5 w-3.5" />
          Preview
        </>
      )}
    </Button>
  )

  /** Title for row 1 — meeting name (not section) */
  const headerMeetingTitle = meetingTitle || "Meeting"

  return (
    <div className="flex-1 flex flex-col min-w-0 min-h-0">
      {paneChrome ? (
        /* Same two-row chrome language as NotePane */
        <div
          className={cn("pm-ws-pane-h", paneChrome.focused && "is-focus")}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <div className="flex items-center gap-1.5 px-4 pt-3 pb-1 min-h-9">
            <span
              className={cn(
                "pm-ws-pane-title flex-1 min-w-0",
                docSwapPhase === "out" && "is-title-out",
                docSwapPhase === "in" && "is-title-in"
              )}
              onClick={claimFocus}
            >
              {headerMeetingTitle}
            </span>
            {paneChrome.showSplit && paneChrome.onSplit && (
              <Button
                variant="ghost"
                size="sm"
                className="pm-ws-icon-btn !h-6 !w-6"
                onClick={() => {
                  claimFocus()
                  paneChrome.onSplit?.()
                }}
                title="Split into second page"
              >
                <Columns2 className="h-3.5 w-3.5" />
              </Button>
            )}
            {paneChrome.showClose && paneChrome.onClose && (
              <Button
                variant="ghost"
                size="sm"
                className="pm-ws-icon-btn !h-6 !w-6"
                onClick={() => paneChrome.onClose?.()}
                title="Close page"
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
          {/* Row 2: section name · Download · Edit/Preview */}
          <div className="flex flex-nowrap items-center gap-0.5 px-4 pb-2.5 min-w-0">
            <span
              className="pm-meta truncate flex-1 min-w-0 text-[var(--pm-muted)]"
              title={sectionTitle}
            >
              {sectionTitle}
            </span>
            {downloadBtn}
            {editToggle}
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-2 px-2.5 pb-2 pt-1.5 min-w-0 shrink-0">
          <span
            className="pm-meta truncate flex-1 min-w-0 text-[var(--pm-muted)]"
            title={sectionTitle}
          >
            {sectionTitle}
          </span>
          {downloadBtn}
          {editToggle}
        </div>
      )}

      <div
        className={cn(
          "pm-ws-doc-body pm-ws-meeting-summary relative flex-1 min-h-0 flex flex-col",
          docSwapPhase === "out" && "is-doc-out",
          docSwapPhase === "in" && "is-doc-in",
          docSwapPhase === "idle" && "is-doc-idle"
        )}
      >
        {/*
          Same reading shell as Meeting content card Summary:
          body-prose pad + body-read + SummaryMarkdownViewer (full card width).
        */}
        <div
          className={cn(
            "flex-1 overflow-y-auto min-h-0",
            swapBusy && "pointer-events-none select-none"
          )}
        >
          {mode === "view" ? (
            <div className="pm-meeting-body-prose pm-ws-meeting-body-prose">
              <div className="pm-meeting-body-read">
                <SummaryMarkdownViewer
                  md={rawContent}
                  speakerNames={speakerNames}
                  onRefClick={() => {
                    /* note context: no transcript seek */
                  }}
                />
              </div>
            </div>
          ) : (
            <div className="pm-meeting-body-prose pm-ws-meeting-body-prose">
              <MarkdownEditor
                value={editContent}
                onChange={swapBusy ? () => {} : handleEditChange}
                showToolbar={false}
                flush
                className="min-h-[200px] w-full max-w-none"
                placeholder="Summary is empty..."
              />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
