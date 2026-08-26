import { useEffect, useState } from "react"
import { toast } from "sonner"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  addMeetingGroupMember,
  createMeetingGroup,
  listGroupsForMeeting,
  listMeetingGroups,
  removeMeetingGroupMember,
  type MeetingGroup,
} from "@/api/meeting"
import { MeetingPickList } from "./meeting-pick-list"

export function MeetingGroupsPanel({
  meetingId,
  onOpenGroup,
  onGroupsChanged,
}: {
  meetingId: string
  onOpenGroup?: (groupId: string) => void
  onGroupsChanged?: () => void
}) {
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

  const afterChange = () => {
    reload()
    onGroupsChanged?.()
  }

  const joinGroup = async (groupId: string) => {
    try {
      await addMeetingGroupMember(groupId, meetingId)
      setJoinOpen(false)
      afterChange()
    } catch (err) {
      toast.error(t("common.failedWithError", { error: formatApiError(err, t) }))
    }
  }

  const leaveGroup = async (groupId: string) => {
    try {
      await removeMeetingGroupMember(groupId, meetingId)
      afterChange()
    } catch (err) {
      toast.error(t("common.failedWithError", { error: formatApiError(err, t) }))
    }
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="pm-meeting-section-rail-head px-3.5">
        <span className="pm-meeting-section-rail-title">{t("meeting.groupsTab")}</span>
        <span className="pm-meta tabular-nums">{mine.length}</span>
      </div>
      <div className="pm-meeting-group-actions mx-3 mb-2">
        <button type="button" className="pm-meeting-group-action" onClick={() => setCreateOpen(true)}>
          {t("meeting.createGroupShort")}
        </button>
        <button
          type="button"
          className="pm-meeting-group-action"
          onClick={() => { reload(); setJoinOpen(true) }}
        >
          {t("meeting.addToGroupShort")}
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden px-2 pb-2">
        <MeetingPickList
          items={mine.map((g) => ({
            id: g.id,
            title: g.title,
            status: t("meeting.groupCount", { n: g.members.length }),
            sortAt: g.last_chat_at || g.updated_at || g.created_at,
          }))}
          emptyText={t("meeting.meetingNotInGroups")}
          onSelect={(id) => onOpenGroup?.(id)}
          onRemove={(id) => void leaveGroup(id)}
          removeLabel={t("meeting.removeFromGroup")}
        />
      </div>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="pm-meeting-join-dialog">
          <DialogHeader className="pm-dialog-header--premium">
            <DialogKicker>{t("meeting.groupsTab")}</DialogKicker>
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
                  afterChange()
                }).catch((err) => {
                  toast.error(t("common.failedWithError", { error: formatApiError(err, t) }))
                })
              }}
            >
              {t("meeting.createGroup")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={joinOpen} onOpenChange={setJoinOpen}>
        <DialogContent className="pm-meeting-join-dialog">
          <DialogHeader className="pm-dialog-header--premium">
            <DialogKicker>{t("meeting.groupsTab")}</DialogKicker>
            <DialogTitle>{t("meeting.addToGroup")}</DialogTitle>
            <DialogDescription>{t("meeting.joinExistingGroupHint")}</DialogDescription>
          </DialogHeader>
          <div className="pm-dialog-body min-w-0">
            <MeetingPickList
              items={others.map((g) => ({
                id: g.id,
                title: g.title,
                status: t("meeting.groupCount", { n: g.members.length }),
                sortAt: g.last_chat_at || g.updated_at || g.created_at,
              }))}
              emptyText={t("meeting.joinExistingGroupEmpty")}
              filterPlaceholder={t("meeting.pickFilterGroups")}
              onSelect={(id) => void joinGroup(id)}
            />
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
