import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { ChevronRight, FileText, GripVertical, Plus, Video } from "lucide-react"
import { cn } from "@/lib/utils"
import { type NoteListItem, type Meeting, type MeetingTab } from "@/api/client"
import { useT } from "@/i18n/use-t"

export type SidebarTab = "notes" | "meetings"

export type ActiveMeetingSelection = {
  meetingId: string
  tabId: string
}

interface NoteSidebarLeftProps {
  notes: NoteListItem[]
  meetings: Meeting[]
  activeNoteIds: string[]
  focusedNoteId?: string | null
  activeMeetings: ActiveMeetingSelection[]
  focusedMeeting?: ActiveMeetingSelection | null
  sidebarTab: SidebarTab
  onSidebarTabChange: (tab: SidebarTab) => void
  onSwitchNote: (id: string) => void
  onOpenMeetingTab: (meetingId: string, tabId: string) => void
  onCreateNote?: () => void
}

export function meetingsWithSummary(meetings: Meeting[]): Meeting[] {
  return meetings.filter((m) => {
    if (m.detail && String(m.detail).trim()) return true
    const tabs = m.tabs ?? []
    if (
      tabs.some(
        (t) =>
          (t.tab_id === "tab_general" || t.type === "general") &&
          !!t.md_file_path
      )
    ) {
      return true
    }
    if (
      tabs.some(
        (t) =>
          (t.type === "section" || t.tab_id) &&
          !!t.md_file_path &&
          t.tab_id !== "tab_general"
      )
    ) {
      return true
    }
    return false
  })
}

function sectionTabsOf(meeting: Meeting): MeetingTab[] {
  return (meeting.tabs ?? []).filter(
    (t) => t.tab_id !== "tab_general" && t.type !== "general"
  )
}

