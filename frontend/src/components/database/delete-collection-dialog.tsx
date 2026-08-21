import { useState } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { deleteCollection } from "@/api/client"
import { toast } from "sonner"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"

interface DeleteCollectionDialogProps {
  collectionId: string | null
  collectionName: string
  onOpenChange: (open: boolean) => void
  onDeleted: () => void
}

export function DeleteCollectionDialog({ collectionId, collectionName, onOpenChange, onDeleted }: DeleteCollectionDialogProps) {
  const t = useT()
  const [confirmName, setConfirmName] = useState("")
  const [deleting, setDeleting] = useState(false)

  const handleDelete = async () => {
    if (!collectionId || confirmName !== collectionName) return
    setDeleting(true)
    try {
      const res = await deleteCollection(collectionId)
      if (res.error) toast.error(res.error)
      else {
        toast.success(res.message || t("library.collectionDeleted"))
        setConfirmName("")
        onDeleted()
      }
    } catch (err) {
      toast.error(t("common.failedWithError", { error: formatApiError(err, t) }))
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Dialog open={!!collectionId} onOpenChange={(v) => { if (!v) { setConfirmName(""); onOpenChange(false) } }}>
      <DialogContent className="pm-dialog max-w-sm">
        <DialogHeader>
          <DialogTitle>{t("library.deleteCollection")}</DialogTitle>
        </DialogHeader>

        <div className="space-y-3 py-1">
          <p className="pm-dialog-body">
            {t("library.typeToConfirm", { name: "\u0000" }).split("\u0000")[0]}
            <span className="t-mono-family font-medium text-[var(--pm-ink)]">
              {collectionName}
            </span>
            {t("library.typeToConfirm", { name: "\u0000" }).split("\u0000")[1]}
          </p>
          <Input
            value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)}
            placeholder={t("library.typeCollectionName")}
            className="h-8"
          />
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <Button
            variant="ghost"
            onClick={() => {
              setConfirmName("")
              onOpenChange(false)
            }}
          >
            {t("common.cancel")}
          </Button>
          <Button
            variant="destructive-solid"
            onClick={handleDelete}
            disabled={confirmName !== collectionName || deleting}
          >
            {deleting ? t("common.loading") : t("common.delete")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
