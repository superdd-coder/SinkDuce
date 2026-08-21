import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { Copy, Check, ExternalLink, ArrowRight, Download } from "lucide-react"
import type { UpdateInfo } from "@/hooks/use-update-check"
import { openDesktopExternalUrl } from "@/api/client"
import { desktopDmgAssetName } from "@/lib/update-release"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"

/** Default path: pull Hub image (docker-compose.yml). Source builds use docker-compose.build.yml. */
const UPDATE_COMMAND =
  "git pull && \\\ndocker compose pull && \\\ndocker compose up -d"

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

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(UPDATE_COMMAND)
      setCopied(true)
      setTimeout(() => setCopied(false), 2500)
    } catch {
      const ta = document.createElement("textarea")
      ta.value = UPDATE_COMMAND
      ta.style.position = "fixed"
      ta.style.opacity = "0"
      document.body.appendChild(ta)
      ta.select()
      document.execCommand("copy")
      document.body.removeChild(ta)
      setCopied(true)
      setTimeout(() => setCopied(false), 2500)
    }
  }

  const releaseNotes = formatReleaseBody(update.releaseBody)

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

        {/* Plain footer — avoid DialogFooter flex-col-reverse / justify-end defaults */}
        <div className="pm-update-dialog-foot shrink-0">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="pm-update-foot-link"
            onClick={() => window.open(update.releaseUrl, "_blank")}
          >
            {t("update.viewOnGithub")}
            <ExternalLink className="h-3.5 w-3.5" strokeWidth={1.75} />
          </Button>
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
