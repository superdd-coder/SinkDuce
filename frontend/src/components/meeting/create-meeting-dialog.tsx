import { useState } from "react"
import { Loader2 } from "lucide-react"
import { createMeeting } from "@/api/client"
import { formatApiError } from "@/api/http"
import { useT } from "@/i18n/use-t"
import { toast } from "sonner"

interface CreateMeetingButtonProps {
  onCreated: (meetingId: string, opts?: { stayOnCurrent?: boolean }) => void
  /**
   * While a capture is live, create still works but does not navigate away —
   * prevents replacing the recording meeting in the main stage.
   */
  stayOnCurrent?: boolean
  /** Title of the meeting currently recording (for toast). */
  recordingTitle?: string | null
}

export function CreateMeetingButton({
  onCreated,
  stayOnCurrent = false,
  recordingTitle = null,
}: CreateMeetingButtonProps) {
  const t = useT()
  const [creating, setCreating] = useState(false)

  const handleCreate = async () => {
    setCreating(true)
    try {
      const title = new Date().toLocaleString("zh-CN", {
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit",
      }).replace(/\//g, "-")
      const meeting = await createMeeting(title)
      if (stayOnCurrent) {
        const label = (recordingTitle || t("meeting.currentMeeting")).trim()
        toast.success(t("meeting.meetingCreated"), {
          description: t("meeting.stillRecording", { label }),
        })
        onCreated(meeting.id, { stayOnCurrent: true })
      } else {
        toast.success(t("meeting.meetingCreated"))
        onCreated(meeting.id)
      }
    } catch (err) {
      toast.error(t("common.failedWithError", { error: formatApiError(err, t) }))
    } finally {
      setCreating(false)
    }
  }

  return (
    <button
      type="button"
      className="pm-rail-new shrink-0"
      onClick={handleCreate}
      disabled={creating}
      title={
        stayOnCurrent
          ? t("meeting.newMeetingStay")
          : t("meeting.newMeeting")
      }
    >
      {creating ? <Loader2 className="size-3 animate-spin" /> : null}
      {t("common.new")}
    </button>
  )
}
