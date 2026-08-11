import {
  useState,
  useRef,
  useEffect,
  useLayoutEffect,
  useCallback,
  useMemo,
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

function optionMatches(opt: DropdownSelectOption, query: string): boolean {
  if (!query) return true
  const q = query.trim().toLowerCase()
  if (!q) return true
  /* Prefix match — type "zh" → zh, not anything containing zh mid-string */
  return (
    opt.label.toLowerCase().startsWith(q) ||
    opt.value.toLowerCase().startsWith(q)
  )
}

/**
 * Soft float select — Premium green-wash selection (no native OS blue).
 * Menu always portals to document.body (fixed) so dialog/card overflow
 * cannot clip it (todo Chain, node forms, rails, etc.).
 * While open: type to filter options; ↑↓ navigate; Enter select; Esc clear/close.
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
  /** Keyboard type-to-filter while menu is open */
  const [filterQuery, setFilterQuery] = useState("")
  const [highlightIndex, setHighlightIndex] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const isTag = size === "tag"

  const filteredOptions = useMemo(
    () => options.filter((o) => optionMatches(o, filterQuery)),
    [options, filterQuery]
  )

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
      setFilterQuery("")
      setHighlightIndex(-1)
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

  /* When open / filter changes, pin highlight to selected match or first row */
  useEffect(() => {
    if (!open) return
    if (filteredOptions.length === 0) {
      setHighlightIndex(-1)
      return
    }
    const selectedIdx = filteredOptions.findIndex((o) => o.value === value)
    setHighlightIndex(selectedIdx >= 0 ? selectedIdx : 0)
  }, [filterQuery, open, value, filteredOptions.length])

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
  }, [open, mounted, updateMenuPos, options.length, filterQuery])

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

  /* Keyboard: Esc / type-to-filter / arrows / Enter while open */
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      /* Ignore when user is typing in a real input (shouldn't steal focus) */
      const target = e.target as HTMLElement | null
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        /* Still allow Escape to close the select */
        if (e.key === "Escape") {
          e.preventDefault()
          close()
        }
        return
      }

      if (e.key === "Escape") {
        e.preventDefault()
        if (filterQuery) {
          setFilterQuery("")
          return
        }
        close()
        return
      }

      if (e.key === "ArrowDown") {
        e.preventDefault()
        if (filteredOptions.length === 0) return
        setHighlightIndex((i) =>
          i < 0 ? 0 : Math.min(filteredOptions.length - 1, i + 1)
        )
        return
      }

      if (e.key === "ArrowUp") {
        e.preventDefault()
        if (filteredOptions.length === 0) return
        setHighlightIndex((i) =>
          i < 0 ? filteredOptions.length - 1 : Math.max(0, i - 1)
        )
        return
      }

      if (e.key === "Enter" || e.key === " ") {
        if (e.key === " " && filterQuery) {
          /* Space while filtering becomes part of the query */
          e.preventDefault()
          setFilterQuery((q) => q + " ")
          return
        }
        if (highlightIndex >= 0 && highlightIndex < filteredOptions.length) {
          e.preventDefault()
          onChange(filteredOptions[highlightIndex].value)
          close()
        }
        return
      }

      if (e.key === "Backspace") {
        if (!filterQuery) return
        e.preventDefault()
        setFilterQuery((q) => q.slice(0, -1))
        return
      }

      /* Printable character → append to filter (ignore modifiers) */
      if (
        e.key.length === 1 &&
        !e.ctrlKey &&
        !e.metaKey &&
        !e.altKey
      ) {
        e.preventDefault()
        setFilterQuery((q) => q + e.key)
      }
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [
    open,
    close,
    filterQuery,
    filteredOptions,
    highlightIndex,
    onChange,
  ])

  /* Scroll highlighted option into view */
  useEffect(() => {
    if (!open || highlightIndex < 0) return
    const menu = menuRef.current
    if (!menu) return
    const el = menu.querySelector<HTMLElement>(
      `[data-opt-index="${highlightIndex}"]`
    )
    el?.scrollIntoView({ block: "nearest" })
  }, [highlightIndex, open, filteredOptions])

  const selected = options.find((o) => o.value === value)
  const selectedLabel = selected?.label || placeholder || "Select..."
  const selectedHasMark = !!selected?.indicator
  const hasFilter = filterQuery.trim().length > 0

  const menu =
    mounted && menuPos ? (
      <div
        ref={menuRef}
        role="listbox"
        aria-activedescendant={
          highlightIndex >= 0
            ? `pm-select-opt-${highlightIndex}`
            : undefined
        }
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
        {hasFilter ? (
          <div
            className="pm-select-filter sticky top-0 z-[1] px-2.5 py-1.5 text-[11px] text-muted-foreground border-b border-border/40 bg-popover/95 backdrop-blur-sm truncate"
            aria-live="polite"
          >
            <span className="opacity-60">Filter · </span>
            <span className="font-medium text-foreground/80">
              {filterQuery}
            </span>
          </div>
        ) : null}
        {options.length === 0 ? (
          <div className="pm-select-opt is-empty">No options</div>
        ) : filteredOptions.length === 0 ? (
          <div className="pm-select-opt is-empty">No matches</div>
        ) : (
          filteredOptions.map((opt, idx) => {
            const on = opt.value === value
            const hi = idx === highlightIndex
            return (
              <button
                key={opt.value}
                id={`pm-select-opt-${idx}`}
                data-opt-index={idx}
                type="button"
                role="option"
                aria-selected={on}
                onMouseEnter={() => setHighlightIndex(idx)}
                onClick={() => {
                  onChange(opt.value)
                  close()
                }}
                className={cn(
                  "pm-select-opt",
                  on && "is-on",
                  hi && "is-highlight"
                )}
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
