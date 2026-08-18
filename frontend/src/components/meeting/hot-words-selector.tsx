import { useState, useEffect, useCallback, useMemo, useRef } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { BookOpen, AlertTriangle, Ban, Settings2, Pin } from "lucide-react"
import { cn } from "@/lib/utils"
import {
  getHotWordsLibraries,
  type HotWordsLibrarySummary,
} from "@/api/client"
import { toast } from "sonner"
import { HotWordsManager } from "@/components/llm-provider/hot-words-manager"

interface Props {
  meetingId: string
  currentLibraryIds?: string[]
  /** @deprecated use currentLibraryIds */
  currentLibraryId?: string | null
  hasTranscript: boolean
  providerSupportsHotWords: boolean
  onSelectLibraries: (libraryIds: string[]) => void
  onDraftChange?: (draftIds: string[] | undefined) => void
  onRetranscribe?: () => void
  disabled?: boolean
  compact?: boolean
}

export function hotWordsSelectionLabel(ids: string[]): string {
  if (ids.length === 0) return "Hot Words"
  return `Hot Words · ${ids.length}`
}

export function HotWordsSelector({
  meetingId,
  currentLibraryIds,
  currentLibraryId,
  hasTranscript,
  providerSupportsHotWords,
  onSelectLibraries,
  onDraftChange,
  disabled = false,
  compact = false,
}: Props) {
  const [open, setOpen] = useState(false)
  const [libraries, setLibraries] = useState<HotWordsLibrarySummary[]>([])
  const [draftIds, setDraftIds] = useState<string[] | undefined>(undefined)
  const [managerOpen, setManagerOpen] = useState(false)
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const closeAfterSelect = useCallback(() => {
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current)
    closeTimerRef.current = setTimeout(() => {
      setOpen(false)
      closeTimerRef.current = null
    }, 140)
  }, [])

  useEffect(() => {
    return () => {
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current)
    }
  }, [])

  const fetchLibraries = useCallback(async () => {
    try {
      setLibraries(await getHotWordsLibraries())
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    if (open) void fetchLibraries()
  }, [open, fetchLibraries])

  useEffect(() => {
    void fetchLibraries()
  }, [fetchLibraries, meetingId])

  useEffect(() => {
    setDraftIds(undefined)
  }, [meetingId])

  const serverIds = useMemo(() => {
    if (currentLibraryIds !== undefined) return currentLibraryIds
    return currentLibraryId ? [currentLibraryId] : []
  }, [currentLibraryIds, currentLibraryId])

  useEffect(() => {
    if (draftIds === undefined) return
    if (!providerSupportsHotWords && draftIds.length === 0) return
    const same =
      draftIds.length === serverIds.length &&
      draftIds.every((id) => serverIds.includes(id))
    if (same) setDraftIds(undefined)
  }, [serverIds, draftIds, providerSupportsHotWords])

  useEffect(() => {
    onDraftChange?.(draftIds)
  }, [draftIds, onDraftChange])

  useEffect(() => {
    if (disabled || !providerSupportsHotWords) setOpen(false)
  }, [disabled, providerSupportsHotWords])

  useEffect(() => {
    if (providerSupportsHotWords) return
    setOpen(false)
    if (hasTranscript) {
      setDraftIds((d) => (d && d.length === 0 ? d : []))
      return
    }
    setDraftIds((d) => (d === undefined ? d : undefined))
    if (serverIds.length > 0) {
      onSelectLibraries([])
    }
  }, [
    providerSupportsHotWords,
    hasTranscript,
    onSelectLibraries,
    serverIds.length,
  ])

  const activeIds = draftIds !== undefined ? draftIds : serverIds
  const isPending =
    hasTranscript &&
    draftIds !== undefined &&
    (draftIds.length !== serverIds.length ||
      draftIds.some((id) => !serverIds.includes(id)))
  const hasSelection = providerSupportsHotWords && activeIds.length > 0
  const displayName = !providerSupportsHotWords
    ? "Hot Words"
    : hotWordsSelectionLabel(activeIds)

  const persistOrDraft = (next: string[]) => {
    if (!providerSupportsHotWords) {
      if (next.length > 0) {
        toast.warning(
          "Current transcription model does not support hot words.",
          { duration: 5000 },
        )
      }
      return
    }
    const same =
      next.length === serverIds.length &&
      next.every((id) => serverIds.includes(id))
    setDraftIds(same ? undefined : next)
    if (!hasTranscript) {
      onSelectLibraries(next)
    }
  }

  const toggleLibrary = (libraryId: string) => {
    const on = activeIds.includes(libraryId)
    persistOrDraft(on ? activeIds.filter((id) => id !== libraryId) : [...activeIds, libraryId])
  }

  const isDisabled = disabled || !providerSupportsHotWords

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={isDisabled}
        title={
          !providerSupportsHotWords
            ? "Active transcription model does not support hot words"
            : disabled
              ? "Unavailable while transcribing"
              : "Choose hot words libraries"
        }
        className={cn(
          "pm-meeting-pill",
          compact && "is-compact",
          hasSelection && providerSupportsHotWords && "is-active",
        )}
        onClick={() => {
          if (isDisabled) return
          setOpen(true)
        }}
      >
        <BookOpen className="size-3.5 shrink-0 opacity-80" />
        <span className="pm-meeting-pill-label">
          {displayName}
        </span>
        {isPending && providerSupportsHotWords && (
          <span className="text-[10px] text-amber-700 px-1.5 py-0.5 rounded-full bg-amber-50">
            draft
          </span>
        )}
      </Button>

      <HotWordsManager
        nested
        open={managerOpen}
        onOpenChange={(next) => {
          setManagerOpen(next)
          if (!next) void fetchLibraries()
        }}
      />

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-[360px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <BookOpen className="h-4 w-4 text-[var(--pm-green)]" />
              Hot Words
            </DialogTitle>
          </DialogHeader>
          <p className="pm-meta -mt-1">
            Pick one or more libraries. They are concatenated for transcription.
            Pinned ones start on for new meetings; tap again to turn one off for this meeting.
            {hasTranscript && (
              <>
                {" "}
                Changes apply on the next re-transcribe.
              </>
            )}
          </p>

          {!providerSupportsHotWords && (
            <div className="pm-hw-warn" role="status">
              <AlertTriangle className="size-3.5 shrink-0 mt-0.5" />
              <span>
                Active model does not support hot words — selection will not be applied.
              </span>
            </div>
          )}

          <div className="pm-hw-list" role="listbox" aria-multiselectable="true" aria-label="Hot word libraries">
            <button
              type="button"
              role="option"
              aria-selected={activeIds.length === 0}
              className={cn("pm-hw-option", activeIds.length === 0 ? "is-on" : "is-off")}
              onClick={() => persistOrDraft([])}
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
              const isSelected = activeIds.includes(lib.id)
              return (
                <button
                  key={lib.id}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  className={cn("pm-hw-option", isSelected ? "is-on" : "is-off")}
                  onClick={() => toggleLibrary(lib.id)}
                >
                  <span className="pm-hw-option-icon" aria-hidden>
                    <BookOpen className="size-3.5" />
                  </span>
                  <span className="pm-hw-option-body">
                    <span className="pm-hw-option-name">
                      {lib.name}
                      {lib.is_system && (
                        <span className="pm-settings-hw-default-pill ml-1.5">System</span>
                      )}
                      {lib.is_pinned && (
                        <Pin className="inline-block size-3 ml-1 opacity-50" />
                      )}
                    </span>
                    <span className="pm-hw-option-meta">
                      {lib.word_count} word{lib.word_count === 1 ? "" : "s"}
                    </span>
                  </span>
                </button>
              )
            })}

            {libraries.length === 0 && (
              <p className="pm-hw-empty">
                No libraries yet. Create one below or in Settings.
              </p>
            )}
          </div>

          <div className="pm-hw-dialog-footer">
            <button
              type="button"
              className="pm-hw-manage-btn"
              onClick={() => setManagerOpen(true)}
            >
              <Settings2 className="size-3.5 opacity-80" />
              Manage libraries
            </button>
            <button
              type="button"
              className="pm-hw-manage-btn"
              onClick={() => closeAfterSelect()}
            >
              Done
            </button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
