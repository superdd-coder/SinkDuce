import { useEffect, useRef, useState, type RefObject } from "react"
import { Languages, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { SoftMenu, MenuItem } from "@/components/ui/menu"
import { TRANSLATE_LANGUAGES } from "@/api/client"
import { useT } from "@/i18n/use-t"

interface SummaryTranslateControlProps {
  /** Language codes that already have a generated translation file. */
  generatedLangs: string[]
  /** Currently displayed language view; null = original summary. */
  activeLang: string | null
  /** True while a translation is being generated. */
  translating: boolean
  disabled?: boolean
  /** Called with a language code, or null to return to the original. */
  onSelect: (lang: string | null) => void
  /** Called when the dropdown opens (to refresh the generated-lang list). */
  onOpen?: () => void
}

/** Small green glowing dot marking an already-generated language. */
function GlowDot() {
  return (
    <span
      aria-hidden
      className="ml-auto w-1.5 h-1.5 rounded-full shrink-0 animate-pulse"
      style={{
        background: "var(--pm-green, var(--ze-green))",
        boxShadow: "0 0 6px 1px color-mix(in srgb, var(--pm-green, #1a5e3d) 55%, transparent)",
      }}
    />
  )
}

export function SummaryTranslateControl({
  generatedLangs,
  activeLang,
  translating,
  disabled,
  onSelect,
  onOpen,
}: SummaryTranslateControlProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const btnRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      const t = e.target as Node
      if (btnRef.current?.contains(t)) return
      if ((e.target as Element)?.closest?.("[data-slot='menu']")) return
      setOpen(false)
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [open])

  const handleToggle = () => {
    if (disabled || translating) return
    const next = !open
    setOpen(next)
    if (next) onOpen?.()
  }

  return (
    <div className="relative" ref={btnRef as RefObject<HTMLDivElement>}>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        disabled={disabled || translating}
        onClick={handleToggle}
        title={t("meeting.translateSummary")}
        aria-label={t("meeting.translateSummary")}
        className={cn(activeLang && "text-[var(--pm-green)]")}
      >
        {translating ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <Languages className="size-3.5" />
        )}
      </Button>

      <SoftMenu open={open} portal anchorRef={btnRef} align="end" className="min-w-[176px]">
        <MenuItem
          active={activeLang === null}
          onClick={() => { onSelect(null); setOpen(false) }}
        >
          {t("chat.original")}
        </MenuItem>
        {TRANSLATE_LANGUAGES.map(({ code, label }) => {
          const generated = generatedLangs.includes(code)
          const active = activeLang === code
          return (
            <MenuItem
              key={code}
              active={active}
              onClick={() => { onSelect(code); setOpen(false) }}
            >
              <span className="flex-1 min-w-0 truncate">{label}</span>
              <span className="pm-meta shrink-0 ml-1">{code}</span>
              {generated && <GlowDot />}
            </MenuItem>
          )
        })}
      </SoftMenu>
    </div>
  )
}
