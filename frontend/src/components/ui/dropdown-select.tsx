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

export type DropdownSelectOption = {
  value: string
  label: string
  /**
   * Visual mark only (e.g. green status dot) — no text badge.
   * Used by Recall Evaluate for collections that already have test cases.
   */
  indicator?: boolean
}

interface DropdownSelectProps {
  value: string
  onChange: (value: string) => void
  options: DropdownSelectOption[]
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
 * Menu always portals to document.body (fixed) so dialog/card overflow
 * cannot clip it (todo Chain, node forms, rails, etc.).
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
    /* Tag: min width; form: match trigger, never thinner than 10rem */
    const width = isTag
      ? Math.max(11.5 * 16, rect.width)
      : Math.max(rect.width, 10 * 16)
    const gap = 6
    const pad = 8
    const maxH = 240 /* max-h-60 */
    let top = rect.bottom + gap
    /* Tag aligns to trigger end; form fields open flush-left under trigger */
    let left = isTag ? rect.right - width : rect.left

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
  }, [isTag])

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

  /* Fixed coords for all sizes — escapes overflow:hidden dialogs / cards */
  useLayoutEffect(() => {
    if (!open && !mounted) return
    updateMenuPos()
    if (!open) return
    const onReposition = () => updateMenuPos()
    window.addEventListener("resize", onReposition)
    window.addEventListener("scroll", onReposition, true)
    return () => {
      window.removeEventListener("resize", onReposition)
      window.removeEventListener("scroll", onReposition, true)
    }
  }, [open, mounted, updateMenuPos, options.length])

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

  const selected = options.find((o) => o.value === value)
  const selectedLabel = selected?.label || placeholder || "Select..."
  const selectedHasMark = !!selected?.indicator

  const menu =
    mounted && menuPos ? (
      <div
        ref={menuRef}
        role="listbox"
        className={cn(
          "pm-select-menu pm-select-menu--fixed max-h-60 overflow-y-auto",
          isTag && "pm-select-menu--tag",
          shown && "is-open"
        )}
        style={{
          top: menuPos.top,
          left: menuPos.left,
          width: menuPos.width,
          maxHeight: menuPos.maxHeight,
        }}
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
                {opt.indicator ? (
                  <span
                    className="pm-select-opt-mark"
                    title="Has test cases"
                    aria-label="Has test cases"
                  />
                ) : null}
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
        {selectedHasMark ? (
          <span
            className="pm-select-opt-mark shrink-0"
            title="Has test cases"
            aria-label="Has test cases"
          />
        ) : null}
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

      {typeof document !== "undefined" && menu
        ? createPortal(menu, document.body)
        : null}
    </div>
  )
}
