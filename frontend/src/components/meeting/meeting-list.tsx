import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  Archive,
  ArchiveRestore,
  ChevronRight,
  FilePlus2,
  Folder,
  FolderMinus,
  FolderOpen,
  Mic,
  Trash2,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { useScrollEdgeFade } from "@/hooks/use-scroll-edge-fade"
import { CreateMeetingButton } from "./create-meeting-dialog"
import type { Meeting, MeetingGroup } from "@/api/client"

interface MeetingListProps {
  meetings: Meeting[]
  activeMeeting: string | null
  /** Meeting currently capturing audio (may differ from active selection). */
  recordingMeetingId?: string | null
  onSelect: (id: string) => void
  onCreated: (meetingId: string, opts?: { stayOnCurrent?: boolean }) => void
  onDelete: (id: string) => void
  /**
   * When true, creating a meeting refreshes the list but does **not** steal focus
   * from the meeting being recorded (avoids “overwriting” the live session in UI).
   */
  keepFocusOnCreate?: boolean
  groups?: MeetingGroup[]
  activeGroup?: string | null
  /** Group row click: enter the Group stage (expansion toggles in-list). */
  onSelectGroup: (id: string) => void
  onDeleteGroup: (id: string) => void
  onToggleArchiveMeeting: (meeting: Meeting) => void
  /** Dialog flow: create a group headed by this meeting. */
  onCreateGroupFromMeeting: (meetingId: string, title: string) => Promise<void>
  /** Create a fresh meeting inside this group and open it. */
  onCreateMeetingInGroup: (groupId: string) => void
  onDropMeetingInGroup: (meetingId: string, groupId: string) => void
  onRemoveMeetingFromGroup: (meetingId: string, groupId: string) => void
}

interface DragInfo {
  meetingId: string
  fromGroupId: string | null
}

