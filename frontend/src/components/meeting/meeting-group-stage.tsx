import { useCallback, useEffect, useMemo, useRef, useState, type TransitionEvent } from "react"
import { Archive, ArchiveRestore, Check, FilePlus2, PanelRightClose, Pencil, Trash2, X } from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"
import { Button } from "@/components/ui/button"
import {
  getMeeting,
  getMeetingGroup,
  getMeetingTranscript,
  updateMeetingGroup,
  type Meeting,
  type MeetingGroup,
  type TranscriptSegment,
} from "@/api/meeting"
import { MeetingQuickChat } from "./meeting-quick-chat"
import { TranscriptTab } from "./transcript-panel"
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
  onGroupChanged,
  onRequestDeleteGroup,
  onCreateMeetingInGroup,
  onToggleArchiveGroup,
}: {
  groupId: string
  meetings: Meeting[]
  onGroupChanged: (g: MeetingGroup) => void
  /** Delete is confirmed by the parent dialog (cascade deletes member meetings). */
  onRequestDeleteGroup: (groupId: string) => void
  /** Create a fresh meeting inside this group and open it. */
  onCreateMeetingInGroup: (groupId: string) => void
  /** Archive/unarchive the group (cascades to member meetings). */
  onToggleArchiveGroup: (groupId: string, archived: boolean) => void
}) {
  const t = useT()
  const [group, setGroup] = useState<MeetingGroup | null>(null)
  const [overlayMeeting, setOverlayMeeting] = useState<Meeting | null>(null)
  const [overlayOpen, setOverlayOpen] = useState(false)
  const overlayOpenRef = useRef(false)
  overlayOpenRef.current = overlayOpen
  const [segments, setSegments] = useState<TranscriptSegment[]>([])
  const [focusRef, setFocusRef] = useState<{ id: string; ts: number } | undefined>()
  const [playbackTime, setPlaybackTime] = useState<number | null>(null)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState("")
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
    setEditingTitle(false)
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
    // The cite snapshot's meeting_id is authoritative: roster n is reused
    // after a member leaves and a new one joins with the same number.
    const mem = meetingId
      ? group?.members.find((x) => x.meeting_id === meetingId)
      : group?.members.find((x) => Number(x.n) === Number(n))
    if (!mem) {
      if (meetingId) toast.error(t("meeting.citeMeetingRemoved"))
      return
    }
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

  const handleStartEditTitle = () => {
    if (!group) return
    setTitleDraft(group.title)
    setEditingTitle(true)
  }

  const handleSaveTitle = async () => {
    if (!group || !titleDraft.trim()) {
      setEditingTitle(false)
      return
    }
    try {
      const next = await updateMeetingGroup(group.id, titleDraft.trim())
      setGroup(next)
      onGroupChanged(next)
      setEditingTitle(false)
    } catch (err) {
      toast.error(t("meeting.renameFailed", { error: String(err) }))
    }
  }

  const handleToggleArchive = () => {
    if (!group) return
    const next = !group.archived
    // Flip locally so the button tracks the state; parent refreshes both lists.
    setGroup({ ...group, archived: next })
    onToggleArchiveGroup(group.id, next)
  }

  const metaCreated = group?.created_at
    ? new Date(group.created_at).toLocaleDateString(undefined, {
        month: "short", day: "numeric", year: "numeric",
        hour: "2-digit", minute: "2-digit",
      })
    : "—"

  return (
    <div className="pm-meeting-group-stage">
      <div className="pm-meeting-group-head">
        <div className="pm-meeting-title-card pm-meeting-group-title-card" data-meeting-title>
        <div className="pm-meeting-head">
          {editingTitle ? (
            <div className="flex items-center gap-1 flex-1 min-w-0">
              <input
                className="pm-meeting-title-input"
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void handleSaveTitle()
                  if (e.key === "Escape") setEditingTitle(false)
                }}
                autoFocus
              />
              <Button variant="ghost" size="icon-sm" className="shrink-0" onClick={() => void handleSaveTitle()} aria-label={t("meeting.saveTitle")}>
                <Check className="size-3.5" />
              </Button>
              <Button variant="ghost" size="icon-sm" className="shrink-0" onClick={() => setEditingTitle(false)} aria-label={t("meeting.cancelEdit")}>
                <X className="size-3.5" />
              </Button>
            </div>
          ) : (
            <div className="flex items-start gap-1 min-w-0 flex-1">
              <h2 className="pm-meeting-title">{group?.title || ""}</h2>
              <Button
                variant="ghost"
                size="icon-sm"
                className="shrink-0 opacity-50 hover:opacity-100 mt-0.5"
                onClick={handleStartEditTitle}
                aria-label={t("meeting.editTitle")}
              >
                <Pencil className="size-3.5" />
              </Button>
            </div>
          )}

          <div className="pm-meeting-meta-stack">
            <div className="pm-meeting-meta-row">
              <span className="pm-meeting-meta-key">{t("common.created")}</span>
              <span className="pm-meeting-meta-val">{metaCreated}</span>
            </div>
            <div className="pm-meeting-meta-row">
              <span className="pm-meeting-meta-key">{t("meeting.groupMetaMeetings")}</span>
              <span className="pm-meeting-meta-val">
                {t("meeting.groupCount", { n: group?.members.length ?? 0 })}
              </span>
            </div>
          </div>
          </div>
        </div>

        {/* Side action card: three full-width labeled buttons (create/archive/delete) */}
        <div className="pm-meeting-title-actions" role="group" aria-label={t("meeting.groupTag")}>
          <button
            type="button"
            className="pm-meeting-card-btn is-cta"
            onClick={() => onCreateMeetingInGroup(groupId)}
          >
            <FilePlus2 aria-hidden />
            <span>{t("meeting.createMeetingBtn")}</span>
          </button>
          <button
            type="button"
            className="pm-meeting-card-btn"
            title={group?.archived ? t("meeting.unarchiveGroup") : t("meeting.archiveGroup")}
            aria-label={group?.archived ? t("meeting.unarchiveGroup") : t("meeting.archiveGroup")}
            onClick={handleToggleArchive}
          >
            {group?.archived ? <ArchiveRestore aria-hidden /> : <Archive aria-hidden />}
            <span>{group?.archived ? t("meeting.unarchiveGroup") : t("meeting.archiveGroup")}</span>
          </button>
          <button
            type="button"
            className="pm-meeting-card-btn is-danger"
            title={t("meeting.deleteGroup")}
            aria-label={t("meeting.deleteGroup")}
            onClick={() => onRequestDeleteGroup(groupId)}
          >
            <Trash2 aria-hidden />
            <span>{t("meeting.deleteGroup")}</span>
          </button>
        </div>
      </div>

      {/* Chat card + cite-click transcript column. The column reserves the
          original right-sidebar width; the chat card glides narrower as it
          opens (no overlay covering chat text). */}
      <div className="pm-meeting-group-chat-zone">
        <div className="pm-meeting-group-chat-card">
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

        <div className={cn("pm-meeting-tx-side-col", overlayOpen && "is-open")} aria-hidden={!overlayOpen}>
          <div className="pm-meeting-tx-side-inner">
            <section
              className={cn("pm-meeting-tx-overlay", overlayOpen && "is-open")}
              onTransitionEnd={finishOverlayExit}
              aria-hidden={!overlayOpen}
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
            <div className="pm-meeting-tx-player mx-3 mb-2 overflow-hidden rounded-[20px] px-3.5 py-2.5">
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
        </div>
      </div>
    </div>
  )
}
