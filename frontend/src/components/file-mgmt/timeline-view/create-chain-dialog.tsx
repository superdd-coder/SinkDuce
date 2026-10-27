import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { toast } from "sonner"
import { createChain, createNode } from "@/api/file-mgmt"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"

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
  const t = useT()
  const [title, setTitle] = useState("")
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async () => {
    if (!parentChainId || !parentNodeId) return
    setSubmitting(true)
    try {
      const newChain = await createChain(collectionId, {
        parent_chain_id: parentChainId,
        parent_node_id: parentNodeId,
        title: title.trim() || t("fileMgmt.newBranch"),
      })
      // Auto-create a start node as the first node of the branch chain (order 1 = anchor)
      await createNode(collectionId, newChain.chain_id, {
        group_id: null,
        node_type: "start",
        title: null,
        order: 1,
        event_time: null,
      })
      toast.success(
        t("fileMgmt.branchCreatedWithStart", {
          title: title.trim() || t("fileMgmt.newBranch"),
        })
      )
      setTitle("")
      onCreated()
    } catch (err) {
      toast.error(t("fileMgmt.failed", { error: formatApiError(err, t) }))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="pm-dialog pm-dialog--silk max-w-sm"
        overlayClassName="pm-dialog-overlay--silk"
      >
        <DialogHeader>
          <DialogTitle>{t("fileMgmt.createBranch")}</DialogTitle>
        </DialogHeader>
        <div className="pm-dialog-body space-y-3">
          <p className="pm-meta text-[var(--pm-muted)]">
            {t("fileMgmt.createBranchHint")}
          </p>
          <div>
            <label className="pm-field-label">{t("fileMgmt.branchTitle")}</label>
            <input
              className="pm-field w-full"
              placeholder={t("fileMgmt.branchTitlePh")}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
              autoFocus
            />
          </div>
        </div>
        <DialogFooter className="gap-2 sm:justify-end">
          <Button
            variant="ghost"
            size="xs"
            className="pm-btn-ghost"
            onClick={() => onOpenChange(false)}
          >
            {t("common.cancel")}
          </Button>
          <Button
            size="xs"
            className="pm-btn-pri"
            onClick={handleSubmit}
            disabled={submitting}
          >
            {submitting ? t("common.creating") : t("common.create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
