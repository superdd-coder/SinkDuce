import { useState, useRef, useEffect, useCallback } from "react"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { Check } from "lucide-react"
import { useT } from "@/i18n/use-t"

interface ComboboxProps {
  value: string
  onChange: (value: string) => void
  options: string[]
  placeholder?: string
  disabled?: boolean
  className?: string
}

/**
 * Premium Combobox — Input + soft menu (same language as DropdownSelect).
 */
export function Combobox({
  value,
  onChange,
  options,
  placeholder,
  disabled,
  className,
}: ComboboxProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [highlightIdx, setHighlightIdx] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)

  const filtered = options.filter((o) =>
    o.toLowerCase().includes((value || "").toLowerCase())
  )

  const select = useCallback(
    (v: string) => {
      onChange(v)
      setOpen(false)
      setHighlightIdx(-1)
    },
    [onChange]
  )

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
        setHighlightIdx(-1)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        setOpen(true)
        e.preventDefault()
      }
      return
    }
    if (e.key === "Escape") {
      setOpen(false)
      setHighlightIdx(-1)
      return
    }
    if (e.key === "Enter") {
      e.preventDefault()
      if (highlightIdx >= 0 && highlightIdx < filtered.length) {
        select(filtered[highlightIdx])
      } else {
        setOpen(false)
        setHighlightIdx(-1)
      }
      return
    }
    if (e.key === "ArrowDown") {
      e.preventDefault()
      setHighlightIdx((p) => (p + 1 >= filtered.length ? 0 : p + 1))
      return
    }
    if (e.key === "ArrowUp") {
      e.preventDefault()
      setHighlightIdx((p) => (p - 1 < 0 ? filtered.length - 1 : p - 1))
      return
    }
  }

  return (
    <div ref={containerRef} className={cn("relative", className)}>
      <Input
        value={value}
        onChange={(e) => {
          onChange(e.target.value)
          setOpen(true)
          setHighlightIdx(-1)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        autoComplete="off"
      />
      {open && (
        <div
          role="listbox"
          className="pm-menu absolute z-50 mt-1 w-full max-h-48 overflow-y-auto"
        >
          {options.length === 0 ? (
            <div className="pm-menu-item is-empty pointer-events-none">
              {t("common.noOptions")}
            </div>
          ) : filtered.length === 0 ? (
            <div className="pm-menu-item is-empty pointer-events-none">
              {t("common.noMatches")}
            </div>
          ) : (
            filtered.map((opt, i) => {
              const on = opt === value
              return (
                <button
                  key={opt}
                  type="button"
                  role="option"
                  aria-selected={on}
                  className={cn(
                    "pm-menu-item",
                    on && "is-on",
                    i === highlightIdx && !on && "bg-[var(--pm-green-wash)]"
                  )}
                  onMouseDown={(e) => {
                    e.preventDefault()
                    select(opt)
                  }}
                  onMouseEnter={() => setHighlightIdx(i)}
                >
                  <span className="flex-1 text-left truncate">{opt}</span>
                  {on && (
                    <Check className="h-3.5 w-3.5 shrink-0 text-[var(--pm-green)]" />
                  )}
                </button>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}
