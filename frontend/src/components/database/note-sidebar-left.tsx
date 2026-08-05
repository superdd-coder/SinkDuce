import { useMemo, useState } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { ChevronRight, FileText, GripVertical, Plus, Video } from "lucide-react"
import { cn } from "@/lib/utils"
import { type NoteListItem, type Meeting, type MeetingTab } from "@/api/client"

export type SidebarTab = "notes" | "meetings"

export type ActiveMeetingSelection = {
  meetingId: string
  tabId: string
}

interface NoteSidebarLeftProps {
  notes: NoteListItem[]
  meetings: Meeting[]
  /** All note ids open in panes (dual-pane: both highlighted) */
  activeNoteIds: string[]
  /** Note id of the currently focused pane (stronger highlight) */
  focusedNoteId?: string | null
  /** All meeting tabs open in panes (dual-pane: both highlighted) */
  activeMeetings: ActiveMeetingSelection[]
  /** Meeting tab of the currently focused pane (stronger highlight) */
  focusedMeeting?: ActiveMeetingSelection | null
  sidebarTab: SidebarTab
  onSidebarTabChange: (tab: SidebarTab) => void
  onSwitchNote: (id: string) => void
  /** Open a meeting tab (general or section) in the focused pane */
  onOpenMeetingTab: (meetingId: string, tabId: string) => void
  onCreateNote?: () => void
}

/** Meetings that already have a General Summary (or legacy detail). */
export function meetingsWithSummary(meetings: Meeting[]): Meeting[] {
  return meetings.filter((m) => {
    if (m.detail && String(m.detail).trim()) return true
    const tabs = m.tabs ?? []
    // General summary written to disk (or legacy detail above)
    if (
      tabs.some(
        (t) =>
          (t.tab_id === "tab_general" || t.type === "general") &&
          !!t.md_file_path
      )
    ) {
      return true
    }
    // Section summaries only if pipeline wrote md files
    if (tabs.some((t) => (t.type === "section" || t.tab_id) && !!t.md_file_path && t.tab_id !== "tab_general")) {
      return true
    }
    return false
  })
}

function sectionTabsOf(meeting: Meeting): MeetingTab[] {
  return (meeting.tabs ?? []).filter(
    (t) => t.tab_id !== "tab_general" && t.type !== "general",
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
  const summarized = useMemo(() => meetingsWithSummary(meetings), [meetings])
  const openNoteSet = useMemo(() => new Set(activeNoteIds), [activeNoteIds])

  // Expand tree for every meeting that has an open tab (dual-pane: both)
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())

  const effectiveExpanded = useMemo(() => {
    const next = new Set(expandedIds)
    for (const m of activeMeetings) {
      if (m.meetingId) next.add(m.meetingId)
    }
    return next
  }, [expandedIds, activeMeetings])

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
    <div className="w-72 border-r border-border flex flex-col shrink-0">
      {/* NOTES | MEETINGS switch */}
      <div className="px-2 h-9 border-b border-border flex items-center gap-1 shrink-0">
        <button
          type="button"
          className={cn(
            "flex-1 h-7 text-[11px] font-semibold uppercase tracking-[0.18em] rounded-sm transition-colors",
            sidebarTab === "notes"
              ? "text-primary bg-primary/10"
              : "text-muted-foreground hover:text-foreground"
          )}
          onClick={() => onSidebarTabChange("notes")}
        >
          Notes
        </button>
        <button
          type="button"
          className={cn(
            "flex-1 h-7 text-[11px] font-semibold uppercase tracking-[0.18em] rounded-sm transition-colors",
            sidebarTab === "meetings"
              ? "text-primary bg-primary/10"
              : "text-muted-foreground hover:text-foreground"
          )}
          onClick={() => onSidebarTabChange("meetings")}
        >
          Meetings
        </button>
        {sidebarTab === "notes" && onCreateNote && (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0 shrink-0"
            onClick={onCreateNote}
            title="New Note"
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>

      <ScrollArea className="flex-1">
        <div className="p-1.5 space-y-0.5">
          {sidebarTab === "notes" ? (
            <>
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
                <p className="text-xs text-muted-foreground px-2 py-4 text-center">
                  No notes in this collection
                </p>
              )}
            </>
          ) : (
            <>
              {summarized.map((meeting) => {
                const sections = sectionTabsOf(meeting)
                const expanded = effectiveExpanded.has(meeting.id)
                const isGeneralActive = isMeetingTabOpen(meeting.id, "tab_general")

                return (
                  <div key={meeting.id} className="space-y-0">
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
                          isFocused={isMeetingTabFocused(meeting.id, sec.tab_id)}
                          onClick={() => onOpenMeetingTab(meeting.id, sec.tab_id)}
                        />
                      ))}
                  </div>
                )
              })}
              {summarized.length === 0 && (
                <p className="text-xs text-muted-foreground px-2 py-4 text-center">
                  No meetings with summary yet
                </p>
              )}
            </>
          )}
        </div>
      </ScrollArea>
    </div>
  )
}

