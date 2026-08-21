import { useState, useEffect, useCallback, useMemo } from "react"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Download, Loader2, Check, AlertCircle } from "lucide-react"
import { getModelStatus, downloadModels, type ModelStatus } from "@/api/client"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"

interface BundleDef {
  id: string
  label: string
  description: string
  modelIds: string[]
}

const BUNDLES: BundleDef[] = [
  {
    id: "file",
    label: "models.fileOnnx",
    description: "models.fileOnnxDesc",
    modelIds: ["transcription", "vad", "speaker", "punc"],
  },
  {
    id: "realtime",
    label: "models.realtimeOnnx",
    description: "models.realtimeOnnxDesc",
    modelIds: ["realtime"],
  },
]

interface ModelDownloadDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onComplete: () => void
  /** Called when a download job is successfully started (so parent can poll). */
  onDownloadStart?: () => void
}

export function ModelDownloadDialog({
  open,
  onOpenChange,
  onComplete,
  onDownloadStart,
}: ModelDownloadDialogProps) {
  const t = useT()
  const [models, setModels] = useState<ModelStatus[]>([])
  const [loading, setLoading] = useState(true)
  const [downloading, setDownloading] = useState(false)

  const modelMap = useMemo(() => {
    const map = new Map<string, ModelStatus>()
    for (const m of models) map.set(m.id, m)
    return map
  }, [models])

  const bundleStates = useMemo(() => {
    return BUNDLES.map((b) => {
      const memberModels = b.modelIds
        .map((mid) => modelMap.get(mid))
        .filter(Boolean) as ModelStatus[]
      const allDone =
        memberModels.length > 0 && memberModels.every((m) => m.downloaded)
      const anyDownloading = memberModels.some(
        (m) => m.status === "downloading" || m.status === "extracting"
      )
      const anyExtracting = memberModels.some(
        (m) => m.status === "extracting"
      )
      const anyError = memberModels.some((m) => m.status === "error")
      const totalSize = memberModels.reduce((sum, m) => sum + m.size_mb, 0)
      return {
        bundle: b,
        memberModels,
        allDone,
        anyDownloading,
        anyExtracting,
        anyError,
        totalSize,
      }
    })
  }, [modelMap])

  const allBundlesDone = bundleStates.every((b) => b.allDone)
  const isDownloading = models.some(
    (m) => m.status === "downloading" || m.status === "extracting"
  )
  const isExtracting = models.some((m) => m.status === "extracting")
  const downloadProgress = Math.max(
    0,
    ...models
      .filter((m) => m.status === "downloading" || m.status === "extracting")
      .map((m) => Math.round(m.progress || 0)),
    0
  )
  const hasError = models.some((m) => m.status === "error")
  const missingCount = models.filter((m) => !m.downloaded).length
  const totalMissingMb = models
    .filter((m) => !m.downloaded)
    .reduce((sum, m) => sum + (m.size_mb || 0), 0)
  const primaryBtnLabel = isExtracting
    ? t("common.extracting")
    : isDownloading
      ? t("settings.downloadingPct", { n: downloadProgress })
      : t("common.download")

  const fetchStatus = useCallback(async () => {
    try {
      const status = await getModelStatus()
      setModels(status)
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setDownloading(false)
    void fetchStatus()
  }, [open, fetchStatus])

  // Poll progress while downloading
  useEffect(() => {
    if (!downloading) return
    const interval = setInterval(async () => {
      try {
        const status = await getModelStatus()
        setModels(status)
      } catch {
        /* ignore */
      }
    }, 2000)
    return () => clearInterval(interval)
  }, [downloading])

  // Auto-detect download completion
  useEffect(() => {
    if (!downloading || models.length === 0) return
    const stillActive = models.some(
      (m) => m.status === "downloading" || m.status === "extracting"
    )
    if (!stillActive) {
      setDownloading(false)
      void fetchStatus()
      const allDone = BUNDLES.every((b) =>
        b.modelIds.every((mid) => modelMap.get(mid)?.downloaded)
      )
      if (allDone) {
        toast.success(t("shell.allModelsDownloaded"))
      }
    }
  }, [models, downloading, modelMap, fetchStatus])

  const handleDownload = async () => {
    setDownloading(true)
    try {
      // Full GitHub Release pack — no per-bundle selection
      await downloadModels()
      onDownloadStart?.()
      toast.info(t("models.downloadingOnnx"))
      // Progress continues in Settings toolbar — close dialog after start
      onOpenChange(false)
    } catch {
      toast.error(t("models.failedStart"))
      setDownloading(false)
    }
  }

  const sizeLabel =
    totalMissingMb >= 1000
      ? `${(totalMissingMb / 1000).toFixed(1)} GB`
      : `${Math.round(totalMissingMb)} MB`

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "pm-dialog pm-dialog--silk pm-settings-dlg",
          "max-w-md",
          "!animate-none data-open:!animate-none data-closed:!animate-none"
        )}
        overlayClassName="pm-dialog-overlay--silk"
      >
        <DialogHeader>
          <DialogKicker>{t("nav.settings")}</DialogKicker>
          <DialogTitle>{t("models.downloadLocal")}</DialogTitle>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="h-6 w-6 animate-spin text-[var(--pm-muted)]" />
          </div>
        ) : allBundlesDone ? (
          <div className="text-center py-6 space-y-3">
            <Check className="h-8 w-8 mx-auto text-[var(--pm-green)]" />
            <p className="pm-meta">{t("models.allReady")}</p>
            <Button
              variant="default"
              onClick={() => {
                onComplete()
                onOpenChange(false)
              }}
            >
              {t("common.done")}
            </Button>
          </div>
        ) : (
          <>
            <div className="pm-dialog-body space-y-4 px-1 pb-1">
              <p className="text-[13px] leading-relaxed text-[var(--pm-ink)]">
                {t("models.intro")}
              </p>
              {missingCount > 0 && (
                <p className="pm-meta">
                  {t("models.remaining", { size: sizeLabel, n: missingCount })}
                </p>
              )}

              <div className="space-y-2">
                {bundleStates.map(
                  ({
                    bundle,
                    memberModels,
                    allDone,
                    anyDownloading,
                    anyExtracting,
                    anyError,
                    totalSize,
                  }) => (
                    <div
                      key={bundle.id}
                      className={cn(
                        "rounded-[var(--pm-r-sm)] px-3 py-2.5",
                        allDone
                          ? "bg-[var(--pm-green-wash)]"
                          : "bg-[color-mix(in_srgb,var(--pm-ink)_2.5%,#ffffff)]"
                      )}
                    >
                      <div className="flex items-start gap-2.5">
                        {allDone ? (
                          <Check className="h-4 w-4 text-[var(--pm-green)] shrink-0 mt-0.5" />
                        ) : anyDownloading ? (
                          <Loader2 className="h-4 w-4 animate-spin text-[var(--pm-muted)] shrink-0 mt-0.5" />
                        ) : anyError ? (
                          <AlertCircle className="h-4 w-4 text-[var(--pm-danger)] shrink-0 mt-0.5" />
                        ) : (
                          <Download className="h-4 w-4 text-[var(--pm-muted)] shrink-0 mt-0.5" />
                        )}
                        <div className="min-w-0 flex-1">
                          <div className="pm-title text-[13px]">
                            {t(bundle.label)}
                          </div>
                          <p className="pm-meta mt-0.5">
                            {totalSize >= 1000
                              ? `${(totalSize / 1000).toFixed(1)}GB`
                              : `${totalSize}MB`}
                            {" · "}
                            {allDone
                              ? t("common.installed")
                              : anyExtracting
                                ? t("common.extracting")
                                : anyDownloading
                                  ? t("models.inProgress")
                                  : t("models.willInclude")}
                          </p>
                          {anyError &&
                            memberModels
                              .filter((m) => m.status === "error")
                              .map((m) => (
                                <p
                                  key={m.id}
                                  className="pm-meta text-[var(--pm-danger)] mt-1"
                                >
                                  {m.display_name}: {m.message}
                                </p>
                              ))}
                        </div>
                      </div>
                    </div>
                  )
                )}
              </div>

              {hasError && (
                <div className="pm-meta text-[var(--pm-danger)] p-2 rounded-[var(--pm-r-sm)] bg-[color-mix(in_srgb,var(--pm-danger)_10%,#ffffff)]">
                  {t("models.someFailed")}
                </div>
              )}
            </div>

            <DialogFooter>
              {!isDownloading && (
                <Button
                  variant="ghost"
                  onClick={() => {
                    onComplete()
                    onOpenChange(false)
                  }}
                >
                  {t("common.skip")}
                </Button>
              )}
              <Button
                variant="default"
                onClick={handleDownload}
                disabled={isDownloading}
              >
                {isDownloading ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    {primaryBtnLabel}
                  </>
                ) : (
                  <>
                    <Download className="h-4 w-4" />
                    {t("common.download")}
                  </>
                )}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
