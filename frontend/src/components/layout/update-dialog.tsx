import { useCallback, useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { Copy, Check, ArrowRight, Download, RefreshCw } from "lucide-react"
import type { UpdateInfo } from "@/hooks/use-update-check"
import { openDesktopExternalUrl } from "@/api/client"
import { desktopDmgAssetName } from "@/lib/update-release"
import {
  beginDesktopUpdate,
  desktopUpdateStatus,
  desktopUpdaterAvailable,
  restartDesktopAfterUpdate,
  type DesktopUpdateStatus,
} from "@/lib/desktop-updater"
import { cn } from "@/lib/utils"
import { copyText } from "@/lib/clipboard"
import { useT } from "@/i18n/use-t"

/** Default path: pull the Hub image (compose pins :latest). External Docker
 * users have no git repo — the private one 404s — so no git step here.
 * Source builds use docker-compose.build.yml (mentioned below the card). */
const UPDATE_COMMAND = "docker compose pull && \\\ndocker compose up -d"

interface UpdateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  update: UpdateInfo
}

/** Parse markdown-ish release body into plain text highlights. */
function formatReleaseBody(body: string): string {
  return body
    .replace(/^###?\s+/gm, "— ")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/^[-*]\s/gm, "  · ")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
    .slice(0, 2000)
}

export function UpdateDialog({ open, onOpenChange, update }: UpdateDialogProps) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  const [updaterStatus, setUpdaterStatus] = useState<DesktopUpdateStatus | null>(null)
  const [restarting, setRestarting] = useState(false)
  const pollRef = useRef<number | null>(null)

  const updaterReady = update.desktop && desktopUpdaterAvailable()

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const startPolling = useCallback(() => {
    stopPolling()
    pollRef.current = window.setInterval(async () => {
      const status = await desktopUpdateStatus()
      if (!status) return
      setUpdaterStatus(status)
      if (status.phase === "ready") {
        stopPolling()
        setRestarting(true)
        // Exits the app: sidecars die, instance lock released, new binary spawns.
        void restartDesktopAfterUpdate().catch(() => {})
      } else if (status.phase === "failed" || status.phase === "none_available") {
        stopPolling()
      }
    }, 400)
  }, [stopPolling])

  // Resume if the shell is mid-flight (dialog reopened while it downloads).
  useEffect(() => {
    if (!updaterReady) return
    let cancelled = false
    void desktopUpdateStatus().then((status) => {
      if (cancelled || !status) return
      if (
        status.phase === "checking" ||
        status.phase === "downloading" ||
        status.phase === "installing"
      ) {
        setUpdaterStatus(status)
        startPolling()
      } else if (status.phase === "failed" || status.phase === "none_available") {
        setUpdaterStatus(status)
      }
    })
    return () => {
      cancelled = true
      stopPolling()
    }
  }, [updaterReady, startPolling, stopPolling])

  const handleUpdateNow = useCallback(async () => {
    setUpdaterStatus({ phase: "checking" })
    try {
      await beginDesktopUpdate()
    } catch (err) {
      // Surface the shell's real rejection reason (ACL, missing command, …)
      // instead of a blanket "unsupported shell" claim.
      setUpdaterStatus({
        phase: "failed",
        message: String(err).slice(0, 300) || "invoke rejected",
      })
      return
    }
    startPolling()
  }, [startPolling])

  const handleCopy = async () => {
    try {
      await copyText(UPDATE_COMMAND)
      setCopied(true)
      setTimeout(() => setCopied(false), 2500)
    } catch {
      // 远端普通 HTTP 源连 execCommand 兜底也可能被拒——至少如实提示
    }
  }

  const releaseNotes = formatReleaseBody(update.releaseBody)

  const updaterBusy =
    restarting ||
    (updaterStatus !== null &&
      (updaterStatus.phase === "checking" ||
        updaterStatus.phase === "downloading" ||
        updaterStatus.phase === "installing" ||
        updaterStatus.phase === "ready"))

  let updateLabel = t("update.updateNow")
  let updatePercent: number | null = null
  let updatePercentUnknown = false
  if (restarting) {
    updateLabel = t("update.updateRestarting")
  } else if (updaterStatus?.phase === "checking") {
    updateLabel = t("update.updateChecking")
  } else if (updaterStatus?.phase === "downloading") {
    updateLabel = t("update.updateDownloading")
    if (updaterStatus.total) {
      updatePercent = Math.min(
        100,
        Math.round(((updaterStatus.received ?? 0) / updaterStatus.total) * 100),
      )
    } else {
      updatePercentUnknown = true
    }
  } else if (updaterStatus?.phase === "installing" || updaterStatus?.phase === "ready") {
    updateLabel = t("update.updateInstalling")
  }

  const updaterFailed = updaterStatus?.phase === "failed"
  const updaterNoneAvailable = updaterStatus?.phase === "none_available"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "pm-dialog pm-dialog--silk pm-update-dialog",
          "sm:max-w-lg min-h-[min(70vh,32rem)] max-h-[min(90vh,44rem)]",
          "flex flex-col gap-0 overflow-hidden p-0",
          "!animate-none data-open:!animate-none data-closed:!animate-none",
        )}
        overlayClassName="pm-dialog-overlay--silk"
      >
        <DialogHeader className="pm-update-dialog-head shrink-0">
          <DialogKicker>{t("update.release")}</DialogKicker>
          <DialogTitle>{t("update.updateAvailable")}</DialogTitle>
        </DialogHeader>

        <div className="pm-update-dialog-body min-h-0 flex-1 overflow-y-auto">
          {/* Version path */}
          <div className="pm-update-ver-row" aria-label={t("update.versionChange")}>
            <span className="pm-update-ver-chip is-muted">
              <span className="pm-update-ver-chip-label">{t("update.current")}</span>
              <span className="pm-update-ver-chip-val">
                v{update.currentVersion.replace(/^v/, "")}
              </span>
            </span>
            <ArrowRight className="pm-update-ver-arrow" strokeWidth={1.75} aria-hidden />
            <span className="pm-update-ver-chip is-new">
              <span className="pm-update-ver-chip-label">{t("update.latest")}</span>
              <span className="pm-update-ver-chip-val">
                {update.latestVersion.startsWith("v")
                  ? update.latestVersion
                  : `v${update.latestVersion}`}
              </span>
            </span>
          </div>

          {releaseNotes ? (
            <section
              className="pm-update-card pm-update-card--notes"
              aria-label={t("update.releaseNotes")}
            >
              <h3 className="pm-update-card-label">{t("update.whatsNew")}</h3>
              <div className="pm-update-notes">{releaseNotes}</div>
            </section>
          ) : null}

          {update.desktop ? (
            <section className="pm-update-card" aria-label={t("update.howToUpdate")}>
              <h3 className="pm-update-card-label">{t("update.howToUpdate")}</h3>
              <p className="pm-update-help">
                {t("update.desktopHelp", {
                  file: desktopDmgAssetName(update.latestVersion),
                })}
              </p>
            </section>
          ) : (
            <section className="pm-update-card" aria-label={t("update.howToUpdate")}>
              <div className="pm-update-card-head-row">
                <h3 className="pm-update-card-label">{t("update.howToUpdate")}</h3>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className={cn(
                    "pm-update-cmd-copy",
                    copied && "is-copied",
                  )}
                  onClick={() => { void handleCopy() }}
                >
                  {copied ? (
                    <>
                      <Check className="h-3.5 w-3.5" strokeWidth={2} />
                      {t("common.copied")}
                    </>
                  ) : (
                    <>
                      <Copy className="h-3.5 w-3.5" strokeWidth={1.75} />
                      {t("common.copy")}
                    </>
                  )}
                </Button>
              </div>
              <p className="pm-update-help">
                {t("update.dockerHelp")}
              </p>
              <div className="pm-update-cmd">
                <pre className="pm-update-cmd-pre">{UPDATE_COMMAND}</pre>
              </div>
              <p className="pm-update-help" style={{ marginTop: "0.65rem" }}>
                {t("update.buildFromSource", {
                  command:
                    "docker compose -f docker-compose.build.yml up -d --build",
                })}
              </p>
            </section>
          )}
        </div>

        {updaterStatus?.phase === "downloading" ? (
          <div className="pm-update-progress" aria-hidden>
            <div
              className={cn("pm-update-progress-fill", updatePercentUnknown && "is-unknown")}
              style={updatePercentUnknown ? undefined : { width: `${updatePercent ?? 0}%` }}
            />
          </div>
        ) : null}

        {/* Plain footer — avoid DialogFooter flex-col-reverse / justify-end defaults */}
        <div className="pm-update-dialog-foot shrink-0">
          {updaterFailed ? (
            <p className="pm-update-foot-error">
              {t("update.updateFailed")}
              {updaterStatus?.message && updaterStatus.message !== "unsupported-shell" ? (
                <span>（{updaterStatus.message}）</span>
              ) : null}
            </p>
          ) : updaterNoneAvailable ? (
            <p className="pm-update-foot-note">{t("update.alreadyLatest")}</p>
          ) : null}
          <div className="pm-update-foot-actions">
            {update.desktop ? (
              <>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="pm-update-foot-link"
                  onClick={() => onOpenChange(false)}
                >
                  {t("common.close")}
                </Button>
                {updaterReady ? (
                  <>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="pm-update-foot-link"
                      disabled={!update.downloadUrl}
                      onClick={() => {
                        if (!update.downloadUrl) return
                        void openDesktopExternalUrl(update.downloadUrl).catch(() => {
                          window.open(update.downloadUrl!, "_blank", "noopener,noreferrer")
                        })
                      }}
                    >
                      <Download className="h-3.5 w-3.5" strokeWidth={1.75} />
                      {update.downloadUrl
                        ? t("update.downloadVersion", {
                            version: update.latestVersion.startsWith("v")
                              ? update.latestVersion
                              : `v${update.latestVersion}`,
                          })
                        : t("update.installerNotReady")}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      className="pm-update-foot-close"
                      disabled={updaterBusy}
                      onClick={() => {
                        void handleUpdateNow()
                      }}
                    >
                      <RefreshCw
                        className={cn("h-3.5 w-3.5", updaterBusy && "animate-spin")}
                        strokeWidth={1.75}
                      />
                      {updateLabel}
                    </Button>
                  </>
                ) : (
                  <Button
                    type="button"
                    size="sm"
                    className="pm-update-foot-close"
                    disabled={!update.downloadUrl}
                    onClick={() => {
                      if (!update.downloadUrl) return
                      void openDesktopExternalUrl(update.downloadUrl).catch(() => {
                        window.open(update.downloadUrl!, "_blank", "noopener,noreferrer")
                      })
                    }}
                  >
                    <Download className="h-3.5 w-3.5" strokeWidth={1.75} />
                    {update.downloadUrl
                      ? t("update.downloadVersion", {
                          version: update.latestVersion.startsWith("v")
                            ? update.latestVersion
                            : `v${update.latestVersion}`,
                        })
                      : t("update.installerNotReady")}
                  </Button>
                )}
              </>
            ) : (
              <Button
                type="button"
                size="sm"
                className="pm-update-foot-close"
                onClick={() => onOpenChange(false)}
              >
                {t("common.close")}
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
