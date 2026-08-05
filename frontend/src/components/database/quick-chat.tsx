import { useState, useRef, useEffect, useCallback, useLayoutEffect } from "react"
import { createPortal } from "react-dom"
import { Send, Loader2, AlertTriangle, Globe, MessageCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import { StreamingAnswerBody } from "@/components/chat/streaming-answer-body"
import { createSession, getSession, deleteSession } from "@/api/client"
import {
  loadWebSearchForSession,
  setSessionWebSearch,
} from "@/lib/session-web-search"
import {
  getWebSearchConfirmAnchor,
  setWebSearchConfirmAnchor,
} from "@/lib/web-search-confirm"

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

/** Count Q&A turns: 1 user message = 1 round (request + reply). */
function countTurns(msgs: { role: string; content: string }[]): number {
  return msgs.filter((m) => m.role === "user").length
}

// ── Hint bubble config ──
const HINT_MESSAGES = [
  "Chat with this collection?",
  "Ask a question?",
  "Quick Q&A?",
  "Got a question?",
  "Ask anything...",
  "Curious about the content?",
  "Ask about the collection?",
]
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
  files?: { source: string; display_name?: string }[]
  className?: string
  /**
   * When true, size/position the float card to Overview right rail
   * (To-do + Notes + Meetings). When false, use a viewport fallback.
   */
  railActive?: boolean
}

