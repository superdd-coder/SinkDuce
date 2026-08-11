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
import { Input } from "@/components/ui/input"
import { FieldLabel } from "@/components/ui/field-label"
import { Download, Loader2, Check, AlertCircle, Eye, EyeOff } from "lucide-react"
import { getModelStatus, downloadModels, type ModelStatus } from "@/api/client"
import { toast } from "sonner"
import { cn } from "@/lib/utils"

interface BundleDef {
  id: string
  label: string
  description: string
  modelIds: string[]
}

const BUNDLES: BundleDef[] = [
  {
    id: "file",
    label: "File Transcription",
    description: "SenseVoiceSmall + FSMN-VAD + CAM++ Speaker + CT-Punc",
    modelIds: ["transcription", "vad", "speaker", "punc"],
  },
  {
    id: "realtime",
    label: "Real-time Transcription",
    description: "Paraformer Streaming",
    modelIds: ["realtime"],
  },
]

interface ModelDownloadDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onComplete: () => void
}

export function ModelDownloadDialog({ open, onOpenChange, onComplete }: ModelDownloadDialogProps) {
  const [models, setModels] = useState<ModelStatus[]>([])
  const [loading, setLoading] = useState(true)
  const [hfToken, setHfToken] = useState("")
  const [showToken, setShowToken] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [selectedBundles, setSelectedBundles] = useState<Set<string>>(new Set())

  const modelMap = useMemo(() => {
    const map = new Map<string, ModelStatus>()
    for (const m of models) map.set(m.id, m)
    return map
  }, [models])

  // Compute selected model IDs from selected bundles (only non-downloaded)
  const selectedModelIds = useMemo(() => {
    const ids = new Set<string>()
    for (const b of BUNDLES) {
      if (selectedBundles.has(b.id)) {
        for (const mid of b.modelIds) {
          const m = modelMap.get(mid)
          if (m && !m.downloaded) ids.add(mid)
        }
      }
    }
    return ids
  }, [selectedBundles, modelMap])

  const bundleStates = useMemo(() => {
    return BUNDLES.map((b) => {
      const memberModels = b.modelIds.map((mid) => modelMap.get(mid)).filter(Boolean) as ModelStatus[]
      const allDone = memberModels.length > 0 && memberModels.every((m) => m.downloaded)
      const anyDownloading = memberModels.some((m) => m.status === "downloading")
      const anyError = memberModels.some((m) => m.status === "error")
      const totalSize = memberModels.reduce((sum, m) => sum + m.size_mb, 0)
      return { bundle: b, memberModels, allDone, anyDownloading, anyError, totalSize }
    })
  }, [modelMap])

  const allBundlesDone = bundleStates.every((b) => b.allDone)
  const isDownloading = models.some((m) => m.status === "downloading")
  const hasError = models.some((m) => m.status === "error")

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
    if (open) {
      setLoading(true)
      setDownloading(false)
      fetchStatus().then(() => {
        // After loading, auto-select bundles with missing models
        setSelectedBundles(() => {
          const toSelect = new Set<string>()
          for (const b of BUNDLES) {
            const hasMissing = b.modelIds.some((mid) => {
              const m = modelMap.get(mid)
              return m && !m.downloaded
            })
            if (hasMissing) toSelect.add(b.id)
          }
          return toSelect
        })
      })
    }
  }, [open, fetchStatus])

  // Poll progress while downloading
  useEffect(() => {
    if (!downloading) return
    const interval = setInterval(async () => {
      try {
        const status = await getModelStatus()
        setModels(status)
      } catch { /* ignore */ }
    }, 2000)
    return () => clearInterval(interval)
  }, [downloading])

  // Auto-detect download completion and refresh
  useEffect(() => {
    if (!downloading || models.length === 0) return
    const stillActive = models.some((m) => m.status === "downloading")
    if (!stillActive) {
      setDownloading(false)
      // Refresh to get final state
      fetchStatus()
      const allDone = BUNDLES.every((b) =>
        b.modelIds.every((mid) => {
          const m = modelMap.get(mid)
          return m?.downloaded
        })
      )
      if (allDone) {
        toast.success("All models downloaded!")
      }
    }
  }, [models, downloading, modelMap, fetchStatus])

  const handleDownload = async () => {
    setDownloading(true)
    try {
      const ids = Array.from(selectedModelIds)
      await downloadModels(hfToken || undefined, ids.length > 0 ? ids : undefined)
      toast.info("Download started...")
    } catch {
      toast.error("Failed to start download")
      setDownloading(false)
    }
  }

  const toggleBundle = (bundleId: string) => {
    setSelectedBundles((prev) => {
      const next = new Set(prev)
      if (next.has(bundleId)) next.delete(bundleId)
      else next.add(bundleId)
      return next
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "pm-dialog pm-dialog--silk pm-settings-dlg",
          "max-w-lg",
          "!animate-none data-open:!animate-none data-closed:!animate-none",
        )}
        overlayClassName="pm-dialog-overlay--silk"
      >
        <DialogHeader>
          <DialogKicker>Settings</DialogKicker>
          <DialogTitle>Download local models</DialogTitle>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-[var(--pm-muted)]" />
          </div>
        ) : allBundlesDone ? (
          <div className="text-center py-6 space-y-3">
            <Check className="h-8 w-8 mx-auto text-[var(--pm-green)]" />
            <p className="pm-meta">All models are downloaded and ready.</p>
            <Button variant="default" onClick={() => { onComplete(); onOpenChange(false) }}>
              Done
            </Button>
          </div>
        ) : (
          <>
            <div className="pm-settings-dlg-scroll">
              <div className="pm-dialog-body pm-settings-dlg-body">
                <section className="pm-settings-dlg-card">
                  <span className="pm-settings-dlg-card-kicker">Authentication</span>
                  <div className="pm-settings-dlg-field">
                    <FieldLabel>HuggingFace token</FieldLabel>
                    <p className="pm-settings-dlg-card-hint mb-1.5">
                      Optional. Some models need a token from huggingface.co/settings/tokens
                    </p>
                    <div className="pm-settings-dlg-secret">
                      <Input
                        type={showToken ? "text" : "password"}
                        value={hfToken}
                        onChange={(e) => setHfToken(e.target.value)}
                        placeholder="hf_xxxxx"
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        className="pm-settings-dlg-secret-btn"
                        onClick={() => setShowToken(!showToken)}
                      >
                        {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </Button>
                    </div>
                  </div>
                </section>

                <section className="pm-settings-dlg-card">
                  <span className="pm-settings-dlg-card-kicker">Bundles</span>
                  <div className="space-y-2">
                    {bundleStates.map(({ bundle, memberModels, allDone, anyDownloading, anyError, totalSize }) => (
                      <div
                        key={bundle.id}
                        className={cn(
                          "rounded-[var(--pm-r-sm)] p-3 transition-colors",
                          allDone
                            ? "bg-[var(--pm-green-wash)]"
                            : selectedBundles.has(bundle.id)
                              ? "bg-[color-mix(in_srgb,var(--pm-green)_6%,#ffffff)]"
                              : "bg-[color-mix(in_srgb,var(--pm-ink)_2.5%,#ffffff)]",
                        )}
                      >
                        <div className="flex items-center gap-3">
                          {!allDone && (
                            <input
                              type="checkbox"
                              checked={selectedBundles.has(bundle.id)}
                              disabled={isDownloading}
                              onChange={() => toggleBundle(bundle.id)}
                              className="pm-settings-check"
                              aria-label={`Select ${bundle.label}`}
                            />
                          )}
                          {allDone && <Check className="h-4 w-4 text-[var(--pm-green)] shrink-0" />}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="pm-title">{bundle.label}</span>
                              {anyDownloading && (
                                <Loader2 className="h-4 w-4 animate-spin text-[var(--pm-muted)]" />
                              )}
                              {anyError && (
                                <AlertCircle className="h-4 w-4 text-[var(--pm-danger)]" />
                              )}
                            </div>
                            <p className="pm-meta">
                              {totalSize >= 1000
                                ? `${(totalSize / 1000).toFixed(1)}GB`
                                : `${totalSize}MB`}{" "}
                              · {bundle.description}
                            </p>
                          </div>
                        </div>
                        <div className="mt-2 ml-7 space-y-0.5">
                          {memberModels.map((m) => (
                            <div key={m.id} className="flex items-center gap-2 pm-meta">
                              <span className="truncate">{m.display_name}</span>
                              <span>·</span>
                              <span className="shrink-0">{m.size_mb}MB</span>
                              {m.downloaded && (
                                <Check className="h-3 w-3 text-[var(--pm-green)] shrink-0" />
                              )}
                              {m.status === "downloading" && (
                                <Loader2 className="h-3 w-3 animate-spin shrink-0" />
                              )}
                              {m.status === "error" && (
                                <span className="text-[var(--pm-danger)] shrink-0">{m.message}</span>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                  {hasError && (
                    <div className="pm-meta text-[var(--pm-danger)] p-2 rounded-[var(--pm-r-sm)] bg-[color-mix(in_srgb,var(--pm-danger)_10%,#ffffff)]">
                      {models
                        .filter((m) => m.status === "error")
                        .map((m) => (
                          <p key={m.id}>
                            {m.display_name}: {m.message}
                          </p>
                        ))}
                    </div>
                  )}
                </section>
              </div>
            </div>
            <DialogFooter>
              {!isDownloading && (
                <Button variant="ghost" onClick={() => { onComplete(); onOpenChange(false) }}>
                  Skip
                </Button>
              )}
              <Button
                variant="default"
                onClick={handleDownload}
                disabled={selectedModelIds.size === 0 || isDownloading}
              >
                {isDownloading ? (
                  <><Loader2 className="h-4 w-4 animate-spin" />Downloading…</>
                ) : (
                  <><Download className="h-4 w-4" />Download ({selectedBundles.size})</>
                )}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
