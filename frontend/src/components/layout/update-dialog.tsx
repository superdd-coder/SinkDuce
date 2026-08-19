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
import { cn } from "@/lib/utils"

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
          <DialogKicker>Release</DialogKicker>
          <DialogTitle>Update available</DialogTitle>
        </DialogHeader>

        <div className="pm-update-dialog-body min-h-0 flex-1 overflow-y-auto">
          {/* Version path */}
          <div className="pm-update-ver-row" aria-label="Version change">
            <span className="pm-update-ver-chip is-muted">
              <span className="pm-update-ver-chip-label">Current</span>
              <span className="pm-update-ver-chip-val">
                v{update.currentVersion.replace(/^v/, "")}
              </span>
            </span>
            <ArrowRight className="pm-update-ver-arrow" strokeWidth={1.75} aria-hidden />
            <span className="pm-update-ver-chip is-new">
              <span className="pm-update-ver-chip-label">Latest</span>
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
              aria-label="Release notes"
            >
              <h3 className="pm-update-card-label">What&rsquo;s new</h3>
              <div className="pm-update-notes">{releaseNotes}</div>
            </section>
          ) : null}

          {update.desktop ? (
            <section className="pm-update-card" aria-label="How to update">
              <h3 className="pm-update-card-label">How to update</h3>
              <p className="pm-update-help">
                Download the disk image, open it, and drag SinkDuce to
                Applications. Quit the running app with{" "}
                <code className="pm-update-inline-code">Cmd+Q</code> before
                replacing it — the red window button only hides to the menu bar.
                If macOS blocks the app, right-click → Open.
              </p>
              {!update.downloadUrl ? (
                <p className="pm-update-help" style={{ marginTop: "0.65rem" }}>
                  The installer is not on this GitHub Release yet. Use View on
                  GitHub, or wait for the desktop package to be uploaded.
                </p>
              ) : null}
            </section>
          ) : (
            <section className="pm-update-card" aria-label="How to update">
              <div className="pm-update-card-head-row">
                <h3 className="pm-update-card-label">How to update</h3>
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
                      Copied
                    </>
                  ) : (
                    <>
                      <Copy className="h-3.5 w-3.5" strokeWidth={1.75} />
                      Copy
                    </>
                  )}
                </Button>
              </div>
              <p className="pm-update-help">
                On the server,{" "}
                <code className="pm-update-inline-code">cd</code> into the project
                directory (where{" "}
                <code className="pm-update-inline-code">docker-compose.yml</code>{" "}
                lives), then run:
              </p>
              <div className="pm-update-cmd">
                <pre className="pm-update-cmd-pre">{UPDATE_COMMAND}</pre>
              </div>
              <p className="pm-update-help" style={{ marginTop: "0.65rem" }}>
                Building from source instead? Use{" "}
                <code className="pm-update-inline-code">
                  docker compose -f docker-compose.build.yml up -d --build
                </code>
                .
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
            View on GitHub
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
                  Close
                </Button>
                <Button
                  type="button"
                  size="sm"
                  className="pm-update-foot-close"
                  disabled={!update.downloadUrl}
                  onClick={() => {
                    if (!update.downloadUrl) return
                    window.open(update.downloadUrl, "_blank", "noopener,noreferrer")
                  }}
                >
                  <Download className="h-3.5 w-3.5" strokeWidth={1.75} />
                  {update.downloadUrl
                    ? `Download ${
                        update.latestVersion.startsWith("v")
                          ? update.latestVersion
                          : `v${update.latestVersion}`
                      }`
                    : "Installer not on this release yet"}
                </Button>
              </>
            ) : (
              <Button
                type="button"
                size="sm"
                className="pm-update-foot-close"
                onClick={() => onOpenChange(false)}
              >
                Close
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
