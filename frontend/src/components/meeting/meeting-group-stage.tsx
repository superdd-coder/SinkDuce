import { useCallback, useEffect, useMemo, useRef, useState, type TransitionEvent } from "react"
import { PanelRightClose } from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  addMeetingGroupMember,
  createMeeting,
  getMeeting,
  getMeetingGroup,
  getMeetingTranscript,
  removeMeetingGroupMember,
  type Meeting,
  type MeetingGroup,
  type TranscriptSegment,
} from "@/api/meeting"
import { MeetingQuickChat } from "./meeting-quick-chat"
import { TranscriptTab } from "./transcript-panel"
import { MeetingPickList } from "./meeting-pick-list"
import { MediaBar, type MediaBarHandle } from "./media-bar"

function findTranscriptHit(
  segs: TranscriptSegment[],
  sentenceId?: string,
): TranscriptSegment | undefined {
  const raw = (sentenceId || "").trim()
  if (!raw || segs.length === 0) return undefined
  const exact = segs.find((s) => s.sentence_id === raw || s.sentence_id?.endsWith(raw))
  if (exact) return exact
  const num = raw.match(/(\d+)/)?.[1]
  if (!num) return undefined
  const padded = `stt_${num.padStart(4, "0")}`
  const byId = segs.find((s) => s.sentence_id === padded || s.sentence_id?.endsWith(padded))
  if (byId) return byId
  const idx = parseInt(num, 10) - 1
  if (idx >= 0 && idx < segs.length) return segs[idx]
  return undefined
}

