/**
 * Chain-end smart todo suggestion pill.
 *
 * Rotate cycle (idle, 2+ items):
 *   expand L→R + type text → hold → collapse R→L (width, text stays) → gap → next
 * Width hugs text. Hover (2+): fans up/down, carousel paused.
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { ListTodo } from "lucide-react"
import { cn } from "@/lib/utils"
import { getChainTodoSuggestions } from "@/api/file-mgmt"
import type { TodoSuggestionItem } from "@/types/file-mgmt"
import { useT } from "@/i18n/use-t"

/** Open hold: 2.5–4s random */
const HOLD_MS_MIN = 2500
const HOLD_MS_MAX = 4000
/** Collapsed gap before next expand: 1–2s random */
const GAP_MS_MIN = 1000
const GAP_MS_MAX = 2000
const EXPAND_MS = 720
const COLLAPSE_MS = 480
const POLL_BUSY_MS = 3000
const POLL_IDLE_MS = 12000
const STACK_STEP = 40

type Phase = "expand" | "hold" | "collapse" | "gap"

interface TodoSuggestBubbleProps {
  collectionId: string
  chainId: string
  refreshKey?: number
  onPick: (item: TodoSuggestionItem) => void
  className?: string
}

function itemsKey(items: TodoSuggestionItem[]): string {
  return items.map((i) => `${i.suggestion_id}:${i.title}`).join("|")
}

function randMs(min: number, max: number): number {
  return min + Math.random() * (max - min)
}

