import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { toast } from "sonner"
import { createChain, createNode } from "@/api/file-mgmt"

interface CreateChainDialogProps {
  collectionId: string
  parentChainId: string
  parentNodeId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: () => void
}

export function CreateChainDialog({
  collectionId,
  parentChainId,
  parentNodeId,
  open,
  onOpenChange,
  onCreated,
}: CreateChainDialogProps) {
  const [title, setTitle] = useState("")
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async () => {
    if (!parentChainId || !parentNodeId) return
    setSubmitting(true)
    try {
      const newChain = await createChain(collectionId, {
        parent_chain_id: parentChainId,
        parent_node_id: parentNodeId,
        title: title.trim() || "New Branch",
      })
      // Auto-create a start node as the first node of the branch chain (order 1 = anchor)
      await createNode(collectionId, newChain.chain_id, {
        group_id: null,
        node_type: "start",
        title: null,
        order: 1,
        event_time: null,
      })
      toast.success(`Branch "${title.trim() || "New Branch"}" created with start node`)
      setTitle("")
      onCreated()
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Create Branch Chain</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 mt-2">
          <p className="text-xs text-muted-foreground">
            A new branch chain will be created from this node, with its own
            branch folder and timeline. A start node will be added automatically.
          </p>
          <div>
            <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60 block mb-1">
              Branch Title
            </label>
            <input
              className="w-full text-xs border rounded px-2 py-1.5 bg-background"
              placeholder="Branch title..."
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
              autoFocus
            />
          </div>
        </div>
        <div className="flex justify-end gap-2 pt-3">
          <Button variant="outline" size="sm" className="text-[10px] h-7" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button size="sm" className="text-[10px] h-7" onClick={handleSubmit} disabled={submitting}>
            {submitting ? "Creating..." : "Create"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
