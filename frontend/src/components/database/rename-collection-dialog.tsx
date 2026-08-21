import { useState } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { renameCollection } from "@/api/client"
import { toast } from "sonner"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"

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
  const t = useT()
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
        toast.success(res.message || t("library.collectionRenamed"))
        onOpenChange(false)
        onRenamed()
      }
    } catch (err) {
      toast.error(t("common.failedWithError", { error: formatApiError(err, t) }))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="pm-dialog max-w-sm">
        <DialogHeader>
          <DialogTitle>{t("library.renameCollection")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 py-1">
          <div>
            <label className="pm-field-label">{t("library.newName")}</label>
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder={t("library.enterNewName")}
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
              {t("common.cancel")}
            </Button>
            <Button
              onClick={handleRename}
              disabled={saving || !newName.trim()}
            >
              {saving ? t("common.loading") : t("common.rename")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
