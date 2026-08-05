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

export function FolderUploadConfirmDialog() {
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
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-sm">Upload folder?</DialogTitle>
        </DialogHeader>
        <div className="text-xs text-muted-foreground">
          <p>
            Upload{" "}
            <span className="font-medium text-foreground tabular-nums">
              {stats.keep}
            </span>{" "}
            file{stats.keep === 1 ? "" : "s"}
            {stats.topNames.length > 0 && (
              <>
                {" "}
                from{" "}
                <span className="font-medium text-foreground">
                  {stats.topNames.join(", ")}
                  {stats.topNames.length >= 3 ? "…" : ""}
                </span>
              </>
            )}
            .
          </p>
          {stats.keep === 0 && (
            <p className="mt-2 text-amber-600 dark:text-amber-400">
              No uploadable files found in the selection.
            </p>
          )}
        </div>
        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => cancelFolderUploadConfirm()}
          >
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={busy || stats.keep === 0}
            onClick={() => void handleConfirm()}
          >
            {busy ? "Uploading…" : "Upload"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
