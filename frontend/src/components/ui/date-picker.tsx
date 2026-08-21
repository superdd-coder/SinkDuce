/**
 * Premium DatePicker — soft float calendar (no native OS picker / system blue).
 * Value is yyyy-mm-dd or "". Open/close uses the same soft menu clock as DropdownSelect.
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react"
import { createPortal } from "react-dom"
import { Calendar, ChevronLeft, ChevronRight, X } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { useT } from "@/i18n/use-t"
import { useAppStore } from "@/stores/app-store"

const PANEL_MS = 180

function weekdayLabels(locale: string): string[] {
  const fmt = new Intl.DateTimeFormat(locale, { weekday: "narrow" })
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(2021, 7, 1 + i) // 2021-08-01 is a Sunday
    return fmt.format(d)
  })
}

export interface DatePickerProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  disabled?: boolean
  id?: string
  className?: string
  /** Show clear control when a date is set (default true) */
  allowClear?: boolean
  /** default — form fields; sm — dense chrome (timeline chips, compact dialogs) */
  size?: "default" | "sm"
}

type PanelPos = { top: number; left: number; width: number }

function parseYmd(s: string): Date | null {
  if (!s || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return null
  const [y, m, d] = s.split("-").map(Number)
  const dt = new Date(y, m - 1, d)
  if (
    dt.getFullYear() !== y ||
    dt.getMonth() !== m - 1 ||
    dt.getDate() !== d
  ) {
    return null
  }
  return dt
}

function formatYmd(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${y}-${m}-${day}`
}

function formatDisplay(s: string, locale: string): string {
  const d = parseYmd(s)
  if (!d) return ""
  return d.toLocaleDateString(locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}

export function DatePicker({
  value,
  onChange,
  placeholder,
  disabled = false,
  id,
  className,
  allowClear = true,
  size = "default",
}: DatePickerProps) {
  const t = useT()
  const locale = useAppStore((s) => s.locale)
  const [open, setOpen] = useState(false)
  const [mounted, setMounted] = useState(false)
  const [shown, setShown] = useState(false)
  const [panelPos, setPanelPos] = useState<PanelPos | null>(null)
  const selected = useMemo(() => parseYmd(value), [value])
  const today = useMemo(() => {
    const t = new Date()
    return new Date(t.getFullYear(), t.getMonth(), t.getDate())
  }, [])
  const [viewYear, setViewYear] = useState(
    () => selected?.getFullYear() ?? today.getFullYear()
  )
  const [viewMonth, setViewMonth] = useState(
    () => selected?.getMonth() ?? today.getMonth()
  )

  const containerRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  const close = useCallback(() => setOpen(false), [])

  const updatePanelPos = useCallback(() => {
    const trigger = triggerRef.current
    if (!trigger) return
    const rect = trigger.getBoundingClientRect()
    /* Compact panel — avoid spanning full form width when trigger is wide */
    const minW = size === "sm" ? 220 : 236
    const width = Math.min(Math.max(minW, Math.min(rect.width, 280)), 280)
    const gap = 6
    const pad = 8
    const panelH = size === "sm" ? 268 : 286
    let top = rect.bottom + gap
    let left = rect.left

    left = Math.max(pad, Math.min(left, window.innerWidth - width - pad))
    const spaceBelow = window.innerHeight - rect.bottom - gap - pad
    const spaceAbove = rect.top - gap - pad
    if (spaceBelow < panelH && spaceAbove > spaceBelow) {
      top = Math.max(pad, rect.top - gap - panelH)
    }
    setPanelPos({ top, left, width })
  }, [size])

  useEffect(() => {
    let exitTimer: ReturnType<typeof setTimeout> | null = null
    let raf1 = 0
    let raf2 = 0
    if (open) {
      setMounted(true)
      if (selected) {
        setViewYear(selected.getFullYear())
        setViewMonth(selected.getMonth())
      }
      raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => setShown(true))
      })
    } else {
      setShown(false)
      exitTimer = setTimeout(() => {
        setMounted(false)
        setPanelPos(null)
      }, PANEL_MS)
    }
    return () => {
      if (exitTimer) clearTimeout(exitTimer)
      if (raf1) cancelAnimationFrame(raf1)
      if (raf2) cancelAnimationFrame(raf2)
    }
  }, [open, selected])

  useLayoutEffect(() => {
    if (!open && !mounted) return
    updatePanelPos()
    if (!open) return
    const onReposition = () => updatePanelPos()
    window.addEventListener("resize", onReposition)
    window.addEventListener("scroll", onReposition, true)
    return () => {
      window.removeEventListener("resize", onReposition)
      window.removeEventListener("scroll", onReposition, true)
    }
  }, [open, mounted, updatePanelPos])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const t = e.target as Node
      if (containerRef.current?.contains(t)) return
      if (panelRef.current?.contains(t)) return
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

  const cells = useMemo(() => {
    const first = new Date(viewYear, viewMonth, 1)
    const startPad = first.getDay()
    const days = new Date(viewYear, viewMonth + 1, 0).getDate()
    const prevDays = new Date(viewYear, viewMonth, 0).getDate()
    const out: { date: Date; inMonth: boolean }[] = []
    for (let i = startPad - 1; i >= 0; i--) {
      out.push({
        date: new Date(viewYear, viewMonth - 1, prevDays - i),
        inMonth: false,
      })
    }
    for (let d = 1; d <= days; d++) {
      out.push({ date: new Date(viewYear, viewMonth, d), inMonth: true })
    }
    while (out.length % 7 !== 0 || out.length < 42) {
      const n = out.length - (startPad + days) + 1
      out.push({
        date: new Date(viewYear, viewMonth + 1, n),
        inMonth: false,
      })
    }
    return out.slice(0, 42)
  }, [viewYear, viewMonth])

  const shiftMonth = (delta: number) => {
    const d = new Date(viewYear, viewMonth + delta, 1)
    setViewYear(d.getFullYear())
    setViewMonth(d.getMonth())
  }

  const pick = (d: Date) => {
    onChange(formatYmd(d))
    close()
  }

  const clear = (e: ReactMouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    onChange("")
  }

  const weekdays = useMemo(() => weekdayLabels(locale), [locale])
  const monthTitle = useMemo(
    () =>
      new Date(viewYear, viewMonth, 1).toLocaleDateString(locale, {
        month: "long",
        year: "numeric",
      }),
    [locale, viewYear, viewMonth],
  )
  const label = selected ? formatDisplay(value, locale) : (placeholder ?? t("common.optional"))

  const panel = mounted
    ? createPortal(
        <div
          ref={panelRef}
          role="dialog"
          aria-modal="false"
          aria-label={t("common.chooseDate")}
          className={cn(
            "pm-date-panel",
            size === "sm" && "pm-date-panel--sm",
            shown && "is-open"
          )}
          style={
            panelPos
              ? {
                  top: panelPos.top,
                  left: panelPos.left,
                  width: panelPos.width,
                }
              : undefined
          }
        >
          <div className="pm-date-panel-head">
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              className="pm-date-nav"
              aria-label={t("common.prevMonth")}
              onClick={() => shiftMonth(-1)}
            >
              <ChevronLeft className="h-3.5 w-3.5" strokeWidth={1.75} />
            </Button>
            <span className="pm-date-panel-title">
              {monthTitle}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              className="pm-date-nav"
              aria-label={t("common.nextMonth")}
              onClick={() => shiftMonth(1)}
            >
              <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.75} />
            </Button>
          </div>

          <div className="pm-date-weekdays" aria-hidden>
            {weekdays.map((w, i) => (
              <span key={i} className="pm-date-weekday">
                {w}
              </span>
            ))}
          </div>

          <div className="pm-date-grid" role="grid">
            {cells.map(({ date, inMonth }) => {
              const isSelected = !!selected && sameDay(date, selected)
              const isToday = sameDay(date, today)
              return (
                <button
                  key={formatYmd(date) + (inMonth ? "" : "-o")}
                  type="button"
                  role="gridcell"
                  aria-selected={isSelected}
                  disabled={!inMonth}
                  onClick={() => inMonth && pick(date)}
                  className={cn(
                    "pm-date-day",
                    !inMonth && "is-muted",
                    isToday && "is-today",
                    isSelected && "is-on"
                  )}
                >
                  {date.getDate()}
                </button>
              )
            })}
          </div>

          <div className="pm-date-panel-foot">
            <button
              type="button"
              className="pm-date-today-link"
              onClick={() => pick(today)}
            >
              {t("common.today")}
            </button>
            {allowClear && value && (
              <button
                type="button"
                className="pm-date-clear-link"
                onClick={() => {
                  onChange("")
                  close()
                }}
              >
                {t("common.clear")}
              </button>
            )}
          </div>
        </div>,
        document.body
      )
    : null

  return (
    <div
      ref={containerRef}
      className={cn(
        "pm-date-picker",
        size === "sm" && "pm-date-picker--sm",
        className
      )}
    >
      <button
        ref={triggerRef}
        id={id}
        type="button"
        disabled={disabled}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          if (disabled) return
          setOpen((v) => !v)
        }}
        className={cn(
          "pm-date-trigger",
          size === "sm" && "pm-date-trigger--sm",
          open && "is-open",
          !selected && "is-empty",
          disabled && "is-disabled"
        )}
      >
        <Calendar
          className="pm-date-trigger-icon"
          strokeWidth={1.75}
          aria-hidden
        />
        <span className="pm-date-trigger-label">{label}</span>
        {allowClear && selected && !disabled && (
          <span
            role="button"
            tabIndex={-1}
            className="pm-date-trigger-clear"
            title={t("common.clearDate")}
            aria-label={t("common.clearDate")}
            onClick={clear}
            onKeyDown={(e: ReactKeyboardEvent) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault()
                onChange("")
              }
            }}
          >
            <X className="h-3.5 w-3.5" strokeWidth={1.75} />
          </span>
        )}
      </button>
      {panel}
    </div>
  )
}
