import { useState, useRef, useEffect, useCallback, useId } from "react"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { SoftMenu, MenuCheck } from "./menu"
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
  const menuId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
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
        !containerRef.current.contains(e.target as Node) &&
        !(e.target instanceof Element && e.target.closest(`[data-combobox="${menuId}"]`))
      ) {
        setOpen(false)
        setHighlightIdx(-1)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [menuId])

  useEffect(() => {
    if (!open || highlightIdx < 0) return
    const frame = requestAnimationFrame(() => document.getElementById(`${menuId}-${highlightIdx}`)?.scrollIntoView({ block: 'nearest' }))
    return () => cancelAnimationFrame(frame)
  }, [open, highlightIdx, menuId])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.nativeEvent.isComposing) return
    if (!open) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        setOpen(true)
        e.preventDefault()
      }
      return
    }
    if (e.key === "Escape") {
      e.preventDefault()
      e.stopPropagation()
      setOpen(false)
      setHighlightIdx(-1)
      return
    }
    if (e.key === "Tab") { setOpen(false); setHighlightIdx(-1); return }
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
        ref={inputRef}
        role="combobox" aria-expanded={open} aria-controls={`${menuId}-list`} aria-autocomplete="list"
        aria-activedescendant={open && highlightIdx >= 0 ? `${menuId}-${highlightIdx}` : undefined}
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
      <SoftMenu open={open && !disabled} portal anchorRef={inputRef} matchAnchorWidth onClose={() => setOpen(false)}
        data-combobox={menuId} role="listbox" id={`${menuId}-list`} className="sd-combobox-menu">
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
                  id={`${menuId}-${i}`}
                  type="button"
                  role="option"
                  aria-selected={on}
                  className={cn(
                    "pm-menu-item",
                    on && "is-on",
                    i === highlightIdx && "is-highlight"
                  )}
                  onMouseDown={(e) => {
                    e.preventDefault()
                  }}
                  onClick={() => select(opt)}
                  onMouseEnter={() => setHighlightIdx(i)}
                >
                  <span className="flex-1 text-left truncate">{opt}</span>
                  <MenuCheck checked={on} />
                </button>
              )
            })
          )}
      </SoftMenu>
    </div>
  )
}
