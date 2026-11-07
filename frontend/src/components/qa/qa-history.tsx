import { useQaModal } from "./use-qa-modal"
import { useEffect, useState } from "react"
import { Download, Plus } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { useT } from "@/i18n/use-t"
import { useAppStore } from "@/stores/app-store"
import { downloadQaOriginal, getQaImports, getQaVersions, type QaImportRecord, type QaListDetail, type QaVersion } from "@/api/client"
import { saveQaDownload } from "@/lib/qa-download"
import { QaActionMenu, QaMenuItem } from "./qa-controls"
import "./qa-settings-refinement.css"

export function QaHistoryRows({ list, versions, records, busy, onOpen, onDownload, onCorrect }: {
  list: QaListDetail; versions: QaVersion[]; records: QaImportRecord[]; busy: boolean;
  onOpen: (version: QaVersion) => void; onDownload: (version: QaVersion, record: QaImportRecord) => void; onCorrect: (record: QaImportRecord) => void;
}) {
  const t = useT()
  const locale = useAppStore(s => s.locale)
  return <ul className="qa-dialog-scroll qa-history-rows">{versions.map(version => {
    const record = records.find(r => r.committed_version_id === version.id)
    return <li className="qa-history-row" key={version.id}>
      <button className="qa-history-open" disabled={busy} onClick={() => onOpen(version)}>
        <span className="qa-history-version">V{version.number}</span>
        <span className="qa-history-caption"><strong>{record?.filename || t("qa.manualVersion")}</strong><span className="qa-meta">{new Date(version.created_at).toLocaleString(locale)}{version.id === list.current_version_id && ` · ${t("qa.current")}`}</span></span>
      </button>
      {record && <div className="qa-actions"><Button variant="ghost" size="icon-sm" disabled={busy} aria-label={t("qa.original")} title={t("qa.original")} onClick={() => onDownload(version, record)}><Download /></Button>
        {list.can_edit && <QaActionMenu label={t("qa.importRecordActions")}><QaMenuItem disabled={busy} onClick={() => onCorrect(record)}>{t("qa.correctMatch")}</QaMenuItem></QaActionMenu>}
      </div>}
    </li>
  })}</ul>
}

export function QaHistory({ collectionId, list, busy, close: onClosed, onOpen, onCorrect, onNewVersion }: {
  collectionId: string; list: QaListDetail; busy: boolean; close: () => void;
  onOpen: (version: QaVersion) => void; onCorrect: (record: QaImportRecord) => void; onNewVersion: () => Promise<boolean>;
}) {
  const { open, close, onOpenChangeComplete, exit } = useQaModal(onClosed)
  const t = useT()
  const [history, setHistory] = useState<{ versions: QaVersion[]; records: QaImportRecord[] } | null>(null)
  const [error, setError] = useState(false)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let alive = true
    Promise.all([getQaVersions(collectionId, list.id), getQaImports(collectionId, list.id)])
      .then(([versions, records]) => { if (alive) { setHistory({ versions, records }); setError(false) } })
      .catch(() => { if (alive) setError(true) })
    return () => { alive = false }
  }, [collectionId, list.id, retry])
  return <Dialog open={open} onOpenChangeComplete={onOpenChangeComplete} onOpenChange={open => { if (!open && !busy) close() }}><DialogContent className="qa-dialog qa-refinement-dialog sm:max-w-2xl"><DialogHeader><DialogTitle>{t("qa.history")}</DialogTitle></DialogHeader>
    {error ? <div className="qa-notice" role="alert">{t("qa.failed")} <Button size="sm" variant="secondary" onClick={() => setRetry(n => n + 1)}>{t("qa.retry")}</Button></div> : !history ? <p className="qa-meta" role="status">{t("common.loading")}</p> : <>
      <QaHistoryRows list={list} {...history} busy={busy} onOpen={version => exit(() => onOpen(version))} onCorrect={record => exit(() => onCorrect(record))}
        onDownload={(version, record) => { void downloadQaOriginal(collectionId, { ...list, version }).then(blob => saveQaDownload(blob, record.filename)).catch(() => toast.error(t("qa.noOriginal"))) }} />
      {list.can_edit && <div className="qa-refinement-footer"><p className="qa-meta">{t("qa.newVersionHelp")}</p><Button variant="secondary" size="sm" disabled={busy} onClick={async () => { if (await onNewVersion()) close() }}><Plus />{t("qa.nextVersion")}</Button></div>}
    </>}
  </DialogContent></Dialog>
}
