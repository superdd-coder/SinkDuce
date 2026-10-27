import { HelpHint } from "@/components/shared/help-hint"
/**
 * Definitive suggestions dialog — accumulated post-upload judgments.
 *
 * House premium recipe (create-collection-dialog language): green kicker →
 * serif display title → muted description; system × closes (no footer).
 * Each row: star chip, file name (opens a nested shared file-detail dialog),
 * one-sentence reason, Apply / Dismiss.
 * Any decision removes the row; decided files re-appear only when their
 * content changes (backend ledger binds decisions to the content hash).
 */

import { useState } from "react"
import { Check, Loader2, Star, X } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  applyDefinitiveSuggestion,
  dismissAllDefinitiveSuggestions,
  dismissDefinitiveSuggestion,
} from "@/api/file-mgmt"
import type { DefinitiveSuggestion } from "@/types/file-mgmt"
import { FileMgmtDetailDialog } from "@/components/file-mgmt/file-detail"
import { useT } from "@/i18n/use-t"
import { formatApiError } from "@/api/http"

export function DefinitiveSuggestionsDialog({
  collection,
  open,
  onOpenChange,
  suggestions,
  onDecided,
  onDismissAll,
}: {
  collection: string
  open: boolean
  onOpenChange: (open: boolean) => void
  suggestions: DefinitiveSuggestion[]
  onDecided: (fileId: string) => void
  onDismissAll: () => void
}) {
  const t = useT()
  const [detailFileId, setDetailFileId] = useState<string | null>(null)
  /** file_id currently running Apply/Dismiss (row shows its own spinner). */
  const [busyId, setBusyId] = useState<string | null>(null)
  const [busyAll, setBusyAll] = useState(false)

  const handleApply = async (fileId: string) => {
    setBusyId(fileId)
    try {
      await applyDefinitiveSuggestion(collection, fileId)
      toast.success(t("library.suggestAppliedToast"))
      onDecided(fileId)
    } catch (err: unknown) {
      toast.error(t("common.failedWithError", { error: formatApiError(err, t) }))
    } finally {
      setBusyId(null)
    }
  }

  const handleDismiss = async (fileId: string) => {
    setBusyId(fileId)
    try {
      await dismissDefinitiveSuggestion(collection, fileId)
      toast.info(t("library.suggestDismissedToast"))
      onDecided(fileId)
    } catch (err: unknown) {
      toast.error(t("common.failedWithError", { error: formatApiError(err, t) }))
    } finally {
      setBusyId(null)
    }
  }

  const handleDismissAll = async () => {
    setBusyAll(true)
    try {
      const res = await dismissAllDefinitiveSuggestions(collection)
      toast.info(
        t("library.suggestDismissedAllToast", { n: res.dismissed }),
      )
      onDismissAll()
    } catch (err: unknown) {
      toast.error(t("common.failedWithError", { error: formatApiError(err, t) }))
    } finally {
      setBusyAll(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) setDetailFileId(null)
        onOpenChange(nextOpen)
      }}
    >
      <DialogContent className="pm-dialog pm-suggest-dialog">
        <DialogHeader className="shrink-0">
          <DialogKicker>{t("library.definitive")}</DialogKicker>
          <DialogTitle>{t("library.suggestTitle")}<HelpHint label={t("library.suggestTitle")}>{t("library.suggestBody")}</HelpHint></DialogTitle>
        </DialogHeader>

        {suggestions.length === 0 ? (
          <div className="pm-suggest-empty">
            <span className="pm-suggest-empty-badge" aria-hidden>
              <Star className="h-4.5 w-4.5" />
            </span>
            <p className="pm-suggest-empty-title">
              {t("library.suggestEmptyTitle")}
            </p>
            <p className="pm-suggest-empty-hint">{t("library.suggestEmpty")}</p>
          </div>
        ) : (
          <div className="pm-suggest-list">
            <div className="pm-suggest-list-toolbar">
              <Button
                type="button"
                variant="ghost"
                size="xs"
                disabled={busyAll}
                onClick={() => void handleDismissAll()}
              >
                {busyAll ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <X className="h-3 w-3" />
                )}
                {t("library.suggestDismissAll")}
              </Button>
            </div>
            {suggestions.map((s) => (
              <div
                key={s.suggestion_id}
                className="pm-suggest-card"
                role="button"
                tabIndex={0}
                title={t("library.suggestOpenDetail")}
                onClick={() => setDetailFileId(s.file_id)}
                onKeyDown={(e) => {
                  if (e.target !== e.currentTarget) return
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    setDetailFileId(s.file_id)
                  }
                }}
              >
                <span className="pm-suggest-row-star" aria-hidden>
                  <Star className="h-3 w-3" />
                </span>
                <div className="pm-suggest-card-body">
                  <span className="pm-suggest-card-name">
                    {s.display_name || s.filename || s.file_id.slice(0, 8)}
                  </span>
                  {s.reason && (
                    <p className="pm-suggest-card-reason">{s.reason}</p>
                  )}
                </div>
                <div
                  className="pm-suggest-card-actions"
                  onClick={(e) => e.stopPropagation()}
                >
                  <Button
                    type="button"
                    variant="default"
                    size="xs"
                    disabled={busyAll || busyId === s.file_id}
                    onClick={() => void handleApply(s.file_id)}
                  >
                    {busyId === s.file_id ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Check className="h-3 w-3" />
                    )}
                    {t("library.suggestApply")}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    disabled={busyAll || busyId === s.file_id}
                    onClick={() => void handleDismiss(s.file_id)}
                  >
                    <X className="h-3 w-3" />
                    {t("library.suggestDismiss")}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </DialogContent>

      {/* Keep the detail in this Dialog's context so focus and dismissal stack. */}
      {detailFileId && (
        <FileMgmtDetailDialog
          collectionId={collection}
          fileId={detailFileId}
          open={open}
          onOpenChange={(nextOpen) => {
            if (!nextOpen) setDetailFileId(null)
          }}
          onDeleted={() => {
            onDecided(detailFileId)
            setDetailFileId((current) => current === detailFileId ? null : current)
          }}
        />
      )}
    </Dialog>
  )
}
