import { useState } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { renameCollection } from "@/api/client"
import { toast } from "sonner"

interface RenameCollectionDialogProps {
  collectionId: string
  currentName: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onRenamed: () => void
}

export function RenameCollectionDialog({
  collectionId,
  currentName,
  open,
  onOpenChange,
  onRenamed,
}: RenameCollectionDialogProps) {
  const [newName, setNewName] = useState(currentName)
  const [saving, setSaving] = useState(false)

  const handleRename = async () => {
    if (!newName.trim() || newName.trim() === currentName) {
      onOpenChange(false)
      return
    }
    setSaving(true)
    try {
      const res = await renameCollection(collectionId, newName.trim())
      if (res.error) {
        toast.error(res.error)
      } else {
        toast.success(res.message || "Collection renamed")
        onOpenChange(false)
        onRenamed()
      }
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="pm-dialog max-w-sm">
        <DialogHeader>
          <DialogTitle>Rename Collection</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 py-1">
          <div>
            <label className="pm-field-label">New Name</label>
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Enter new name"
              className="h-8"
              onKeyDown={(e) => {
                if (e.key === "Enter") handleRename()
              }}
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              onClick={handleRename}
              disabled={saving || !newName.trim()}
            >
              {saving ? "Renaming..." : "Rename"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
