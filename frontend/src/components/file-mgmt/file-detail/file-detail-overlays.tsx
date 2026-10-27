import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { Loader2, Star } from "lucide-react"
import type { FileDetail, FileVersion } from "@/types/file-mgmt"
import { useT } from "@/i18n/use-t"

export function FileDetailTitleChrome({
  titleName,
  isIngesting,
  ingestProgress,
  isHistoricalFocus,
  focusVersion,
  chunksTotal,
  detail,
}: {
  titleName: string
  isIngesting: boolean
  ingestProgress?: { message?: string; progress?: number } | null
  isHistoricalFocus: boolean
  focusVersion: FileVersion | null | undefined
  chunksTotal: number
  detail: FileDetail | null
}) {
  const t = useT()
  return (
          <div className="pm-ws-chrome">
            <DialogHeader className="shrink-0 flex-1 min-w-0 !p-0">
              <DialogTitle className="flex items-center gap-2 min-w-0 text-left">
                <span className="pm-ws-title truncate" title={titleName}>
                  {titleName}
                </span>
                {isIngesting && (
                  <Badge
                    variant="secondary"
                    className="pm-ws-badge is-live"
                    title={ingestProgress?.message || t("fileMgmt.ingestingEllipsis")}
                  >
                    <Loader2 className="h-3 w-3 animate-spin mr-1 inline" />
                    {t("fileMgmt.ingestingEllipsis")}
                    {typeof ingestProgress?.progress === "number"
                      ? ` ${Math.round(ingestProgress.progress)}%`
                      : ""}
                  </Badge>
                )}
                {isHistoricalFocus && (
                  <Badge variant="secondary" className="pm-ws-badge">
                    {focusVersion
                      ? `v${focusVersion.version_no} · ${t("fileMgmt.oldVersion")}`
                      : t("fileMgmt.oldVersion")}
                  </Badge>
                )}
                {chunksTotal > 0 && !isIngesting && (
                  <Badge variant="secondary" className="ml-1 pm-ws-badge">
                    {t("fileMgmt.chunksN", { n: chunksTotal })}
                  </Badge>
                )}
                {detail?.archived && (
                  <Badge variant="secondary" className="pm-ws-badge">
                    {t("fileMgmt.archived")}
                  </Badge>
                )}
                {detail?.unsupported && !isHistoricalFocus && (
                  <Badge variant="outline" className="pm-ws-badge">
                    {t("fileMgmt.unsupported")}
                  </Badge>
                )}
                {detail?.is_definitive && (
                  <Star className="h-3.5 w-3.5 shrink-0 text-[var(--pm-green)] fill-[var(--pm-green)]" />
                )}
              </DialogTitle>
            </DialogHeader>
            {/* room for dialog close button */}
            <div className="w-8 shrink-0" />
          </div>

  )
}

export function FileDetailRollbackDialog({
  rollbackConfirm,
  setRollbackConfirm,
  rollingBack,
  focusVersion,
  focusVersionId,
  handleRollback,
}: {
  rollbackConfirm: boolean
  setRollbackConfirm: (open: boolean) => void
  rollingBack: boolean
  focusVersion: FileVersion | null | undefined
  focusVersionId: string | null | undefined
  handleRollback: () => void | Promise<void>
}) {
  const t = useT()
  return (
    <>
      {/* Rollback historical version — premium compact confirm */}
      <Dialog
        open={rollbackConfirm}
        onOpenChange={(v) => {
          if (!rollingBack) setRollbackConfirm(v)
        }}
      >
        <DialogContent
          showCloseButton={false}
          overlayClassName="pm-dialog-overlay--silk"
          className="pm-dialog pm-dialog-confirm"
        >
          <DialogHeader>
            <DialogKicker>{t("common.version")}</DialogKicker>
            <DialogTitle>{t("fileMgmt.rollbackQ")}</DialogTitle>
            {focusVersion || focusVersionId ? (
              <p
                className="pm-dialog-confirm-target"
                title={
                  focusVersion?.storage_file_id
                    ? `v${focusVersion.version_no} · ${focusVersion.storage_file_id}`
                    : focusVersion
                      ? `v${focusVersion.version_no}`
                      : t("fileMgmt.selectedVersion")
                }
              >
                {focusVersion ? (
                  <>
                    <span className="tabular-nums">
                      v{focusVersion.version_no}
                    </span>
                    {focusVersion.storage_file_id
                      ? ` · ${focusVersion.storage_file_id}`
                      : ""}
                  </>
                ) : (
                  t("fileMgmt.selectedVersion")
                )}
              </p>
            ) : null}
            <DialogDescription>
              {t("fileMgmt.rollbackBody")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={rollingBack}
              onClick={() => setRollbackConfirm(false)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              type="button"
              variant="destructive-solid"
              size="sm"
              disabled={rollingBack}
              onClick={() => void handleRollback()}
            >
              {rollingBack ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                  {t("fileMgmt.rollingBack")}
                </>
              ) : (
                t("fileMgmt.rollBack")
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
