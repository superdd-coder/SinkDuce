import { useState, useEffect, useRef } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Languages } from "lucide-react"
import { cn } from "@/lib/utils"
import type { LanguageHintOption } from "@/api/client"
import {
  DEFAULT_LANGUAGE_HINTS,
  toggleLanguageHint,
} from "@/lib/language-hints"

export { DEFAULT_LANGUAGE_HINTS, toggleLanguageHint }

/** Short rotating tips — explicit language improves ASR accuracy */
const LANG_HINT_MESSAGES = [
  "Pick a language for accuracy",
  "Language hint → cleaner text",
  "Specify language for better ASR",
  "A set language is more precise",
  "Hint the language for best results",
  "Known language, sharper captions",
]

const HINT_SHOW_DURATION = 3200
const HINT_INITIAL_DELAY = 600
const HINT_MIN_INTERVAL = 2200
const HINT_MAX_INTERVAL = 4800
const HINT_EXIT_MS = 320

interface Props {
  selected: string[]
  onChange: (hints: string[]) => void
  options: LanguageHintOption[]
  /** Hide floating tip bubble (e.g. during file transcribe busy) */
  showTipBubble?: boolean
  /** e.g. file transcription in progress */
  disabled?: boolean
  /** Compact control (review footer) — no full-width stretch */
  compact?: boolean
  /** Official cap for the active model (1 = single-select, 4 = Qwen). */
  maxHints?: number
}

export function LanguageHintsSelector({
  selected,
  onChange,
  options,
  showTipBubble = true,
  disabled = false,
  compact = false,
  maxHints = 1,
}: Props) {
  const [open, setOpen] = useState(false)
  const [hintVisible, setHintVisible] = useState(false)
  const [hintExiting, setHintExiting] = useState(false)
  const [hintMessage, setHintMessage] = useState(LANG_HINT_MESSAGES[0])
  const hintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastHintIdxRef = useRef(0)

  const pickMessage = () => {
    if (LANG_HINT_MESSAGES.length <= 1) return LANG_HINT_MESSAGES[0]
    let idx = Math.floor(Math.random() * LANG_HINT_MESSAGES.length)
    if (idx === lastHintIdxRef.current) {
      idx = (idx + 1) % LANG_HINT_MESSAGES.length
    }
    lastHintIdxRef.current = idx
    return LANG_HINT_MESSAGES[idx]
  }

  useEffect(() => {
    if (disabled) setOpen(false)
  }, [disabled])

  // Same show/hide cadence as Meeting Quick Chat FAB tip
  useEffect(() => {
    if (!showTipBubble || open || disabled) {
      setHintVisible(false)
      setHintExiting(false)
      if (hintTimerRef.current) clearTimeout(hintTimerRef.current)
      return
    }

    const scheduleHint = () => {
      setHintMessage(pickMessage())
      setHintVisible(true)
      setHintExiting(false)
      if (hintTimerRef.current) clearTimeout(hintTimerRef.current)
      hintTimerRef.current = setTimeout(() => {
        setHintExiting(true)
        hintTimerRef.current = setTimeout(() => {
          setHintVisible(false)
          setHintExiting(false)
          const delay =
            HINT_MIN_INTERVAL + Math.random() * (HINT_MAX_INTERVAL - HINT_MIN_INTERVAL)
          hintTimerRef.current = setTimeout(scheduleHint, delay)
        }, HINT_EXIT_MS)
      }, HINT_SHOW_DURATION)
    }

    hintTimerRef.current = setTimeout(scheduleHint, HINT_INITIAL_DELAY)
    return () => {
      if (hintTimerRef.current) clearTimeout(hintTimerRef.current)
    }
  }, [showTipBubble, open, disabled])

  const toggle = (code: string) => {
    onChange(toggleLanguageHint(selected, code, maxHints))
  }

  const isAutoOnly =
    selected.length === 0 ||
    (selected.length === 1 && selected[0] === "auto")

  // When disabled (e.g. re-transcribing), show short codes: zh / en / zh,en
  const display = isAutoOnly
    ? "Auto"
    : disabled
      ? selected.filter((c) => c !== "auto").join(", ") || "Auto"
      : selected.length <= 2
        ? selected.map((c) => options.find((o) => o.code === c)?.label ?? c).join(", ")
        : `${selected.length} languages`

  // Ensure auto appears in the list even if options omit it
  const pills: LanguageHintOption[] = (() => {
    const hasAuto = options.some((o) => o.code === "auto")
    return hasAuto
      ? options
      : [{ code: "auto", label: "Auto" }, ...options]
  })()

  return (
    <>
      {/* Language button fixed; bubble sits top-right and bobs on its own */}
      <div className={cn("pm-lang-hint-anchor", compact ? "w-auto" : "w-full")}>
        {(hintVisible || hintExiting) && showTipBubble && !open && !disabled && (
          <div className="pm-lang-hint-bubble-slot" aria-hidden>
            {/* Bob on outer wrap; scale emerge on inner — no transform fight */}
            <div className="pm-lang-hint-bob">
              <div
                className={cn(
                  "pm-lang-hint-bubble",
                  hintExiting ? "is-retracting" : "is-emerging",
                )}
              >
                <span className="pm-meta whitespace-nowrap text-[var(--pm-green)]">
                  {hintMessage}
                </span>
              </div>
            </div>
          </div>
        )}
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          className={cn(
            "pm-meeting-pill",
            compact && "is-compact",
            !isAutoOnly && "is-active",
          )}
          onClick={() => {
            if (disabled) return
            setOpen(true)
          }}
        >
          <Languages className="size-3.5 shrink-0 opacity-80" />
          <span
            className={cn(
              "pm-meeting-pill-label",
              disabled && "t-mono-family uppercase tracking-wide",
            )}
          >
            {display}
          </span>
        </Button>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="pm-dialog sm:max-w-[340px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Languages className="h-4 w-4 text-[var(--pm-green)]" />
              Language Hints
            </DialogTitle>
          </DialogHeader>
          <p className="pm-meta -mt-1">
            {maxHints <= 1
              ? "This model accepts one language. Auto lets it detect the language."
              : `This model accepts up to ${maxHints} languages. Auto lets it detect.`}
          </p>
          <div className="pm-lang-pills" role="group" aria-label="Language hints">
            {pills.map(({ code, label }) => {
              const isSelected =
                code === "auto"
                  ? isAutoOnly
                  : selected.includes(code)
              return (
                <button
                  key={code}
                  type="button"
                  className={cn(
                    "pm-lang-pill",
                    isSelected ? "is-on" : "is-off",
                  )}
                  aria-pressed={isSelected}
                  onClick={() => toggle(code)}
                >
                  <span className="pm-lang-pill-label">{label}</span>
                  {code !== "auto" && (
                    <span className="pm-lang-pill-code t-mono-family">{code}</span>
                  )}
                </button>
              )
            })}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
