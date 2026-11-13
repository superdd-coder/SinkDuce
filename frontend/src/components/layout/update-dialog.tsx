import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ArrowRight, Check, ChevronRight, Copy, Download, RefreshCw } from "lucide-react"
import type { UpdateInfo } from "@/hooks/use-update-check"
import { openDesktopExternalUrl } from "@/api/client"
import { releaseNotesForLocale } from "@/lib/update-release"
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
import { useAppStore } from "@/stores/app-store"

const UPDATE_COMMAND = "docker compose pull && \\\ndocker compose up -d"
const BUILD_COMMAND = "docker compose -f docker-compose.build.yml up -d --build"
const UPDATE_ACTION_LABELS = [
  "update.updateNow",
  "update.updateChecking",
  "update.updateDownloading",
  "update.updateInstalling",
  "update.updateRestarting",
] as const

interface UpdateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  update: UpdateInfo
  onIgnore?: () => void
}

function versionLabel(version: string) {
  return version === "—" ? version : `v${version.replace(/^v/i, "")}`
}

function UpdateDisclosure({ label, children, open, onOpenChange }: {
  label: string
  children: ReactNode
  open?: boolean
  onOpenChange?: (open: boolean) => void
}) {
  const panelId = useId()
  const [expanded, setExpanded] = useState(false)
  const isOpen = open ?? expanded

  return (
    <div className="pm-update-details">
      <button
        type="button"
        className="pm-update-details-toggle"
        aria-expanded={isOpen}
        aria-controls={panelId}
        onClick={() => (onOpenChange ?? setExpanded)(!isOpen)}
      >
        <ChevronRight aria-hidden />{label}
      </button>
      <div id={panelId} className="pm-update-details-panel" inert={!isOpen} aria-hidden={!isOpen}>
        <div className="pm-update-details-inner">{children}</div>
      </div>
    </div>
  )
}

