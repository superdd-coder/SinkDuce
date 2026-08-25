import { useEffect, useState } from "react"
import { useT } from "@/i18n/use-t"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  addMeetingGroupMember,
  createMeetingGroup,
  listGroupsForMeeting,
  listMeetingGroups,
  type MeetingGroup,
} from "@/api/meeting"

export function MeetingGroupsMeta({ meetingId }: { meetingId: string }) {
  const t = useT()
  const [mine, setMine] = useState<MeetingGroup[]>([])
  const [all, setAll] = useState<MeetingGroup[]>([])
  const [createOpen, setCreateOpen] = useState(false)
  const [joinOpen, setJoinOpen] = useState(false)
  const [title, setTitle] = useState("")

  const reload = () => {
    void listGroupsForMeeting(meetingId).then(setMine).catch(() => {})
    void listMeetingGroups().then(setAll).catch(() => {})
  }

  useEffect(() => {
    reload()
  }, [meetingId])

  const others = all.filter((g) => !mine.some((m) => m.id === g.id))

  return (
    <>
      <div className="pm-meeting-meta-row">
        <span className="pm-meeting-meta-key">{t("meeting.groupsTab")}</span>
        <span className="pm-meeting-meta-val">
          {mine.length === 0 ? "—" : mine.map((g) => g.title).join(", ")}{" "}
          <Button type="button" variant="ghost" size="xs" onClick={() => setCreateOpen(true)}>
            {t("meeting.createGroup")}
          </Button>
          <Button type="button" variant="ghost" size="xs" onClick={() => { reload(); setJoinOpen(true) }}>
            {t("meeting.addToGroup")}
          </Button>
        </span>
      </div>
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("meeting.createGroup")}</DialogTitle>
          </DialogHeader>
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t("meeting.groupName")}
          />
          <DialogFooter>
            <Button
              type="button"
              onClick={() => {
                void createMeetingGroup(meetingId, title).then(() => {
                  setCreateOpen(false)
                  setTitle("")
                  reload()
                })
              }}
            >
              {t("meeting.createGroup")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={joinOpen} onOpenChange={setJoinOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("meeting.addToGroup")}</DialogTitle>
          </DialogHeader>
          <div className="max-h-72 overflow-auto">
            {others.map((g) => (
              <button
                type="button"
                key={g.id}
                className="mb-2 w-full rounded-xl bg-white px-3 py-2 text-left shadow-[var(--pm-shadow-sm)]"
                onClick={() => {
                  void addMeetingGroupMember(g.id, meetingId).then(() => {
                    setJoinOpen(false)
                    reload()
                  })
                }}
              >
                {g.title}
              </button>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
