import { useCallback, useEffect, useMemo, useState } from "react"
import { X } from "lucide-react"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  addMeetingGroupMember,
  createMeeting,
  getMeeting,
  getMeetingGroup,
  getMeetingTranscript,
  type Meeting,
  type MeetingGroup,
  type TranscriptSegment,
} from "@/api/meeting"
import { MeetingQuickChat } from "./meeting-quick-chat"
import { TranscriptTab } from "./transcript-panel"

export function MeetingGroupStage({
  groupId,
  meetings,
  onOpenMeeting,
  onGroupChanged,
}: {
  groupId: string
  meetings: Meeting[]
  onOpenMeeting: (id: string) => void
  onGroupChanged: (g: MeetingGroup) => void
}) {
  const t = useT()
  const [group, setGroup] = useState<MeetingGroup | null>(null)
  const [overlayN, setOverlayN] = useState<number | null>(null)
  const [overlayMeeting, setOverlayMeeting] = useState<Meeting | null>(null)
  const [segments, setSegments] = useState<TranscriptSegment[]>([])
  const [joinOpen, setJoinOpen] = useState(false)
  const [focusRef, setFocusRef] = useState<{ id: string; ts: number } | undefined>()

  const reload = useCallback(() => {
    void getMeetingGroup(groupId).then((g) => {
      setGroup(g)
      onGroupChanged(g)
    })
  }, [groupId, onGroupChanged])

  useEffect(() => {
    setOverlayN(null)
    setOverlayMeeting(null)
    reload()
  }, [groupId, reload])

  const meetingById = useMemo(() => {
    const m = new Map<string, Meeting>()
    for (const row of meetings) m.set(row.id, row)
    return m
  }, [meetings])

  const openOverlay = async (n: number) => {
    const mem = group?.members.find((x) => x.n === n)
    if (!mem) return
    setOverlayN(n)
    const m = meetingById.get(mem.meeting_id) || (await getMeeting(mem.meeting_id))
    setOverlayMeeting(m)
    try {
      const trs = await getMeetingTranscript(mem.meeting_id)
      const segs = (trs.segments || []) as TranscriptSegment[]
      setSegments(segs)
      const first = segs[0]
      if (first?.sentence_id) {
        setFocusRef({ id: first.sentence_id, ts: Date.now() })
      }
    } catch {
      setSegments([])
    }
  }

  const addExisting = async (meetingId: string) => {
    const g = await addMeetingGroupMember(groupId, meetingId)
    setGroup(g)
    onGroupChanged(g)
    setJoinOpen(false)
  }

  const addNew = async () => {
    const created = await createMeeting()
    const g = await addMeetingGroupMember(groupId, created.id)
    setGroup(g)
    onGroupChanged(g)
    onOpenMeeting(created.id)
  }

  const overlayMember = group?.members.find((m) => m.n === overlayN)
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
            onGroupCite={(n) => void openOverlay(n)}
          />
        )}
      </div>

      <div className="relative min-h-0 min-w-0">
        <section className="absolute inset-0 flex min-h-0 flex-col overflow-hidden rounded-[20px] bg-[var(--pm-float,#fdfbf7)] shadow-[var(--pm-shadow)]">
          <div className="flex items-baseline justify-between px-3.5 pb-2 pt-3.5">
            <h3 className="m-0 text-[13px] uppercase tracking-wide">{t("meeting.groupMembers")}</h3>
            <span className="text-[11px] text-muted-foreground">{group?.members.length || 0}</span>
          </div>
          <div className="flex gap-2 px-3.5 pb-3">
            <Button type="button" variant="ghost" className="h-9 flex-1 rounded-full" onClick={() => void addNew()}>
              {t("meeting.addNewMeeting")}
            </Button>
            <Button type="button" variant="ghost" className="h-9 flex-1 rounded-full" onClick={() => setJoinOpen(true)}>
              {t("meeting.addExistingMeeting")}
            </Button>
          </div>
          <div className="min-h-0 flex-1 overflow-auto px-2 pb-3">
            {(group?.members || [])
              .slice()
              .sort((a, b) => {
                const da = meetingById.get(a.meeting_id)?.created_at || ""
                const db = meetingById.get(b.meeting_id)?.created_at || ""
                return da.localeCompare(db)
              })
              .map((mem) => {
                const m = meetingById.get(mem.meeting_id)
                return (
                  <button
                    type="button"
                    key={mem.meeting_id}
                    className="mb-2 w-full rounded-xl bg-white px-3 py-2.5 text-left shadow-[var(--pm-shadow-sm)]"
                    onClick={() => onOpenMeeting(mem.meeting_id)}
                  >
                    <div className="text-[13px]">
                      {mem.n}{" "}
                      {m?.title || mem.meeting_id}
                    </div>
                    <div className="mt-0.5 text-[11px] text-muted-foreground">
                      {m?.transcript_index_status === "ready" ? t("common.ready") : (m?.transcript_index_status || "")}
                    </div>
                  </button>
                )
              })}
          </div>
        </section>

        <section
          className={cn(
            "absolute inset-0 z-[2] flex min-h-0 flex-col overflow-hidden rounded-[20px] bg-[var(--pm-float,#fdfbf7)] shadow-[var(--pm-shadow)] transition-opacity",
            overlayMeeting ? "opacity-100" : "pointer-events-none opacity-0",
          )}
        >
          <div className="flex items-center justify-between gap-2 px-3 pb-2 pt-3">
            <h3 className="m-0 text-[13px] uppercase tracking-wide">
              {overlayMeeting?.title || ""}
            </h3>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              onClick={() => {
                setOverlayN(null)
                setOverlayMeeting(null)
              }}
              aria-label={t("meeting.collapseTranscript")}
            >
              <X className="size-3.5" />
            </Button>
          </div>
          {overlayMeeting?.audio_path && (
            <div className="mx-3 mb-2 rounded-xl bg-white p-2.5 shadow-[var(--pm-shadow-sm)]">
              <audio
                className="w-full"
                controls
                src={`/api/meetings/${overlayMeeting.id}/audio`}
              />
            </div>
          )}
          <div className="min-h-0 flex-1 overflow-hidden px-1">
            <TranscriptTab
              segments={segments}
              onSegmentClick={() => {}}
              focusRef={focusRef}
              speakerNames={overlayMeeting?.speaker_names ?? {}}
              showSearch
            />
          </div>
        </section>
      </div>

      <Dialog open={joinOpen} onOpenChange={setJoinOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("meeting.joinGroupTitle")}</DialogTitle>
          </DialogHeader>
          <div className="max-h-72 overflow-auto">
            {joinCandidates.map((m) => (
              <button
                type="button"
                key={m.id}
                className="mb-2 w-full rounded-xl bg-white px-3 py-2 text-left shadow-[var(--pm-shadow-sm)]"
                onClick={() => void addExisting(m.id)}
              >
                {m.title}
              </button>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
