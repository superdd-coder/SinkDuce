import { useState, useEffect } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useT } from "@/i18n/use-t"

interface CreateNoteDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (title: string) => Promise<void>
}

export function CreateNoteDialog({ open, onOpenChange, onCreate }: CreateNoteDialogProps) {
  const t = useT()
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
          <DialogTitle>{t("library.createNote")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 pt-1">
          <div>
            <label className="pm-field-label">{t("common.title")}</label>
            <Input
              placeholder={t("library.noteTitlePh")}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleCreate()}
              autoFocus
              className="h-8"
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
              onClick={handleCreate}
              disabled={!title.trim() || creating}
            >
              {creating ? t("common.loading") : t("common.create")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
