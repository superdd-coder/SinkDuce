import { ScrollArea } from "@/components/ui/scroll-area"
import { Trash2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { CreateMeetingButton } from "./create-meeting-dialog"
import type { Meeting } from "@/api/client"

interface MeetingListProps {
  meetings: Meeting[]
  activeMeeting: string | null
  onSelect: (id: string) => void
  onCreated: (meetingId: string) => void
  onDelete: (id: string) => void
}

export function MeetingList({ meetings, activeMeeting, onSelect, onCreated, onDelete }: MeetingListProps) {
  return (
    <div className="w-64 border-r border-border flex flex-col shrink-0">
      <div className="flex items-center justify-between px-4 h-12 border-b border-border">
        <span
          className="uppercase t-body-family"
          style={{
            fontSize: "14px",
            fontWeight: 300,
            letterSpacing: "-0.015em",
            color: "var(--muted-foreground)",
          }}
        >
          Meetings
        </span>
        <CreateMeetingButton onCreated={onCreated} />
      </div>
      <ScrollArea className="flex-1">
        <div className="px-4 py-2">
          {meetings.map((m) => {
            const isActive = activeMeeting === m.id
            return (
              <div
                key={m.id}
                className={cn(
                  "group relative flex items-start gap-2 py-2.5 cursor-pointer transition-colors border-b border-dashed border-border",
                  isActive ? "text-primary font-medium" : "hover:text-primary text-foreground",
                )}
                onClick={() => onSelect(m.id)}
              >
                {/* Active indicator */}
                {isActive && (
                  <span className="absolute left-[-16px] top-0 bottom-0 w-[1.5px] bg-primary" />
                )}

                <span className="flex-1 line-clamp-2 break-words leading-snug text-xs">{m.title}</span>
                <button
                  className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive bg-transparent border-none cursor-pointer p-0.5"
                  onClick={(e) => { e.stopPropagation(); onDelete(m.id) }}
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            )
          })}
          {meetings.length === 0 && (
            <p className="text-xs text-muted-foreground py-4 text-center">No meetings yet</p>
          )}
        </div>
      </ScrollArea>
    </div>
  )
}
