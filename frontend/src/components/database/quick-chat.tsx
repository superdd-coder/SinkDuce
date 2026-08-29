import { useState, useRef, useEffect, useCallback, useLayoutEffect } from "react"
import { createPortal } from "react-dom"
import { Send, Loader2, AlertTriangle, Globe, MessageCircle, BrushCleaning } from "lucide-react"
import { cn } from "@/lib/utils"
import { createImeEnterGuard } from "@/lib/ime"
import { useT } from "@/i18n/use-t"
import { humanSourceLabel } from "@/lib/source-display"
import { StreamingAnswerBody } from "@/components/chat/streaming-answer-body"
import { createSession, getSession, deleteSession, iterateSessionSse, postSessionMessage } from "@/api/client"
import {
  loadWebSearchForSession,
  setSessionWebSearch,
} from "@/lib/session-web-search"
import {
  getWebSearchConfirmAnchor,
  setWebSearchConfirmAnchor,
} from "@/lib/web-search-confirm"
import { refreshTodosAfterChatTool } from "@/lib/todo-refresh"
import { qcFloatSlideMotion } from "@/lib/quick-chat-float"

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

const QUICK_SESSION_PREFIX = "quick_"
const WARN_THRESHOLD = 20
const MAX_MESSAGES = 30
/** Slide duration — keep in sync with CSS --pm-qc-motion if added */
const ANIM_DURATION = 320
const RAIL_ANCHOR_SEL = "[data-pm-rail-anchor]"
/** Stable diamond park on stage — same top-right for all content tabs */
const FAB_ANCHOR_SEL = "[data-pm-qc-fab-anchor]"
const MIN_RAIL_W = 48

function isUsableRailAnchor(el: HTMLElement | null): el is HTMLElement {
  if (!el || !el.isConnected) return false
  return el.getBoundingClientRect().width >= MIN_RAIL_W
}

/** Only the visible collection tab — never a keep-alive Overview/Files/Timeline sibling. */
function findActiveRailAnchor(): HTMLElement | null {
  const panel = document.querySelector(".pm-panel-fade.is-active")
  if (!panel) return null
  const el = panel.querySelector(RAIL_ANCHOR_SEL) as HTMLElement | null
  return isUsableRailAnchor(el) ? el : null
}

/** Count Q&A turns: 1 user message = 1 round (request + reply). */
function countTurns(msgs: { role: string; content: string }[]): number {
  return msgs.filter((m) => m.role === "user").length
}

// ── Hint bubble config ──
const HINT_KEYS = [
  "library.qcHint1",
  "library.qcHint2",
  "library.qcHint3",
  "library.qcHint4",
  "library.qcHint5",
  "library.qcHint6",
  "library.qcHint7",
] as const
const HINT_SHOW_DURATION = 4000
const HINT_INITIAL_DELAY = 1000
const HINT_MIN_INTERVAL = 5000
const HINT_MAX_INTERVAL = 15000

// ── Diamond icon (outer ring thicker, inner solid slightly narrower)
// Split paths keep independent hover / open spin on .diamond-outer / .diamond-inner

const DIAMOND_SETTLE_MS = 750
const DIAMOND_SETTLE_EASE = "cubic-bezier(0.22, 1, 0.36, 1)"

/** Current rotation in degrees from computed matrix (−180…180). */
function getRotationDegrees(el: Element): number {
  const t = getComputedStyle(el).transform
  if (!t || t === "none") return 0
  try {
    const m = new DOMMatrixReadOnly(t)
    return (Math.atan2(m.b, m.a) * 180) / Math.PI
  } catch {
    return 0
  }
}

/** Drop any leftover WAAPI / CSS animations and inline transform so CSS hover can win. */
function resetDiamondPath(el: Element) {
  const node = el as HTMLElement | SVGElement
  node.getAnimations?.().forEach((a) => a.cancel())
  node.style.transition = ""
  node.style.transform = ""
  node.style.animation = ""
}

/**
 * Ease from current angle → 0 via WAAPI, then cancel so no inline transform
 * remains (otherwise :hover rotate(±180deg) is blocked by style.transform).
 */
function settleDiamondPath(el: Element, fromDeg: number): Animation | null {
  const node = el as HTMLElement | SVGElement
  node.getAnimations?.().forEach((a) => a.cancel())
  node.style.transition = ""
  node.style.transform = ""
  node.style.animation = ""
  if (Math.abs(fromDeg) < 0.5) return null
  const anim = node.animate(
    [
      { transform: `rotate(${fromDeg}deg)` },
      { transform: "rotate(0deg)" },
    ],
    {
      duration: DIAMOND_SETTLE_MS,
      easing: DIAMOND_SETTLE_EASE,
      fill: "forwards",
    }
  )
  anim.finished
    .then(() => {
      anim.cancel() // release transform back to stylesheet (hover works again)
    })
    .catch(() => {
      /* cancelled */
    })
  return anim
}

function DiamondIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" className={className} fill="none" xmlns="http://www.w3.org/2000/svg">
      {/* Hollow outer diamond — thicker stroke. Transform via CSS (hover / spin). */}
      <path
        className="diamond-outer"
        d="M24 5.2C24.9 5.2 25.75 5.55 26.4 6.2L41.8 21.6C42.45 22.25 42.8 23.1 42.8 24C42.8 24.9 42.45 25.75 41.8 26.4L26.4 41.8C25.75 42.45 24.9 42.8 24 42.8C23.1 42.8 22.25 42.45 21.6 41.8L6.2 26.4C5.55 25.75 5.2 24.9 5.2 24C5.2 23.1 5.55 22.25 6.2 21.6L21.6 6.2C22.25 5.55 23.1 5.2 24 5.2Z"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinejoin="round"
        fill="none"
      />
      {/* Solid inner diamond — slightly narrower than previous */}
      <path
        className="diamond-inner"
        d="M24 13.5C24.45 13.5 24.88 13.68 25.2 14L34 22.8C34.32 23.12 34.5 23.55 34.5 24C34.5 24.45 34.32 24.88 34 25.2L25.2 34C24.88 34.32 24.45 34.5 24 34.5C23.55 34.5 23.12 34.32 22.8 34L14 25.2C13.68 24.88 13.5 24.45 13.5 24C13.5 23.55 13.68 23.12 14 22.8L22.8 14C23.12 13.68 23.55 13.5 24 13.5Z"
        fill="currentColor"
      />
    </svg>
  )
}

// ── Component ──

interface QuickChatProps {
  collectionId: string
  collectionName: string
  open: boolean
  onOpen: () => void
  onClose: () => void
  onSourceClick?: (source: string, chunkIndex?: number) => void
  onMeetingClick?: (meetingId: string) => void
  files?: { source: string; display_name?: string; file_id?: string }[]
  className?: string
  /**
   * When true, size the float card to the active right rail
   * (Overview To-do stack or Files Messages). When false, viewport fallback.
   */
  railActive?: boolean
  /**
   * Tab / surface key so we re-bind the float rail host when switching
   * Overview ↔ Files.
   */
  railKey?: string
  /**
   * Show the diamond FAB. False on Config (Settings) — icon stays parked
   * elsewhere; do not use fixed fallback that drifts.
   */
  fabVisible?: boolean
}

