import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Languages } from "lucide-react"
import { cn } from "@/lib/utils"
import type { LanguageHintOption } from "@/api/client"

export const DEFAULT_LANGUAGE_HINTS = ["auto"]

/**
 * Toggle language hint selection with auto exclusivity:
 * - pick "auto" → only ["auto"]
 * - pick any language → drop "auto", multi-select languages
 * - deselect last language → fall back to ["auto"]
 */
export function toggleLanguageHint(selected: string[], code: string): string[] {
  const isAuto = code === "auto"
  const isOn = selected.includes(code)

  if (isAuto) {
    // Selecting auto clears others; deselecting auto with nothing else → stay auto
    return isOn ? ["auto"] : ["auto"]
  }

  if (isOn) {
    const next = selected.filter((c) => c !== code && c !== "auto")
    return next.length === 0 ? ["auto"] : next
  }

  // Add language, strip auto
  const withoutAuto = selected.filter((c) => c !== "auto")
  if (withoutAuto.includes(code)) return withoutAuto
  return [...withoutAuto, code]
}

interface Props {
  selected: string[]
  onChange: (hints: string[]) => void
  options: LanguageHintOption[]
}

export function LanguageHintsSelector({ selected, onChange, options }: Props) {
  const [open, setOpen] = useState(false)

  const toggle = (code: string) => {
    onChange(toggleLanguageHint(selected, code))
  }

  const isAutoOnly =
    selected.length === 0 ||
    (selected.length === 1 && selected[0] === "auto")

  const display = isAutoOnly
    ? "Auto"
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
      <Button
        type="button"
        variant="outline"
        size="sm"
        className={cn(
          "flex items-center gap-1.5",
          !isAutoOnly && "border-[color-mix(in_srgb,var(--pm-green)_28%,transparent)] text-[var(--pm-green)]",
        )}
        onClick={() => setOpen(true)}
      >
        <Languages className="h-3.5 w-3.5" />
        <span className="max-w-[140px] truncate">{display}</span>
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="pm-dialog sm:max-w-[340px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Languages className="h-4 w-4 text-[var(--pm-green)]" />
              Language Hints
            </DialogTitle>
          </DialogHeader>
          <p className="pm-meta -mt-1">
            Languages that may appear in the audio. Choosing a language clears Auto;
            Auto clears every language.
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
