import { useState, useRef, useEffect, useCallback } from "react"
import { ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"

interface DropdownSelectProps {
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
  placeholder?: string
  disabled?: boolean
  className?: string
  /** Compact trigger for dialogs / dense forms */
  size?: "default" | "sm"
}

/** Open/close duration — keep in sync with CSS `--pm-select-ms` */
const SELECT_MS = 180

/**
 * Soft float select — Premium green-wash selection (no native OS blue).
 * Menu enter/exit: opacity + soft scale/slide, open/close same duration.
 */
export function DropdownSelect({
  value,
  onChange,
  options,
  placeholder,
  disabled,
  className,
  size = "default",
}: DropdownSelectProps) {
  const [open, setOpen] = useState(false)
  /** Keep in DOM while exit animation plays */
  const [mounted, setMounted] = useState(false)
  const [shown, setShown] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const close = useCallback(() => {
    setOpen(false)
  }, [])

  // Drive mount → shown (enter) / shown → unmount (exit).
  // Open/close share SELECT_MS; only depend on `open` so enter rAF is not cancelled.
  useEffect(() => {
    let exitTimer: ReturnType<typeof setTimeout> | null = null
    let raf1 = 0
    let raf2 = 0

    if (open) {
      setMounted(true)
      // Double rAF: paint closed state, then add .is-open
      raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => {
          setShown(true)
        })
      })
    } else {
      setShown(false)
      exitTimer = setTimeout(() => {
        setMounted(false)
      }, SELECT_MS)
    }

    return () => {
      if (exitTimer) clearTimeout(exitTimer)
      if (raf1) cancelAnimationFrame(raf1)
      if (raf2) cancelAnimationFrame(raf2)
    }
  }, [open])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        close()
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [close])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close()
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [open, close])

  const selectedLabel =
    options.find((o) => o.value === value)?.label || placeholder || "Select..."

  return (
    <div ref={containerRef} className={cn("relative", className)}>
      <button
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => !disabled && setOpen((v) => !v)}
        className={cn(
          "pm-select-trigger flex w-full items-center justify-between gap-2",
          size === "sm" ? "pm-select-trigger--sm" : "pm-select-trigger--md",
          (open || shown) && "is-open",
          disabled && "is-disabled"
        )}
      >
        <span
          className={cn(
            "min-w-0 truncate text-left",
            value ? "text-[var(--pm-text)]" : "text-[var(--pm-faint)]"
          )}
        >
          {selectedLabel}
        </span>
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-[var(--pm-faint)] pm-select-chev",
            (open || shown) && "is-open"
          )}
        />
      </button>

      {mounted && (
        <div
          role="listbox"
          className={cn("pm-select-menu absolute z-50 mt-1 w-full max-h-60 overflow-y-auto", shown && "is-open")}
        >
          {options.length === 0 ? (
            <div className="pm-select-opt is-empty">No options</div>
          ) : (
            options.map((opt) => {
              const on = opt.value === value
              return (
                <button
                  key={opt.value}
                  type="button"
                  role="option"
                  aria-selected={on}
                  onClick={() => {
                    onChange(opt.value)
                    close()
                  }}
                  className={cn("pm-select-opt", on && "is-on")}
                >
                  <span
                    className={cn("pm-select-dot shrink-0", on && "is-on")}
                    aria-hidden
                  />
                  <span className="min-w-0 truncate">{opt.label}</span>
                </button>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}
