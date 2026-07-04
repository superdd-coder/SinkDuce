import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Copy, Check, ExternalLink } from "lucide-react"
import type { UpdateInfo } from "@/hooks/use-update-check"

const UPDATE_COMMAND = "git pull && \\\ndocker compose up -d --build"

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
      // Fallback for insecure contexts
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
      <DialogContent className="max-w-md max-h-[85vh] overflow-hidden flex flex-col">
        <DialogHeader className="shrink-0">
          <DialogTitle
            className="text-base font-light tracking-[0.15em] uppercase t-sans-family"
          >
            Update Available
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3 overflow-y-auto flex-1 min-h-0">
          {/* Version summary */}
          <div className="flex items-baseline gap-2 text-sm">
            <span
              className="text-[10px] uppercase tracking-[0.12em] font-light text-muted-foreground t-sans-family"
            >
              Current
            </span>
            <span
              className="font-light text-muted-foreground text-[13px] t-sans-family"
            >
              {update.currentVersion}
            </span>
            <span className="text-muted-foreground/60 mx-1">→</span>
            <span
              className="text-[13px] font-light t-sans-family"
              style={{ color: "var(--ze-ink)" }}
            >
              {update.latestVersion}
            </span>
          </div>

          {/* Release notes */}
          {releaseNotes && (
            <div>
              <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground/60 mb-1.5">
                What&rsquo;s New
              </p>
              <div
                className="text-[12px] leading-relaxed text-muted-foreground whitespace-pre-line border border-border/60 rounded-md p-2.5 max-h-32 overflow-y-auto t-sans-family"
              >
                {releaseNotes}
              </div>
            </div>
          )}

          {/* How to update */}
          <div>
            <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground/60 mb-1.5">
              How to Update
            </p>
            <p className="text-[12px] text-muted-foreground leading-relaxed mb-1.5">
              On the server where SinkDuce is deployed, <code className="text-[11px] bg-muted/60 px-1 rounded">cd</code> into the project directory first:
            </p>
            <div className="relative">
              <pre
                className="text-[12px] p-2.5 pr-24 rounded-md border border-border/60 bg-muted/40 overflow-x-auto whitespace-pre-wrap break-all t-mono-family"
              >
                {UPDATE_COMMAND}
              </pre>
              <Button
                variant="ghost"
                size="sm"
                className="absolute right-1 top-0.5 h-7"
                onClick={handleCopy}
              >
                {copied ? (
                  <><Check className="h-3 w-3 mr-1 text-emerald-600" /><span className="text-[10px] uppercase tracking-[0.08em] text-emerald-600">Copied</span></>
                ) : (
                  <><Copy className="h-3 w-3 mr-1" /><span className="text-[10px] uppercase tracking-[0.08em]">Copy</span></>
                )}
              </Button>
            </div>
          </div>

          {/* External link to release */}
          <div className="flex justify-between items-center">
            <Button
              variant="link"
              size="sm"
              className="h-auto px-0 text-[11px] text-muted-foreground hover:text-primary gap-1 font-light"
              onClick={() => window.open(update.releaseUrl, "_blank")}
            >
              View on GitHub <ExternalLink className="h-3 w-3" />
            </Button>
            <Button variant="outline" size="sm" onClick={() => onOpenChange(false)} className="font-light uppercase text-[10px] tracking-[0.08em] h-7">
              Close
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