export function TodoSuggestBubble({
  collectionId,
  chainId,
  refreshKey = 0,
  onPick,
  className,
}: TodoSuggestBubbleProps) {
  const t = useT()
  const [items, setItems] = useState<TodoSuggestionItem[]>([])
  const [idx, setIdx] = useState(0)
  const [hover, setHover] = useState(false)
  const [phase, setPhase] = useState<Phase>("expand")
  const [typed, setTyped] = useState("")
  /** Explicit width during collapse (px); null = content-sized */
  const [clipWidth, setClipWidth] = useState<number | null>(null)
  const itemsRef = useRef(items)
  itemsRef.current = items
  const resumeHoldRef = useRef(false)
  const activeClipRef = useRef<HTMLSpanElement | null>(null)

  useEffect(() => {
    if (!collectionId || !chainId) return
    let cancelled = false
    let pollTimer: ReturnType<typeof setTimeout> | null = null

    const load = async () => {
      try {
        const res = await getChainTodoSuggestions(collectionId, chainId)
        if (cancelled) return
        const next = res.suggestions || []
        const busy =
          res.status === "pending" || res.status === "generating"
        const display =
          res.status === "ready" || res.status === "idle"
            ? next
            : itemsRef.current

        if (itemsKey(display) !== itemsKey(itemsRef.current)) {
          setItems(display)
          setIdx(0)
          setPhase("expand")
          setTyped("")
          setClipWidth(null)
          resumeHoldRef.current = false
        } else if (!busy && next.length === 0 && itemsRef.current.length > 0) {
          if (res.status === "ready" || res.status === "idle") {
            setItems([])
            setIdx(0)
            setTyped("")
            setClipWidth(null)
          }
        } else if (!busy && res.status === "ready") {
          if (JSON.stringify(next) !== JSON.stringify(itemsRef.current)) {
            setItems(next)
          }
        }

        pollTimer = setTimeout(load, busy ? POLL_BUSY_MS : POLL_IDLE_MS)
      } catch {
        if (!cancelled) {
          pollTimer = setTimeout(load, POLL_IDLE_MS)
        }
      }
    }

    void load()
    return () => {
      cancelled = true
      if (pollTimer) clearTimeout(pollTimer)
    }
  }, [collectionId, chainId, refreshKey])

  // Typewriter during expand; full title on hold / collapse / hover
  useEffect(() => {
    if (items.length === 0) return
    const full = items[idx]?.title || ""

    if (hover || phase === "hold" || phase === "collapse") {
      setTyped(full)
      return
    }
    if (phase === "gap") {
      return
    }
    // expand — type out
    setTyped("")
    if (!full) return

    let i = 0
    const step = Math.max(28, Math.min(55, EXPAND_MS / Math.max(full.length, 1)))
    const t = setInterval(() => {
      i += 1
      setTyped(full.slice(0, i))
      if (i >= full.length) clearInterval(t)
    }, step)
    return () => clearInterval(t)
  }, [items, idx, phase, hover])

  // Collapse: lock current pixel width, then animate width → 0 (text stays inside)
  useLayoutEffect(() => {
    if (hover || items.length <= 1) {
      setClipWidth(null)
      return
    }
    if (phase === "expand" || phase === "hold") {
      setClipWidth(null)
      return
    }
    if (phase === "gap") {
      setClipWidth(0)
      return
    }
    if (phase !== "collapse") return

    const el = activeClipRef.current
    if (!el) return
    // Pin current pixel width, then animate to 0 so glass + text shrink together
    const w = Math.ceil(el.scrollWidth)
    setClipWidth(w)
    const id = requestAnimationFrame(() => {
      requestAnimationFrame(() => setClipWidth(0))
    })
    return () => cancelAnimationFrame(id)
  }, [phase, hover, items.length, idx])

  // expand → hold → collapse → gap → next
  useEffect(() => {
    if (items.length === 0) return
    if (items.length === 1 || hover) {
      setPhase("hold")
      setClipWidth(null)
      return
    }

    let cancelled = false
    const timers: ReturnType<typeof setTimeout>[] = []
    const later = (fn: () => void, ms: number) => {
      timers.push(setTimeout(fn, ms))
    }

    const afterHold = () => {
      if (cancelled) return
      setPhase("collapse")
      later(() => {
        if (cancelled) return
        setPhase("gap")
        later(() => {
          if (cancelled) return
          setIdx((i) => (i + 1) % items.length)
          setTyped("")
          setClipWidth(null)
          beginExpand()
        }, randMs(GAP_MS_MIN, GAP_MS_MAX))
      }, COLLAPSE_MS)
    }

    const beginHold = () => {
      if (cancelled) return
      setPhase("hold")
      later(afterHold, randMs(HOLD_MS_MIN, HOLD_MS_MAX))
    }

    const beginExpand = () => {
      if (cancelled) return
      setPhase("expand")
      later(beginHold, EXPAND_MS)
    }

    if (resumeHoldRef.current) {
      resumeHoldRef.current = false
      beginHold()
    } else {
      beginExpand()
    }

    return () => {
      cancelled = true
      timers.forEach(clearTimeout)
    }
  }, [items, hover])

  if (items.length === 0) return null

  const n = items.length
  const center = hover ? (n - 1) / 2 : idx

  return (
    <div
      className={cn(
        "pm-todo-suggest-host",
        hover && n > 1 && "is-open",
        className
      )}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => {
        resumeHoldRef.current = true
        setHover(false)
      }}
    >
      <div className="pm-todo-suggest-stack" aria-label={t("fileMgmt.todoSuggestions")}>
        {items.map((item, i) => {
          const isActive = i === idx
          const offset = hover && n > 1 ? (i - center) * STACK_STEP : 0
          const visible = hover || isActive
          // Keep full title during collapse so text + pill shrink together
          const label =
            hover || !isActive
              ? item.title
              : phase === "gap"
                ? item.title
                : phase === "collapse" || phase === "hold"
                  ? item.title
                  : typed

          const animating =
            !hover && isActive && n > 1 && (phase === "collapse" || phase === "gap")

          return (
            <button
              key={item.suggestion_id}
              type="button"
              tabIndex={-1}
              className={cn(
                "pm-todo-suggest-bubble",
                isActive && "is-active",
                !visible && "is-collapsed",
                hover && n > 1 && "is-fanned"
              )}
              style={{
                transform: visible
                  ? `translateY(${offset}px)`
                  : "translateY(0)",
                zIndex: visible
                  ? 10 + (hover ? n - Math.abs(i - center) : 1)
                  : 0,
                transitionDelay:
                  hover && n > 1
                    ? `${Math.abs(i - center) * 24}ms`
                    : "0ms",
              }}
              title={
                item.body
                  ? `${item.title}\n${item.body}`
                  : item.title || "Smart todo suggestion"
              }
              onMouseDown={(e) => e.preventDefault()}
              onClick={(e) => {
                e.stopPropagation()
                onPick(item)
              }}
            >
              <span
                className={cn(
                  "pm-todo-suggest-bubble-shell",
                  !hover && isActive && n > 1 && phase === "gap" && "is-gap"
                )}
              >
                <span
                  ref={isActive ? activeClipRef : undefined}
                  className={cn(
                    "pm-todo-suggest-bubble-clip",
                    !hover &&
                      isActive &&
                      n > 1 &&
                      phase === "expand" &&
                      "is-expand",
                    (!hover && isActive && n > 1 && phase === "hold") ||
                      hover ||
                      n === 1
                      ? "is-hold"
                      : null,
                    !hover &&
                      isActive &&
                      n > 1 &&
                      phase === "collapse" &&
                      "is-collapse",
                    !hover &&
                      isActive &&
                      n > 1 &&
                      phase === "gap" &&
                      "is-gap"
                  )}
                  style={
                    animating && clipWidth !== null
                      ? {
                          width: clipWidth,
                          maxWidth: clipWidth,
                        }
                      : undefined
                  }
                >
                  <span className="pm-todo-suggest-bubble-inner">
                    <span className="pm-todo-suggest-bubble-icon" aria-hidden>
                      <ListTodo className="h-3.5 w-3.5" strokeWidth={1.75} />
                    </span>
                    <span className="pm-todo-suggest-bubble-text">
                      {label}
                      {!hover &&
                      isActive &&
                      n > 1 &&
                      phase === "expand" &&
                      typed.length < (item.title?.length || 0) ? (
                        <span className="pm-todo-suggest-caret" aria-hidden />
                      ) : null}
                    </span>
                  </span>
                </span>
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