export function QuickChat({
  collectionId,
  collectionName,
  open,
  onOpen,
  onClose,
  onSourceClick,
  onMeetingClick,
  files,
  className,
  railActive = true,
  railKey,
  fabVisible = true,
}: QuickChatProps) {
  const t = useT()
  const [messages, setMessages] = useState<QAMessage[]>([])
  const [input, setInput] = useState("")
  const [streamingBySid, setStreamingBySid] = useState<Record<string, boolean>>({})
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [msgCount, setMsgCount] = useState(0)
  const [loadingHistory, setLoadingHistory] = useState(true)
  const [expandedSources, setExpandedSources] = useState<Set<string>>(new Set())
  // Web toggle remembered per quick session (quick_<collectionId>); default OFF
  const [webSearch, setWebSearch] = useState(false)
  const abortBySidRef = useRef<Map<string, AbortController>>(new Map())
  const sessionIdRef = useRef<string | null>(null)
  const streamingBySidRef = useRef<Record<string, boolean>>({})
  const messagesCacheRef = useRef<Map<string, QAMessage[]>>(new Map())
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const messagesScrollRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)
  const ignoreScrollEvent = useRef(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const imeGuardRef = useRef(createImeEnterGuard())
  /** Stable host for web-confirm portal — avoid inline ref callbacks (re-fire every render). */
  const webConfirmHostRef = useRef<HTMLDivElement | null>(null)
  sessionIdRef.current = sessionId
  const streaming = !!(sessionId && streamingBySid[sessionId])

  const markStreaming = (sid: string, on: boolean) => {
    const next = { ...streamingBySidRef.current }
    if (on) next[sid] = true
    else delete next[sid]
    streamingBySidRef.current = next
    setStreamingBySid(next)
  }
  // ── Hint bubble state ──
  const [hintVisible, setHintVisible] = useState(false)
  const [hintExiting, setHintExiting] = useState(false)
  const [hintMessage, setHintMessage] = useState("")
  const hintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** Spin class lags `open` so we can freeze angle and ease to rest on close. */
  const [diamondSpinning, setDiamondSpinning] = useState(false)
  const diamondSpinningRef = useRef(false)
  const diamondBtnRef = useRef<HTMLButtonElement>(null)
  const diamondSettleRafRef = useRef<number | null>(null)
  const diamondSettleAnimsRef = useRef<Animation[]>([])
  /**
   * Float host = active panel's right rail (cover To-do / Messages).
   * Prefer `.pm-panel-fade.is-active` so idle keep-alive rails are ignored.
   */
  const [railHost, setRailHost] = useState<HTMLElement | null>(null)
  /** One frame at translateX(100%) after the host attaches so the slide-in can run. */
  const [railDocked, setRailDocked] = useState(false)
  /**
   * FAB host = stage-level park — same coordinates on Overview / Files / Timeline.
   * Never use fixed viewport fallback for the diamond (Timeline/Config looked wrong).
   */
  const [fabHost, setFabHost] = useState<HTMLElement | null>(null)

  useLayoutEffect(() => {
    const findFab = () => {
      const el = document.querySelector(FAB_ANCHOR_SEL) as HTMLElement | null
      setFabHost(el)
    }
    findFab()
    const t = window.setTimeout(findFab, 0)
    const t2 = window.setTimeout(findFab, 50)
    return () => {
      window.clearTimeout(t)
      window.clearTimeout(t2)
    }
  }, [collectionId, railKey, fabVisible])

  useLayoutEffect(() => {
    if (!railActive) {
      setRailHost(null)
      return
    }
    /**
     * Bind only the active tab's rail. Do not fall back to the first
     * [data-pm-rail-anchor] in the document — keep-alive Overview/Files/Timeline
     * all have one, and picking the wrong host teleports the card mid-animation.
     * Skip zero-width Files Messages (collapsed curtain). Keep the last good
     * host while the new rail is expanding so we never flash position:fixed.
     */
    let cancelled = false
    const apply = () => {
      if (cancelled) return
      const next = findActiveRailAnchor()
      setRailHost((prev) => {
        if (next) return next
        const panel = document.querySelector(".pm-panel-fade.is-active")
        if (prev && panel?.contains(prev) && isUsableRailAnchor(prev)) return prev
        return null
      })
    }
    apply()
    const delays = open
      ? [16, 50, 120, 220, 360, 480, 600]
      : [16, 80, 200]
    const timers = delays.map((ms) => window.setTimeout(apply, ms))
    const ro = new ResizeObserver(apply)
    const panel = document.querySelector(".pm-panel-fade.is-active")
    if (panel) ro.observe(panel)
    const anchor = panel?.querySelector(RAIL_ANCHOR_SEL)
    if (anchor) ro.observe(anchor)
    return () => {
      cancelled = true
      timers.forEach((t) => window.clearTimeout(t))
      ro.disconnect()
    }
  }, [railActive, railKey, collectionId, open])

  useLayoutEffect(() => {
    if (!open || !railHost) {
      setRailDocked(false)
      return
    }
    setRailDocked(false)
    const id = requestAnimationFrame(() => setRailDocked(true))
    return () => cancelAnimationFrame(id)
  }, [open, railHost])

  const showFloat = open && (!!railHost ? railDocked : !railActive)

  // Keep a per-session message cache so switching collections does not
  // clobber an in-flight stream (or lock the other collection's composer).
  useEffect(() => {
    if (!sessionId || loadingHistory) return
    const expected = collectionId ? `${QUICK_SESSION_PREFIX}${collectionId}` : null
    if (expected && sessionId !== expected) return
    messagesCacheRef.current.set(sessionId, messages)
  }, [sessionId, collectionId, messages, loadingHistory])

  // ── Init session ──

  useEffect(() => {
    if (!collectionId) return
    const sid = `${QUICK_SESSION_PREFIX}${collectionId}`
    setSessionId(sid)
    setWebSearch(loadWebSearchForSession(sid))
    if (streamingBySidRef.current[sid]) {
      const cached = messagesCacheRef.current.get(sid)
      if (cached) {
        setMessages(cached)
        setMsgCount(countTurns(cached))
        setLoadingHistory(false)
        return
      }
    }
    void initSession(sid)
  }, [collectionId])

  // Claim / release web-confirm anchor only when this Quick Chat is on screen
  useEffect(() => {
    if (open && webConfirmHostRef.current) {
      setWebSearchConfirmAnchor(webConfirmHostRef.current, sessionId)
    }
    return () => {
      const cur = getWebSearchConfirmAnchor()
      if (cur && cur === webConfirmHostRef.current) {
        setWebSearchConfirmAnchor(null)
      }
    }
  }, [open, sessionId])

  /*
   * Diamond spin: infinite CSS while open; on close WAAPI ease to rest.
   * Settle must not leave inline transform — that blocks CSS hover (±180°).
   */
  useLayoutEffect(() => {
    const btn = diamondBtnRef.current
    const outer = btn?.querySelector(".diamond-outer") as SVGElement | null
    const inner = btn?.querySelector(".diamond-inner") as SVGElement | null

    const cancelSettle = () => {
      if (diamondSettleRafRef.current != null) {
        cancelAnimationFrame(diamondSettleRafRef.current)
        diamondSettleRafRef.current = null
      }
      diamondSettleAnimsRef.current.forEach((a) => {
        try {
          a.cancel()
        } catch {
          /* ignore */
        }
      })
      diamondSettleAnimsRef.current = []
    }

    if (open) {
      cancelSettle()
      if (outer) resetDiamondPath(outer)
      if (inner) resetDiamondPath(inner)
      diamondSpinningRef.current = true
      setDiamondSpinning(true)
      return cancelSettle
    }

    // Closing: capture angle while spin class still on
    if (!diamondSpinningRef.current || !outer || !inner) {
      diamondSpinningRef.current = false
      setDiamondSpinning(false)
      return cancelSettle
    }

    const angleOuter = getRotationDegrees(outer)
    const angleInner = getRotationDegrees(inner)

    diamondSpinningRef.current = false
    setDiamondSpinning(false)

    // After spin class drops, run WAAPI settle (no permanent inline transform)
    diamondSettleRafRef.current = requestAnimationFrame(() => {
      diamondSettleRafRef.current = requestAnimationFrame(() => {
        diamondSettleRafRef.current = null
        const anims: Animation[] = []
        const aO = settleDiamondPath(outer, angleOuter)
        const aI = settleDiamondPath(inner, angleInner)
        if (aO) anims.push(aO)
        if (aI) anims.push(aI)
        diamondSettleAnimsRef.current = anims
      })
    })

    return cancelSettle
  }, [open])

  const initSession = async (sid: string) => {
    setLoadingHistory(true)
    try {
      const detail = await getSession(sid).catch(() => null)
      if (detail?.messages?.length) {
        // Dialogue only: skip tool rows and empty tool-call placeholders
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
        await createSession(collectionName, [collectionId], sid).catch(() => {})
        setMessages([])
        setMsgCount(0)
      }
    } catch {
      setMessages([])
    } finally {
      setLoadingHistory(false)
    }
  }

  // ── Smart auto-scroll: stick while streaming; unlock on scroll-up; re-stick at bottom ──

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

  // Throttle stick-scroll while streaming
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

  // Opening the panel must land on the latest message (history is already
  // loaded; the thread is just sliding in / rail is expanding).
  useLayoutEffect(() => {
    if (!open) return
    stickToBottom.current = true
    pinToBottom()
    document.querySelector(".pm-stage")?.scrollTo?.(0, 0)
    const delays = [0, 50, 120, 220, 360, ANIM_DURATION + 40]
    const timers = delays.map((ms) =>
      window.setTimeout(() => {
        stickToBottom.current = true
        const el = messagesScrollRef.current
        if (el) {
          ignoreScrollEvent.current = true
          el.scrollTop = el.scrollHeight
          requestAnimationFrame(() => {
            ignoreScrollEvent.current = false
          })
        }
        // Never scrollIntoView — it scrolls .pm-stage and clips the title/tabs.
      }, ms),
    )
    return () => timers.forEach((t) => window.clearTimeout(t))
  }, [open, loadingHistory])

  useEffect(() => {
    return () => {
      abortBySidRef.current.forEach((c) => c.abort())
      abortBySidRef.current.clear()
    }
  }, [])

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
      const key = HINT_KEYS[Math.floor(Math.random() * HINT_KEYS.length)]
      setHintMessage(t(key))
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
  }, [collectionId, open, t])

  // ── Send ──

  const send = useCallback(async () => {
    const text = input.trim()
    const sid = sessionId
    if (!text || !sid || streamingBySidRef.current[sid]) return

    setInput("")
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto"
    }

    // New user message → re-stick to bottom for this stream
    stickToBottom.current = true

    const userMsg: QAMessage = { id: crypto.randomUUID(), role: "user", content: text, isNew: true }
    const assistantId = crypto.randomUUID()
    const assistantMsg: QAMessage = { id: assistantId, role: "assistant", content: "", isStreaming: true, isNew: true }
    setMessages((prev) => [...prev, userMsg, assistantMsg])
    // Optimistic: one new turn when user sends (reply does not add another)
    setMsgCount((c) => c + 1)
    markStreaming(sid, true)
    requestAnimationFrame(() => pinToBottom())

    setTimeout(() => {
      const clearNew = (prev: QAMessage[]) => prev.map((m) => ({ ...m, isNew: false }))
      if (sessionIdRef.current === sid) setMessages(clearNew)
      else {
        const cached = messagesCacheRef.current.get(sid)
        if (cached) messagesCacheRef.current.set(sid, clearNew(cached))
      }
    }, 500)

    const controller = new AbortController()
    abortBySidRef.current.get(sid)?.abort()
    abortBySidRef.current.set(sid, controller)

    const applySid = (updater: (prev: QAMessage[]) => QAMessage[]) => {
      if (sessionIdRef.current === sid) setMessages(updater)
      else {
        const prev = messagesCacheRef.current.get(sid) ?? []
        messagesCacheRef.current.set(sid, updater(prev))
      }
    }

    // Local helpers — always use assistantId + setMessages (no stale handleSSEEvent)
    const appendThinkingLocal = (token: string) => {
      if (!token) return
      applySid((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, thinkingContent: (m.thinkingContent || "") + token }
            : m,
        ),
      )
    }
    // Immediate content append (same priority as thinking). React 18 batches
    // multiple setStates within one await-read tick → one paint per SSE chunk.
    const appendTokenLocal = (token: string) => {
      if (!token) return
      applySid((prev) =>
        prev.map((m) =>
          m.id === assistantId ? { ...m, content: m.content + token } : m,
        ),
      )
    }
    const finishLocal = (sources?: QAMessage["sources"]) => {
      applySid((prev) =>
        prev.map((m) => {
          if (m.id !== assistantId) return m
          return {
            ...m,
            isStreaming: false,
            ...(sources !== undefined ? { sources } : {}),
          }
        }),
      )
    }

    try {
      const resp = await postSessionMessage(
        sid,
        {
          content: text,
          thinking: true,
          collections: [collectionId],
          mode: "direct",
          web_search_enabled: webSearch,
        },
        controller.signal,
      )

      if (!resp.ok) {
        const err = await resp.text()
        applySid((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: `${t("common.error")}: ${resp.status} - ${err}`, isStreaming: false }
              : m,
          ),
        )
        return
      }

      let sources: NonNullable<QAMessage["sources"]> = []
      let gotDoneCount: number | null = null

      if (resp.body) {
        for await (const { event: eventType, data } of iterateSessionSse(resp.body)) {
              if (eventType === "web_search_confirm") {
                const confirmId = String(data.confirm_id || "")
                const query = String(data.query || "")
                if (confirmId) {
                  const { promptWebSearchConfirm } = await import("@/lib/web-search-confirm")
                  const { confirmWebSearch } = await import("@/api/client")
                  const approved = await promptWebSearchConfirm(
                    confirmId,
                    query,
                    sessionId,
                  )
                  try {
                    await confirmWebSearch(confirmId, approved)
                  } catch (err) {
                    console.error("[QuickChat] web-search-confirm failed:", err)
                  }
                }
              } else if (eventType === "todo_delete_confirm") {
                const confirmId = String(data.confirm_id || "")
                const title = String(data.title || "")
                const collectionName = String(data.collection_name || "")
                if (confirmId) {
                  const { promptTodoDeleteConfirm } = await import("@/lib/todo-delete-confirm")
                  const { confirmTodoDelete } = await import("@/api/client")
                  const approved = await promptTodoDeleteConfirm(
                    confirmId,
                    title,
                    sessionId,
                    collectionName,
                  )
                  try {
                    await confirmTodoDelete(confirmId, approved)
                  } catch (err) {
                    console.error("[QuickChat] todo-delete-confirm failed:", err)
                  }
                }
              } else if (eventType === "thinking") {
                appendThinkingLocal(String(data.content ?? ""))
              } else if (eventType === "planning") {
                appendThinkingLocal(`\n${t("chat.nextStep")}`)
              } else if (eventType === "token") {
                appendTokenLocal(String(data.content ?? ""))
              } else if (eventType === "tool_call_start") {
                const tool = String(data.tool || "tool")
                const q = data.raw_query || data.query
                const line = q
                  ? `\n📡 ${tool}: ${String(q).slice(0, 120)}`
                  : `\n📡 ${tool}`
                appendThinkingLocal(line)
              } else if (eventType === "searching") {
                const q = String(data.query || "")
                appendThinkingLocal(q ? `\n🔍 ${q}` : `\n🔍 ${t("chat.searching")}`)
              } else if (eventType === "tool_result") {
                const st = String(data.status || "done")
                const tool = String(data.tool || "")
                refreshTodosAfterChatTool(tool, {
                  collectionId,
                  status: st,
                  content: data.content,
                })
                const n = data.sources_count
                const isSearchLike =
                  tool === "lookup_collection" ||
                  tool === "search_knowledge_base" ||
                  tool === "request_web_search"
                const line =
                  st === "declined"
                    ? tool === "request_web_search"
                      ? `\n⛔ ${t("chat.web")} · ${t("chat.declined")}`
                      : `\n⛔ ${t("chat.declined")}`
                    : st === "error"
                      ? `\n❌ ${t("common.failed")}`
                      : isSearchLike && typeof n === "number"
                        ? `\n✅ ${t("common.done")} · ${t("library.sourcesN", { n })}`
                        : `\n✅ ${t("common.done")}`
                appendThinkingLocal(line)
              } else if (eventType === "done") {
                if (data.sources) {
                  sources = data.sources as NonNullable<QAMessage["sources"]>
                }
                if (typeof data.message_count === "number") {
                  gotDoneCount = data.message_count
                  if (sessionIdRef.current === sid) setMsgCount(data.message_count)
                }
              } else if (eventType === "error") {
                appendTokenLocal(`${t("common.error")}: ${data.content}`)
              }
        }
      }

      finishLocal(sources.length > 0 ? sources : undefined)
      // Server turn count preferred; do not +1 for assistant (already counted on send)
      if (gotDoneCount != null && sessionIdRef.current === sid) setMsgCount(gotDoneCount)
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return
      applySid((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, content: `${t("common.error")}: ${String(err)}`, isStreaming: false }
            : m,
        ),
      )
    } finally {
      markStreaming(sid, false)
      abortBySidRef.current.delete(sid)
    }
  }, [input, sessionId, collectionId, webSearch, pinToBottom, t])

  const toggleWebSearch = () => {
    setWebSearch((prev) => {
      const next = !prev
      if (sessionId) setSessionWebSearch(sessionId, next)
      if (!next && sessionId) {
        void import("@/lib/web-search-confirm").then((m) =>
          m.clearWebSearchAlwaysAllow(sessionId),
        )
      }
      return next
    })
  }

  // ── Clear ──

  const clearContext = async () => {
    if (!sessionId) return
    abortBySidRef.current.get(sessionId)?.abort()
    abortBySidRef.current.delete(sessionId)
    markStreaming(sessionId, false)
    messagesCacheRef.current.delete(sessionId)
    try { await deleteSession(sessionId) } catch { /* ok */ }
    await createSession(collectionName, [collectionId], sessionId).catch(() => {})
    setMessages([])
    setMsgCount(0)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!imeGuardRef.current.isSubmitEnter(e)) return
    e.preventDefault()
    send()
  }
  const handleCompositionStart = () => imeGuardRef.current.onCompositionStart()
  const handleCompositionEnd = () => {
    imeGuardRef.current.onCompositionEnd()
    requestAnimationFrame(() => imeGuardRef.current.clearJustEnded())
  }

  const hasMessages = messages.length > 0

  const getDisplayName = (
    sourceId: string,
    meta?: Record<string, unknown>,
  ) =>
    humanSourceLabel(
      {
        source: sourceId,
        source_label: meta?.source_label,
        filename: meta?.filename,
        display_name: meta?.display_name,
      },
      files,
    )

  // ── Panel content (Premium compact chat chrome) ──

  const panelContent = (
    <div className="pm-qc-panel">
      {/* Identity header — makes the surface read as Chat, not a doc pane */}
      <header className="pm-qc-header">
        <div className="min-w-0 flex items-center gap-2.5">
          <span className="pm-qc-header-icon" aria-hidden>
            <MessageCircle className="size-3.5" strokeWidth={2} />
          </span>
          <div className="min-w-0">
            <p className="pm-qc-header-title">{t("library.quickChat")}</p>
            <p className="pm-qc-header-sub truncate" title={collectionName}>
              {collectionName}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {msgCount > 0 && (
            <span
              className={cn(
                "pm-meta inline-flex items-center gap-1 tabular-nums",
                msgCount >= WARN_THRESHOLD && "text-amber-600"
              )}
              title={t("library.roundsLimit", { n: msgCount, max: MAX_MESSAGES })}
            >
              {msgCount >= WARN_THRESHOLD && (
                <AlertTriangle className="size-3" />
              )}
              {msgCount}/{MAX_MESSAGES}
            </span>
          )}
          <button
            type="button"
            className="pm-qc-clear-btn"
            onClick={clearContext}
            title={t("library.clearConversation")}
            aria-label={t("library.clearConversation")}
          >
            <BrushCleaning className="size-3.5" strokeWidth={1.75} />
          </button>
        </div>
      </header>

      {/* Thread */}
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
          !hasMessages && !loadingHistory && "pm-qc-thread--empty"
        )}
      >
        {loadingHistory ? (
          <div className="flex flex-1 items-center justify-center gap-2 py-10">
            <Loader2 className="size-4 animate-spin text-[var(--pm-faint)]" />
            <span className="pm-meta">{t("common.loading")}</span>
          </div>
        ) : hasMessages ? (
          <div className="pm-qc-thread-inner">
            {messages.map((msg) =>
              msg.role === "user" ? (
                <div
                  key={msg.id}
                  className={cn(
                    "pm-qc-msg pm-qc-msg--user",
                    msg.isNew && "animate-slide-in-right"
                  )}
                >
                  {/* Match Chat message-bubble: You + underline text */}
                  <span className="pm-qc-msg-role">{t("chat.you")}</span>
                  <div className="pm-qc-bubble pm-qc-bubble--user">
                    <p className="pm-qc-bubble-text">{msg.content}</p>
                  </div>
                </div>
              ) : (
                <div
                  key={msg.id}
                  className={cn(
                    "pm-qc-msg pm-qc-msg--assistant",
                    msg.isNew && "animate-slide-in-right"
                  )}
                >
                  {/* Match Chat: “Assistant” + left rail, prose answer */}
                  <span className="pm-qc-msg-role pm-qc-msg-role--ai">
                    {t("chat.assistant")}
                  </span>
                  <div className="pm-qc-bubble pm-qc-bubble--assistant">
                    {msg.thinkingContent && (
                      <details
                        className="pm-qc-thinking"
                        open={
                          msg.isStreaming && !msg.content ? true : undefined
                        }
                      >
                        <summary>
                          {t("chat.thinking")}
                          {msg.isStreaming && !msg.content && (
                            <Loader2 className="size-2.5 animate-spin inline ml-1" />
                          )}
                        </summary>
                        <p>{msg.thinkingContent}</p>
                      </details>
                    )}
                    {msg.content ? (
                      <StreamingAnswerBody
                        content={msg.content}
                        isStreaming={!!msg.isStreaming}
                        className="pm-qc-answer break-words [&_table]:text-[11px] [&_th]:px-1.5 [&_th]:py-0.5 [&_td]:px-1.5 [&_td]:py-0.5 [&_table]:block [&_table]:overflow-x-auto [&_pre]:text-[11px]"
                      />
                    ) : msg.isStreaming ? (
                      <div className="pm-qc-typing">
                        <Loader2 className="size-3.5 animate-spin" />
                        <span>{t("chat.educing")}</span>
                      </div>
                    ) : (
                      <p className="pm-qc-bubble-text">{msg.content}</p>
                    )}
                    {msg.role === "assistant" &&
                      !msg.isStreaming &&
                      msg.sources &&
                      msg.sources.length > 0 &&
                      (() => {
                        const webN = msg.sources!.filter(
                          (s) => s.metadata?.source_type === "web"
                        ).length
                        const kbN = msg.sources!.length - webN
                        return (
                          <div className="pm-qc-sources">
                            <button
                              type="button"
                              onClick={() =>
                                setExpandedSources((prev) => {
                                  const next = new Set(prev)
                                  next.has(msg.id)
                                    ? next.delete(msg.id)
                                    : next.add(msg.id)
                                  return next
                                })
                              }
                              className="pm-qc-sources-toggle"
                            >
                              <span>
                                {t("library.sourcesN", { n: msg.sources!.length })}
                                {webN > 0 ? ` · ${webN} ${t("chat.web")}` : ""}
                                {kbN > 0 && webN > 0 ? ` · ${kbN} kb` : ""}
                              </span>
                              <svg
                                className={cn(
                                  "size-3 transition-transform duration-300",
                                  expandedSources.has(msg.id) && "rotate-180"
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
                                  : "grid-rows-[0fr] opacity-0"
                              )}
                            >
                              <div className="overflow-hidden">
                                <div className="pm-qc-sources-list">
                                  {msg.sources!.slice(0, 8).map((s, i) => {
                                    const isMeeting =
                                      s.metadata?.source_type === "meeting"
                                    const meetingId = String(
                                      s.metadata?.meeting_id || "",
                                    ).trim()
                                    const isWeb =
                                      s.metadata?.source_type === "web"
                                    const url =
                                      (s.metadata?.url as string) || ""
                                    const src = (s.metadata?.source ||
                                      s.metadata?.filename) as
                                      | string
                                      | undefined
                                    const chunkIdx = s.metadata
                                      ?.chunk_index as number | undefined
                                    const label = isWeb
                                      ? String(
                                          s.metadata?.source_label ||
                                            s.metadata?.source ||
                                            url ||
                                            t("chat.web")
                                        )
                                      : getDisplayName(src || "", s.metadata)
                                    return (
                                      <button
                                        type="button"
                                        key={i}
                                        className={cn(
                                          "pm-qc-source-item",
                                          isWeb && "is-web",
                                          !((isWeb && url) ||
                                            (isMeeting && meetingId && onMeetingClick) ||
                                            (src && onSourceClick)) &&
                                            "is-static"
                                        )}
                                        onClick={() => {
                                          if (isWeb && url) {
                                            window.open(
                                              url,
                                              "_blank",
                                              "noopener,noreferrer"
                                            )
                                            return
                                          }
                                          if (isMeeting && meetingId && onMeetingClick) {
                                            onMeetingClick(meetingId)
                                            return
                                          }
                                          if (src && onSourceClick)
                                            onSourceClick(src, chunkIdx)
                                        }}
                                      >
                                        <div className="flex items-center gap-1 min-w-0">
                                          {isWeb && (
                                            <span className="pm-qc-web-badge">
                                              WEB
                                            </span>
                                          )}
                                          <span className="truncate font-medium">
                                            {label}
                                          </span>
                                        </div>
                                        {!isWeb && chunkIdx != null && (
                                          <div className="pm-qc-source-meta">
                                            {t("library.chunkN", { n: chunkIdx })}
                                          </div>
                                        )}
                                        {isWeb && url && (
                                          <div className="pm-qc-source-meta truncate">
                                            {url}
                                          </div>
                                        )}
                                        <div className="line-clamp-2 opacity-70 mt-0.5">
                                          {s.text}
                                        </div>
                                      </button>
                                    )
                                  })}
                                </div>
                              </div>
                            </div>
                          </div>
                        )
                      })()}
                  </div>
                </div>
              )
            )}
            <div ref={messagesEndRef} />
          </div>
        ) : (
          <div className="pm-qc-empty">
            <span className="pm-qc-empty-icon" aria-hidden>
              <MessageCircle className="size-5" strokeWidth={1.75} />
            </span>
            <p className="pm-qc-empty-title">{t("library.askCollection")}</p>
            <p className="pm-qc-empty-sub">
              {t("library.qcEmptyBefore")}{" "}
              <em className="not-italic text-[var(--pm-green)]">
                {collectionName}
              </em>
              {t("library.qcEmptyAfter")}
            </p>
          </div>
        )}
      </div>

      {/* Floating glass composer — suspended above card bottom (not a dock strip) */}
      <div className="pm-qc-composer-float">
        <div className="pm-qc-composer-float-inner">
          <div
            ref={webConfirmHostRef}
            data-web-confirm-host="inline"
            className="w-full"
          />
          <ChatInputBar
            input={input}
            setInput={setInput}
            streaming={streaming}
            webSearch={webSearch}
            onToggleWebSearch={toggleWebSearch}
            onSend={send}
            onKeyDown={handleKeyDown}
            onCompositionStart={handleCompositionStart}
            onCompositionEnd={handleCompositionEnd}
            textareaRef={textareaRef}
          />
        </div>
      </div>
    </div>
  )

  const ease = "cubic-bezier(0.45, 0.05, 0.55, 0.95)"
  const slideMotion = qcFloatSlideMotion(showFloat)
  const floatPanel = (
    <div
      className={cn(
        "pm-qc-float",
        /* In-rail: fill host exactly. Fallback only when this tab has no rail. */
        railHost ? "pm-qc-float--rail" : "pm-qc-float--fallback",
        className
      )}
      style={{
        pointerEvents: showFloat ? "auto" : "none",
      }}
      aria-hidden={!open}
    >
      {/* Clip translate here so drop-shadow on .pm-qc-float can follow the card. */}
      <div className="pm-qc-float-clip">
        <div
          className="pm-qc-float-slide"
          style={{
            transform: slideMotion.transform,
            opacity: slideMotion.opacity,
            transition: `transform ${ANIM_DURATION}ms ${ease}`,
          }}
        >
          {panelContent}
        </div>
      </div>
    </div>
  )

  const fabBlock = (
    <div className="pm-qc-fab pm-qc-fab--park">
      {/*
        Row: bubble LEFT of diamond, nudged slightly UP (左边偏上).
        Shared bob on the group.
      */}
      <div
        className="qc-float-group flex flex-row items-center justify-end gap-2.5"
        style={{ position: "relative" }}
      >
        {(hintVisible || hintExiting) && !open && (
          <div
            className={cn(
              "inline-flex h-8 items-center rounded-full px-2.5",
              "pointer-events-none whitespace-nowrap shrink-0",
              "border border-white/55 text-[color:var(--ze-green,#1A5E3D)]",
              "shadow-sm backdrop-blur-[10px]",
              hintExiting
                ? "animate-[hint-retract_0.4s_cubic-bezier(0.4,0,0.2,1)_both]"
                : "animate-[hint-emerge_0.55s_cubic-bezier(0.34,1.56,0.64,1)_both]"
            )}
            style={{
              background: "color-mix(in srgb, #fff 78%, transparent)",
              lineHeight: 1,
              boxSizing: "border-box",
              /* 左边偏上：relative top 不与 emerge 动画的 transform 冲突 */
              position: "relative",
              top: -10,
            }}
          >
            <span className="block text-[11px] font-medium leading-none whitespace-nowrap">
              {hintMessage}
            </span>
          </div>
        )}
        <button
          ref={diamondBtnRef}
          type="button"
          onClick={open ? onClose : onOpen}
          className={cn(
            "relative shrink-0 transition-all ease-out quick-chat-btn",
            diamondSpinning && "quick-chat-btn-spinning"
          )}
          style={{ color: "var(--ze-green, #1A5E3D)" }}
          aria-label={open ? t("meeting.closeQuickQa") : t("library.openQuickQa")}
        >
          <DiamondIcon className="w-10 h-10" />
        </button>
      </div>
    </div>
  )

  return (
    <>
      {/*
        Float: portal into the active tab's right rail (Overview / Files / Timeline).
        Fallback: fixed column only when this tab has no rail.
        FAB: always stage park — same top-right for all content tabs.
      */}
      {railHost
        ? createPortal(floatPanel, railHost)
        : !railActive
          ? floatPanel
          : null}
      {fabVisible &&
        (fabHost ? createPortal(fabBlock, fabHost) : null)}
    </>
  )
}