export function MeetingList({
  meetings,
  activeMeeting,
  recordingMeetingId = null,
  onSelect,
  onCreated,
  onDelete,
  keepFocusOnCreate = false,
  groups = [],
  activeGroup = null,
  onSelectGroup,
  onDeleteGroup,
  onToggleArchiveMeeting,
  onCreateGroupFromMeeting,
  onCreateMeetingInGroup,
  onDropMeetingInGroup,
  onRemoveMeetingFromGroup,
}: MeetingListProps) {
  const t = useT()
  /* Sliding mint indicator — same rail language as Collections / Sessions */
  const listRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const [indicator, setIndicator] = useState({ top: 0, height: 0 })
  const [indicatorReady, setIndicatorReady] = useState(false)

  /** Group rows the user expanded (meetings nested below, indented). */
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  /** Bumped when a branch finishes its open/close motion (indicator re-measure). */
  const [branchSettled, setBranchSettled] = useState(0)
  /** The collapsed 已归档 fold at the bottom of the list. */
  const [archiveOpen, setArchiveOpen] = useState(false)
  /** New-group-from-meeting dialog. */
  const [newGroupFor, setNewGroupFor] = useState<Meeting | null>(null)
  const [newGroupTitle, setNewGroupTitle] = useState("")

  /** Drag-a-meeting state (membership only — never reorders the list). */
  const [drag, setDrag] = useState<DragInfo | null>(null)
  /** Group currently hovered in the floating pick-group panel. */
  const [panelDropGroup, setPanelDropGroup] = useState<string | null>(null)
  /** Remove drop-zone hover state (hint text swaps to "release to remove"). */
  const [dropZoneArmed, setDropZoneArmed] = useState(false)

  /* Auto-scroll the pick-group panel while the pointer drifts near its edges. */
  const panelListRef = useRef<HTMLDivElement | null>(null)
  const dragYRef = useRef(0)
  const panelScrollRafRef = useRef<number | null>(null)
  const stopPanelAutoScroll = useCallback(() => {
    if (panelScrollRafRef.current != null) {
      cancelAnimationFrame(panelScrollRafRef.current)
      panelScrollRafRef.current = null
    }
  }, [])
  useEffect(() => {
    if (!drag || drag.fromGroupId != null) {
      stopPanelAutoScroll()
      return
    }
    const step = () => {
      const el = panelListRef.current
      if (el) {
        const rect = el.getBoundingClientRect()
        const edge = 44
        const y = dragYRef.current
        if (y > 0 && y < rect.top + edge) {
          el.scrollTop -= Math.min(14, (rect.top + edge - y) / 2.5)
        } else if (y > rect.bottom - edge) {
          el.scrollTop += Math.min(14, (y - (rect.bottom - edge)) / 2.5)
        }
      }
      panelScrollRafRef.current = requestAnimationFrame(step)
    }
    panelScrollRafRef.current = requestAnimationFrame(step)
    return stopPanelAutoScroll
  }, [drag, stopPanelAutoScroll])

  const meetingById = useMemo(() => {
    const m = new Map<string, Meeting>()
    for (const row of meetings) m.set(row.id, row)
    return m
  }, [meetings])

  /** meeting_id -> group ids containing it (data model allows multi-group). */
  const groupIdsByMeeting = useMemo(() => {
    const map = new Map<string, string[]>()
    for (const g of groups) {
      for (const mem of g.members) {
        const ids = map.get(mem.meeting_id) ?? []
        ids.push(g.id)
        map.set(mem.meeting_id, ids)
      }
    }
    return map
  }, [groups])

  const groupOfActive = activeMeeting ? groupIdsByMeeting.get(activeMeeting) : undefined

  /** Opening a member meeting expands its group so the indented row exists. */
  useEffect(() => {
    if (!groupOfActive || groupOfActive.length === 0) return
    setExpandedIds((prev) => {
      if (groupOfActive.some((gid) => prev.has(gid))) return prev
      const next = new Set(prev)
      next.add(groupOfActive[0])
      return next
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeMeeting, groupOfActive?.join(",")])

  const tsOf = (iso?: string) => new Date(iso || 0).getTime()

  /**
   * A group's time = the newest member meeting's creation time (falls back to
   * the group's own updated_at when it has no timed members). Drives both the
   * rail position and the displayed time.
   */
  const groupLatestCreatedAt = (g: MeetingGroup): string | undefined => {
    let best: string | undefined
    let bestTs = 0
    for (const mem of g.members) {
      const m = meetingById.get(mem.meeting_id)
      const iso = m?.created_at || m?.updated_at
      const ts = tsOf(iso)
      if (ts > bestTs) {
        bestTs = ts
        best = iso
      }
    }
    return best ?? g.updated_at
  }

  type Row =
    | { kind: "meeting"; key: string; ts: number; meeting: Meeting }
    | { kind: "group"; key: string; ts: number; group: MeetingGroup }

  /**
   * Merged top-level rows, newest first. Archived ungrouped meetings and
   * archived groups fold into a collapsed section at the bottom; archived
   * meetings *inside* a group stay in their group's branch.
   */
  const { activeRows, archivedRows } = useMemo(() => {
    const grouped = new Set(groupIdsByMeeting.keys())
    const active: Row[] = []
    const archived: Row[] = []
    for (const m of meetings) {
      if (grouped.has(m.id)) continue
      const row: Row = { kind: "meeting", key: m.id, ts: tsOf(m.created_at || m.updated_at), meeting: m }
      ;(m.archived ? archived : active).push(row)
    }
    for (const g of groups) {
      const row: Row = { kind: "group", key: g.id, ts: tsOf(groupLatestCreatedAt(g)), group: g }
      ;(g.archived ? archived : active).push(row)
    }
    active.sort((a, b) => b.ts - a.ts)
    archived.sort((a, b) => b.ts - a.ts)
    return { activeRows: active, archivedRows: archived }
  }, [meetings, groups, groupIdsByMeeting])

  /** Groups that can still receive drag-drops (archived ones are folded). */
  const droppableGroups = useMemo(() => groups.filter((g) => !g.archived), [groups])

  const activeRowKey = activeMeeting
    ? groupOfActive && groupOfActive.length > 0 && expandedIds.has(groupOfActive[0])
      ? `${groupOfActive[0]}:${activeMeeting}`
      : activeMeeting
    : activeGroup

  useEffect(() => {
    if (!activeRowKey) {
      setIndicatorReady(false)
      return
    }
    const activeEl = itemRefs.current.get(activeRowKey)
    const listEl = listRef.current
    if (!activeEl || !listEl) return
    // Rect math (not offsetTop): member rows sit inside the positioned
    // branch container, so offsetParent is the branch — the pill must be
    // measured against the list's content box instead.
    const rowRect = activeEl.getBoundingClientRect()
    const listRect = listEl.getBoundingClientRect()
    setIndicator({
      top: rowRect.top - listRect.top + listEl.scrollTop,
      height: rowRect.height,
    })
    requestAnimationFrame(() => setIndicatorReady(true))
  }, [activeRowKey, activeRows, expandedIds, branchSettled, archiveOpen])

  const formatTime = (iso?: string) => {
    if (!iso) return ""
    const d = new Date(iso)
    const now = new Date()
    const diff = now.getTime() - d.getTime()
    if (diff < 60_000) return t("common.justNow")
    if (diff < 3600_000) return t("common.minutesAgo", { n: Math.floor(diff / 60_000) })
    if (diff < 86_400_000) return t("common.hoursAgo", { n: Math.floor(diff / 3600_000) })
    return d.toLocaleDateString()
  }

  /** Live / in-flight status shown like Recording (always visible in meta) */
  const liveStatus = (m: Meeting): string | null => {
    if (recordingMeetingId === m.id || m.status === "recording") return t("meeting.recording")
    if (m.status === "transcribing") return t("meeting.transcribing")
    if (m.processing_state === "summarizing") return t("meeting.summarizing")
    if (m.processing_state === "extracting") return t("meeting.extracting")
    return null
  }

  const statusLabel = (m: Meeting) => {
    const live = liveStatus(m)
    if (live) return live
    if (m.status === "completed") return t("common.ready")
    return t("meeting.draft")
  }

  const edgeFade = useScrollEdgeFade(listRef, activeRows.length + archivedRows.length)

  const toggleExpanded = (groupId: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(groupId)) next.delete(groupId)
      else next.add(groupId)
      return next
    })
  }

  const setItemRef = (key: string) => (el: HTMLDivElement | null) => {
    if (el) itemRefs.current.set(key, el)
    else itemRefs.current.delete(key)
  }

  const startDrag = (meetingId: string, fromGroupId: string | null) => (e: React.DragEvent) => {
    e.dataTransfer.setData("text/plain", meetingId)
    e.dataTransfer.effectAllowed = "move"
    setDrag({ meetingId, fromGroupId })
  }

  const endDrag = () => {
    setDrag(null)
    setPanelDropGroup(null)
    setDropZoneArmed(false)
  }

  // The app nav + header live OUTSIDE the meeting view's stacking context —
  // an in-view scrim can't reach them, so they dress their own dim via the
  // body class while a drag is active.
  useEffect(() => {
    if (!drag) return
    document.body.classList.add("pm-dnd-active")
    return () => document.body.classList.remove("pm-dnd-active")
  }, [drag])

  const submitNewGroup = async () => {
    if (!newGroupFor) return
    const meetingId = newGroupFor.id
    const title = newGroupTitle
    setNewGroupFor(null)
    setNewGroupTitle("")
    await onCreateGroupFromMeeting(meetingId, title)
  }

  const renderMeetingRow = (m: Meeting, opts: { key: string; groupId: string | null }) => {
    const isActive = activeMeeting === m.id
    const isCapturing =
      recordingMeetingId === m.id || m.status === "recording"
    const isTranscribing = m.status === "transcribing"
    const isSummarizing = m.processing_state === "summarizing"
    const isExtracting = m.processing_state === "extracting"
    /** Keep status visible on hover (same pattern as Recording) */
    const isLiveStatus =
      isCapturing || isTranscribing || isSummarizing || isExtracting
    const liveKind = isCapturing
      ? "capturing"
      : isTranscribing
        ? "transcribing"
        : isSummarizing
          ? "summarizing"
          : isExtracting
            ? "extracting"
            : null
    const inAnyGroup = (groupIdsByMeeting.get(m.id)?.length ?? 0) > 0
    return (
      <div
        key={opts.key}
        ref={setItemRef(opts.key)}
        role="button"
        tabIndex={0}
        onClick={() => onSelect(m.id)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault()
            onSelect(m.id)
          }
        }}
        draggable
        onDragStart={startDrag(m.id, opts.groupId)}
        onDragEnd={endDrag}
        className={cn(
          "pm-chat-sess-row",
          opts.groupId && "is-child",
          isActive && "is-active",
          isCapturing && "is-capturing",
          isLiveStatus && !isCapturing && "is-live-status",
          liveKind && `is-live-${liveKind}`,
          m.archived && "is-archived",
          drag?.meetingId === m.id && "is-dragging",
        )}
      >
        <div className="pm-chat-sess-name" title={m.title}>{m.title}</div>
        <div className="pm-chat-sess-meta">
          <span
            className={cn(
              "pm-chat-sess-meta-snip",
              isCapturing && "is-capturing",
              isTranscribing && "is-transcribing",
              isSummarizing && "is-summarizing",
              isExtracting && "is-extracting",
              m.archived && "is-archived",
            )}
          >
            {m.archived ? t("meeting.archivedTag") : statusLabel(m)}
          </span>
          <span className="pm-chat-sess-meta-time">
            {formatTime(m.created_at || m.updated_at)}
          </span>
        </div>
        <div className="pm-rail-actions">
          {!inAnyGroup && (
            <button
              type="button"
              className="pm-rail-act is-green is-text"
              title={t("meeting.newGroupBtn")}
              aria-label={t("meeting.newGroupBtn")}
              onClick={(e) => {
                e.stopPropagation()
                setNewGroupFor(m)
                setNewGroupTitle("")
              }}
            >
              {t("meeting.newGroupBtn")}
            </button>
          )}
          <button
            type="button"
            className="pm-rail-act"
            title={m.archived ? t("meeting.unarchiveMeeting") : t("meeting.archiveMeeting")}
            aria-label={m.archived ? t("meeting.unarchiveMeeting") : t("meeting.archiveMeeting")}
            onClick={(e) => {
              e.stopPropagation()
              onToggleArchiveMeeting(m)
            }}
          >
            {m.archived ? <ArchiveRestore className="size-3" /> : <Archive className="size-3" />}
          </button>
          <button
            type="button"
            className="pm-rail-act is-danger"
            title={t("meeting.deleteMeeting")}
            aria-label={t("meeting.deleteMeeting")}
            onClick={(e) => {
              e.stopPropagation()
              onDelete(m.id)
            }}
          >
            <Trash2 className="size-3" />
          </button>
        </div>
      </div>
    )
  }

  /**
   * Group row click: opening a group always expands its branch and enters the
   * group stage; only clicking the *already focused* group toggles (collapses
   * or re-opens) its branch.
   */
  const handleGroupActivate = (g: MeetingGroup) => {
    const isFocused = activeGroup === g.id && !activeMeeting
    if (isFocused) {
      toggleExpanded(g.id)
      return
    }
    setExpandedIds((prev) => {
      if (prev.has(g.id)) return prev
      const next = new Set(prev)
      next.add(g.id)
      return next
    })
    onSelectGroup(g.id)
  }

  const renderGroupRow = (g: MeetingGroup) => {
    const isActive = activeGroup === g.id && !activeMeeting
    const isExpanded = expandedIds.has(g.id)
    const members = g.members
      .map((mem) => meetingById.get(mem.meeting_id))
      .filter((m): m is Meeting => !!m)
      .sort((a, b) => tsOf(b.created_at || b.updated_at) - tsOf(a.created_at || a.updated_at))
    return (
      <div key={g.id} className="pm-rail-group-block">
        <div
          ref={setItemRef(g.id)}
          role="button"
          tabIndex={0}
          aria-expanded={isActive ? isExpanded : undefined}
          onClick={() => handleGroupActivate(g)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault()
              handleGroupActivate(g)
            }
          }}
          className={cn(
            "pm-chat-sess-row is-group",
            isActive && "is-active",
            g.archived && "is-archived",
          )}
        >
          <span className="pm-rail-group-marker" aria-hidden>
            {isExpanded ? <FolderOpen /> : <Folder />}
          </span>
          <div className="pm-rail-group-body">
            <div className="pm-chat-sess-name" title={g.title}>{g.title}</div>
            <div className="pm-chat-sess-meta">
              <span className="pm-chat-sess-meta-snip pm-rail-group-count">
                {g.archived
                  ? t("meeting.archivedTag")
                  : t("meeting.groupCount", { n: members.length })}
              </span>
              <span className="pm-chat-sess-meta-time">
                {formatTime(groupLatestCreatedAt(g))}
              </span>
            </div>
          </div>
          <div className="pm-rail-actions">
            <button
              type="button"
              className="pm-rail-act"
              title={t("meeting.newMeetingInGroup")}
              aria-label={t("meeting.newMeetingInGroup")}
              onClick={(e) => {
                e.stopPropagation()
                onCreateMeetingInGroup(g.id)
              }}
            >
              <FilePlus2 className="size-3" />
            </button>
            <button
              type="button"
              className="pm-rail-act is-danger"
              title={t("meeting.deleteGroup")}
              aria-label={t("meeting.deleteGroup")}
              onClick={(e) => {
                e.stopPropagation()
                onDeleteGroup(g.id)
              }}
            >
              <Trash2 className="size-3" />
            </button>
          </div>
        </div>
        {/* Always mounted so open/close can silk-slide the grid row height */}
        <div
          className={cn("pm-rail-group-branch-wrap", isExpanded && "is-open")}
          aria-hidden={!isExpanded}
          onTransitionEnd={(e) => {
            if (e.target !== e.currentTarget) return
            if (e.propertyName !== "grid-template-rows") return
            // Height settled — re-measure so the mint indicator lands exactly.
            setBranchSettled((n) => n + 1)
          }}
        >
          <div className="pm-rail-group-branch">
            {members.length === 0 ? (
              <p className="pm-rail-group-empty">{t("meeting.groupMembersEmpty")}</p>
            ) : (
              members.map((m) =>
                renderMeetingRow(m, { key: `${g.id}:${m.id}`, groupId: g.id }),
              )
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <aside className="pm-meeting-rail" aria-label={t("meeting.meetings")}>
      <div className="pm-meeting-rail-surface">
        <div className="pm-meeting-rail-head pm-rail-head">
          <h2 className="pm-meeting-rail-title pm-rail-title">{t("meeting.meetings")}</h2>
          <CreateMeetingButton
            onCreated={onCreated}
            stayOnCurrent={keepFocusOnCreate}
            recordingTitle={
              recordingMeetingId
                ? meetings.find((m) => m.id === recordingMeetingId)?.title
                : null
            }
          />
        </div>

        <div className="pm-rail-list-shell">
          <div ref={listRef} className="pm-meeting-rail-list">
            {activeRowKey && (
              <div
                className={cn(
                  "pm-chat-sess-indicator",
                  indicatorReady && "is-ready",
                )}
                style={{
                  transform: `translateY(${indicator.top}px)`,
                  height: indicator.height,
                }}
                aria-hidden
              />
            )}

            {activeRows.length === 0 && archivedRows.length === 0 && (
              <div className="pm-chat-sess-empty">
                <Mic className="size-5 pm-chat-sess-empty-icon" />
                <p className="pm-meta">{t("meeting.noMeetings")}</p>
              </div>
            )}

            {activeRows.map((row) =>
              row.kind === "group"
                ? renderGroupRow(row.group)
                : renderMeetingRow(row.meeting, { key: row.key, groupId: null }),
            )}

            {archivedRows.length > 0 && (
              <div className="pm-rail-archive">
                <div
                  role="button"
                  tabIndex={0}
                  aria-expanded={archiveOpen}
                  className="pm-rail-archive-head"
                  onClick={() => setArchiveOpen((v) => !v)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      setArchiveOpen((v) => !v)
                    }
                  }}
                >
                  <ChevronRight
                    className={cn("pm-rail-archive-chevron", archiveOpen && "is-open")}
                    aria-hidden
                  />
                  <span>{t("meeting.archivedSection")}</span>
                  <span className="pm-rail-archive-count">{archivedRows.length}</span>
                </div>
                {archiveOpen &&
                  archivedRows.map((row) =>
                    row.kind === "group"
                      ? renderGroupRow(row.group)
                      : renderMeetingRow(row.meeting, { key: row.key, groupId: null }),
                  )}
              </div>
            )}
          </div>
          <div
            className={cn(
              "pm-rail-edge-fade pm-rail-edge-fade--top",
              edgeFade.top && "is-visible",
            )}
            aria-hidden
          />
          <div
            className={cn(
              "pm-rail-edge-fade pm-rail-edge-fade--bottom",
              edgeFade.bottom && "is-visible",
            )}
            aria-hidden
          />
        </div>
      </div>

      {/* While dragging, everything outside the two sidebars is dimmed so the
          drop surface reads instantly. */}
      {drag && <div className="pm-rail-dnd-scrim" aria-hidden />}

      {/* Drag temp sidebar — same placement for both flows. Adding: lists the
          groups (drop on one to join). Removing: an empty Library-style drop
          zone (release to pull the meeting out of its group). */}
      {drag && (
        <div className={cn("pm-rail-dnd-panel", dropZoneArmed && "is-armed")}>
          <div className="pm-meeting-rail-head pm-rail-head">
            <h2 className="pm-meeting-rail-title pm-rail-title">
              {drag.fromGroupId
                ? t("meeting.dndRemoveFromGroup")
                : t("meeting.dndAddToGroup")}
            </h2>
          </div>
          <div
            ref={panelListRef}
            className="pm-rail-dnd-panel-list"
            onDragOver={(e) => {
              dragYRef.current = e.clientY
              e.preventDefault()
              // The whole sidebar is the drop surface for the remove flow
              if (drag.fromGroupId) setDropZoneArmed(true)
            }}
            onDragLeave={
              drag.fromGroupId
                ? (e) => {
                    if (e.currentTarget.contains(e.relatedTarget as Node)) return
                    setDropZoneArmed(false)
                  }
                : undefined
            }
            onDrop={(e) => {
              e.preventDefault()
              if (!drag.fromGroupId) return
              const { meetingId, fromGroupId } = drag
              endDrag()
              if (fromGroupId) onRemoveMeetingFromGroup(meetingId, fromGroupId)
            }}
          >
            {drag.fromGroupId ? (
              <div className={cn("pm-rail-dnd-dropzone", dropZoneArmed && "is-armed")}>
                <FolderMinus className="pm-rail-dnd-dropzone-icon" aria-hidden />
                <span>
                  {dropZoneArmed
                    ? t("meeting.dndReleaseToRemove")
                    : t("meeting.dndDropToRemove")}
                </span>
              </div>
            ) : (
              <>
            {droppableGroups.length === 0 && (
              <p className="pm-rail-dnd-empty">{t("meeting.dndNoGroups")}</p>
            )}
            {droppableGroups.map((g) => (
                  <div
                    key={g.id}
                    className={cn(
                      "pm-chat-sess-row is-group",
                      panelDropGroup === g.id && "is-drop-target",
                    )}
                    onDragOver={(e) => {
                      e.preventDefault()
                      e.stopPropagation()
                      dragYRef.current = e.clientY
                      setPanelDropGroup(g.id)
                    }}
                    onDragLeave={(e) => {
                      if (e.currentTarget.contains(e.relatedTarget as Node)) return
                      setPanelDropGroup((cur) => (cur === g.id ? null : cur))
                    }}
                    onDrop={(e) => {
                      e.preventDefault()
                      e.stopPropagation()
                      const meetingId = drag.meetingId
                      endDrag()
                      onDropMeetingInGroup(meetingId, g.id)
                    }}
                  >
                    <span className="pm-rail-group-marker" aria-hidden>
                      <Folder />
                    </span>
                    <div className="pm-rail-group-body">
                      <div className="pm-chat-sess-name" title={g.title}>
                        {g.title}
                      </div>
                      <div className="pm-chat-sess-meta">
                        <span className="pm-chat-sess-meta-snip pm-rail-group-count">
                          {t("meeting.groupCount", { n: g.members.length })}
                        </span>
                        <span className="pm-chat-sess-meta-time">
                          {formatTime(groupLatestCreatedAt(g))}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
      )}

      <Dialog open={!!newGroupFor} onOpenChange={(v) => !v && setNewGroupFor(null)}>
        <DialogContent className="pm-meeting-group-dialog">
          <DialogHeader className="pm-dialog-header--premium">
            <DialogKicker>{t("meeting.groupTag")}</DialogKicker>
            <DialogTitle>{t("meeting.createGroup")}</DialogTitle>
          </DialogHeader>
          <Input
            value={newGroupTitle}
            onChange={(e) => setNewGroupTitle(e.target.value)}
            placeholder={t("meeting.groupName")}
            onKeyDown={(e) => {
              if (e.key === "Enter") void submitNewGroup()
            }}
            autoFocus
          />
          <DialogFooter>
            <Button type="button" onClick={() => void submitNewGroup()}>
              {t("meeting.createGroup")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </aside>
  )
}