export function QuickChat({
  collectionId,
  collectionName,
  open,
  onOpen,
  onClose,
  onSourceClick,
  files,
  className,
  railActive = true,
}: QuickChatProps) {
  const [messages, setMessages] = useState<QAMessage[]>([])
  const [input, setInput] = useState("")
  const [streaming, setStreaming] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [msgCount, setMsgCount] = useState(0)
  const [loadingHistory, setLoadingHistory] = useState(true)
  const [expandedSources, setExpandedSources] = useState<Set<string>>(new Set())
  // Web toggle remembered per quick session (quick_<collectionId>); default OFF
  const [webSearch, setWebSearch] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const messagesScrollRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)
  const ignoreScrollEvent = useRef(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  /** Stable host for web-confirm portal — avoid inline ref callbacks (re-fire every render). */
  const webConfirmHostRef = useRef<HTMLDivElement | null>(null)
  // ── Hint bubble state ──
  const [hintVisible, setHintVisible] = useState(false)
  const [hintExiting, setHintExiting] = useState(false)
  const [hintMessage, setHintMessage] = useState(HINT_MESSAGES[0])
  const hintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** Spin class lags `open` so we can freeze angle and ease to rest on close. */
  const [diamondSpinning, setDiamondSpinning] = useState(false)
  const diamondSpinningRef = useRef(false)
  const diamondBtnRef = useRef<HTMLButtonElement>(null)
  const diamondSettleRafRef = useRef<number | null>(null)
  const diamondSettleAnimsRef = useRef<Animation[]>([])
  /**
   * Portal host = Overview right rail. Absolute inset-0 always matches
   * To-do/Notes/Meetings column — no getBoundingClientRect height drift.
   */
  const [railHost, setRailHost] = useState<HTMLElement | null>(null)

  useLayoutEffect(() => {
    if (!railActive) {
      setRailHost(null)
      return
    }
    const find = () => {
      const el = document.querySelector(RAIL_ANCHOR_SEL) as HTMLElement | null
      setRailHost(el)
    }
    find()
    // InfoPanel may mount one frame later when switching collection / tab
    const t = window.setTimeout(find, 0)
    const t2 = window.setTimeout(find, 50)
    return () => {
      window.clearTimeout(t)
      window.clearTimeout(t2)
    }
  }, [railActive, collectionId, open])

  // ── Init session ──

  useEffect(() => {
    if (!collectionId) return
    const sid = `${QUICK_SESSION_PREFIX}${collectionId}`
    setSessionId(sid)
    setWebSearch(loadWebSearchForSession(sid))
    initSession(sid)
  }, [collectionId])

  // Claim / release web-confirm anchor only when panel open state changes
  useEffect(() => {
    if (open && webConfirmHostRef.current) {
      setWebSearchConfirmAnchor(webConfirmHostRef.current)
    }
    return () => {
      const cur = getWebSearchConfirmAnchor()
      if (cur && cur === webConfirmHostRef.current) {
        setWebSearchConfirmAnchor(null)
      }
    }
  }, [open])

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
  }, [collectionId, open])

  // ── Send ──

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || streaming || !sessionId) return

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
    setStreaming(true)
    requestAnimationFrame(() => pinToBottom())

    setTimeout(() => {
      setMessages((prev) => prev.map((m) => ({ ...m, isNew: false })))
    }, 500)

    const controller = new AbortController()
    abortRef.current = controller

    // Local helpers — always use assistantId + setMessages (no stale handleSSEEvent)
    const appendThinkingLocal = (token: string) => {
      if (!token) return
      setMessages((prev) =>
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
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId ? { ...m, content: m.content + token } : m,
        ),
      )
    }
    const finishLocal = (sources?: QAMessage["sources"]) => {
      setMessages((prev) =>
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
      const resp = await fetch(`/api/sessions/${sessionId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: text,
          thinking: true,
          collections: [collectionId],
          mode: "direct",
          web_search_enabled: webSearch,
        }),
        signal: controller.signal,
      })

      if (!resp.ok) {
        const err = await resp.text()
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: `Error: ${resp.status} - ${err}`, isStreaming: false }
              : m,
          ),
        )
        return
      }

      const reader = resp.body!.getReader()
      const decoder = new TextDecoder()
      let buffer = ""
      let sources: NonNullable<QAMessage["sources"]> = []
      // Must survive across network chunks (event: and data: often split)
      let eventType = ""
      let gotDoneCount: number | null = null

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split("\n")
        buffer = lines.pop() || ""
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7).trim()
          } else if (line.startsWith("data: ") && eventType) {
            try {
              const data = JSON.parse(line.slice(6)) as Record<string, unknown>
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
              } else if (eventType === "thinking") {
                appendThinkingLocal(String(data.content ?? ""))
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
                appendThinkingLocal(q ? `\n🔍 ${q}` : "\n🔍 Searching…")
              } else if (eventType === "tool_result") {
                const st = String(data.status || "done")
                const tool = String(data.tool || "")
                const n = data.sources_count
                const isSearchLike =
                  tool === "lookup_collection" ||
                  tool === "search_knowledge_base" ||
                  tool === "request_web_search"
                const line =
                  st === "declined"
                    ? tool === "request_web_search"
                      ? "\n⛔ Web search declined / off"
                      : "\n⛔ Tool declined / cancelled"
                    : st === "error"
                      ? "\n❌ Tool failed"
                      : isSearchLike && typeof n === "number"
                        ? `\n✅ Done · ${n} source${n === 1 ? "" : "s"}`
                        : "\n✅ Done"
                appendThinkingLocal(line)
              } else if (eventType === "done") {
                if (data.sources) {
                  sources = data.sources as NonNullable<QAMessage["sources"]>
                }
                if (typeof data.message_count === "number") {
                  gotDoneCount = data.message_count
                  setMsgCount(data.message_count)
                }
              } else if (eventType === "error") {
                appendTokenLocal(`Error: ${data.content}`)
              }
            } catch (e) {
              console.error("[QuickChat] SSE parse failed for event:", eventType, "line:", line.slice(0, 200), "err:", e)
            }
            eventType = ""
          }
        }
      }

      finishLocal(sources.length > 0 ? sources : undefined)
      // Server turn count preferred; do not +1 for assistant (already counted on send)
      if (gotDoneCount != null) setMsgCount(gotDoneCount)
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, content: `Error: ${String(err)}`, isStreaming: false }
            : m,
        ),
      )
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }, [input, streaming, sessionId, collectionId, webSearch, pinToBottom])

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
    abortRef.current?.abort()
    setStreaming(false)
    try { await deleteSession(sessionId) } catch { /* ok */ }
    await createSession(collectionName, [collectionId], sessionId).catch(() => {})
    setMessages([])
    setMsgCount(0)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send() }
  }

  const hasMessages = messages.length > 0

  // Resolve source ID → display name
  const getDisplayName = (sourceId: string) => {
    const f = files?.find((f) => f.source === sourceId)
    return f?.display_name || sourceId.split("/").pop() || sourceId
  }

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
            <p className="pm-qc-header-title">Quick Chat</p>
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
              title={`${msgCount}/${MAX_MESSAGES} rounds. Soft limit trims older rounds.`}
            >
              {msgCount >= WARN_THRESHOLD && (
                <AlertTriangle className="size-3" />
              )}
              {msgCount}/{MAX_MESSAGES}
            </span>
          )}
          <button
            type="button"
            onClick={clearContext}
            className="pm-btn-ghost pm-btn-xs"
            title="Clear conversation"
          >
            Clear
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
                    msg.isNew && "animate-slide-in-right"
                  )}
                >
                  {/* Match Chat message-bubble: You + underline text */}
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
                    msg.isNew && "animate-slide-in-right"
                  )}
                >
                  {/* Match Chat: “Assistant” + left rail, prose answer */}
                  <span className="pm-qc-msg-role pm-qc-msg-role--ai">
                    Assistant
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
                          Thinking
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
                        <span>Educing…</span>
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
                                Sources · {msg.sources!.length}
                                {webN > 0 ? ` · ${webN} web` : ""}
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
                                            "Web"
                                        )
                                      : src
                                        ? getDisplayName(src)
                                        : "Unknown"
                                    return (
                                      <button
                                        type="button"
                                        key={i}
                                        className={cn(
                                          "pm-qc-source-item",
                                          isWeb && "is-web",
                                          !((isWeb && url) ||
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
                                            Chunk #{chunkIdx}
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
            <p className="pm-qc-empty-title">Ask this collection</p>
            <p className="pm-qc-empty-sub">
              Chat with{" "}
              <em className="not-italic text-[var(--pm-green)]">
                {collectionName}
              </em>
              . Answers use your indexed sources.
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
            textareaRef={textareaRef}
          />
        </div>
      </div>
    </div>
  )

  const ease = "cubic-bezier(0.45, 0.05, 0.55, 0.95)"

  const floatPanel = (
    <div
      className={cn(
        "pm-qc-float",
        /* In-rail: fill host exactly. Fallback: fixed right column. */
        railHost ? "pm-qc-float--rail" : "pm-qc-float--fallback",
        className
      )}
      style={{
        transform: open ? "translateX(0)" : "translateX(calc(100% + 20px))",
        opacity: open ? 1 : 0,
        pointerEvents: open ? "auto" : "none",
        transition: open
          ? `transform ${ANIM_DURATION}ms ${ease}, opacity ${ANIM_DURATION * 0.85}ms ${ease}`
          : `transform ${ANIM_DURATION}ms ${ease}, opacity ${ANIM_DURATION * 0.7}ms ${ease}`,
      }}
      aria-hidden={!open}
    >
      {panelContent}
    </div>
  )

  const fabBlock = (
    <div
      className={cn(
        "pm-qc-fab",
        railHost ? "pm-qc-fab--rail" : "pm-qc-fab--fallback"
      )}
      style={
        /* Inline wins over stale CSS: diamond above To-do card */
        railHost
          ? { top: -44, right: 0, position: "absolute", zIndex: 50, overflow: "visible" }
          : { top: 92, right: 28, position: "fixed", zIndex: 50, overflow: "visible" }
      }
    >
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
          aria-label={open ? "Close Quick Q&A" : "Open Quick Q&A"}
        >
          <DiamondIcon className="w-10 h-10" />
        </button>
      </div>
    </div>
  )

  return (
    <>
      {/*
        Panel + FAB portaled into right rail when on Overview:
        - float fills rail (covers To-do / Notes / Meetings)
        - diamond sits above the To-do card (top of rail)
      */}
      {railHost ? (
        <>
          {createPortal(floatPanel, railHost)}
          {createPortal(fabBlock, railHost)}
        </>
      ) : (
        <>
          {floatPanel}
          {fabBlock}
        </>
      )}
    </>
  )
}

// ── Chat Input Bar ──

function ChatInputBar({
  input, setInput, streaming, webSearch, onToggleWebSearch, onSend, onKeyDown, textareaRef,
}: {
  input: string
  setInput: (v: string) => void
  streaming: boolean
  webSearch: boolean
  onToggleWebSearch: () => void
  onSend: () => void
  onKeyDown: (e: React.KeyboardEvent) => void
  textareaRef: React.RefObject<HTMLTextAreaElement | null>
}) {
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
            ? "Web search ON — may confirm before searching the internet"
            : "Web search OFF — collection only (API key in Settings)"
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
          placeholder="Message this collection…"
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
        aria-label="Send"
      >
        <Send className="size-3.5" />
      </button>
    </div>
  )
}
