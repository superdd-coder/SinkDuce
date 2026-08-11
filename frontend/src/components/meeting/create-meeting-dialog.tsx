import { useState } from "react"
import { Loader2 } from "lucide-react"
import { createMeeting } from "@/api/client"
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
        const label = (recordingTitle || "current meeting").trim()
        toast.success("Meeting created", {
          description: `Still recording “${label}”. New meeting is in the list.`,
        })
        onCreated(meeting.id, { stayOnCurrent: true })
      } else {
        toast.success("Meeting created")
        onCreated(meeting.id)
      }
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
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
          ? "New meeting (stay on recording)"
          : "New meeting"
      }
    >
      {creating ? <Loader2 className="size-3 animate-spin" /> : null}
      New
    </button>
  )
}
