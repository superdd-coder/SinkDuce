/**
 * Confirm folder upload with system Dialog (not window.confirm).
 * System junk (.DS_Store etc.) is always filtered out silently.
 */
import { useEffect, useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  isSkippedUploadFile,
  useFileMgmtStore,
} from "@/stores/file-mgmt-store"
import { useT } from "@/i18n/use-t"

export function FolderUploadConfirmDialog() {
  const t = useT()
  const pending = useFileMgmtStore((s) => s.folderUploadConfirm)
  const confirmFolderUpload = useFileMgmtStore((s) => s.confirmFolderUpload)
  const cancelFolderUploadConfirm = useFileMgmtStore(
    (s) => s.cancelFolderUploadConfirm
  )
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (pending) setBusy(false)
  }, [pending])

  const stats = useMemo(() => {
    if (!pending) return { keep: 0, topNames: [] as string[] }
    const keepFiles = pending.files.filter((f) => !isSkippedUploadFile(f))
    const names = new Set<string>()
    for (const f of keepFiles) {
      const rel =
        (f as File & { webkitRelativePath?: string }).webkitRelativePath ||
        f.name
      const top = rel.split(/[/\\]/)[0]
      if (top) names.add(top)
      if (names.size >= 3) break
    }
    return { keep: keepFiles.length, topNames: Array.from(names) }
  }, [pending])

  const open = !!pending

  const handleConfirm = async () => {
    setBusy(true)
    try {
      await confirmFolderUpload()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v && !busy) cancelFolderUploadConfirm()
      }}
    >
      <DialogContent className="pm-dialog max-w-md">
        <DialogHeader>
          <DialogTitle>{t("fileMgmt.uploadFolderQ")}</DialogTitle>
        </DialogHeader>
        <div className="pm-dialog-body">
          <p>
            {stats.keep === 1
              ? t("fileMgmt.uploadNFile", { n: stats.keep })
              : t("fileMgmt.uploadNFiles", { n: stats.keep })}
            {stats.topNames.length > 0 && (
              <>
                {" "}
                {t("common.from")}{" "}
                <span className="font-medium text-[var(--pm-ink)]">
                  {stats.topNames.join(", ")}
                  {stats.topNames.length >= 3 ? "…" : ""}
                </span>
              </>
            )}
            .
          </p>
          {stats.keep === 0 && (
            <p className="mt-2 text-[var(--pm-danger)]">
              {t("fileMgmt.noUploadableFiles")}
            </p>
          )}
        </div>
        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => cancelFolderUploadConfirm()}
          >
            {t("common.cancel")}
          </Button>
          <Button
            disabled={busy || stats.keep === 0}
            onClick={() => void handleConfirm()}
          >
            {busy ? t("common.uploading") : t("common.upload")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