// ── Chat Input Bar ──

function ChatInputBar({
  input, setInput, streaming, webSearch, onToggleWebSearch, onSend, onKeyDown,
  onCompositionStart, onCompositionEnd, textareaRef,
}: {
  input: string
  setInput: (v: string) => void
  streaming: boolean
  webSearch: boolean
  onToggleWebSearch: () => void
  onSend: () => void
  onKeyDown: (e: React.KeyboardEvent) => void
  onCompositionStart: () => void
  onCompositionEnd: () => void
  textareaRef: React.RefObject<HTMLTextAreaElement | null>
}) {
  const t = useT()
  return (
    <div
      className={cn(
        "pm-qc-input-row",
        /* Generating: flowing border + glow on whole pill (not textarea) */
        streaming && "pm-qc-input-row--thinking"
      )}
    >
      {/* Stream-only FX layer — opacity crossfades in/out with --thinking */}
      <span className="pm-qc-stream-fx" aria-hidden />
      <button
        type="button"
        onClick={onToggleWebSearch}
        disabled={streaming}
        title={
          webSearch
            ? t("library.qcWebOn")
            : t("library.qcWebOff")
        }
        className={cn(
          "pm-qc-web-btn",
          webSearch && "is-on",
          streaming && "opacity-50"
        )}
      >
        <Globe className="size-3.5" />
      </button>

      {/* No sk-input-frame — textarea is flush inside the pill; breath is on the row */}
      <div className="pm-qc-input-frame">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          onCompositionStart={onCompositionStart}
          onCompositionEnd={onCompositionEnd}
          placeholder={t("library.messageCollection")}
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
          streaming && "is-hidden"
        )}
        aria-label={t("common.send")}
      >
        <Send className="size-3.5" />
      </button>
    </div>
  )
}
