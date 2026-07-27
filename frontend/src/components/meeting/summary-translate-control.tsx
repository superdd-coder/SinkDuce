import { useEffect, useRef, useState } from "react"
import { Languages, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { TRANSLATE_LANGUAGES } from "@/api/client"

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
        background: "var(--ze-green)",
        boxShadow: "0 0 6px 1px var(--ze-green)",
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
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [open])

  const handleToggle = () => {
    const next = !open
    setOpen(next)
    if (next) onOpen?.()
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        disabled={disabled || translating}
        onClick={handleToggle}
        title="Translate summary"
        className={cn(
          "h-7 w-7 inline-flex items-center justify-center rounded-md text-muted-foreground",
          "hover:text-foreground hover:bg-accent transition-colors",
          (disabled || translating) && "opacity-50 cursor-not-allowed",
          activeLang && "text-[var(--ze-green)]",
        )}
      >
        {translating ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Languages className="h-3.5 w-3.5" />
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-1 w-44 rounded-md border bg-popover shadow-md py-1">
          {/* Original (source) option */}
          <button
            type="button"
            onClick={() => { onSelect(null); setOpen(false) }}
            className={cn(
              "w-full flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer hover:bg-accent transition-colors",
              activeLang === null && "bg-accent/50 font-medium",
            )}
          >
            <span className={cn(
              "w-1.5 h-1.5 shrink-0 rotate-45 transition-all",
              activeLang === null ? "bg-primary" : "border border-muted-foreground/30",
            )} />
            Original
          </button>

          <div className="my-1 h-px bg-border/60" />

          {TRANSLATE_LANGUAGES.map(({ code, label }) => {
            const generated = generatedLangs.includes(code)
            const active = activeLang === code
            return (
              <button
                key={code}
                type="button"
                onClick={() => { onSelect(code); setOpen(false) }}
                className={cn(
                  "w-full flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer hover:bg-accent transition-colors",
                  active && "bg-accent/50 font-medium",
                )}
              >
                <span className={cn(
                  "w-1.5 h-1.5 shrink-0 rotate-45 transition-all",
                  active ? "bg-primary" : "border border-muted-foreground/30",
                )} />
                <span>{label}</span>
                <span className="text-[10px] text-muted-foreground/70">{code}</span>
                {generated && <GlowDot />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