// ── Notes ─────────────────────────────────────────────────────────

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
  const [dragging, setDragging] = useState(false)

  return (
    <button
      className={cn(
        "w-full text-left flex items-center gap-2 px-2 py-1.5 text-[12px] transition-colors group border-b border-dashed border-border",
        isFocused
          ? "text-primary font-semibold bg-primary/18"
          : isActive
            ? "text-primary/90 font-medium bg-primary/8"
            : "hover:text-primary text-foreground cursor-grab active:cursor-grabbing",
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
      <GripVertical className="h-3 w-3 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
      <FileText
        className={cn(
          "h-3.5 w-3.5 shrink-0",
          isFocused ? "text-primary" : "text-muted-foreground"
        )}
      />
      <span className="flex-1 truncate">{note.title}</span>
      {note.is_extracted && (
        <span className="h-1.5 w-1.5 rounded-full bg-primary shrink-0" title="Has been extracted" />
      )}
    </button>
  )
}

// ── Meetings tree ─────────────────────────────────────────────────

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
  const [dragging, setDragging] = useState(false)

  return (
    <div
      className={cn(
        "w-full flex items-center gap-0.5 px-1 py-1 text-[12px] transition-colors group border-b border-dashed border-border",
        isFocused
          ? "text-primary font-semibold bg-primary/18"
          : isActive
            ? "text-primary/90 font-medium bg-primary/8"
            : "hover:text-primary text-foreground",
        dragging && "opacity-50"
      )}
    >
      <button
        type="button"
        className={cn(
          "h-5 w-5 flex items-center justify-center shrink-0 rounded-sm",
          hasChildren ? "hover:bg-accent/60" : "opacity-0 pointer-events-none"
        )}
        onClick={onToggleExpand}
        tabIndex={hasChildren ? 0 : -1}
      >
        <ChevronRight
          className={cn(
            "h-3 w-3 text-muted-foreground transition-transform",
            expanded && "rotate-90"
          )}
        />
      </button>
      <button
        type="button"
        className="flex-1 min-w-0 text-left flex items-center gap-1.5 py-0.5 cursor-grab active:cursor-grabbing"
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
        title="Open General Summary · drag onto a note to distill"
      >
        <Video
          className={cn(
            "h-3.5 w-3.5 shrink-0",
            isFocused ? "text-primary" : "text-muted-foreground"
          )}
        />
        <span className="flex-1 truncate">{meeting.title || "Untitled meeting"}</span>
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
  const [dragging, setDragging] = useState(false)
  const label = tab.name || tab.tab_id

  return (
    <button
      type="button"
      className={cn(
        "w-full text-left flex items-center gap-1.5 pl-8 pr-2 py-1.5 text-[12px] transition-colors group border-b border-dashed border-border/70",
        isFocused
          ? "text-primary font-semibold bg-primary/18"
          : isActive
            ? "text-primary/90 font-medium bg-primary/8"
            : "hover:text-primary text-muted-foreground cursor-grab active:cursor-grabbing",
        dragging && "opacity-50"
      )}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("application/meeting-id", meeting.id)
        e.dataTransfer.setData("application/meeting-tab-id", tab.tab_id)
        e.dataTransfer.setData(
          "application/meeting-title",
          `${meeting.title || "Meeting"} / ${label}`
        )
        e.dataTransfer.effectAllowed = "copy"
        setDragging(true)
      }}
      onDragEnd={() => setDragging(false)}
      onClick={onClick}
      title="Open section · drag onto a note to distill"
    >
      <FileText
        className={cn(
          "h-3 w-3 shrink-0",
          isFocused ? "text-primary" : "text-muted-foreground"
        )}
      />
      <span className="flex-1 truncate">{label}</span>
    </button>
  )
}
