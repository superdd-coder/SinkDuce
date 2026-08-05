import { useState, useRef, useEffect, useCallback, type ReactNode } from "react"
import { Send, Loader2, AlertTriangle } from "lucide-react"
import { cn } from "@/lib/utils"
import { createSession, getSession, deleteSession } from "@/api/client"

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
const SIDEBAR_W = 400

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

// ── Diamond icon (split paths for independent hover rotation) ──

function DiamondIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" xmlns="http://www.w3.org/2000/svg">
      <path
        className="diamond-outer"
        d="M12 2L22 12L12 22L2 12Z"
        stroke="currentColor"
        strokeWidth="1.2"
        fill="none"
        style={{ transformOrigin: "center", transition: "transform 0.8s cubic-bezier(0.4, 0, 0.2, 1)" }}
      />
      <path
        className="diamond-inner"
        d="M12 6L17 12L12 18L7 12Z"
        fill="currentColor"
        style={{ transformOrigin: "center", transition: "transform 0.8s cubic-bezier(0.4, 0, 0.2, 1)" }}
      />
    </svg>
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
}

export function MeetingQuickChat({ meetingId, meetingTitle, open, onOpen, onClose, onRefClick, className }: MeetingQuickChatProps) {
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
      const resp = await fetch(`/api/sessions/${sessionId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: text,
          thinking: true,
          collections: [],
          mode: "direct",
        }),
        signal: controller.signal,
      })

      if (!resp.ok) {
        const err = await resp.text()
        updateAssistant(assistantMsg.id, `Error: ${resp.status} - ${err}`)
        return
      }

      const reader = resp.body!.getReader()
      const decoder = new TextDecoder()
      let buffer = ""
      let sources: QAMessage["sources"] = []
      // Survive across network chunks (do not reset each read)
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
              const data = JSON.parse(line.slice(6))
              handleSSEEvent(assistantMsg.id, eventType, data, (s) => { sources = s })
              if (eventType === "done" && typeof data.message_count === "number") {
                gotDoneCount = data.message_count
                setMsgCount(data.message_count)
              }
            } catch (e) {
              console.error("[MeetingQuickChat] SSE parse failed for event:", eventType, "line:", line.slice(0, 200), "err:", e)
            }
            eventType = ""
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

  // Note: meeting chat doesn't use file-level display names

  // ── Panel content ──

  const panelContent = (
    <div className="flex flex-col h-full relative" style={{ width: SIDEBAR_W }}>
      {/* ── Header ── */}
      <div className="flex items-center justify-end shrink-0 px-3 pt-3 pb-2">
        <div className="flex items-center gap-2">
          {msgCount > 0 && (
            <span
              className={cn(
                "flex items-center gap-1 text-[10px] tabular-nums",
                msgCount >= WARN_THRESHOLD
                  ? "text-amber-600 dark:text-amber-400"
                  : "text-muted-foreground/70",
              )}
              title={`${msgCount}/${MAX_MESSAGES} rounds (1 user + 1 reply = 1). Soft limit trims older rounds at ${MAX_MESSAGES}.`}
            >
              {msgCount >= WARN_THRESHOLD && <AlertTriangle className="w-3 h-3" />}
              {msgCount}/{MAX_MESSAGES}
            </span>
          )}
          <button
            onClick={clearContext}
            className="text-[10px] font-medium uppercase tracking-[0.12em] transition-opacity duration-150"
            style={{ color: "var(--ze-green, #1A5E3D)", opacity: 0.5 }}
            onMouseEnter={(e) => { (e.target as HTMLElement).style.opacity = "1" }}
            onMouseLeave={(e) => { (e.target as HTMLElement).style.opacity = "0.5" }}
          >
            CLEAR
          </button>
        </div>
      </div>

      {/* ── Messages area ── */}
      <div
        ref={messagesScrollRef}
        onScroll={() => {
          if (ignoreScrollEvent.current) return
          const el = messagesScrollRef.current
          if (!el) return
          const dist = el.scrollHeight - el.scrollTop - el.clientHeight
          // Leave bottom → unlock; return to bottom → re-stick
          if (dist > 60) stickToBottom.current = false
          else stickToBottom.current = true
        }}
        className={cn(
        "flex-1 overflow-y-auto px-3 min-h-0 transition-all duration-500 ease-out",
        hasMessages ? "pb-14" : "pb-24",
        !hasMessages && "flex flex-col items-center justify-center",
      )}>
        {loadingHistory ? (
          <div className="flex items-center justify-center py-8 text-muted-foreground">
            <Loader2 className="w-4 h-4 animate-spin" />
          </div>
        ) : hasMessages ? (
          <div className="space-y-3 pb-2">
            {messages.map((msg) => (
              <div
                key={msg.id}
                className={cn(
                  "rounded-lg px-3 py-2 max-w-full",
                  msg.role === "user" ? "bg-primary/10 ml-6" : "bg-muted/50 mr-2",
                  msg.isNew && "animate-slide-in-right",
                )}
              >
                {msg.role === "assistant" && (msg.content || msg.thinkingContent) ? (
                  <div>
                    {msg.thinkingContent && (
                      <details className="mb-2" open={msg.isStreaming ? true : undefined}>
                        <summary className="text-[10px] text-muted-foreground/60 cursor-pointer hover:text-muted-foreground transition-colors">
                          Thinking {msg.isStreaming && <Loader2 className="w-2.5 h-2.5 animate-spin inline ml-1 align-[-1px]" />}
                        </summary>
                        <p className="mt-1 text-[10px] text-muted-foreground/50 whitespace-pre-wrap leading-relaxed italic">
                          {msg.thinkingContent}
                        </p>
                      </details>
                    )}
                    {msg.content ? (
                      <div className="max-w-none break-words">
                        <TimeContent
                          content={msg.content}
                          onRefClick={onRefClick}
                        />
                      </div>
                    ) : msg.isStreaming && (
                      <div className="flex items-center gap-1 text-muted-foreground">
                        <Loader2 className="w-3 h-3 animate-spin" />
                        <span className="text-xs">Generating answer...</span>
                      </div>
                    )}
                  </div>
                ) : msg.role === "assistant" && msg.isStreaming ? (
                  <div className="flex items-center gap-1 text-muted-foreground">
                    <Loader2 className="w-3 h-3 animate-spin" />
                    <span className="text-xs">Thinking...</span>
                  </div>
                ) : (
                  <p className="whitespace-pre-wrap break-words text-xs leading-relaxed">{msg.content}</p>
                )}
                {msg.role === "assistant" && !msg.isStreaming && msg.sources && msg.sources.length > 0 && (
                  <div className="mt-3 pt-2 border-t border-dashed border-border">
                    <button
                      onClick={() => setExpandedSources((prev) => {
                        const next = new Set(prev)
                        next.has(msg.id) ? next.delete(msg.id) : next.add(msg.id)
                        return next
                      })}
                      className="flex items-center justify-between w-full text-[10px] font-normal uppercase tracking-[0.12em] text-muted-foreground/70 hover:text-muted-foreground transition-colors cursor-pointer mb-2"
                    >
                      <span>Sources · {msg.sources.length}</span>
                      <svg
                        className={cn("w-3 h-3 transition-transform duration-300", expandedSources.has(msg.id) && "rotate-180")}
                        viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                      >
                        <path d="M6 9l6 6 6-6" />
                      </svg>
                    </button>
                    <div
                      className={cn(
                        "grid transition-all duration-500",
                        "ease-[cubic-bezier(0.23,1,0.32,1)]",
                        expandedSources.has(msg.id)
                          ? "grid-rows-[1fr] opacity-100"
                          : "grid-rows-[0fr] opacity-0",
                      )}
                    >
                      <div className="overflow-hidden">
                        <div className="space-y-1 max-h-32 overflow-y-auto">
                          {msg.sources.slice(0, 5).map((s, i) => {
                            const src = (s.metadata?.source || s.metadata?.filename) as string | undefined
                            const chunkIdx = s.metadata?.chunk_index as number | undefined
                            const displayName = src ? src : "Unknown"
                            return (
                              <div
                                key={i}
                                className={cn(
                                  "text-[10px] text-muted-foreground bg-muted rounded p-1.5 border-b border-dashed border-border/50 last:border-0",
                                  ""
                                )}
                                onClick={() => {
                                  
                                }}
                              >
                                <div className="truncate font-medium">{displayName}</div>
                                {chunkIdx != null && (
                                  <div className="text-[9px] opacity-50">Chunk #{chunkIdx}</div>
                                )}
                                <div className="line-clamp-2 mt-0.5 opacity-70">{s.text}</div>
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          // Empty state — centered, fades out when messages appear
          <div
            className="flex flex-col items-center gap-4"
            style={{ transition: "opacity 0.5s ease-out, transform 0.5s ease-out" }}
          >
            <p
              className="text-center leading-relaxed t-body-family"
              style={{
                fontSize: "14px",
                fontWeight: 300,
                color: "var(--ze-ink)",
                lineHeight: 1.6,
              }}
            >
              Ask a quick question about
              <br />
              <em style={{ fontStyle: "italic", fontWeight: 300, color: "var(--ze-green, #1A5E3D)" }}>
                {meetingTitle}
              </em>
            </p>
          </div>
        )}

      </div>

      {/* Floating input */}
      <div className="absolute bottom-6 left-0 right-0 z-10 pointer-events-none">
        <div className="pointer-events-auto">
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

  return (
    <>
      {/* ── Sidebar panel — width transitions in flex layout ── */}
      <div
        className={cn(
          "h-full border-l border-border bg-background shrink-0 overflow-hidden",
          "transition-all ease-out",
          className,
        )}
        style={{
          width: open ? SIDEBAR_W : 0,
          transitionDuration: `${ANIM_DURATION}ms`,
        }}
      >
        <div
          className="h-full"
          style={{
            transform: `translateX(${open ? 0 : 12}px)`,
            opacity: open ? 1 : 0,
            transition: open
              ? `transform ${ANIM_DURATION}ms cubic-bezier(0.34,1.56,0.64,1), opacity ${ANIM_DURATION}ms ease-out`
              : `transform ${ANIM_DURATION}ms ease-out, opacity ${ANIM_DURATION}ms ease-out`,
          }}
        >
          {panelContent}
        </div>
      </div>

      {/* ── Floating button + hint bubble ── */}
      <div
        className={cn(
              "fixed right-6 z-50",
            )}
        style={{
          bottom: "24px",
          transform: open ? `translateX(-${SIDEBAR_W}px)` : "translateX(0)",
          transition: "transform 0.35s ease-out",
        }}
      >
        <div className="relative qc-float-group">
          {/* Hint bubble */}
          {(hintVisible || hintExiting) && !open && (
          <div
            className={cn(
              "absolute right-full",
              "mr-3 px-2.5 py-1.5",
              "rounded-full whitespace-nowrap",
              "bg-background/70 backdrop-blur-md",
              "border border-border/50",
              "shadow-sm",
              "-top-6",
              hintExiting ? "animate-[hint-retract_0.4s_cubic-bezier(0.4,0,0.2,1)_both]" : "animate-[hint-emerge_0.55s_cubic-bezier(0.34,1.56,0.64,1)_both]",
            )}
            style={{
              color: "var(--ze-green, #1A5E3D)",
              pointerEvents: "none",
            }}
          >
            <span className="text-[11px] font-medium whitespace-nowrap">{hintMessage}</span>
          </div>
          )}
          <button
            onClick={open ? onClose : onOpen}
            className={cn(
              "relative transition-all ease-out quick-chat-btn",
              open && "quick-chat-btn-spinning",
            )}
            style={{
              color: "var(--ze-green, #1A5E3D)",
            }}
            aria-label={open ? "Close Quick Q&A" : "Open Quick Q&A"}
          >
            <DiamondIcon className="w-10 h-10" />
          </button>
        </div>
      </div>
    </>
  )
}

// ── Timestamp-aware content renderer ──

// ── Timestamp-aware inline renderer ──
// Matches Summary's renderInline approach: regex-based parsing with
// clickable [HH:MM:SS] timestamp buttons styled like Summary refs.

// ── Sentence-ref aware inline renderer ──
// Matches Summary's renderInline approach: regex-based parsing with
// clickable [N] ref buttons that convert N to stt_XXXX format.

function renderInlineWithRefs(text: string, onRefClick?: (sentenceId: string) => void): ReactNode[] {
  const parts: ReactNode[] = []
  // Supports [stt_XXXX,...], 【stt_XXXX,...】, [ref: stt_XXXX], and bare [7,7-10]
  const regex = /(\*\*(.+?)\*\*)|(\*(.+?)\*)|(`(.+?)`)|(\[(?:ref:)?\s*(stt_\d+(?:\s*[-–,]\s*stt_\d+)*)\s*\])|(【(?:ref:)?\s*(stt_\d+(?:\s*[-–,]\s*stt_\d+)*)\s*】)|(\[priority:\s*(high|medium|low)\s*\])|(【priority:\s*(high|medium|low)\s*】)|\[(\d+(?:\s*[-–,]\s*\d+)*)\]/gi
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
    } else if (match[8] || match[10] || match[15]) {
      // [stt_0044,...], 【stt_0044,...】, or bare [7,7-10]
      const raw: string = (match[8] || match[10] || match[15])!
      const ids = raw.split(/[,–-]/).map((s: string) => s.trim()).filter(Boolean)
      const parsed = ids
        .map((id) => ({ id, num: parseInt(id.replace(/^stt_0*/, "") || "0", 10) }))
        .sort((a, b) => a.num - b.num)
      let ri = 0
      while (ri < parsed.length) {
        const start = parsed[ri]
        let end = start
        let rj = ri + 1
        while (rj < parsed.length && parsed[rj].num === end.num + 1) { end = parsed[rj]; rj++ }
        const sl = start.id.replace(/^stt_0*/, "") || "0"
        const el = end.id.replace(/^stt_0*/, "") || "0"
        const label = start.id === end.id ? sl : sl + "-" + el
        const sttIds = parsed.slice(ri, rj).map((p) => "stt_" + String(p.num).padStart(4, "0"))
        parts.push(
          <button
            key={`r${lastIdx}${ri}`}
            className="inline-flex items-center px-1 py-0 text-[10px] rounded bg-[rgba(61,175,115,0.12)] text-[#2D8A5E] hover:bg-[rgba(61,175,115,0.20)] t-mono-family align-baseline cursor-pointer mr-1"
            onClick={(e) => { e.stopPropagation(); onRefClick?.(sttIds[0]) }}
            title={`Sources: ${sttIds.join(", ")}`}
          >
            {label}
          </button>,
        )
        ri = rj
      }
    } else if (match[12] || match[14]) {
      const level = (match[12] || match[14])!.toLowerCase()
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
    <div className="flex items-center gap-2 w-[88%] mx-auto transition-all duration-300 ease-out">
      {/* Input pill */}
      <div
        className={cn(
          "flex-1 flex items-center transition-all duration-300 ease-out",
          "rounded-full border bg-background/70 backdrop-blur-lg sk-input-frame",
          streaming && "sk-thinking-flow",
        )}
        style={{
          borderRadius: "9999px",
          borderColor: streaming ? "oklch(0.38 0.08 160 / 0.18)" : "var(--border)",
          minHeight: "32px",
        }}
      >
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask about this meeting..."
          disabled={streaming}
          rows={1}
          className={cn(
            "flex-1 bg-transparent resize-none outline-none px-3 text-xs",
            "placeholder:text-muted-foreground/60 disabled:opacity-60",
            "transition-all duration-300 ease-out rounded-full",
          )}
          style={{
            paddingTop: 0,
            paddingBottom: 0,
            maxHeight: "72px",
            overflowY: "auto",
          }}
        />
      </div>

      {/* Send button */}
      <div
        className={cn(
          "shrink-0 flex items-center transition-all duration-300 ease-out",
          streaming ? "w-0 opacity-0 scale-0 overflow-hidden" : "opacity-100 scale-100",
        )}
      >
        <button
          onClick={onSend}
          disabled={!input.trim() || streaming}
          className={cn(
            "w-7 h-7 rounded-full flex items-center justify-center transition-all duration-200",
            input.trim() && !streaming ? "text-primary-foreground" : "text-muted-foreground/40",
          )}
          style={{
            background: input.trim() && !streaming ? "var(--ze-green, #1A5E3D)" : "transparent",
          }}
        >
          <Send className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  )
}
