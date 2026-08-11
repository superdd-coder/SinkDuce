import { useState, useEffect, useCallback, useRef } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { BookOpen, AlertTriangle, Ban, Settings2 } from "lucide-react"
import { cn } from "@/lib/utils"
import {
  getHotWordsLibraries,
  type HotWordsLibrarySummary,
} from "@/api/client"
import { toast } from "sonner"
import { HotWordsManager } from "@/components/llm-provider/hot-words-manager"

interface Props {
  meetingId: string
  /** Persisted library on the meeting (server). */
  currentLibraryId: string | null | undefined
  /**
   * When true, selection is a local draft only until re-transcribe.
   * Refresh / switch meeting clears the draft and shows currentLibraryId again.
   */
  hasTranscript: boolean
  providerSupportsHotWords: boolean
  /** Persist library on the meeting (immediate when no transcript). */
  onSelectLibrary: (libraryId: string | null) => void
  /** Optional: parent tracks draft so Re-transcribe can commit it first. */
  onDraftChange?: (draftLibraryId: string | null | undefined) => void
  /** @deprecated no longer forced; kept optional for call-site compatibility */
  onRetranscribe?: () => void
  disabled?: boolean
  compact?: boolean
}

export function HotWordsSelector({
  meetingId,
  currentLibraryId,
  hasTranscript,
  providerSupportsHotWords,
  onSelectLibrary,
  onDraftChange,
  disabled = false,
  compact = false,
}: Props) {
  const [open, setOpen] = useState(false)
  const [libraries, setLibraries] = useState<HotWordsLibrarySummary[]>([])
  /**
   * Local draft when a transcript already exists.
   * `undefined` = no draft (show server value).
   * `string | null` = user picked a library (or None) without re-transcribing yet.
   */
  const [draftId, setDraftId] = useState<string | null | undefined>(undefined)
  const [managerOpen, setManagerOpen] = useState(false)
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  /** Apply selection, show selected row briefly, then silk-fade the dialog closed. */
  const closeAfterSelect = useCallback(() => {
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current)
    // Let is-on state paint, then setOpen(false) so Base UI can run data-ending-style (280ms silk)
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

  // Prefetch so the pill can show the library name without opening the dialog
  useEffect(() => {
    void fetchLibraries()
  }, [fetchLibraries, meetingId])

  // Leaving the meeting drops any uncommitted draft
  useEffect(() => {
    setDraftId(undefined)
  }, [meetingId])

  // Server caught up (e.g. after re-transcribe saved the library) → clear draft
  useEffect(() => {
    if (draftId === undefined) return
    // Keep forced "None" draft while ASR has no hot-word support — clearing to
    // undefined would fight the unsupported effect (null ⇄ undefined loop).
    if (!providerSupportsHotWords && draftId === null) return
    if (draftId === (currentLibraryId ?? null)) {
      setDraftId(undefined)
    }
  }, [currentLibraryId, draftId, providerSupportsHotWords])

  useEffect(() => {
    onDraftChange?.(draftId)
  }, [draftId, onDraftChange])

  useEffect(() => {
    if (disabled || !providerSupportsHotWords) setOpen(false)
  }, [disabled, providerSupportsHotWords])

  const serverId = currentLibraryId ?? null
  const activeId = draftId !== undefined ? draftId : serverId
  const isPending = draftId !== undefined && draftId !== serverId

  /**
   * Active ASR path does not support hot words → force None (label + selection).
   * - With transcript: draft = None (commit on re-tx if needed)
   * - Without: clear server library immediately
   * Functional setState so we do not depend on draftId (avoids update loops).
   */
  useEffect(() => {
    if (providerSupportsHotWords) return
    setOpen(false)
    const server = currentLibraryId ?? null
    if (hasTranscript) {
      setDraftId((d) => (d === null ? d : null))
      return
    }
    setDraftId((d) => (d === undefined ? d : undefined))
    if (server != null) {
      onSelectLibrary(null)
    }
  }, [
    providerSupportsHotWords,
    currentLibraryId,
    hasTranscript,
    onSelectLibrary,
  ])

  const displayLib =
    libraries.find((l) => l.id === activeId) ||
    // Prefer name from list; if draft/server id not loaded yet, still show active styling
    undefined
  const hasSelection =
    providerSupportsHotWords && activeId != null && activeId !== ""
  const noneSelected = !hasSelection
  // Unsupported model: always show None (even if server still has a stale id briefly)
  const displayName = !providerSupportsHotWords
    ? "None"
    : hasSelection && displayLib
      ? displayLib.name
      : hasSelection
        ? "Hot Words"
        : "None"

  const handleSelect = (libraryId: string | null) => {
    if (!providerSupportsHotWords) {
      if (libraryId !== null) {
        toast.warning(
          "Current transcription model does not support hot words.",
          { duration: 5000 },
        )
      }
      return
    }

    if (hasTranscript) {
      // Local draft only — do not persist; do not force re-transcribe
      if (libraryId === serverId) {
        setDraftId(undefined)
      } else {
        setDraftId(libraryId)
      }
      closeAfterSelect()
      return
    }

    // No transcript yet — persist immediately
    setDraftId(undefined)
    onSelectLibrary(libraryId)
    closeAfterSelect()
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
              : "Choose hot words library"
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
              Hot Words Library
            </DialogTitle>
          </DialogHeader>
          <p className="pm-meta -mt-1">
            Boost domain terms during transcription. Pick one library or None.
            {hasTranscript && (
              <>
                {" "}
                Changes apply on the next re-transcribe; leave without re-transcribing
                to keep the previous library.
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
                No libraries yet. Create one below or in Settings.
              </p>
            )}
          </div>

          {/* Footer action — secondary path, not competing with the title/close */}
          <div className="pm-hw-dialog-footer">
            <button
              type="button"
              className="pm-hw-manage-btn"
              onClick={() => setManagerOpen(true)}
            >
              <Settings2 className="size-3.5 opacity-80" />
              Manage libraries
            </button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
