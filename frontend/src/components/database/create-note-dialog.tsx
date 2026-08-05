import { useState, useEffect } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

interface CreateNoteDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (title: string) => Promise<void>
}

export function CreateNoteDialog({ open, onOpenChange, onCreate }: CreateNoteDialogProps) {
  const [title, setTitle] = useState("")
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    if (open) {
      setTitle("")
      setCreating(false)
    }
  }, [open])

  const handleCreate = async () => {
    if (!title.trim()) return
    setCreating(true)
    try {
      await onCreate(title.trim())
      onOpenChange(false)
    } finally {
      setCreating(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="pm-dialog max-w-md">
        <DialogHeader>
          <DialogTitle>Create New Note</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 pt-2">
          <Input
            placeholder="Note title..."
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            autoFocus
          />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button size="sm" onClick={handleCreate} disabled={!title.trim() || creating}>
              {creating ? "Creating..." : "Create"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
