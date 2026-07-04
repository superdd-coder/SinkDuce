import { useState } from "react"
import { Loader2 } from "lucide-react"
import { createMeeting } from "@/api/client"
import { toast } from "sonner"

interface CreateMeetingButtonProps {
  onCreated: (meetingId: string) => void
}

export function CreateMeetingButton({ onCreated }: CreateMeetingButtonProps) {
  const [creating, setCreating] = useState(false)

  const handleCreate = async () => {
    setCreating(true)
    try {
      const title = new Date().toLocaleString("zh-CN", {
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit",
      }).replace(/\//g, "-")
      const meeting = await createMeeting(title)
      toast.success("Meeting created")
      onCreated(meeting.id)
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setCreating(false)
    }
  }

  return (
    <button
      type="button"
      onClick={handleCreate}
      disabled={creating}
      className="text-[10px] font-medium uppercase tracking-[0.1em] px-2 py-0.5 cursor-pointer transition-opacity hover:opacity-85 bg-primary text-primary-foreground border-none t-sans-family"
      style={{ borderRadius: "2px" }}
    >
      {creating ? <Loader2 className="h-3 w-3 animate-spin inline mr-1" /> : null}
      + New
    </button>
  )
}
