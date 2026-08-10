import { useEffect, useRef, useState } from "react"
import { Trash2, Mic } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { useScrollEdgeFade } from "@/hooks/use-scroll-edge-fade"
import { CreateMeetingButton } from "./create-meeting-dialog"
import type { Meeting } from "@/api/client"

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
}

export function MeetingList({
  meetings,
  activeMeeting,
  recordingMeetingId = null,
  onSelect,
  onCreated,
  onDelete,
  keepFocusOnCreate = false,
}: MeetingListProps) {
  /* Sliding mint indicator — Collections / Sessions language */
  const listRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const [indicator, setIndicator] = useState({ top: 0, height: 0 })
  const [indicatorReady, setIndicatorReady] = useState(false)

  useEffect(() => {
    if (!activeMeeting) {
      setIndicatorReady(false)
      return
    }
    const activeEl = itemRefs.current.get(activeMeeting)
    if (!activeEl) return
    /* offsetTop is stable inside the scroll container (unlike getBoundingClientRect) */
    setIndicator({
      top: activeEl.offsetTop,
      height: activeEl.offsetHeight,
    })
    requestAnimationFrame(() => setIndicatorReady(true))
  }, [activeMeeting, meetings])

  const formatTime = (iso?: string) => {
    if (!iso) return ""
    const d = new Date(iso)
    const now = new Date()
    const diff = now.getTime() - d.getTime()
    if (diff < 60_000) return "just now"
    if (diff < 3600_000) return `${Math.floor(diff / 60_000)}m ago`
    if (diff < 86_400_000) return `${Math.floor(diff / 3600_000)}h ago`
    return d.toLocaleDateString()
  }

  /** Live / in-flight status shown like Recording (always visible in meta) */
  const liveStatus = (m: Meeting): string | null => {
    if (recordingMeetingId === m.id || m.status === "recording") return "Recording"
    if (m.status === "transcribing") return "Transcribing…"
    if (m.processing_state === "summarizing") return "Summarizing…"
    if (m.processing_state === "extracting") return "Extracting…"
    return null
  }

  const statusLabel = (m: Meeting) => {
    const live = liveStatus(m)
    if (live) return live
    if (m.status === "completed") return "Ready"
    return "Draft"
  }

  const edgeFade = useScrollEdgeFade(listRef, meetings.length)

  return (
    <aside className="pm-meeting-rail" aria-label="Meetings">
      <div className="pm-meeting-rail-surface">
        <div className="pm-meeting-rail-head pm-rail-head">
          <h2 className="pm-meeting-rail-title pm-rail-title">Meetings</h2>
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
            {activeMeeting && (
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

            {meetings.length === 0 && (
              <div className="pm-chat-sess-empty">
                <Mic className="size-5 pm-chat-sess-empty-icon" />
                <p className="pm-meta">No meetings yet</p>
              </div>
            )}

            {meetings.map((m) => {
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
                    title="Delete meeting"
                    aria-label="Delete meeting"
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
