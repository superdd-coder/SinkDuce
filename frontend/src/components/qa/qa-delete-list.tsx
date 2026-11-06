import { useQaModal } from "./use-qa-modal"
import { useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { deleteQaList, type QaListDetail } from "@/api/client"
import { useT } from "@/i18n/use-t"
import { QaField } from "./qa-controls"
import "./qa-settings-refinement.css"

export function QaDeleteList({ collectionId, list, close: onClosed, onDeleted }: {
  collectionId: string; list: QaListDetail; close: () => void; onDeleted: () => void;
}) {
  const { open, close, onOpenChangeComplete, exit } = useQaModal(onClosed)
  const t = useT()
  const [name, setName] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  return <Dialog open={open} onOpenChangeComplete={onOpenChangeComplete} onOpenChange={open => { if (!open && !busy) close() }}><DialogContent className="qa-dialog qa-refinement-dialog sm:max-w-md"><DialogHeader><DialogTitle>{t("qa.deleteList")}</DialogTitle>
    <DialogDescription>{t("qa.deleteListHelp", { name: list.title })} {t("qa.deleteListScope")}</DialogDescription>
  </DialogHeader>
    {error && <p className="qa-notice" role="alert">{error}</p>}
    <form className="qa-form" onSubmit={async event => {
      event.preventDefault()
      if (busy || name !== list.title) return
      setBusy(true); setError("")
      try {
        const result = await deleteQaList(collectionId, list, name)
        if (result.cleanup_pending) toast.warning(t("qa.deleteCleanupPending"))
        else toast.success(t("qa.listDeleted"))
        exit(onDeleted)
      } catch (err) {
        const message = err instanceof Error ? err.message : ""
        setError(t(message.includes("stale_revision") ? "qa.stale" : message.includes("delete_index_unavailable") ? "qa.deleteIndexUnavailable" : "qa.failed"))
      } finally { setBusy(false) }
    }}>
      <QaField label={t("qa.confirmListName")}><Input autoFocus value={name} disabled={busy} onChange={e => setName(e.target.value)} placeholder={list.title} autoComplete="off" /></QaField>
      <div className="qa-refinement-footer"><Button type="button" variant="ghost" disabled={busy} onClick={close}>{t("qa.cancel")}</Button><Button type="submit" variant="destructive-solid" disabled={busy || name !== list.title}>{t(busy ? "qa.processing" : "qa.deleteList")}</Button></div>
    </form>
  </DialogContent></Dialog>
}
