import { useState } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { FileText, GripVertical, Plus } from "lucide-react"
import { cn } from "@/lib/utils"
import { type NoteListItem } from "@/api/client"

interface NoteSidebarLeftProps {
  notes: NoteListItem[]
  activeNoteId: string
  onSwitchNote: (id: string) => void
  onCreateNote?: () => void
}

export function NoteSidebarLeft({
  notes,
  activeNoteId,
  onSwitchNote,
  onCreateNote,
}: NoteSidebarLeftProps) {
  return (
    <div className="w-56 border-r border-border flex flex-col shrink-0">
      <div className="px-3 h-9 border-b border-border flex items-center justify-between shrink-0">
        <span className="text-[12px] font-semibold uppercase tracking-[0.25em] text-muted-foreground">
          Notes
        </span>
        {onCreateNote && (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0"
            onClick={onCreateNote}
            title="New Note"
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
      <ScrollArea className="flex-1">
        <div className="p-1.5 space-y-0.5">
          {notes.map((note) => (
            <NoteDraggableItem
              key={note.id}
              note={note}
              isActive={note.id === activeNoteId}
              onClick={() => onSwitchNote(note.id)}
            />
          ))}
          {notes.length === 0 && (
            <p className="text-xs text-muted-foreground px-2 py-4 text-center">
              No notes in this collection
            </p>
          )}
        </div>
      </ScrollArea>
    </div>
  )
}

interface NoteDraggableItemProps {
  note: NoteListItem
  isActive: boolean
  onClick: () => void
}

function NoteDraggableItem({ note, isActive, onClick }: NoteDraggableItemProps) {
  const [dragging, setDragging] = useState(false)

  const handleDragStart = (e: React.DragEvent) => {
    if (isActive) {
      e.preventDefault()
      return
    }
    e.dataTransfer.setData("application/note-id", note.id)
    e.dataTransfer.effectAllowed = "copy"
    setDragging(true)
  }

  const handleDragEnd = () => {
    setDragging(false)
  }

  return (
    <button
      className={cn(
        "w-full text-left flex items-center gap-2 px-2 py-1.5 text-[12px] transition-colors group border-b border-dashed border-border",
        isActive
          ? "text-primary font-medium cursor-default"
          : "hover:text-primary text-foreground cursor-grab active:cursor-grabbing",
        dragging && "opacity-50"
      )}
      draggable={!isActive}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onClick={onClick}
    >
      {!isActive && (
        <GripVertical className="h-3 w-3 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
      )}
      <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
      <span className="flex-1 truncate">{note.title}</span>
      {note.is_extracted && (
        <span className="h-1.5 w-1.5 rounded-full bg-primary shrink-0" title="Has been extracted" />
      )}
    </button>
  )
}
