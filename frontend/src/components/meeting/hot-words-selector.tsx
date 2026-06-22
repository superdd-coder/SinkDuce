import { useState, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { BookOpen, AlertTriangle } from "lucide-react"
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

  // Reset pending state when meeting changes
  useEffect(() => {
    setPendingLibraryId(null)
  }, [meetingId])

  const currentLib = libraries.find((l) => l.id === currentLibraryId)
  const pendingLib = libraries.find((l) => l.id === pendingLibraryId)
  const displayLib = pendingLib || currentLib
  const isPending = pendingLibraryId !== null && pendingLibraryId !== currentLibraryId

  const handleSelect = (libraryId: string | null) => {
    if (!providerSupportsHotWords && libraryId !== null) {
      toast.warning(
        "Current transcription model does not support hot words. Hot words will NOT be applied. Consider switching to None or changing the transcription model.",
        { duration: 6000 }
      )
    }

    if (hasTranscript && libraryId !== currentLibraryId) {
      // Need re-transcription
      setPendingChangeId(libraryId)
      setRetranscribeConfirmOpen(true)
      return
    }

    // No transcript yet, just select
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
        variant="outline"
        size="sm"
        className="flex items-center gap-1.5"
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
          <span className="text-[10px] text-amber-700 dark:text-amber-300 px-1 rounded">
            pending
          </span>
        )}
      </Button>

      {/* Library selector dialog */}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <BookOpen className="h-4 w-4" />
              Select Hot Words Library
            </DialogTitle>
          </DialogHeader>

          {!providerSupportsHotWords && (
            <div className="flex items-start gap-2 px-3 py-2 text-sm border border-amber-200 dark:border-amber-800 rounded-lg text-amber-700 dark:text-amber-300">
              <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
              <span>Current transcription model does not support hot words. Hot words will NOT be applied.</span>
            </div>
          )}

          <ScrollArea className="max-h-64">
            <div className="space-y-1">
              <div
                className={`flex items-center gap-2 px-3 py-2 rounded cursor-pointer text-sm ${
                  !currentLibraryId && !pendingLibraryId
                    ? "text-primary font-light"
                    : "hover:bg-muted"
                }`}
                onClick={() => handleSelect(null)}
              >
                <span>None</span>
              </div>
              {libraries.map((lib) => {
                const isSelected = lib.id === currentLibraryId || lib.id === pendingLibraryId
                return (
                  <div
                    key={lib.id}
                    className={`flex items-center justify-between px-3 py-2 rounded cursor-pointer text-sm ${
                      isSelected
                        ? "text-primary font-light"
                        : "hover:bg-muted"
                    }`}
                    onClick={() => handleSelect(lib.id)}
                  >
                    <span className="truncate">{lib.name}</span>
                    <span className="text-xs text-muted-foreground shrink-0 ml-2">
                      {lib.word_count} words
                    </span>
                  </div>
                )
              })}
              {libraries.length === 0 && (
                <p className="text-xs text-muted-foreground p-3 text-center">
                  No hot word libraries. Create one in Settings → Hot Words.
                </p>
              )}
            </div>
          </ScrollArea>
        </DialogContent>
      </Dialog>

      {/* Retranscribe confirmation */}
      <Dialog open={retranscribeConfirmOpen} onOpenChange={setRetranscribeConfirmOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Re-transcribe Required</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            This meeting has already been transcribed. Changing hot words requires re-transcription. Re-transcribe now?
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={handleCancelRetranscribe}>Cancel</Button>
            <Button onClick={handleConfirmRetranscribe}>Re-transcribe</Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
