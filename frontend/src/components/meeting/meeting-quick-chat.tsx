import { useState, useRef, useEffect, useCallback, type ReactNode } from "react"
import { Send, Loader2, AlertTriangle, MessageCircle, BrushCleaning } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { parseMeetingRefGroups } from "@/lib/meeting-ref-chips"
import { useScrollEdgeFade } from "@/hooks/use-scroll-edge-fade"
import { createSession, getSession, deleteSession, iterateSessionSse, postSessionMessage } from "@/api/client"

// ── Types ──

interface QAMessage {
  id: string
  role: "user" | "assistant"
  content: string
  thinkingContent?: string
  sources?: { text: string; score: number; metadata: Record<string, unknown> }[]
  isStreaming?: boolean
  isNew?: boolean
}

const MEETING_SESSION_PREFIX = "meeting_"
const WARN_THRESHOLD = 20
const MAX_MESSAGES = 50 // align with backend _MEETING_MAX_MESSAGES
const ANIM_DURATION = 350
const SIDEBAR_W = 360

/** 1 user message = 1 round (request + reply). */
function countTurns(msgs: { role: string; content: string }[]): number {
  return msgs.filter((m) => m.role === "user").length
}

// ── Hint bubble config ──
const HINT_MESSAGES = [
  "Chat with this meeting?",
  "Ask a question?",
  "Quick Q&A?",
  "Got a question?",
  "Ask anything...",
  "Curious about the discussion?",
  "Ask about the meeting?",
]
const HINT_SHOW_DURATION = 4000
const HINT_INITIAL_DELAY = 1000
const HINT_MIN_INTERVAL = 5000
const HINT_MAX_INTERVAL = 15000

// ── Diamond icon (Collection-aligned rounded version) ──

function DiamondIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" className={className} fill="none" xmlns="http://www.w3.org/2000/svg">
      <path
        className="diamond-outer"
        d="M24 5.2C24.9 5.2 25.75 5.55 26.4 6.2L41.8 21.6C42.45 22.25 42.8 23.1 42.8 24C42.8 24.9 42.45 25.75 41.8 26.4L26.4 41.8C25.75 42.45 24.9 42.8 24 42.8C23.1 42.8 22.25 42.45 21.6 41.8L6.2 26.4C5.55 25.75 5.2 24.9 5.2 24C5.2 23.1 5.55 22.25 6.2 21.6L21.6 6.2C22.25 5.55 23.1 5.2 24 5.2Z"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinejoin="round"
        fill="none"
      />
      <path
        className="diamond-inner"
        d="M24 13.5C24.45 13.5 24.88 13.68 25.2 14L34 22.8C34.32 23.12 34.5 23.55 34.5 24C34.5 24.45 34.32 24.88 34 25.2L25.2 34C24.88 34.32 24.45 34.5 24 34.5C23.55 34.5 23.12 34.32 22.8 34L14 25.2C13.68 24.88 13.5 24.45 13.5 24C13.5 23.55 13.68 23.12 14 22.8L22.8 14C23.12 13.68 23.55 13.5 24 13.5Z"
        fill="currentColor"
      />
    </svg>
  )
}

/**
 * Spin phases (parent timeline):
 * idle → Collection CSS hover flip
 * exiting → WAAPI accel (visible then fade)
 * enter-hold → brief high-speed hold while fading in
 * enter-decel-top → ease-out into cruise speed
 * cruise → 6s linear infinite (same as Collection open spin)
 * enter-decel-bottom → ease-out to rest
 */
export type MeetingQcSpinPhase =
  | "idle"
  | "exiting"
  | "enter-hold"
  | "enter-decel-top"
  | "cruise"
  | "enter-decel-bottom"

/** Match Collection open spin (css qc-spin-cw 6s linear infinite) */
const CRUISE_PERIOD_MS = 6000
/** Exit accel window (opacity fade starts later — see parent) */
const EXIT_ACCEL_MS = 250
const ENTER_HOLD_MS = 160
const ENTER_DECEL_TOP_MS = 420
/** Longer taper so angular speed visibly shrinks toward stop */
const ENTER_DECEL_BOTTOM_MS = 520

function matrixRotationDeg(el: Element): number {
  const t = getComputedStyle(el).transform
  if (!t || t === "none") return 0
  try {
    const m = new DOMMatrixReadOnly(t)
    return (Math.atan2(m.b, m.a) * 180) / Math.PI
  } catch {
    return 0
  }
}

function unwrapAngle(prevContinuous: number, sampleDeg: number): number {
  const prevN = ((prevContinuous % 360) + 360) % 360
  const sampleN = ((sampleDeg % 360) + 360) % 360
  let d = sampleN - prevN
  if (d > 180) d -= 360
  if (d < -180) d += 360
  return prevContinuous + d
}

function cancelPathAnims(el: Element) {
  const node = el as HTMLElement | SVGElement
  node.getAnimations?.().forEach((a) => a.cancel())
  node.style.animation = ""
  node.style.transition = ""
}

/**
 * QC diamond FAB — Collection look + hover when idle;
 * WAAPI continuous spin for park teleport phases.
 */
