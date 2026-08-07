import {
  useState,
  useRef,
  useEffect,
  useLayoutEffect,
  useCallback,
} from "react"
import { createPortal } from "react-dom"
import { ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"

interface DropdownSelectProps {
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
  placeholder?: string
  disabled?: boolean
  className?: string
  /**
   * default — form field
   * sm — dense form
   * tag — pill chrome (Node group tag editor)
   */
  size?: "default" | "sm" | "tag"
}

/** Open/close duration — keep in sync with CSS `--pm-select-ms` */
const SELECT_MS = 180

type MenuPos = { top: number; left: number; width: number; maxHeight: number }

/**
 * Soft float select — Premium green-wash selection (no native OS blue).
 * Menu enter/exit: opacity + soft scale/slide, open/close same duration.
 * Tag size portals the menu so accordion overflow cannot clip it.
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
  const [menuPos, setMenuPos] = useState<MenuPos | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const isTag = size === "tag"

  const close = useCallback(() => {
    setOpen(false)
  }, [])

  const updateMenuPos = useCallback(() => {
    const trigger = triggerRef.current
    if (!trigger) return
    const rect = trigger.getBoundingClientRect()
    const width = Math.max(11.5 * 16, rect.width)
    const gap = 6
    const pad = 8
    const maxH = 240 /* max-h-60 */
    let top = rect.bottom + gap
    let left = rect.right - width

    /* Keep in viewport */
    left = Math.max(pad, Math.min(left, window.innerWidth - width - pad))
    const spaceBelow = window.innerHeight - rect.bottom - gap - pad
    const spaceAbove = rect.top - gap - pad
    let maxHeight = maxH
    if (spaceBelow < 120 && spaceAbove > spaceBelow) {
      maxHeight = Math.min(maxH, Math.max(80, spaceAbove))
      top = rect.top - gap - maxHeight
    } else {
      maxHeight = Math.min(maxH, Math.max(80, spaceBelow))
    }

    setMenuPos({ top, left, width, maxHeight })
  }, [])

  // Drive mount → shown (enter) / shown → unmount (exit).
  useEffect(() => {
    let exitTimer: ReturnType<typeof setTimeout> | null = null
    let raf1 = 0
    let raf2 = 0

    if (open) {
      setMounted(true)
      raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => {
          setShown(true)
        })
      })
    } else {
      setShown(false)
      exitTimer = setTimeout(() => {
        setMounted(false)
        setMenuPos(null)
      }, SELECT_MS)
    }

    return () => {
      if (exitTimer) clearTimeout(exitTimer)
      if (raf1) cancelAnimationFrame(raf1)
      if (raf2) cancelAnimationFrame(raf2)
    }
  }, [open])

  /* Tag menu: measure trigger → fixed coords (escapes overflow:hidden pads) */
  useLayoutEffect(() => {
    if (!isTag || (!open && !mounted)) return
    updateMenuPos()
    if (!open) return
    const onReposition = () => updateMenuPos()
    window.addEventListener("resize", onReposition)
    /* capture scroll from accordion / rail ancestors */
    window.addEventListener("scroll", onReposition, true)
    return () => {
      window.removeEventListener("resize", onReposition)
      window.removeEventListener("scroll", onReposition, true)
    }
  }, [open, mounted, isTag, updateMenuPos, options.length])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const t = e.target as Node
      if (containerRef.current?.contains(t)) return
      if (menuRef.current?.contains(t)) return
      close()
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

  const menu = mounted ? (
    <div
      ref={menuRef}
      role="listbox"
      className={cn(
        "pm-select-menu max-h-60 overflow-y-auto",
        isTag
          ? "pm-select-menu--tag pm-select-menu--fixed"
          : "absolute z-50 mt-1 w-full",
        shown && "is-open"
      )}
      style={
        isTag && menuPos
          ? {
              top: menuPos.top,
              left: menuPos.left,
              width: menuPos.width,
              maxHeight: menuPos.maxHeight,
            }
          : undefined
      }
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
              <span className="pm-select-opt-label min-w-0 truncate">
                {opt.label}
              </span>
            </button>
          )
        })
      )}
    </div>
  ) : null

  return (
    <div
      ref={containerRef}
      className={cn("relative", isTag && "pm-select--tag", className)}
    >
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => !disabled && setOpen((v) => !v)}
        className={cn(
          "pm-select-trigger flex w-full items-center gap-1.5",
          isTag ? "justify-center" : "justify-between",
          size === "sm" && "pm-select-trigger--sm",
          size === "default" && "pm-select-trigger--md",
          isTag && "pm-select-trigger--tag",
          (open || shown) && "is-open",
          disabled && "is-disabled"
        )}
      >
        <span
          className={cn(
            "pm-select-trigger-label min-w-0 truncate",
            isTag ? "text-center" : "text-left",
            !value && "is-placeholder"
          )}
        >
          {selectedLabel}
        </span>
        {/* Tag pills match static .pm-node-tag — no chevron */}
        {!isTag && (
          <ChevronDown
            className={cn(
              "pm-select-chev shrink-0 h-3.5 w-3.5",
              (open || shown) && "is-open"
            )}
            strokeWidth={1.75}
          />
        )}
      </button>

      {isTag
        ? typeof document !== "undefined" && menu && menuPos
          ? createPortal(menu, document.body)
          : null
        : menu}
    </div>
  )
}