export function NoteSidebarLeft({
  notes,
  meetings,
  activeNoteIds,
  focusedNoteId = null,
  activeMeetings,
  focusedMeeting = null,
  sidebarTab,
  onSidebarTabChange,
  onSwitchNote,
  onOpenMeetingTab,
  onCreateNote,
}: NoteSidebarLeftProps) {
  const t = useT()
  const summarized = useMemo(() => meetingsWithSummary(meetings), [meetings])
  const openNoteSet = useMemo(() => new Set(activeNoteIds), [activeNoteIds])

  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  const trackRef = useRef<HTMLDivElement>(null)
  const notesBtnRef = useRef<HTMLButtonElement>(null)
  const meetingsBtnRef = useRef<HTMLButtonElement>(null)
  const [pill, setPill] = useState({ left: 0, width: 0 })

  const effectiveExpanded = useMemo(() => {
    const next = new Set(expandedIds)
    for (const m of activeMeetings) {
      if (m.meetingId) next.add(m.meetingId)
    }
    return next
  }, [expandedIds, activeMeetings])

  const measurePill = () => {
    const track = trackRef.current
    const btn =
      sidebarTab === "notes" ? notesBtnRef.current : meetingsBtnRef.current
    if (!track || !btn) return
    const tr = track.getBoundingClientRect()
    const br = btn.getBoundingClientRect()
    setPill({ left: br.left - tr.left, width: br.width })
  }

  useLayoutEffect(() => {
    measurePill()
  }, [sidebarTab, onCreateNote])

  useEffect(() => {
    const onResize = () => measurePill()
    window.addEventListener("resize", onResize)
    return () => window.removeEventListener("resize", onResize)
  }, [sidebarTab, onCreateNote])

  const isMeetingTabOpen = (meetingId: string, tabId: string) =>
    activeMeetings.some(
      (m) =>
        m.meetingId === meetingId &&
        (m.tabId === tabId || (tabId === "tab_general" && !m.tabId))
    )

  const isMeetingTabFocused = (meetingId: string, tabId: string) =>
    !!focusedMeeting &&
    focusedMeeting.meetingId === meetingId &&
    (focusedMeeting.tabId === tabId ||
      (tabId === "tab_general" && !focusedMeeting.tabId))

  const toggleExpand = (meetingId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setExpandedIds((prev) => {
      const n = new Set(prev)
      if (n.has(meetingId)) n.delete(meetingId)
      else n.add(meetingId)
      return n
    })
  }

  return (
    <div className="pm-ws-rail">
      {/* Segmented track: sliding pill; + lives inside Notes segment */}
      <div className="pm-ws-seg">
        <div ref={trackRef} className="pm-ws-seg-track">
          <span
            className="pm-ws-seg-indicator"
            style={{
              transform: `translateX(${pill.left}px)`,
              width: pill.width || undefined,
            }}
            aria-hidden
          />
          <button
            ref={notesBtnRef}
            type="button"
            className={cn(
              "pm-ws-seg-btn pm-ws-seg-btn--notes",
              sidebarTab === "notes" && "is-on"
            )}
            onClick={() => onSidebarTabChange("notes")}
          >
            <span>{t("common.notes")}</span>
            {/* + only when Notes tab is active */}
            {onCreateNote && sidebarTab === "notes" && (
              <span
                role="button"
                tabIndex={0}
                className="pm-ws-seg-add"
                title={t("library.newNote")}
                onClick={(e) => {
                  e.stopPropagation()
                  e.preventDefault()
                  onCreateNote()
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.stopPropagation()
                    e.preventDefault()
                    onCreateNote()
                  }
                }}
              >
                <Plus className="h-3.5 w-3.5" strokeWidth={2} />
              </span>
            )}
          </button>
          <button
            ref={meetingsBtnRef}
            type="button"
            className={cn(
              "pm-ws-seg-btn",
              sidebarTab === "meetings" && "is-on"
            )}
            onClick={() => onSidebarTabChange("meetings")}
          >
            {t("meeting.meetings")}
          </button>
        </div>
      </div>

      {/* Dual-mounted panels — opacity crossfade, no hard cut */}
      <div className="pm-ws-list-host">
        <div
          className={cn(
            "pm-ws-list-panel",
            sidebarTab === "notes" && "is-active"
          )}
          aria-hidden={sidebarTab !== "notes"}
        >
          <ScrollArea className="h-full">
            <div className="p-2 space-y-0.5">
              {notes.map((note) => (
                <NoteDraggableItem
                  key={note.id}
                  note={note}
                  isActive={openNoteSet.has(note.id)}
                  isFocused={note.id === focusedNoteId}
                  onClick={() => onSwitchNote(note.id)}
                />
              ))}
              {notes.length === 0 && (
                <p className="pm-ws-empty">{t("library.noNotesInCollection")}</p>
              )}
            </div>
          </ScrollArea>
        </div>

        <div
          className={cn(
            "pm-ws-list-panel",
            sidebarTab === "meetings" && "is-active"
          )}
          aria-hidden={sidebarTab !== "meetings"}
        >
          <ScrollArea className="h-full">
            <div className="p-2 space-y-0.5">
              {summarized.map((meeting) => {
                const sections = sectionTabsOf(meeting)
                const expanded = effectiveExpanded.has(meeting.id)
                const isGeneralActive = isMeetingTabOpen(
                  meeting.id,
                  "tab_general"
                )

                return (
                  <div key={meeting.id}>
                    <MeetingTreeRow
                      meeting={meeting}
                      isActive={isGeneralActive}
                      isFocused={isMeetingTabFocused(meeting.id, "tab_general")}
                      expanded={expanded}
                      hasChildren={sections.length > 0}
                      onToggleExpand={(e) => toggleExpand(meeting.id, e)}
                      onClick={() => {
                        setExpandedIds((prev) => new Set(prev).add(meeting.id))
                        onOpenMeetingTab(meeting.id, "tab_general")
                      }}
                    />
                    {expanded &&
                      sections.map((sec) => (
                        <MeetingSectionRow
                          key={sec.tab_id}
                          meeting={meeting}
                          tab={sec}
                          isActive={isMeetingTabOpen(meeting.id, sec.tab_id)}
                          isFocused={isMeetingTabFocused(
                            meeting.id,
                            sec.tab_id
                          )}
                          onClick={() =>
                            onOpenMeetingTab(meeting.id, sec.tab_id)
                          }
                        />
                      ))}
                  </div>
                )
              })}
              {summarized.length === 0 && (
                <p className="pm-ws-empty">{t("library.noMeetingsSummary")}</p>
              )}
            </div>
          </ScrollArea>
        </div>
      </div>
    </div>
  )
}