export function MeetingQcFab({
  open,
  onOpen,
  onClose,
  spinPhase = "idle",
  className,
}: {
  open: boolean
  onOpen: () => void
  onClose: () => void
  spinPhase?: MeetingQcSpinPhase
  className?: string
}) {
  const [hintVisible, setHintVisible] = useState(false)
  const [hintExiting, setHintExiting] = useState(false)
  const [hintMessage, setHintMessage] = useState(HINT_MESSAGES[0])
  const hintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const angleRef = useRef({ outer: 0, inner: 0 })
  const phaseRef = useRef(spinPhase)
  phaseRef.current = spinPhase
  const idle = spinPhase === "idle"

  useEffect(() => {
    if (!idle) {
      setHintVisible(false)
      setHintExiting(false)
      if (hintTimerRef.current) clearTimeout(hintTimerRef.current)
      return
    }
    const scheduleHint = () => {
      const msg = HINT_MESSAGES[Math.floor(Math.random() * HINT_MESSAGES.length)]
      setHintMessage(msg)
      setHintVisible(true)
      setHintExiting(false)
      if (hintTimerRef.current) clearTimeout(hintTimerRef.current)
      hintTimerRef.current = setTimeout(() => {
        setHintExiting(true)
        hintTimerRef.current = setTimeout(() => {
          setHintVisible(false)
          setHintExiting(false)
          const delay = HINT_MIN_INTERVAL + Math.random() * (HINT_MAX_INTERVAL - HINT_MIN_INTERVAL)
          hintTimerRef.current = setTimeout(scheduleHint, delay)
        }, 500)
      }, HINT_SHOW_DURATION)
    }
    hintTimerRef.current = setTimeout(scheduleHint, HINT_INITIAL_DELAY)
    return () => {
      if (hintTimerRef.current) clearTimeout(hintTimerRef.current)
    }
  }, [idle])

  // WAAPI spin (continuous angles). Idle → CSS Collection hover owns transform.
  useEffect(() => {
    const btn = btnRef.current
    const outer = btn?.querySelector(".diamond-outer") as SVGElement | null
    const inner = btn?.querySelector(".diamond-inner") as SVGElement | null
    if (!outer || !inner) return

    const sample = (el: Element, key: "outer" | "inner") => {
      const next = unwrapAngle(angleRef.current[key], matrixRotationDeg(el))
      angleRef.current[key] = next
      return next
    }

    const animatePath = (el: Element, key: "outer" | "inner", dir: 1 | -1) => {
      const node = el as HTMLElement | SVGElement

      // Cruise already chained from enter-decel-top — do not cancel/restart (was the hitch)
      if (spinPhase === "cruise") {
        const cruising = node.getAnimations().some((a) => {
          try {
            return a.playState === "running" && a.effect?.getTiming()?.iterations === Infinity
          } catch {
            return false
          }
        })
        if (cruising) return
      }

      const from = sample(el, key)
      cancelPathAnims(el)
      // Pin visual at `from` so canceling fill:forwards never flashes to 0° before next WAAPI
      node.style.transform = `rotate(${from}deg)`

      if (spinPhase === "idle") {
        // Rest only after enter-decel-bottom landed on 90° grid (same look as 0°).
        // Never snap a random mid-angle → identity (that was the close-Chat hitch).
        const mod = ((from % 90) + 90) % 90
        if (mod < 1.5 || mod > 88.5) {
          node.style.transform = ""
          return
        }
        const rest =
          dir === 1
            ? Math.ceil((from + 1e-3) / 90) * 90
            : Math.floor((from - 1e-3) / 90) * 90
        const anim = node.animate(
          [{ transform: `rotate(${from}deg)` }, { transform: `rotate(${rest}deg)` }],
          { duration: 140, easing: "ease-out", fill: "forwards" },
        )
        anim.finished
          .then(() => {
            angleRef.current[key] = rest
            cancelPathAnims(el)
            // k×90° ≡ identity for this glyph
            node.style.transform = ""
          })
          .catch(() => {})
        return
      }

      const isInfiniteRunning = () =>
        node.getAnimations().some((a) => {
          try {
            const t = a.effect?.getTiming()
            return a.playState === "running" && t?.iterations === Infinity
          } catch {
            return false
          }
        })

      const startCruiseFrom = (startDeg: number) => {
        angleRef.current[key] = startDeg
        cancelPathAnims(el)
        node.style.transform = ""
        node.animate(
          [
            { transform: `rotate(${startDeg}deg)` },
            { transform: `rotate(${startDeg + dir * 360}deg)` },
          ],
          { duration: CRUISE_PERIOD_MS, iterations: Infinity, easing: "linear" },
        )
      }

      const run = (to: number, duration: number, easing: string, infinite = false) => {
        node.style.transform = ""
        const anim = node.animate(
          [{ transform: `rotate(${from}deg)` }, { transform: `rotate(${to}deg)` }],
          infinite
            ? { duration, iterations: Infinity, easing }
            : { duration, easing, fill: "forwards" },
        )
        if (!infinite) {
          anim.finished.then(() => { angleRef.current[key] = to }).catch(() => {})
        }
        return anim
      }

      /**
       * Multi-keyframe decelerate: equal time slices, shrinking Δθ → ω steps down.
       * Optional onDone chains without a frozen frame (used for → cruise).
       */
      const runTaperDecel = (
        rest: number,
        duration: number,
        shares: number[],
        onDone?: (restDeg: number) => void,
      ) => {
        const span = rest - from
        const frames: Keyframe[] = [{ transform: `rotate(${from}deg)`, offset: 0 }]
        let acc = 0
        const n = shares.length
        for (let i = 0; i < n; i++) {
          acc += shares[i]!
          frames.push({
            transform: `rotate(${from + span * Math.min(1, acc)}deg)`,
            offset: (i + 1) / n,
          })
        }
        frames[frames.length - 1] = { transform: `rotate(${rest}deg)`, offset: 1 }
        node.style.transform = ""
        const anim = node.animate(frames, {
          duration,
          easing: "linear",
          fill: "forwards",
        })
        anim.finished
          .then(() => {
            angleRef.current[key] = rest
            onDone?.(rest)
          })
          .catch(() => {})
        return anim
      }

      if (spinPhase === "exiting") {
        run(from + dir * 90, EXIT_ACCEL_MS, "cubic-bezier(0.35, 0, 0.65, 0.25)")
        return
      }

      if (spinPhase === "enter-hold") {
        run(from + dir * 55, ENTER_HOLD_MS, "linear")
        return
      }

      if (spinPhase === "enter-decel-top") {
        // Taper into cruise speed, then chain cruise immediately (no dead frame)
        // Last slice keeps a little motion so handoff isn't a full stop
        const rest = from + dir * 85
        runTaperDecel(rest, ENTER_DECEL_TOP_MS, [0.38, 0.28, 0.20, 0.14], (restDeg) => {
          const ph = phaseRef.current
          if (ph === "enter-decel-top" || ph === "cruise") {
            startCruiseFrom(restDeg)
          }
        })
        return
      }

      if (spinPhase === "cruise") {
        // Already chained from enter-decel-top — don't cancel/restart (that was the hitch)
        if (isInfiniteRunning()) return
        startCruiseFrom(from)
        return
      }

      if (spinPhase === "enter-decel-bottom") {
        let rest =
          dir === 1
            ? Math.ceil((from + 1e-3) / 90) * 90
            : Math.floor((from - 1e-3) / 90) * 90
        if (Math.abs(rest - from) < 20) rest = rest + dir * 90
        const anim = runTaperDecel(rest, ENTER_DECEL_BOTTOM_MS, [0.40, 0.28, 0.18, 0.10, 0.04])
        anim.finished
          .then(() => {
            if (phaseRef.current !== "idle" && phaseRef.current !== "enter-decel-bottom") return
            angleRef.current[key] = rest
            cancelPathAnims(el)
            node.style.transform = `rotate(${rest}deg)`
            requestAnimationFrame(() => {
              if (phaseRef.current === "idle" || phaseRef.current === "enter-decel-bottom") {
                node.style.transform = ""
              }
            })
          })
          .catch(() => {})
      }
    }

    animatePath(outer, "outer", 1)
    animatePath(inner, "inner", -1)
  }, [spinPhase])

  return (
    <div className={cn("pm-meeting-qc-fab relative shrink-0", className)}>
      <div className={cn("qc-float-group relative", !idle && "qc-float-group--static")}>
        {(hintVisible || hintExiting) && idle && (
          <div
            className={cn(
              "absolute right-full mr-2 px-2.5 py-1.5",
              "rounded-full whitespace-nowrap",
              "border border-white/50 backdrop-blur-[8px]",
              "-top-5",
              hintExiting
                ? "animate-[hint-retract_0.4s_cubic-bezier(0.4,0,0.2,1)_both]"
                : "animate-[hint-emerge_0.55s_cubic-bezier(0.34,1.56,0.64,1)_both]",
            )}
            style={{
              color: "var(--pm-green, #1A5E3D)",
              pointerEvents: "none",
              background: "color-mix(in srgb, #fff 72%, transparent)",
            }}
          >
            <span className="pm-meta whitespace-nowrap text-[var(--pm-green)]">{hintMessage}</span>
          </div>
        )}
        <button
          ref={btnRef}
          type="button"
          onClick={open ? onClose : onOpen}
          className={cn(
            "relative ease-out quick-chat-btn pm-meeting-qc-fab-btn",
            /* Idle: Collection hover flip. Driven: add spinning class only to block global :hover flip;
               CSS keyframe spin is disabled — WAAPI owns rotation. */
            idle ? "pm-meeting-qc-fab-btn--idle" : "pm-meeting-qc-fab-btn--driven quick-chat-btn-spinning",
          )}
          style={{ color: "var(--pm-green, #1A5E3D)", background: "transparent" }}
          aria-label={open ? "Close Quick Q&A" : "Open Quick Q&A"}
        >
          <DiamondIcon className="w-10 h-10" />
        </button>
      </div>
    </div>
  )
}

