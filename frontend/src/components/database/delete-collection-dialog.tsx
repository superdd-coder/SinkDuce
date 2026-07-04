import { useState } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { deleteCollection } from "@/api/client"
import { toast } from "sonner"

interface DeleteCollectionDialogProps {
  collectionId: string | null
  collectionName: string
  onOpenChange: (open: boolean) => void
  onDeleted: () => void
}

export function DeleteCollectionDialog({ collectionId, collectionName, onOpenChange, onDeleted }: DeleteCollectionDialogProps) {
  const [confirmName, setConfirmName] = useState("")
  const [deleting, setDeleting] = useState(false)

  const handleDelete = async () => {
    if (!collectionId || confirmName !== collectionName) return
    setDeleting(true)
    try {
      const res = await deleteCollection(collectionId)
      if (res.error) toast.error(res.error)
      else {
        toast.success(res.message || "Collection deleted")
        setConfirmName("")
        onDeleted()
      }
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Dialog open={!!collectionId} onOpenChange={(v) => { if (!v) { setConfirmName(""); onOpenChange(false) } }}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Delete Collection</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <p className="text-sm text-muted-foreground">
            Type <span className="t-mono-family font-medium text-foreground">{collectionName}</span> to confirm deletion.
          </p>
          <Input
            value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)}
            placeholder="Type collection name"
          />
        </div>

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => { setConfirmName(""); onOpenChange(false) }}>Cancel</Button>
          <Button
            variant="destructive"
            onClick={handleDelete}
            disabled={confirmName !== collectionName || deleting}
          >
            {deleting ? "Deleting..." : "Delete"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