function NoteDraggableItem({
  note,
  isActive,
  isFocused,
  onClick,
}: {
  note: NoteListItem
  isActive: boolean
  isFocused: boolean
  onClick: () => void
}) {
  const t = useT()
  const [dragging, setDragging] = useState(false)

  return (
    <button
      type="button"
      className={cn(
        "pm-ws-item group",
        isFocused && "is-focus",
        isActive && !isFocused && "is-active",
        dragging && "opacity-50"
      )}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("application/note-id", note.id)
        e.dataTransfer.setData("application/note-title", note.title || note.id)
        e.dataTransfer.effectAllowed = "copy"
        setDragging(true)
      }}
      onDragEnd={() => setDragging(false)}
      onClick={onClick}
    >
      <GripVertical className="h-3 w-3 text-[var(--pm-faint)] opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
      <FileText className="pm-ws-item-icon h-3.5 w-3.5" />
      <span className="flex-1 truncate">{note.title}</span>
      {note.is_extracted && (
        <span className="pm-ws-item-dot" title={t("library.hasExtracted")} />
      )}
    </button>
  )
}

function MeetingTreeRow({
  meeting,
  isActive,
  isFocused,
  expanded,
  hasChildren,
  onToggleExpand,
  onClick,
}: {
  meeting: Meeting
  isActive: boolean
  isFocused: boolean
  expanded: boolean
  hasChildren: boolean
  onToggleExpand: (e: React.MouseEvent) => void
  onClick: () => void
}) {
  const t = useT()
  const [dragging, setDragging] = useState(false)

  return (
    <div
      className={cn(
        "pm-ws-item group !cursor-default",
        isFocused && "is-focus",
        isActive && !isFocused && "is-active",
        dragging && "opacity-50"
      )}
    >
      <button
        type="button"
        className={cn(
          "h-5 w-5 flex items-center justify-center shrink-0 rounded-[var(--pm-r-sm)]",
          hasChildren
            ? "hover:bg-[color-mix(in_srgb,var(--pm-ink)_6%,transparent)]"
            : "opacity-0 pointer-events-none"
        )}
        onClick={onToggleExpand}
        tabIndex={hasChildren ? 0 : -1}
      >
        <ChevronRight
          className={cn(
            "h-3 w-3 text-[var(--pm-faint)] transition-transform duration-200",
            expanded && "rotate-90"
          )}
        />
      </button>
      <button
        type="button"
        className="flex-1 min-w-0 text-left flex items-center gap-1.5 py-0.5 cursor-grab active:cursor-grabbing bg-transparent border-0 p-0"
        draggable
        onDragStart={(e) => {
          e.dataTransfer.setData("application/meeting-id", meeting.id)
          e.dataTransfer.setData("application/meeting-tab-id", "tab_general")
          e.dataTransfer.setData(
            "application/meeting-title",
            meeting.title || meeting.id
          )
          e.dataTransfer.effectAllowed = "copy"
          setDragging(true)
        }}
        onDragEnd={() => setDragging(false)}
        onClick={onClick}
        title={t("library.openGeneralSummary")}
      >
        <Video className="pm-ws-item-icon h-3.5 w-3.5" />
        <span className="flex-1 truncate">
          {meeting.title || t("library.untitledMeeting")}
        </span>
      </button>
    </div>
  )
}

function MeetingSectionRow({
  meeting,
  tab,
  isActive,
  isFocused,
  onClick,
}: {
  meeting: Meeting
  tab: MeetingTab
  isActive: boolean
  isFocused: boolean
  onClick: () => void
}) {
  const t = useT()
  const [dragging, setDragging] = useState(false)
  const label = tab.name || tab.tab_id

  return (
    <button
      type="button"
      className={cn(
        "pm-ws-item group !pl-8",
        isFocused && "is-focus",
        isActive && !isFocused && "is-active",
        dragging && "opacity-50"
      )}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("application/meeting-id", meeting.id)
        e.dataTransfer.setData("application/meeting-tab-id", tab.tab_id)
        e.dataTransfer.setData(
          "application/meeting-title",
          `${meeting.title || t("nav.meeting")} / ${label}`
        )
        e.dataTransfer.effectAllowed = "copy"
        setDragging(true)
      }}
      onDragEnd={() => setDragging(false)}
      onClick={onClick}
      title={t("library.openSectionDistill")}
    >
      <FileText className="pm-ws-item-icon h-3 w-3" />
      <span className="flex-1 truncate">{label}</span>
    </button>
  )
}