// ── Component ──

interface MeetingQuickChatProps {
  meetingId: string
  meetingTitle: string
  open: boolean
  onOpen: () => void
  onClose: () => void
  onRefClick?: (sentenceId: string) => void
  className?: string
  /**
   * dock — fills parent stage-right card (Collection QC width).
   * rail — legacy flex-sibling that expands width when open (default).
   */
  layout?: "dock" | "rail"
}

export function MeetingQuickChat({
  meetingId,
  meetingTitle,
  open,
  onOpen,
  onClose,
  onRefClick,
  className,
  layout = "rail",
}: MeetingQuickChatProps) {
  const [messages, setMessages] = useState<QAMessage[]>([])
  const [input, setInput] = useState("")
  const [streaming, setStreaming] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [msgCount, setMsgCount] = useState(0)
  const [loadingHistory, setLoadingHistory] = useState(true)
  const [expandedSources, setExpandedSources] = useState<Set<string>>(new Set())
  const abortRef = useRef<AbortController | null>(null)
  const messagesScrollRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)
  const ignoreScrollEvent = useRef(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  // ── Hint bubble state ──
  const [hintVisible, setHintVisible] = useState(false)
  const [hintExiting, setHintExiting] = useState(false)
  const [hintMessage, setHintMessage] = useState(HINT_MESSAGES[0])
  const hintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── Init session ──

  const prevMeetingIdRef = useRef(meetingId)
  const hasInitializedRef = useRef(false)

  useEffect(() => {
    const idChanged = prevMeetingIdRef.current !== meetingId
    prevMeetingIdRef.current = meetingId
    if (idChanged) {
      setMessages([])
      setMsgCount(0)
      hasInitializedRef.current = false
    }
    // Wait until meetingTitle is available (meeting object loaded) before
    // creating the session. Avoids creating sessions with empty names during
    // the brief transition when the meeting object is still loading.
    if (!meetingId || !meetingTitle || hasInitializedRef.current) return
    hasInitializedRef.current = true
    const sid = `${MEETING_SESSION_PREFIX}${meetingId}`
    setSessionId(sid)
    initSession(sid)
  }, [meetingId, meetingTitle])

  const initSession = async (sid: string) => {
    setLoadingHistory(true)
    try {
      const detail = await getSession(sid).catch(() => null)
      if (detail?.messages?.length) {
        // Dialogue only (skip system transcript / tool placeholders)
        const msgs: QAMessage[] = detail.messages
          .filter((m) => {
            if (m.role === "user") return true
            if (m.role === "assistant") {
              const meta = (m.metadata ?? {}) as Record<string, unknown>
              if (!m.content && meta.tool_calls) return false
              return !!(m.content || "").trim()
            }
            return false
          })
          .map((m) => ({
            id: m.id,
            role: m.role as "user" | "assistant",
            content: m.content,
            sources: m.sources ?? undefined,
          }))
        setMessages(msgs)
        setMsgCount(countTurns(msgs))
      } else {
        await createSession(meetingTitle, [meetingId], sid).catch(() => {})
        setMessages([])
        setMsgCount(0)
      }
    } catch {
      setMessages([])
    } finally {
      setLoadingHistory(false)
    }
  }

  // ── Smart auto-scroll (aligned with Collection Quick Chat / main Chat) ──
  // Stick while at bottom; unlock on scroll-up; re-stick when user returns to bottom.

  const pinRaf = useRef(0)
  const pinToBottom = useCallback(() => {
    if (pinRaf.current) return
    pinRaf.current = requestAnimationFrame(() => {
      pinRaf.current = 0
      const el = messagesScrollRef.current
      if (!el || !stickToBottom.current) return
      ignoreScrollEvent.current = true
      el.scrollTop = el.scrollHeight
      requestAnimationFrame(() => {
        ignoreScrollEvent.current = false
      })
    })
  }, [])

  const unlockStick = useCallback(() => {
    if (ignoreScrollEvent.current) return
    stickToBottom.current = false
  }, [])

  useEffect(() => {
    const el = messagesScrollRef.current
    if (!el) return
    const onWheel = () => unlockStick()
    const onTouch = () => unlockStick()
    el.addEventListener("wheel", onWheel, { passive: true })
    el.addEventListener("touchmove", onTouch, { passive: true })
    return () => {
      el.removeEventListener("wheel", onWheel)
      el.removeEventListener("touchmove", onTouch)
    }
  }, [unlockStick, open])

  const lastPinAt = useRef(0)
  useEffect(() => {
    if (!stickToBottom.current) return
    if (streaming) {
      const now = Date.now()
      if (now - lastPinAt.current < 80) return
      lastPinAt.current = now
    }
    pinToBottom()
  }, [messages, pinToBottom, streaming])

  // ── Auto-resize textarea ──

  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = "auto"
    ta.style.height = Math.min(ta.scrollHeight, 72) + "px"
  }, [input])

  useEffect(() => {
    if (streaming && textareaRef.current) {
      textareaRef.current.style.height = "auto"
    }
  }, [streaming])

  // ── Hint bubble cycle ──
  useEffect(() => {
    if (open) return
    const scheduleHint = () => {
      const msg = HINT_MESSAGES[Math.floor(Math.random() * HINT_MESSAGES.length)]
      setHintMessage(msg)
      setHintVisible(true)
      setHintExiting(false)
      if (hintTimerRef.current) clearTimeout(hintTimerRef.current)
      hintTimerRef.current = setTimeout(() => {
        // Start exit animation
        setHintExiting(true)
        // After retract animation completes (350ms), unmount + schedule next
        hintTimerRef.current = setTimeout(() => {
          setHintVisible(false)
          setHintExiting(false)
          const delay = HINT_MIN_INTERVAL + Math.random() * (HINT_MAX_INTERVAL - HINT_MIN_INTERVAL)
          hintTimerRef.current = setTimeout(scheduleHint, delay)
        }, 500)
      }, HINT_SHOW_DURATION)
    }
    hintTimerRef.current = setTimeout(scheduleHint, HINT_INITIAL_DELAY)
    return () => {
      if (hintTimerRef.current) clearTimeout(hintTimerRef.current)
    }
  }, [meetingId, open])

  // ── Send ──

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || streaming || !sessionId) return

    setInput("")
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto"
    }

    // New send → re-stick to bottom for this stream
    stickToBottom.current = true

    const userMsg: QAMessage = { id: crypto.randomUUID(), role: "user", content: text, isNew: true }
    const assistantMsg: QAMessage = { id: crypto.randomUUID(), role: "assistant", content: "", isStreaming: true, isNew: true }
    setMessages((prev) => [...prev, userMsg, assistantMsg])
    setMsgCount((c) => c + 1) // optimistic: one new round
    setStreaming(true)
    requestAnimationFrame(() => pinToBottom())

    setTimeout(() => {
      setMessages((prev) => prev.map((m) => ({ ...m, isNew: false })))
    }, 500)

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const resp = await postSessionMessage(
        sessionId,
        {
          content: text,
          thinking: true,
          collections: [],
          mode: "direct",
        },
        controller.signal,
      )

      if (!resp.ok) {
        const err = await resp.text()
        updateAssistant(assistantMsg.id, `Error: ${resp.status} - ${err}`)
        return
      }

      let sources: QAMessage["sources"] = []
      let gotDoneCount: number | null = null

      if (resp.body) {
        for await (const { event: eventType, data } of iterateSessionSse(resp.body)) {
              handleSSEEvent(assistantMsg.id, eventType, data, (s) => { sources = s })
              if (eventType === "done" && typeof data.message_count === "number") {
                gotDoneCount = data.message_count
                setMsgCount(data.message_count)
              }
        }
      }

      updateAssistant(assistantMsg.id, undefined, sources.length > 0 ? sources : undefined)
      // Server turn count preferred; assistant reply does not add a second count
      if (gotDoneCount != null) setMsgCount(gotDoneCount)
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return
      updateAssistant(assistantMsg.id, `Error: ${String(err)}`)
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }, [input, streaming, sessionId, meetingId, pinToBottom])

  const handleSSEEvent = (
    assistantId: string, type: string,
    data: Record<string, unknown>,
    setSources: (s: QAMessage["sources"]) => void,
  ) => {
    switch (type) {
      case "thinking": appendThinking(assistantId, data.content as string); break
      case "token": appendToken(assistantId, data.content as string); break
      case "searching":
        // Show searching indicator in the assistant message
        setMessages((prev) => prev.map((m) =>
          m.id === assistantId ? { ...m, thinkingContent: (m.thinkingContent || "") + `\n🔍 Searching: ${data.query || ""}...` } : m
        ));
        break
      case "tool_call_start":
        setMessages((prev) => prev.map((m) =>
          m.id === assistantId ? { ...m, thinkingContent: (m.thinkingContent || "") + `\n📡 Calling ${data.tool || "tool"}...` } : m
        ));
        break
      case "tool_result":
        setMessages((prev) => prev.map((m) =>
          m.id === assistantId ? { ...m, thinkingContent: (m.thinkingContent || "") + `\n✅ Found ${data.sources_count || 0} sources` } : m
        ));
        break
      case "done":
        console.log("[QuickChat] DONE event received — sources:", data.sources, "type:", typeof data.sources, "isArray:", Array.isArray(data.sources))
        if (data.sources) {
          const srcs = data.sources as QAMessage["sources"]
          console.log("[QuickChat] DONE: setting sources count:", srcs?.length)
          setSources(srcs)
        }
        break
      case "error": updateAssistant(assistantId, `Error: ${data.content}`); break
    }
  }

  const appendThinking = (id: string, token: string) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, thinkingContent: (m.thinkingContent || "") + token } : m)))
  }

  const appendToken = (id: string, token: string) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, content: m.content + token } : m)))
  }

  const updateAssistant = (id: string, content?: string, sources?: QAMessage["sources"]) => {
    setMessages((prev) => prev.map((m) => {
      if (m.id !== id) return m
      return { ...m, ...(content !== undefined ? { content } : {}), ...(sources !== undefined ? { sources } : {}), isStreaming: false }
    }))
  }

  // ── Clear ──

  const clearContext = async () => {
    if (!sessionId) return
    abortRef.current?.abort()
    setStreaming(false)
    try { await deleteSession(sessionId) } catch { /* ok */ }
    await createSession(meetingTitle, [meetingId], sessionId).catch(() => {})
    setMessages([])
    setMsgCount(0)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send() }
  }

  const hasMessages = messages.length > 0
  const threadEdgeFade = useScrollEdgeFade(messagesScrollRef, messages.length)

  // Note: meeting chat doesn't use file-level display names

  // ── Panel content — same pm-qc chrome as Collection Quick Chat ──

  const panelContent = (
    <div
      className={cn(
        "pm-qc-panel",
        layout === "dock" && "pm-meeting-qc-dock-inner",
      )}
      style={layout === "rail" ? { width: SIDEBAR_W } : undefined}
    >
      {/*
        Dock: side-head already shows Chat title + diamond close — no second title
        bar / divider. Tools row (Clear) floats over thread with soft fade only.
        Rail: full Collection-style identity header.
      */}
      <header
        className={cn(
          "pm-qc-header",
          layout === "dock" && "pm-qc-header--dock",
          layout === "dock" && "pm-qc-header--dock-tools-only",
        )}
      >
        <div className="min-w-0 flex items-center gap-2.5">
          {layout !== "dock" && (
            <span className="pm-qc-header-icon" aria-hidden>
              <MessageCircle className="size-3.5" strokeWidth={2} />
            </span>
          )}
          <div className="min-w-0">
            {layout !== "dock" && (
              <p className="pm-qc-header-title">Quick Chat</p>
            )}
            {layout !== "dock" && (
              <p className="pm-qc-header-sub truncate" title={meetingTitle}>
                {meetingTitle}
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {msgCount > 0 && (
            <span
              className={cn(
                "pm-meta inline-flex items-center gap-1 tabular-nums",
                msgCount >= WARN_THRESHOLD && "text-amber-600",
              )}
              title={`${msgCount}/${MAX_MESSAGES} rounds. Soft limit trims older rounds.`}
            >
              {msgCount >= WARN_THRESHOLD && <AlertTriangle className="size-3" />}
              {msgCount}/{MAX_MESSAGES}
            </span>
          )}
          <button
            type="button"
            className="pm-qc-clear-btn"
            onClick={clearContext}
            title="Clear conversation"
            aria-label="Clear conversation"
          >
            <BrushCleaning className="size-3.5" strokeWidth={1.75} />
          </button>
          {layout !== "dock" && (
            <Button
              type="button"
              variant="ghost"
              size="xs"
              onClick={onClose}
              aria-label="Close Quick Q&A"
            >
              Close
            </Button>
          )}
        </div>
      </header>

      <div className="pm-panel-scroll-shell pm-qc-thread-shell">
      <div
        ref={messagesScrollRef}
        onScroll={() => {
          if (ignoreScrollEvent.current) return
          const el = messagesScrollRef.current
          if (!el) return
          const dist = el.scrollHeight - el.scrollTop - el.clientHeight
          if (dist > 60) stickToBottom.current = false
          else stickToBottom.current = true
        }}
        className={cn(
          "pm-qc-thread",
          layout === "dock" && "pm-qc-thread--dock",
          !hasMessages && !loadingHistory && "pm-qc-thread--empty",
        )}
      >
        {loadingHistory ? (
          <div className="flex flex-1 items-center justify-center gap-2 py-10">
            <Loader2 className="size-4 animate-spin text-[var(--pm-faint)]" />
            <span className="pm-meta">Loading…</span>
          </div>
        ) : hasMessages ? (
          <div className="pm-qc-thread-inner">
            {messages.map((msg) =>
              msg.role === "user" ? (
                <div
                  key={msg.id}
                  className={cn(
                    "pm-qc-msg pm-qc-msg--user",
                    msg.isNew && "animate-slide-in-right",
                  )}
                >
                  <span className="pm-qc-msg-role">You</span>
                  <div className="pm-qc-bubble pm-qc-bubble--user">
                    <p className="pm-qc-bubble-text">{msg.content}</p>
                  </div>
                </div>
              ) : (
                <div
                  key={msg.id}
                  className={cn(
                    "pm-qc-msg pm-qc-msg--assistant",
                    msg.isNew && "animate-slide-in-right",
                  )}
                >
                  <span className="pm-qc-msg-role pm-qc-msg-role--ai">Assistant</span>
                  <div className="pm-qc-bubble pm-qc-bubble--assistant">
                    {msg.thinkingContent && (
                      <details
                        className="pm-qc-thinking"
                        open={msg.isStreaming && !msg.content ? true : undefined}
                      >
                        <summary>
                          Thinking
                          {msg.isStreaming && !msg.content && (
                            <Loader2 className="size-2.5 animate-spin inline ml-1" />
                          )}
                        </summary>
                        <p>{msg.thinkingContent}</p>
                      </details>
                    )}
                    {msg.content ? (
                      <div className="pm-qc-answer break-words">
                        <TimeContent content={msg.content} onRefClick={onRefClick} />
                      </div>
                    ) : msg.isStreaming ? (
                      <div className="pm-qc-typing">
                        <Loader2 className="size-3.5 animate-spin" />
                        <span>Educing…</span>
                      </div>
                    ) : (
                      <p className="pm-qc-bubble-text">{msg.content}</p>
                    )}
                    {msg.role === "assistant" &&
                      !msg.isStreaming &&
                      msg.sources &&
                      msg.sources.length > 0 && (
                        <div className="pm-qc-sources">
                          <button
                            type="button"
                            onClick={() =>
                              setExpandedSources((prev) => {
                                const next = new Set(prev)
                                next.has(msg.id) ? next.delete(msg.id) : next.add(msg.id)
                                return next
                              })
                            }
                            className="pm-qc-sources-toggle"
                          >
                            <span>Sources · {msg.sources.length}</span>
                            <svg
                              className={cn(
                                "size-3 transition-transform duration-300",
                                expandedSources.has(msg.id) && "rotate-180",
                              )}
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                            >
                              <path d="M6 9l6 6 6-6" />
                            </svg>
                          </button>
                          <div
                            className={cn(
                              "grid transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)]",
                              expandedSources.has(msg.id)
                                ? "grid-rows-[1fr] opacity-100"
                                : "grid-rows-[0fr] opacity-0",
                            )}
                          >
                            <div className="overflow-hidden">
                              <div className="pm-qc-sources-list">
                                {msg.sources.slice(0, 8).map((s, i) => {
                                  const src = (s.metadata?.source || s.metadata?.filename) as
                                    | string
                                    | undefined
                                  const chunkIdx = s.metadata?.chunk_index as number | undefined
                                  return (
                                    <div key={i} className="pm-qc-source-item is-static">
                                      <div className="flex items-center gap-1 min-w-0">
                                        <span className="truncate font-medium">
                                          {src || "Meeting"}
                                        </span>
                                      </div>
                                      {chunkIdx != null && (
                                        <div className="pm-qc-source-meta">Chunk #{chunkIdx}</div>
                                      )}
                                      <div className="line-clamp-2 opacity-70 mt-0.5">{s.text}</div>
                                    </div>
                                  )
                                })}
                              </div>
                            </div>
                          </div>
                        </div>
                      )}
                  </div>
                </div>
              ),
            )}
          </div>
        ) : (
          <div className="pm-qc-empty">
            <span className="pm-qc-empty-icon" aria-hidden>
              <MessageCircle className="size-5" strokeWidth={1.75} />
            </span>
            <p className="pm-qc-empty-title">Ask this meeting</p>
            <p className="pm-qc-empty-sub">
              Chat with{" "}
              <em className="not-italic text-[var(--pm-green)]">{meetingTitle || "this meeting"}</em>
              . Answers use the transcript and summary.
            </p>
          </div>
        )}
      </div>
      <div
        className={cn(
          "pm-rail-edge-fade pm-rail-edge-fade--top",
          threadEdgeFade.top && "is-visible",
        )}
        aria-hidden
      />
      <div
        className={cn(
          "pm-rail-edge-fade pm-rail-edge-fade--bottom",
          threadEdgeFade.bottom && "is-visible",
        )}
        aria-hidden
      />
      </div>

      <div className="pm-qc-composer-float">
        <div className="pm-qc-composer-float-inner">
          <ChatInputBar
            input={input}
            setInput={setInput}
            streaming={streaming}
            onSend={send}
            onKeyDown={handleKeyDown}
            textareaRef={textareaRef}
          />
        </div>
      </div>
    </div>
  )

  const fab = (
    <div className="relative qc-float-group">
      {(hintVisible || hintExiting) && !open && (
        <div
          className={cn(
            "absolute right-full",
            "mr-3 px-2.5 py-1.5",
            "rounded-full whitespace-nowrap",
            "bg-[var(--pm-float,#fdfbf7)]",
            "shadow-[var(--pm-shadow-sm)]",
            "-top-6",
            hintExiting
              ? "animate-[hint-retract_0.4s_cubic-bezier(0.4,0,0.2,1)_both]"
              : "animate-[hint-emerge_0.55s_cubic-bezier(0.34,1.56,0.64,1)_both]",
          )}
          style={{
            color: "var(--pm-green, #1A5E3D)",
            pointerEvents: "none",
          }}
        >
          <span className="pm-meta whitespace-nowrap text-[var(--pm-green)]">{hintMessage}</span>
        </div>
      )}
      <button
        type="button"
        onClick={open ? onClose : onOpen}
        className={cn(
          "relative transition-all ease-out quick-chat-btn",
          open && "quick-chat-btn-spinning",
        )}
        style={{ color: "var(--pm-green, #1A5E3D)" }}
        aria-label={open ? "Close Quick Q&A" : "Open Quick Q&A"}
      >
        <DiamondIcon className="w-10 h-10" />
      </button>
    </div>
  )

  /* Docked: panel only. FAB lives in side-head via MeetingQcFab (scheme D). */
  if (layout === "dock") {
    if (!open) return null
    return (
      <div className={cn("pm-meeting-qc-dock is-open", className)}>
        <div className="pm-meeting-qc-panel pm-meeting-qc-panel--dock">
          {panelContent}
        </div>
      </div>
    )
  }

  return (
    <>
      {/* ── Soft float rail (curtain width + panel fade · §3.6 / §4.5 language) ── */}
      <div
        className={cn("pm-meeting-qc-rail", open && "is-open", className)}
        style={{ width: open ? SIDEBAR_W : 0 }}
        aria-hidden={!open}
      >
        <div
          className="pm-meeting-qc-panel"
          style={{
            transform: `translateX(${open ? 0 : 12}px)`,
            opacity: open ? 1 : 0,
            pointerEvents: open ? "auto" : "none",
            transition: open
              ? `transform ${ANIM_DURATION}ms cubic-bezier(0.34,1.56,0.64,1), opacity ${ANIM_DURATION}ms ease-out`
              : `transform ${ANIM_DURATION}ms ease-out, opacity ${ANIM_DURATION}ms ease-out`,
          }}
        >
          {open ? panelContent : null}
        </div>
      </div>

      {/* ── Floating button + hint bubble ── */}
      <div
        className="fixed right-6 z-50"
        style={{
          bottom: "24px",
          transform: open ? `translateX(-${SIDEBAR_W}px)` : "translateX(0)",
          transition: "transform 0.35s ease-out",
        }}
      >
        {fab}
      </div>
    </>
  )
}

// ── Timestamp-aware content renderer ──

// ── Timestamp-aware inline renderer ──
// Matches Summary's renderInline approach: regex-based parsing with
// clickable [HH:MM:SS] timestamp buttons styled like Summary refs.

// ── Sentence-ref aware inline renderer ──
// Clickable [ref:N] / [stt_N] chips; ranges expand via parseMeetingRefGroups.

function renderInlineWithRefs(text: string, onRefClick?: (sentenceId: string) => void): ReactNode[] {
  const parts: ReactNode[] = []
  // [ref:67] / [ref:1-5] / [ref:47, 78-86] / [stt_0001] and 【】 variants.
  // Bare [67] is ordinary text — do not chip it.
  const regex = /(\*\*(.+?)\*\*)|(\*(.+?)\*)|(`(.+?)`)|((?:\[|【)(?:ref:\s*((?:stt_)?\d+(?:\s*[-–—,，、;；]\s*(?:stt_)?\d+)*)|(stt_\d+(?:\s*[-–—,，、;；]\s*(?:stt_)?\d+)*))\s*(?:\]|】))|((?:\[|【)\s*priority:\s*(high|medium|low)\s*(?:\]|】))/gi
  let lastIdx = 0
  let match
  regex.lastIndex = 0
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIdx) {
      parts.push(<span key={`t${lastIdx}`}>{text.slice(lastIdx, match.index)}</span>)
    }
    if (match[1]) {
      parts.push(<strong key={`b${lastIdx}`}>{renderInlineWithRefs(match[2], onRefClick)}</strong>)
    } else if (match[3]) {
      parts.push(<em key={`i${lastIdx}`}>{renderInlineWithRefs(match[4], onRefClick)}</em>)
    } else if (match[5]) {
      parts.push(<code key={`c${lastIdx}`} className="bg-muted px-1 rounded text-xs t-mono-family">{match[6]}</code>)
    } else if (match[8] || match[9]) {
      const groups = parseMeetingRefGroups(match[8] || match[9])
      for (const [gi, g] of groups.entries()) {
        parts.push(
          <button
            key={`r${lastIdx}${gi}`}
            className="inline-flex items-center px-1 py-0 pm-meta rounded bg-[var(--pm-green-soft)] text-[var(--pm-green)] hover:bg-[var(--pm-green-wash)] t-mono-family align-baseline cursor-pointer mr-1"
            onClick={(e) => { e.stopPropagation(); if (g.ids[0]) onRefClick?.(g.ids[0]) }}
            title={`Sources: ${g.ids.join(", ")}`}
          >
            {g.label}
          </button>,
        )
      }
    } else if (match[11]) {
      const level = match[11].toLowerCase()
      const colors: Record<string, { bg: string; fg: string }> = {
        high:    { bg: "rgba(140,46,46,0.12)",  fg: "#C06060" },
        medium:  { bg: "rgba(138,101,0,0.10)",   fg: "#B09030" },
        low:     { bg: "rgba(26,94,61,0.10)",    fg: "#5A9070" },
      }
      const c = colors[level] ?? colors.medium
      parts.push(
        <span
          key={`p${lastIdx}`}
          className="inline-flex items-center px-1 py-0 text-[9px] rounded font-medium tracking-wider align-baseline select-none"
          style={{ backgroundColor: c.bg, color: c.fg }}
        >
          {level.toUpperCase()}
        </span>,
      )
    }
    lastIdx = match.index + match[0].length
  }
  if (lastIdx < text.length) {
    parts.push(<span key={`t${lastIdx}`}>{text.slice(lastIdx)}</span>)
  }
  return parts
}

