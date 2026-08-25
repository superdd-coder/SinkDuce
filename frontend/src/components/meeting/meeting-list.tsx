import { useEffect, useRef, useState } from "react"
import { Trash2, Mic } from "lucide-react"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"
import { Button } from "@/components/ui/button"
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
  railTab?: "meetings" | "groups"
  onRailTab?: (tab: "meetings" | "groups") => void
  groups?: MeetingGroup[]
  activeGroup?: string | null
  onSelectGroup?: (id: string) => void
  onDeleteGroup?: (id: string) => void
}

export function MeetingList({
  meetings,
  activeMeeting,
  recordingMeetingId = null,
  onSelect,
  onCreated,
  onDelete,
  keepFocusOnCreate = false,
  railTab = "meetings",
  onRailTab,
  groups = [],
  activeGroup = null,
  onSelectGroup,
  onDeleteGroup,
}: MeetingListProps) {
  const t = useT()
  /* Sliding mint indicator — Collections / Sessions language */
  const listRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const [indicator, setIndicator] = useState({ top: 0, height: 0 })
  const [indicatorReady, setIndicatorReady] = useState(false)

  const indicatorId = railTab === "groups" ? activeGroup : activeMeeting
  useEffect(() => {
    if (!indicatorId) {
      setIndicatorReady(false)
      return
    }
    const activeEl = itemRefs.current.get(indicatorId)
    if (!activeEl) return
    setIndicator({
      top: activeEl.offsetTop,
      height: activeEl.offsetHeight,
    })
    requestAnimationFrame(() => setIndicatorReady(true))
  }, [indicatorId, meetings, groups, railTab])

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

  const edgeFade = useScrollEdgeFade(listRef, meetings.length)

  return (
    <aside className="pm-meeting-rail" aria-label={t("meeting.meetings")}>
      <div className="pm-meeting-rail-surface">
        <div className="pm-meeting-rail-head pm-rail-head">
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            {onRailTab && (
              <div className="flex rounded-full p-0.5" style={{ background: "color-mix(in srgb, var(--pm-ink) 4%, transparent)" }}>
                <button
                  type="button"
                  className={cn("flex-1 rounded-full py-1 text-[11px] tracking-wide", railTab === "meetings" && "bg-white shadow-sm")}
                  onClick={() => onRailTab("meetings")}
                >
                  {t("meeting.meetingsTab")}
                </button>
                <button
                  type="button"
                  className={cn("flex-1 rounded-full py-1 text-[11px] tracking-wide", railTab === "groups" && "bg-white shadow-sm")}
                  onClick={() => onRailTab("groups")}
                >
                  {t("meeting.groupsTab")}
                </button>
              </div>
            )}
            <h2 className="pm-meeting-rail-title pm-rail-title">
              {railTab === "groups" ? t("meeting.groupsTab") : t("meeting.meetings")}
            </h2>
          </div>
          {railTab !== "groups" && (
          <CreateMeetingButton
            onCreated={onCreated}
            stayOnCurrent={keepFocusOnCreate}
            recordingTitle={
              recordingMeetingId
                ? meetings.find((m) => m.id === recordingMeetingId)?.title
                : null
            }
          />
          )}
        </div>

        <div className="pm-rail-list-shell">
          <div ref={listRef} className="pm-meeting-rail-list">
            {((railTab === "groups" && activeGroup) || (railTab !== "groups" && activeMeeting)) && (
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

            {railTab === "groups" && groups.length === 0 && (
              <div className="pm-chat-sess-empty">
                <p className="pm-meta">{t("meeting.noGroups")}</p>
              </div>
            )}

            {railTab === "groups" && groups.map((g) => {
              const isActive = activeGroup === g.id
              return (
                <div
                  key={g.id}
                  ref={(el) => {
                    if (el) itemRefs.current.set(g.id, el)
                    else itemRefs.current.delete(g.id)
                  }}
                  role="button"
                  tabIndex={0}
                  onClick={() => onSelectGroup?.(g.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      onSelectGroup?.(g.id)
                    }
                  }}
                  className={cn("pm-chat-sess-row", isActive && "is-active")}
                >
                  <div className="pm-chat-sess-name" title={g.title}>{g.title}</div>
                  <div className="pm-chat-sess-meta">
                    <span className="pm-chat-sess-meta-snip">
                      {t("meeting.groupCount", { n: g.members.length })}
                    </span>
                    <span className="pm-chat-sess-meta-time">
                      {formatTime(g.last_chat_at || g.updated_at)}
                    </span>
                  </div>
                  {onDeleteGroup && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      className="pm-chat-sess-del"
                      onClick={(e) => {
                        e.stopPropagation()
                        onDeleteGroup(g.id)
                      }}
                    >
                      <Trash2 className="size-3" />
                    </Button>
                  )}
                </div>
              )
            })}

            {railTab !== "groups" && meetings.length === 0 && (
              <div className="pm-chat-sess-empty">
                <Mic className="size-5 pm-chat-sess-empty-icon" />
                <p className="pm-meta">{t("meeting.noMeetings")}</p>
              </div>
            )}

            {railTab !== "groups" && meetings.map((m) => {
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
              return (
                <div
                  key={m.id}
                  ref={(el) => {
                    if (el) itemRefs.current.set(m.id, el)
                    else itemRefs.current.delete(m.id)
                  }}
                  role="button"
                  tabIndex={0}
                  onClick={() => onSelect(m.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      onSelect(m.id)
                    }
                  }}
                  className={cn(
                    "pm-chat-sess-row",
                    isActive && "is-active",
                    isCapturing && "is-capturing",
                    isLiveStatus && !isCapturing && "is-live-status",
                    liveKind && `is-live-${liveKind}`,
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
                      )}
                    >
                      {statusLabel(m)}
                    </span>
                    <span className="pm-chat-sess-meta-time">
                      {formatTime(m.updated_at || m.created_at)}
                    </span>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    className="pm-chat-sess-del"
                    title={t("meeting.deleteMeeting")}
                    aria-label={t("meeting.deleteMeeting")}
                    onClick={(e) => {
                      e.stopPropagation()
                      onDelete(m.id)
                    }}
                  >
                    <Trash2 className="size-3" />
                  </Button>
                </div>
              )
            })}
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
    </aside>
  )
}
