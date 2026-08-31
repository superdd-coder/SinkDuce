import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Check } from "lucide-react"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"

/** LiveTranslate target languages (text output, meeting-relevant set). */
export const TRANSLATION_TARGET_LANGUAGES: { code: string; label: string }[] = [
  { code: "en", label: "English" },
  { code: "zh", label: "Chinese" },
  { code: "ja", label: "Japanese" },
  { code: "ko", label: "Korean" },
  { code: "de", label: "German" },
  { code: "fr", label: "French" },
  { code: "ru", label: "Russian" },
  { code: "es", label: "Spanish" },
  { code: "pt", label: "Portuguese" },
  { code: "it", label: "Italian" },
  { code: "th", label: "Thai" },
  { code: "vi", label: "Vietnamese" },
  { code: "id", label: "Indonesian" },
  { code: "ms", label: "Malay" },
  { code: "ar", label: "Arabic" },
  { code: "tr", label: "Turkish" },
  { code: "hi", label: "Hindi" },
]

export const DEFAULT_TRANSLATION_TARGET = "en"

const CLOSE_HOVER_DELAY = 160
const RETRACT_MS = 150

interface Props {
  enabled: boolean
  target: string
  onEnabledChange: (v: boolean) => void
  onTargetChange: (code: string) => void
  /** e.g. transcription not running */
  disabled?: boolean
}

/**
 * Live-translation control (in-recording chip) — click toggles on/off,
 * hover opens a language dropdown where picking a language enables
 * translation into it (and hot-swaps the engine mid-recording via the
 * parent callbacks).
 */
export function TranslationSelector({
  enabled,
  target,
  onEnabledChange,
  onTargetChange,
  disabled = false,
}: Props) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [closing, setClosing] = useState(false)
  const closeTimerRef = useRef<number | null>(null)
  const retractTimerRef = useRef<number | null>(null)

  useEffect(
    () => () => {
      if (closeTimerRef.current) window.clearTimeout(closeTimerRef.current)
      if (retractTimerRef.current) window.clearTimeout(retractTimerRef.current)
    },
    [],
  )

  const openNow = () => {
    if (disabled) return
    if (closeTimerRef.current) {
      window.clearTimeout(closeTimerRef.current)
      closeTimerRef.current = null
    }
    if (retractTimerRef.current) {
      window.clearTimeout(retractTimerRef.current)
      retractTimerRef.current = null
    }
    setClosing(false)
    setOpen(true)
  }

  const closeNow = () => {
    if (closeTimerRef.current) window.clearTimeout(closeTimerRef.current)
    closeTimerRef.current = null
    if (!open) return
    // Retract animation, then unmount — no hard cut.
    setClosing(true)
    if (retractTimerRef.current) window.clearTimeout(retractTimerRef.current)
    retractTimerRef.current = window.setTimeout(() => {
      retractTimerRef.current = null
      setOpen(false)
      setClosing(false)
    }, RETRACT_MS)
  }

  const scheduleClose = () => {
    if (closeTimerRef.current) window.clearTimeout(closeTimerRef.current)
    closeTimerRef.current = window.setTimeout(() => {
      closeTimerRef.current = null
      closeNow()
    }, CLOSE_HOVER_DELAY)
  }

  const pick = (code: string) => {
    if (code !== target) onTargetChange(code)
    if (!enabled) onEnabledChange(true)
    closeNow()
  }

  const label = enabled
    ? `${t("meeting.liveTranslationShort")} · ${target.toUpperCase()}`
    : t("meeting.liveTranslation")

  const title = enabled
    ? `${t("meeting.liveTranslation")} · ${target.toUpperCase()} — ${t("meeting.liveTranslationCaveat")}`
    : t("meeting.liveTranslationDesc")

  const trigger = (
    <Button
      type="button"
      variant={enabled ? "secondary" : "ghost"}
      size="sm"
      disabled={disabled}
      className="pm-meeting-live-captions-btn pm-lt-btn"
      aria-haspopup="menu"
      aria-expanded={open}
      aria-label={t("meeting.liveTranslation")}
      title={title}
      onClick={() => onEnabledChange(!enabled)}
    >
      <span
        className={cn(
          "w-1.5 h-1.5 rounded-full mr-1.5 shrink-0",
          enabled ? "bg-[var(--pm-green)]" : "bg-[var(--pm-faint)]",
        )}
        aria-hidden
      />
      {label}
    </Button>
  )

  return (
    <div
      className="pm-lt-wrap"
      onMouseEnter={openNow}
      onMouseLeave={scheduleClose}
      onFocusCapture={openNow}
      onBlurCapture={(e) => {
        const next = e.relatedTarget as Node | null
        if (next && e.currentTarget.contains(next)) return
        scheduleClose()
      }}
    >
      {trigger}

      {(open || closing) && (
        <div
          role="menu"
          aria-label={t("meeting.liveTranslationTarget")}
          className={cn(
            "pm-lt-menu",
            closing ? "is-retracting" : "is-emerging",
          )}
          onMouseEnter={openNow}
        >
          <span className="pm-lt-menu-label">
            {t("meeting.liveTranslationTarget")}
          </span>
          {TRANSLATION_TARGET_LANGUAGES.map(({ code, label: lang }) => {
            // Highlight only while translation is on — after toggling off the
            // menu shows a clean list, but the remembered target is reused
            // the next time translation is enabled.
            const isOn = enabled && code === target
            return (
              <button
                key={code}
                type="button"
                role="menuitemradio"
                aria-checked={isOn}
                className={cn("pm-lt-item", isOn && "is-on")}
                onClick={() => pick(code)}
              >
                <span className="pm-lt-item-label">{lang}</span>
                {isOn ? (
                  <Check className="pm-lt-check" strokeWidth={2.25} />
                ) : (
                  <span className="pm-lt-item-code t-mono-family">{code}</span>
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
