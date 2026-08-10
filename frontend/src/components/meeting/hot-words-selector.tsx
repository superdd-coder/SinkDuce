import { useState, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { BookOpen, AlertTriangle, Ban } from "lucide-react"
import { cn } from "@/lib/utils"
import {
  getHotWordsLibraries,
  type HotWordsLibrarySummary,
} from "@/api/client"
import { toast } from "sonner"

interface Props {
  meetingId: string
  currentLibraryId: string | null | undefined
  hasTranscript: boolean
  providerSupportsHotWords: boolean
  onSelectLibrary: (libraryId: string | null) => void
  onRetranscribe: () => void
}

export function HotWordsSelector({
  meetingId,
  currentLibraryId,
  hasTranscript,
  providerSupportsHotWords,
  onSelectLibrary,
  onRetranscribe,
}: Props) {
  const [open, setOpen] = useState(false)
  const [libraries, setLibraries] = useState<HotWordsLibrarySummary[]>([])
  const [pendingLibraryId, setPendingLibraryId] = useState<string | null>(null)
  const [retranscribeConfirmOpen, setRetranscribeConfirmOpen] = useState(false)
  const [pendingChangeId, setPendingChangeId] = useState<string | null>(null)

  const fetchLibraries = useCallback(async () => {
    try {
      setLibraries(await getHotWordsLibraries())
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    if (open) fetchLibraries()
  }, [open, fetchLibraries])

  useEffect(() => {
    setPendingLibraryId(null)
  }, [meetingId])

  const currentLib = libraries.find((l) => l.id === currentLibraryId)
  const pendingLib = libraries.find((l) => l.id === pendingLibraryId)
  const displayLib = pendingLib || currentLib
  const isPending =
    pendingLibraryId !== null && pendingLibraryId !== currentLibraryId
  const activeId =
    pendingLibraryId !== null ? pendingLibraryId : (currentLibraryId ?? null)
  const noneSelected = activeId == null || activeId === ""

  const handleSelect = (libraryId: string | null) => {
    if (!providerSupportsHotWords && libraryId !== null) {
      toast.warning(
        "Current transcription model does not support hot words. Hot words will NOT be applied. Consider switching to None or changing the transcription model.",
        { duration: 6000 },
      )
    }

    if (hasTranscript && libraryId !== currentLibraryId) {
      setPendingChangeId(libraryId)
      setRetranscribeConfirmOpen(true)
      return
    }

    setPendingLibraryId(libraryId)
    onSelectLibrary(libraryId)
    setOpen(false)
  }

  const handleConfirmRetranscribe = () => {
    const libraryId = pendingChangeId
    setRetranscribeConfirmOpen(false)
    setPendingChangeId(null)
    if (libraryId !== null && libraryId !== undefined) {
      onSelectLibrary(libraryId)
    } else if (libraryId === null) {
      onSelectLibrary(null)
    }
    onRetranscribe()
    setOpen(false)
  }

  const handleCancelRetranscribe = () => {
    setRetranscribeConfirmOpen(false)
    setPendingChangeId(null)
  }

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className={cn(
          "flex items-center gap-1.5",
          displayLib &&
            "border-[color-mix(in_srgb,var(--pm-green)_28%,transparent)] text-[var(--pm-green)]",
        )}
        onClick={() => setOpen(true)}
      >
        <BookOpen className="h-3.5 w-3.5" />
        <span className="max-w-[120px] truncate">
          {displayLib ? displayLib.name : "Hot Words"}
        </span>
        {!providerSupportsHotWords && displayLib && (
          <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
        )}
        {isPending && (
          <span className="text-[10px] text-amber-700 px-1.5 py-0.5 rounded-full bg-amber-50">
            pending
          </span>
        )}
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="pm-dialog sm:max-w-[360px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <BookOpen className="h-4 w-4 text-[var(--pm-green)]" />
              Hot Words Library
            </DialogTitle>
          </DialogHeader>
          <p className="pm-meta -mt-1">
            Boost domain terms during transcription. Pick one library or None.
          </p>

          {!providerSupportsHotWords && (
            <div className="pm-hw-warn" role="status">
              <AlertTriangle className="size-3.5 shrink-0 mt-0.5" />
              <span>
                Active model does not support hot words — selection will not be applied.
              </span>
            </div>
          )}

          <div className="pm-hw-list" role="listbox" aria-label="Hot word libraries">
            <button
              type="button"
              role="option"
              aria-selected={noneSelected}
              className={cn("pm-hw-option", noneSelected ? "is-on" : "is-off")}
              onClick={() => handleSelect(null)}
            >
              <span className="pm-hw-option-icon" aria-hidden>
                <Ban className="size-3.5" />
              </span>
              <span className="pm-hw-option-body">
                <span className="pm-hw-option-name">None</span>
                <span className="pm-hw-option-meta">No vocabulary boost</span>
              </span>
            </button>

            {libraries.map((lib) => {
              const isSelected = lib.id === activeId
              return (
                <button
                  key={lib.id}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  className={cn("pm-hw-option", isSelected ? "is-on" : "is-off")}
                  onClick={() => handleSelect(lib.id)}
                >
                  <span className="pm-hw-option-icon" aria-hidden>
                    <BookOpen className="size-3.5" />
                  </span>
                  <span className="pm-hw-option-body">
                    <span className="pm-hw-option-name">{lib.name}</span>
                    <span className="pm-hw-option-meta">
                      {lib.word_count} word{lib.word_count === 1 ? "" : "s"}
                    </span>
                  </span>
                </button>
              )
            })}

            {libraries.length === 0 && (
              <p className="pm-hw-empty">
                No libraries yet. Create one in Settings → Hot Words.
              </p>
            )}
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={retranscribeConfirmOpen} onOpenChange={setRetranscribeConfirmOpen}>
        <DialogContent className="pm-dialog sm:max-w-[320px]">
          <DialogHeader>
            <DialogTitle>Re-transcribe required</DialogTitle>
          </DialogHeader>
          <p className="pm-meta">
            This meeting already has a transcript. Changing hot words needs a re-transcription.
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" size="sm" onClick={handleCancelRetranscribe}>
              Cancel
            </Button>
            <Button type="button" variant="default" size="sm" onClick={handleConfirmRetranscribe}>
              Re-transcribe
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