function TimeContent({ content, onRefClick }: {
  content: string
  onRefClick?: (sentenceId: string) => void
}) {
  return (
    <>
      {content.split("\n").map((line, i) => {
        if (line.startsWith("### ")) {
          return <h3 key={i} className="text-sm font-semibold mt-3 mb-1">{renderInlineWithRefs(line.slice(4), onRefClick)}</h3>
        }
        if (line.startsWith("## ")) {
          return <h2 key={i} className="text-base font-semibold mt-3 mb-1">{renderInlineWithRefs(line.slice(3), onRefClick)}</h2>
        }
        if (line.startsWith("# ")) {
          return <h1 key={i} className="text-lg font-bold mt-3 mb-1">{renderInlineWithRefs(line.slice(2), onRefClick)}</h1>
        }
        if (!line.trim()) return <div key={i} className="h-2" />
        if (/^\s*[-*+]\s/.test(line)) {
          return <li key={i} className="text-sm leading-relaxed ml-4">{renderInlineWithRefs(line.replace(/^\s*[-*+]\s/, ""), onRefClick)}</li>
        }
        if (/^>\s/.test(line)) {
          return <blockquote key={i} className="border-l-2 border-muted-foreground/30 pl-3 italic text-muted-foreground text-xs leading-relaxed mb-1">{renderInlineWithRefs(line.replace(/^>\s*/, ""), onRefClick)}</blockquote>
        }
        return <p key={i} className="text-sm leading-relaxed mb-1">{renderInlineWithRefs(line, onRefClick)}</p>
      })}
    </>
  )
}

function ChatInputBar({
  input, setInput, streaming, onSend, onKeyDown, textareaRef,
}: {
  input: string
  setInput: (v: string) => void
  streaming: boolean
  onSend: () => void
  onKeyDown: (e: React.KeyboardEvent) => void
  textareaRef: React.RefObject<HTMLTextAreaElement | null>
}) {
  return (
    <div
      className={cn(
        "pm-qc-input-row",
        streaming && "pm-qc-input-row--thinking",
      )}
    >
      <span className="pm-qc-stream-fx" aria-hidden />
      <div className="pm-qc-input-frame">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Message this meeting…"
          disabled={streaming}
          rows={1}
          className="pm-qc-textarea"
        />
      </div>
      <button
        type="button"
        onClick={onSend}
        disabled={!input.trim() || streaming}
        className={cn(
          "pm-qc-send",
          input.trim() && !streaming && "is-ready",
          streaming && "is-hidden",
        )}
        aria-label="Send"
      >
        <Send className="size-3.5" />
      </button>
    </div>
  )
}
