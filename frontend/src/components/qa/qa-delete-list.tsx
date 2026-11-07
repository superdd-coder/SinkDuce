import { useQaModal } from "./use-qa-modal"
import { useRef, useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { deleteQaList, type QaListDetail } from "@/api/client"
import { useT } from "@/i18n/use-t"
import "./qa-settings-refinement.css"

export function QaDeleteList({ collectionId, list, close: onClosed, onDeleted }: {
  collectionId: string; list: QaListDetail; close: () => void; onDeleted: () => void;
}) {
  const { open, close, onOpenChangeComplete, exit } = useQaModal(onClosed)
  const t = useT()
  const cancelButton = useRef<HTMLButtonElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  async function confirmDelete() {
    if (!open || busy) return
    setBusy(true); setError("")
    try {
      const result = await deleteQaList(collectionId, list, list.title)
      if (result.cleanup_pending) toast.warning(t("qa.deleteCleanupPending"))
      else toast.success(t("qa.listDeleted"))
      exit(onDeleted)
    } catch (err) {
      const message = err instanceof Error ? err.message : ""
      setError(t(message.includes("stale_revision") || message.includes("confirmation_mismatch") ? "qa.stale" : message.includes("delete_index_unavailable") ? "qa.deleteIndexUnavailable" : "qa.failed"))
    } finally { setBusy(false) }
  }
  return <Dialog open={open} onOpenChangeComplete={onOpenChangeComplete} onOpenChange={open => { if (!open && !busy) close() }}><DialogContent className="qa-dialog qa-refinement-dialog sm:max-w-md" initialFocus={cancelButton}><DialogHeader><DialogTitle>{t("qa.deleteList")}</DialogTitle>
    <DialogDescription>{t("qa.deleteListHelp", { name: list.title })} {t("qa.deleteListScope")}</DialogDescription>
  </DialogHeader>
    {error && <p className="qa-notice" role="alert">{error}</p>}
    <div className="qa-refinement-footer">
      <Button ref={cancelButton} type="button" variant="ghost" disabled={busy} onClick={close}>{t("qa.cancel")}</Button>
      <Button type="button" variant="destructive-solid" disabled={busy} onClick={confirmDelete}>{t(busy ? "qa.processing" : "qa.deleteList")}</Button>
    </div>
  </DialogContent></Dialog>
}