export function UpdateDialog({ open, onOpenChange, update, onIgnore }: UpdateDialogProps) {
  const t = useT()
  const locale = useAppStore(s => s.locale)
  const [copied, setCopied] = useState(false)
  const [updaterStatus, setUpdaterStatus] = useState<DesktopUpdateStatus | null>(null)
  const [manualInstallOpen, setManualInstallOpen] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const pollRef = useRef<number | null>(null)
  const title = useRef<HTMLHeadingElement>(null)

  const updaterReady = update.desktop && typeof window !== "undefined" && desktopUpdaterAvailable()

  const recordUpdaterStatus = useCallback((status: DesktopUpdateStatus) => {
    setUpdaterStatus(status)
    if (status.phase === "failed") setManualInstallOpen(true)
  }, [])

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
      recordUpdaterStatus(status)
      if (status.phase === "ready") {
        stopPolling()
        setRestarting(true)
        // Exits the app: sidecars die, instance lock released, new binary spawns.
        void restartDesktopAfterUpdate().catch(() => {})
      } else if (status.phase === "failed" || status.phase === "none_available") {
        stopPolling()
      }
    }, 400)
  }, [recordUpdaterStatus, stopPolling])

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
        recordUpdaterStatus(status)
        startPolling()
      } else if (status.phase === "failed" || status.phase === "none_available") {
        recordUpdaterStatus(status)
      }
    })
    return () => {
      cancelled = true
      stopPolling()
    }
  }, [updaterReady, recordUpdaterStatus, startPolling, stopPolling])

  const handleUpdateNow = useCallback(async () => {
    recordUpdaterStatus({ phase: "checking" })
    try {
      await beginDesktopUpdate()
    } catch (err) {
      // Surface the shell's real rejection reason (ACL, missing command, …)
      // instead of a blanket "unsupported shell" claim.
      recordUpdaterStatus({
        phase: "failed",
        message: String(err).slice(0, 300) || "invoke rejected",
      })
      return
    }
    startPolling()
  }, [recordUpdaterStatus, startPolling])

  const handleCopy = async () => {
    try {
      await copyText(UPDATE_COMMAND)
      setCopied(true)
      setTimeout(() => setCopied(false), 2500)
    } catch {
      // 远端普通 HTTP 源连 execCommand 兜底也可能被拒——至少如实提示
    }
  }

  function handleDownload() {
    const url = update.downloadUrl
    if (!url) return
    void openDesktopExternalUrl(url).catch(() => window.open(url, "_blank", "noopener,noreferrer"))
  }

  const releaseNotes = releaseNotesForLocale(update.releaseBody, locale)

  const updaterBusy =
    restarting ||
    (updaterStatus !== null &&
      (updaterStatus.phase === "checking" ||
        updaterStatus.phase === "downloading" ||
        updaterStatus.phase === "installing" ||
        updaterStatus.phase === "ready"))

  const downloading = updaterStatus?.phase === "downloading"
  let updateLabel: typeof UPDATE_ACTION_LABELS[number] = "update.updateNow"
  let updatePercent: number | null = null
  let updatePercentUnknown = false
  if (restarting) {
    updateLabel = "update.updateRestarting"
    updatePercent = 100
  } else if (updaterStatus?.phase === "checking") {
    updateLabel = "update.updateChecking"
  } else if (downloading) {
    updateLabel = "update.updateDownloading"
    if (updaterStatus.total) {
      updatePercent = Math.min(
        100,
        Math.round(((updaterStatus.received ?? 0) / updaterStatus.total) * 100),
      )
    } else {
      updatePercentUnknown = true
    }
  } else if (updaterStatus?.phase === "installing" || updaterStatus?.phase === "ready") {
    updateLabel = "update.updateInstalling"
    updatePercent = 100
  }

  const updaterFailed = updaterStatus?.phase === "failed"
  const updaterNoneAvailable = updaterStatus?.phase === "none_available"

  const notesList = (
    <ul className="pm-update-notes" lang={releaseNotes.language}>
      {releaseNotes.items.map((note, index) => <li key={index}>{note}</li>)}
    </ul>
  )
  const downloadButton = (
    <Button
      type="button"
      size="sm"
      variant={updaterReady ? "outline" : "default"}
      className={updaterReady ? undefined : "pm-update-primary"}
      disabled={!update.downloadUrl}
      onClick={handleDownload}
    >
      <Download aria-hidden />
      {update.downloadUrl ? t("update.downloadInstaller") : t("update.installerNotReady")}
    </Button>
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="pm-update-dialog" initialFocus={title}>
        <DialogHeader className="pm-update-dialog-head">
          <DialogTitle ref={title} tabIndex={-1}>{t("update.updateAvailable")}</DialogTitle>
        </DialogHeader>

        <div className="pm-update-dialog-body">
          <section className="pm-update-version-card" aria-label={t("update.versionChange")}>
            <div className="pm-update-installed">
              <span>{t("update.current")}</span>
              <strong>{versionLabel(update.currentVersion)}</strong>
            </div>
            <ArrowRight size={16} strokeWidth={1.5} aria-hidden />
            <div className="pm-update-version">
              <span>{t("update.latest")}</span>
              <strong>{versionLabel(update.latestVersion)}</strong>
            </div>
          </section>

          {releaseNotes.items.length > 0 && (releaseNotes.language === locale ? (
            <section className="pm-update-highlights" aria-label={t("update.releaseNotes")}>
              <h3>{t("update.whatsNew")}</h3>
              {notesList}
            </section>
          ) : (
            <UpdateDisclosure label={t("update.originalReleaseNotes")}>
              {notesList}
            </UpdateDisclosure>
          ))}

          {update.desktop ? (
            <UpdateDisclosure
              label={t("update.manualInstall")}
              open={manualInstallOpen}
              onOpenChange={setManualInstallOpen}
            >
              <div className="pm-update-details-content">
                <p>{t("update.desktopHelp")}</p>
                {updaterReady && downloadButton}
              </div>
            </UpdateDisclosure>
          ) : (
            <section className="pm-update-docker" aria-label={t("update.howToUpdate")}>
              <p>{t("update.dockerHelp")}</p>
              <pre className="pm-update-command"><code>{UPDATE_COMMAND}</code></pre>
              <UpdateDisclosure label={t("update.buildFromSource")}>
                <pre className="pm-update-command"><code>{BUILD_COMMAND}</code></pre>
              </UpdateDisclosure>
            </section>
          )}
        </div>

        <div className="pm-update-dialog-foot">
          <div
            className="pm-update-progress"
            role={downloading ? "progressbar" : undefined}
            aria-hidden={!downloading || undefined}
            aria-label={downloading ? t("update.updateDownloading") : undefined}
            aria-valuenow={downloading ? updatePercent ?? undefined : undefined}
          >
            <div
              className={cn("pm-update-progress-fill", updatePercentUnknown && "is-unknown")}
              style={updatePercentUnknown ? undefined : { width: `${updatePercent ?? 0}%` }}
            />
          </div>
          {updaterFailed && (
            <div className="pm-update-feedback is-error">
              <p role="alert">{t("update.updateFailed")}</p>
              {updaterStatus?.message && updaterStatus.message !== "unsupported-shell" && (
                <UpdateDisclosure label={t("update.errorDetails")}>
                  <p className="pm-update-error-text">{updaterStatus.message}</p>
                </UpdateDisclosure>
              )}
            </div>
          )}
          {updaterNoneAvailable && <p className="pm-update-feedback" role="status">{t("update.alreadyLatest")}</p>}

          <div className="pm-update-foot-actions">
            {onIgnore && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="pm-update-ignore"
                disabled={updaterBusy}
                onClick={() => {
                  onIgnore()
                  onOpenChange(false)
                }}
              >
                {t("shell.ignoreVersion")}
              </Button>
            )}
            {update.desktop ? (updaterReady ? (
              <Button type="button" size="sm" className="pm-update-primary" disabled={updaterBusy}
                onClick={() => { void handleUpdateNow() }}>
                <RefreshCw className={cn(updaterBusy && "animate-spin")} aria-hidden />
                <span className="pm-update-action-labels">
                  {UPDATE_ACTION_LABELS.map(label => (
                    <span key={label} aria-hidden={label !== updateLabel || undefined}>{t(label)}</span>
                  ))}
                </span>
              </Button>
            ) : downloadButton) : (
              <Button type="button" size="sm" className="pm-update-primary" onClick={() => { void handleCopy() }}>
                {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
                {copied ? t("common.copied") : t("update.copyCommand")}
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
