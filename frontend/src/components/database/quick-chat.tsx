import { useState, useRef, useEffect, useCallback } from "react"
import { Send, Loader2, AlertTriangle } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
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

const QUICK_SESSION_PREFIX = "quick_"
const WARN_THRESHOLD = 20
const MAX_MESSAGES = 30
const ANIM_DURATION = 350
const SIDEBAR_W = 400

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

interface QuickChatProps {
  collectionId: string
  collectionName: string
  open: boolean
  onOpen: () => void
  onClose: () => void
  onSourceClick?: (source: string, chunkIndex?: number) => void
  files?: { source: string; display_name?: string }[]
  className?: string
}

export function QuickChat({ collectionId, collectionName, open, onOpen, onClose, onSourceClick, files, className }: QuickChatProps) {
  const [messages, setMessages] = useState<QAMessage[]>([])
  const [input, setInput] = useState("")
  const [streaming, setStreaming] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [msgCount, setMsgCount] = useState(0)
  const [loadingHistory, setLoadingHistory] = useState(true)
  const [panelVisible, setPanelVisible] = useState(false)
  const [expandedSources, setExpandedSources] = useState<Set<string>>(new Set())
  const abortRef = useRef<AbortController | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const prevOpenRef = useRef(open)
  const animTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── Coordinated open/close: button + panel animate simultaneously ──

  useEffect(() => {
    if (open === prevOpenRef.current) return
    prevOpenRef.current = open
    if (animTimerRef.current) clearTimeout(animTimerRef.current)

    if (open) {
      setPanelVisible(true)
    } else {
      animTimerRef.current = setTimeout(() => {
        setPanelVisible(false)
      }, ANIM_DURATION)
    }

    return () => {
      if (animTimerRef.current) clearTimeout(animTimerRef.current)
    }
  }, [open])

  // ── Init session ──

  useEffect(() => {
    if (!collectionId) return
    const sid = `${QUICK_SESSION_PREFIX}${collectionId}`
    setSessionId(sid)
    initSession(sid)
  }, [collectionId])

  const initSession = async (sid: string) => {
    setLoadingHistory(true)
    try {
      const detail = await getSession(sid).catch(() => null)
      if (detail?.messages?.length) {
        const msgs: QAMessage[] = detail.messages.map((m) => ({
          id: m.id,
          role: m.role as "user" | "assistant",
          content: m.content,
          sources: m.sources ?? undefined,
        }))
        setMessages(msgs)
        setMsgCount(detail.messages.length)
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

  // ── Auto-scroll ──

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

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

  // ── Send ──

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || streaming || !sessionId) return

    setInput("")
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto"
    }

    const userMsg: QAMessage = { id: crypto.randomUUID(), role: "user", content: text, isNew: true }
    const assistantMsg: QAMessage = { id: crypto.randomUUID(), role: "assistant", content: "", isStreaming: true, isNew: true }
    setMessages((prev) => [...prev, userMsg, assistantMsg])
    setStreaming(true)

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
          collections: [collectionId],
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

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split("\n")
        buffer = lines.pop() || ""
        let eventType = ""
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7).trim()
          } else if (line.startsWith("data: ") && eventType) {
            try {
              const data = JSON.parse(line.slice(6))
              handleSSEEvent(assistantMsg.id, eventType, data, (s) => { sources = s })
              if (eventType === "done" && data.message_count != null) {
                setMsgCount(data.message_count)
              }
            } catch { /* skip */ }
            eventType = ""
          }
        }
      }

      updateAssistant(assistantMsg.id, undefined, sources.length > 0 ? sources : undefined)
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return
      updateAssistant(assistantMsg.id, `Error: ${String(err)}`)
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }, [input, streaming, sessionId, collectionId])

  const handleSSEEvent = (
    assistantId: string, type: string,
    data: Record<string, unknown>,
    setSources: (s: QAMessage["sources"]) => void,
  ) => {
    switch (type) {
      case "thinking": appendThinking(assistantId, data.content as string); break
      case "token": appendToken(assistantId, data.content as string); break
      case "done": if (data.sources) setSources(data.sources as QAMessage["sources"]); break
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

  // ── Panel content ──

  const panelContent = (
    <div className="flex flex-col h-full relative" style={{ width: SIDEBAR_W }}>
      {/* ── Header ── */}
      <div className="flex items-center justify-end shrink-0 px-3 pt-3 pb-2">
        <div className="flex items-center gap-2">
          {msgCount >= WARN_THRESHOLD && (
            <span className="flex items-center gap-1 text-[10px] text-amber-600 dark:text-amber-400"
              title={`${msgCount}/${MAX_MESSAGES} messages`}>
              <AlertTriangle className="w-3 h-3" />
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
      <div className={cn(
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
                {msg.role === "assistant" && msg.content ? (
                  <div>
                    {msg.thinkingContent && (
                      <details className="mb-2">
                        <summary className="text-[10px] text-muted-foreground/60 cursor-pointer hover:text-muted-foreground transition-colors">
                          Thinking
                        </summary>
                        <p className="mt-1 text-[10px] text-muted-foreground/50 whitespace-pre-wrap leading-relaxed italic">
                          {msg.thinkingContent}
                        </p>
                      </details>
                    )}
                    <div className="prose prose-sm dark:prose-invert max-w-none break-words [&_table]:text-xs [&_th]:px-2 [&_th]:py-1 [&_td]:px-2 [&_td]:py-1 [&_table]:block [&_table]:overflow-x-auto [&_pre]:text-xs">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                    </div>
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
                            const displayName = src ? getDisplayName(src) : "Unknown"
                            return (
                              <div
                                key={i}
                                className={cn(
                                  "text-[10px] text-muted-foreground bg-muted rounded p-1.5 border-b border-dashed border-border/50 last:border-0",
                                  src && onSourceClick && "cursor-pointer hover:bg-primary/10 hover:text-foreground transition-colors",
                                )}
                                onClick={() => {
                                  if (src && onSourceClick) onSourceClick(src, chunkIdx)
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
            <div ref={messagesEndRef} />
          </div>
        ) : (
          /* Empty state — centered, fades out when messages appear */
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
                {collectionName}
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
        {panelVisible && panelContent}
      </div>

      {/* ── Floating button ── */}
      <button
        onClick={open ? onClose : onOpen}
        className={cn(
          "fixed right-6 z-50 transition-all ease-out quick-chat-btn",
          open && "quick-chat-btn-spinning",
        )}
        style={{
          color: "var(--ze-green, #1A5E3D)",
          transform: open ? `translateX(-${SIDEBAR_W}px)` : "translateX(0)",
        }}
        aria-label={open ? "Close Quick Q&A" : "Open Quick Q&A"}
      >
        <DiamondIcon className="w-8 h-8" />
      </button>
    </>
  )
}

// ── Chat Input Bar ──

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
          placeholder="Ask about this collection..."
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
