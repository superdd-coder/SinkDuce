import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Loader2, ArrowRight, AlertTriangle } from "lucide-react"
import { type PropagationPreview } from "@/api/client"

interface PropagationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  preview: PropagationPreview | null
  onConfirm: () => void
  propagating: boolean
}

export function PropagationDialog({
  open,
  onOpenChange,
  preview,
  onConfirm,
  propagating,
}: PropagationDialogProps) {
  if (!preview || preview.total_affected === 0) return null

  // Build chain display: deduplicate by target ID
  // e.g., A → B → C
  const seenIds = new Set<string>([preview.origin_id])
  const chain: { id: string; title: string }[] = [{ id: preview.origin_id, title: preview.origin_title }]
  const uniqueLinks: typeof preview.links = []
  for (const link of preview.links) {
    if (!seenIds.has(link.target_id)) {
      seenIds.add(link.target_id)
      chain.push({ id: link.target_id, title: link.target_title })
      uniqueLinks.push(link)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="pm-dialog max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-[var(--pm-green)]" />
            Propagate Changes?
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-4 pt-1">
          <p className="pm-dialog-body">
            This note has been distilled into other notes. Content changes will trigger
            re-distillation for the following chain:
          </p>

          {/* Chain visualization */}
          <div className="pm-nested-pane p-3">
            <div className="flex items-center flex-wrap gap-1.5">
              {chain.map((item, i) => (
                <span key={item.id} className="flex items-center gap-1.5">
                  {i > 0 && (
                    <ArrowRight className="h-3 w-3 text-[var(--pm-faint)]" />
                  )}
                  <span
                    className={
                      i === 0
                        ? "pm-title text-[var(--pm-green)]"
                        : "pm-title"
                    }
                  >
                    {item.title}
                  </span>
                </span>
              ))}
            </div>
          </div>

          {/* Detailed links */}
          <div className="space-y-1.5">
            {uniqueLinks.map((link) => (
              <div
                key={link.target_id}
                className="flex items-center gap-2 pm-meta px-2"
              >
                <span className="font-medium text-[var(--pm-ink)]">
                  {link.source_title}
                </span>
                <ArrowRight className="h-3 w-3 text-[var(--pm-faint)]" />
                <span className="font-medium text-[var(--pm-ink)]">
                  {link.target_title}
                </span>
              </div>
            ))}
          </div>

          <p className="pm-meta">
            Downstream propagations (indirect dependencies) will run automatically
            without additional prompts.
          </p>

          <div className="flex justify-end gap-2 pt-1">
            <Button
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={propagating}
            >
              Skip
            </Button>
            <Button
              onClick={onConfirm}
              disabled={propagating}
            >
              {propagating ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
                  Propagating...
                </>
              ) : (
                "Propagate Changes"
              )}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
