import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Languages } from "lucide-react"
import { cn } from "@/lib/utils"
import type { LanguageHintOption } from "@/api/client"

export const DEFAULT_LANGUAGE_HINTS = ["auto"]

interface Props {
  selected: string[]
  onChange: (hints: string[]) => void
  options: LanguageHintOption[]
}

export function LanguageHintsSelector({ selected, onChange, options }: Props) {
  const [open, setOpen] = useState(false)

  const toggle = (code: string) => {
    if (selected.includes(code)) {
      onChange(selected.filter((c) => c !== code))
    } else {
      onChange([...selected, code])
    }
  }

  const display = selected.length === 0
    ? "Languages"
    : selected.length <= 2
      ? selected.map((c) => options.find((o) => o.code === c)?.label ?? c).join(", ")
      : `${selected.length} languages`

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="flex items-center gap-1.5"
        onClick={() => setOpen(true)}
      >
        <Languages className="h-3.5 w-3.5" />
        <span className="max-w-[120px] truncate">{display}</span>
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-xs">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Languages className="h-4 w-4" />
              Language Hints
            </DialogTitle>
          </DialogHeader>
          <p className="text-xs text-muted-foreground -mt-1">
            Help the ASR model recognize mixed-language audio. Select the languages that may appear in the recording.
          </p>
          <div className="space-y-0.5">
            {options.map(({ code, label }) => {
              const isSelected = selected.includes(code)
              return (
                <div
                  key={code}
                  className={cn(
                    "flex items-center gap-2 px-3 py-2 rounded cursor-pointer text-sm select-none",
                    isSelected ? "text-primary font-light" : "hover:bg-muted"
                  )}
                  onClick={() => toggle(code)}
                >
                  <div
                    className={cn(
                      "h-4 w-4 rounded border-2 flex items-center justify-center shrink-0 transition-colors",
                      isSelected
                        ? "bg-primary border-primary"
                        : "border-muted-foreground/40"
                    )}
                  >
                    {isSelected && (
                      <svg className="h-3 w-3 text-primary-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                  </div>
                  <span className="flex-1">{label}</span>
                  <span className="text-xs text-muted-foreground font-mono">{code}</span>
                </div>
              )
            })}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