export function MeetingGroupStage({
  groupId,
  meetings,
  onOpenMeeting,
  onGroupChanged,
  onMeetingsChanged,
}: {
  groupId: string
  meetings: Meeting[]
  onOpenMeeting: (id: string) => void
  onGroupChanged: (g: MeetingGroup) => void
  onMeetingsChanged?: () => void
}) {
  const t = useT()
  const [group, setGroup] = useState<MeetingGroup | null>(null)
  const [overlayMeeting, setOverlayMeeting] = useState<Meeting | null>(null)
  const [overlayOpen, setOverlayOpen] = useState(false)
  const overlayOpenRef = useRef(false)
  overlayOpenRef.current = overlayOpen
  const [segments, setSegments] = useState<TranscriptSegment[]>([])
  const [joinOpen, setJoinOpen] = useState(false)
  const [focusRef, setFocusRef] = useState<{ id: string; ts: number } | undefined>()
  const [playbackTime, setPlaybackTime] = useState<number | null>(null)
  const mediaBarRef = useRef<MediaBarHandle | null>(null)
  const pendingSeekRef = useRef<number | null>(null)
  const noop = useRef(() => {}).current

  const onGroupChangedRef = useRef(onGroupChanged)
  onGroupChangedRef.current = onGroupChanged

  const reload = useCallback(() => {
    void getMeetingGroup(groupId).then((g) => {
      setGroup(g)
      onGroupChangedRef.current(g)
    })
  }, [groupId])

  useEffect(() => {
    setOverlayMeeting(null)
    setOverlayOpen(false)
    overlayOpenRef.current = false
    setSegments([])
    pendingSeekRef.current = null
    reload()
  }, [groupId, reload])

  const meetingById = useMemo(() => {
    const m = new Map<string, Meeting>()
    for (const row of meetings) m.set(row.id, row)
    return m
  }, [meetings])

  const citeMeetings = useMemo(
    () =>
      (group?.members || []).map((mem) => {
        const m = meetingById.get(mem.meeting_id)
        return {
          id: mem.meeting_id,
          title: m?.title || mem.meeting_id,
          created_at: m?.created_at,
        }
      }),
    [group, meetingById],
  )

  const seekPlayer = (start?: number) => {
    const t = start != null && Number.isFinite(start) ? start : 0
    pendingSeekRef.current = t
    mediaBarRef.current?.seekTo(t)
  }

  useEffect(() => {
    const t = pendingSeekRef.current
    if (t == null || !overlayMeeting) return
    const id = requestAnimationFrame(() => {
      mediaBarRef.current?.seekTo(t)
    })
    return () => cancelAnimationFrame(id)
  }, [overlayMeeting?.id, segments, focusRef?.ts])

  const finishOverlayExit = (e: TransitionEvent<HTMLElement>) => {
    if (e.target !== e.currentTarget) return
    if (e.propertyName !== "opacity") return
    if (overlayOpenRef.current) return
    setOverlayMeeting(null)
    setSegments([])
    setPlaybackTime(null)
  }

  const closeOverlay = () => {
    setOverlayOpen(false)
    pendingSeekRef.current = null
    setPlaybackTime(null)
    if (typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setOverlayMeeting(null)
      setSegments([])
    }
  }

  const openOverlay = async (n: number, sentenceId?: string, meetingId?: string) => {
    const mem =
      group?.members.find((x) => Number(x.n) === Number(n)) ||
      (meetingId ? group?.members.find((x) => x.meeting_id === meetingId) : undefined)
    if (!mem) return
    const cached = meetingById.get(mem.meeting_id)
    if (cached) setOverlayMeeting(cached)
    setOverlayOpen(true)
    setPlaybackTime(null)
    try {
      const m = cached || (await getMeeting(mem.meeting_id))
      setOverlayMeeting(m)
      const trs = await getMeetingTranscript(mem.meeting_id)
      const segs = (trs.segments || []) as TranscriptSegment[]
      setSegments(segs)
      const hit = findTranscriptHit(segs, sentenceId)
      const focusId = hit?.sentence_id || sentenceId || segs[0]?.sentence_id
      if (focusId) {
        setFocusRef({ id: focusId, ts: Date.now() })
      }
      seekPlayer(hit?.start ?? segs[0]?.start ?? 0)
    } catch {
      setSegments([])
    }
  }

  const removeMember = async (meetingId: string) => {
    const g = await removeMeetingGroupMember(groupId, meetingId)
    setGroup(g)
    onGroupChanged(g)
    if (overlayMeeting?.id === meetingId) closeOverlay()
  }

  const addExisting = async (meetingId: string) => {
    const g = await addMeetingGroupMember(groupId, meetingId)
    setGroup(g)
    onGroupChanged(g)
    setJoinOpen(false)
  }

  const addNew = async () => {
    try {
      const title = new Date().toLocaleString("zh-CN", {
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit",
      }).replace(/\//g, "-")
      const created = await createMeeting(title)
      const g = await addMeetingGroupMember(groupId, created.id)
      setGroup(g)
      onGroupChanged(g)
      onMeetingsChanged?.()
      toast.success(t("meeting.meetingCreated"))
      onOpenMeeting(created.id)
    } catch (err) {
      toast.error(t("common.failedWithError", { error: formatApiError(err, t) }))
    }
  }

  const statusLabel = (m: Meeting | undefined) => {
    if (!m) return ""
    if (m.status === "recording") return t("meeting.recording")
    if (m.status === "transcribing") return t("meeting.transcribing")
    if (m.processing_state === "summarizing") return t("meeting.summarizing")
    if (m.status === "completed") return t("common.ready")
    return t("meeting.draft")
  }

  const inGroup = new Set((group?.members || []).map((m) => m.meeting_id))
  const joinCandidates = meetings.filter((m) => !inGroup.has(m.id))

  return (
    <div
      className="grid h-full min-h-0 w-full gap-2.5"
      style={{ gridTemplateColumns: "minmax(0,1fr) clamp(272px, 30vw, 360px)" }}
    >
      <div className="min-h-0 min-w-0 overflow-hidden rounded-[20px] bg-[var(--pm-float,#fdfbf7)] shadow-[var(--pm-shadow)]">
        {group && (
          <MeetingQuickChat
            meetingId={group.id}
            meetingTitle={group.title}
            open
            onOpen={() => {}}
            onClose={() => {}}
            layout="dock"
            sessionKind="group"
            onGroupCite={(n, sentenceId, meetingId) => void openOverlay(n, sentenceId, meetingId)}
            citeMeetings={citeMeetings}
          />
        )}
      </div>

      <div className="relative min-h-0 min-w-0">
        <section className="absolute inset-0 flex min-h-0 flex-col overflow-hidden rounded-[20px] bg-[var(--pm-float,#fdfbf7)] shadow-[var(--pm-shadow)]">
          <div className="pm-meeting-rail-head">
            <h3 className="pm-meeting-rail-title m-0 min-w-0 truncate">{t("meeting.groupMembers")}</h3>
            <span className="pm-meta tabular-nums shrink-0">{group?.members.length || 0}</span>
          </div>
          <div className="pm-meeting-group-actions mx-3 mb-2">
            <button type="button" className="pm-meeting-group-action" onClick={() => void addNew()}>
              {t("meeting.addNewMeeting")}
            </button>
            <button type="button" className="pm-meeting-group-action" onClick={() => setJoinOpen(true)}>
              {t("meeting.addExistingMeeting")}
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-hidden px-2 pb-2">
            <MeetingPickList
              items={(group?.members || []).map((mem) => {
                const m = meetingById.get(mem.meeting_id)
                return {
                  id: mem.meeting_id,
                  title: m?.title || mem.meeting_id,
                  status: statusLabel(m),
                  sortAt: m?.created_at || m?.updated_at,
                }
              })}
              emptyText={t("meeting.groupMembersEmpty")}
              onSelect={(id) => onOpenMeeting(id)}
              onRemove={(id) => void removeMember(id)}
              removeLabel={t("meeting.removeFromGroup")}
            />
          </div>
        </section>

        <section
          className={cn("pm-meeting-tx-overlay", overlayOpen && "is-open")}
          onTransitionEnd={finishOverlayExit}
        >
          <div className="pm-meeting-tx-overlay-head">
            <h3 className="pm-meeting-tx-overlay-title">
              {overlayMeeting?.title || ""}
            </h3>
            <button
              type="button"
              className="pm-meeting-side-collapse"
              title={t("meeting.collapseTranscript")}
              aria-label={t("meeting.collapseTranscript")}
              onClick={closeOverlay}
            >
              <PanelRightClose className="size-3.5" />
            </button>
          </div>
          {overlayMeeting?.audio_path ? (
            <div className="mx-3 mb-2 overflow-hidden rounded-[20px] bg-white px-3.5 py-2.5 shadow-[var(--pm-shadow-sm)]">
              <MediaBar
                ref={mediaBarRef}
                meetingId={overlayMeeting.id}
                status={overlayMeeting.status}
                hasAudio
                audioPath={overlayMeeting.audio_path}
                audioUrl={`/api/meetings/${overlayMeeting.id}/audio`}
                audioVersion={0}
                duration={0}
                isRecording={false}
                isPaused={false}
                onUploadAudio={noop}
                onStartRecord={noop}
                onStopRecord={noop}
                onPauseRecord={noop}
                onResumeRecord={noop}
                onTranscribe={noop}
                hasRealtimeProvider={false}
                hasTranscript
                playbackOnly
                onTimeUpdate={setPlaybackTime}
              />
            </div>
          ) : null}
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-1">
            <TranscriptTab
              segments={segments}
              onSegmentClick={(start) => seekPlayer(start)}
              focusRef={focusRef}
              speakerNames={overlayMeeting?.speaker_names ?? {}}
              playbackTime={playbackTime}
              showSearch
            />
          </div>
        </section>
      </div>

      <Dialog open={joinOpen} onOpenChange={setJoinOpen}>
        <DialogContent className="pm-meeting-join-dialog">
          <DialogHeader className="pm-dialog-header--premium">
            <DialogKicker>{t("meeting.groupsTab")}</DialogKicker>
            <DialogTitle>{t("meeting.joinGroupTitle")}</DialogTitle>
            <DialogDescription>{t("meeting.joinGroupHint")}</DialogDescription>
          </DialogHeader>
          <div className="pm-dialog-body min-w-0">
            <MeetingPickList
              items={joinCandidates.map((m) => ({
                id: m.id,
                title: m.title,
                status: statusLabel(m),
                sortAt: m.created_at || m.updated_at,
              }))}
              emptyText={t("meeting.joinGroupEmpty")}
              filterPlaceholder={t("meeting.pickFilterMeetings")}
              onSelect={(id) => void addExisting(id)}
            />
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
