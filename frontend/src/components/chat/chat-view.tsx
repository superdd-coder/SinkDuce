import { useEffect, useRef, useState, useCallback } from "react"
import { useShallow } from "zustand/react/shallow"
import { useAppStore } from "@/stores/app-store"
import { MessageBubble } from "./message-bubble"
import { ChatInput } from "./chat-input"
import { SourceDetailPanel } from "./source-detail-panel"
import { SessionSidebar } from "./session-sidebar"
import { PanelRightClose, ArrowDown } from "lucide-react"
import { Button } from "@/components/ui/button"
import { getLLMProviders } from "@/api/client"
import type { Source } from "@/stores/app-store"

export function ChatView() {
  const {
    messages,
    setProviders,
    setActiveProvider,
    setActiveModel,
    activeProvider,
    activeModel,
    sessionId,
    sessionHydratedId,
    sessionLoading,
    sessions,
    isStreaming,
    loadSessionMessages,
  } = useAppStore(
    useShallow((s) => ({
      messages: s.messages,
      setProviders: s.setProviders,
      setActiveProvider: s.setActiveProvider,
      setActiveModel: s.setActiveModel,
      activeProvider: s.activeProvider,
      activeModel: s.activeModel,
      sessionId: s.sessionId,
      sessionHydratedId: s.sessionHydratedId,
      sessionLoading: s.sessionLoading,
      sessions: s.sessions,
      isStreaming: s.isStreaming,
      loadSessionMessages: s.loadSessionMessages,
    }))
  )
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  /** Stick to bottom while streaming until user intentionally scrolls. */
  const stickToBottom = useRef(true)
  /** Ignore scroll events caused by our own programmatic scroll. */
  const ignoreScrollEvent = useRef(false)
  const [selectedSource, setSelectedSource] = useState<Source | null>(null)
  const [showScrollBtn, setShowScrollBtn] = useState(false)

  useEffect(() => {
    const loadProviders = async () => {
      try {
        const list = await getLLMProviders()
        setProviders(list)
        if (!activeProvider) {
          const defaultP = list.find((p) => p.is_default) || list[0]
          if (defaultP) {
            setActiveProvider(defaultP.id)
            if (!activeModel) {
              setActiveModel(defaultP.default_model || defaultP.selected_models?.[0] || defaultP.model || null)
            }
          }
        }
      } catch {
        // ignore
      }
    }
    loadProviders()
  }, [])

  // Restore history whenever the active session is not yet hydrated
  // (sessionId is persisted; messages are not — blank UI used to reuse an old session).
  useEffect(() => {
    if (!sessionId) return
    if (sessionHydratedId === sessionId) return
    if (sessionLoading) return
    void loadSessionMessages(sessionId)
  }, [sessionId, sessionHydratedId, sessionLoading, loadSessionMessages])

  // Open / switch session → jump to bottom once history is hydrated
  useEffect(() => {
    if (!sessionId || sessionHydratedId !== sessionId || sessionLoading) return
    stickToBottom.current = true
    setShowScrollBtn(false)
    // Wait for message DOM after hydrate
    const t = window.setTimeout(() => {
      const el = scrollRef.current
      if (!el) return
      ignoreScrollEvent.current = true
      el.scrollTop = el.scrollHeight
      requestAnimationFrame(() => {
        ignoreScrollEvent.current = false
      })
    }, 50)
    return () => clearTimeout(t)
  }, [sessionId, sessionHydratedId, sessionLoading])

  const pinRaf = useRef(0)
  const pinToBottom = useCallback((behavior: ScrollBehavior = "auto") => {
    const el = scrollRef.current
    if (!el) return
    // Coalesce rapid stream updates into one scroll per frame (prevents main-thread freeze)
    if (behavior !== "smooth") {
      if (pinRaf.current) return
      pinRaf.current = requestAnimationFrame(() => {
        pinRaf.current = 0
        const box = scrollRef.current
        if (!box || !stickToBottom.current) return
        ignoreScrollEvent.current = true
        box.scrollTop = box.scrollHeight
        requestAnimationFrame(() => {
          ignoreScrollEvent.current = false
        })
      })
      return
    }
    ignoreScrollEvent.current = true
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" })
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        ignoreScrollEvent.current = false
      })
    })
  }, [])

  const unlockStick = useCallback(() => {
    if (ignoreScrollEvent.current) return
    if (!stickToBottom.current) return
    stickToBottom.current = false
    setShowScrollBtn(true)
  }, [])

  // User wheel / touch → stop auto-follow for this stream
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onWheel = () => unlockStick()
    const onTouch = () => unlockStick()
    el.addEventListener("wheel", onWheel, { passive: true })
    el.addEventListener("touchmove", onTouch, { passive: true })
    return () => {
      el.removeEventListener("wheel", onWheel)
      el.removeEventListener("touchmove", onTouch)
    }
  }, [unlockStick])

  const onScroll = useCallback(() => {
    if (ignoreScrollEvent.current) return
    const el = scrollRef.current
    if (!el) return
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight
    // Scrolling away from bottom unlocks stick
    if (dist > 80) {
      stickToBottom.current = false
      setShowScrollBtn(true)
    } else {
      setShowScrollBtn(false)
    }
  }, [])

  /** Re-enable stick (jump-to-bottom button or new user message). */
  const scrollToBottom = useCallback(() => {
    stickToBottom.current = true
    setShowScrollBtn(false)
    if (pinRaf.current) {
      cancelAnimationFrame(pinRaf.current)
      pinRaf.current = 0
    }
    pinToBottom("smooth")
  }, [pinToBottom])

  // New send / stream starts → re-stick and pin bottom
  const wasStreaming = useRef(false)
  useEffect(() => {
    if (isStreaming && !wasStreaming.current) {
      stickToBottom.current = true
      setShowScrollBtn(false)
      pinToBottom("auto")
    }
    wasStreaming.current = isStreaming
  }, [isStreaming, pinToBottom])

  // While stuck, follow content growth (throttled via rAF inside pinToBottom)
  useEffect(() => {
    if (stickToBottom.current) {
      pinToBottom("auto")
    }
  }, [messages, pinToBottom])

  const handleSelectSource = (source: Source) => {
    setSelectedSource(source)
  }

  const handleClosePanel = () => {
    setSelectedSource(null)
  }

  const selectedSourceId = (selectedSource?.metadata?.id as string) || null

  const currentSession = sessions.find(s => s.id === sessionId)
  const sessionTitle = currentSession?.title || "New Chat"

  return (
    <div className={`flex flex-col h-full overflow-hidden relative ${isStreaming ? "sk-reasoning-flow" : ""}`}>
      <div className="flex-1 flex min-h-0">
        {/* Session sidebar — left */}
        <SessionSidebar />

        {/* Main chat area */}
        <div className={`flex flex-col flex-1 min-w-0 relative ${selectedSource ? "hidden sm:flex" : ""}`}>
          {/* Session title header — h-12 (48px) matches Collections/Sessions headers */}
          <div className="shrink-0 px-12 h-12 flex items-center border-b border-border/30">
            <h1
              className="truncate t-body-family"
              style={{
                fontSize: "clamp(20px, 2vw, 24px)",
                fontWeight: 300,
                letterSpacing: "-0.01em",
                lineHeight: 1.2,
                color: "var(--ze-ink)",
              }}
            >
              {sessionTitle}
            </h1>
          </div>

          <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto pb-44 relative">
            {sessionLoading || (sessionId && sessionHydratedId !== sessionId) ? (
              <div
                className="flex flex-col items-center justify-center h-full gap-2 py-20"
                style={{ color: "var(--ze-muted)" }}
              >
                <p className="text-sm t-body-family" style={{ color: "var(--ze-ink)" }}>
                  Loading conversation…
                </p>
                <p className="text-xs">Restoring messages for the selected session</p>
              </div>
            ) : messages.length === 0 ? (
              <div
                className="flex flex-col items-center justify-center h-full gap-2 py-20"
                style={{ color: "var(--ze-muted)" }}
              >
                <p
                  className="text-sm font-medium t-body-family"
                  style={{ color: "var(--ze-ink)" }}
                >
                  Ask a question about your documents
                </p>
                <p className="text-xs">Upload documents first, then start chatting</p>
              </div>
            ) : (
              <div className="max-w-4xl mx-auto py-4 px-12">
                {messages.map((msg) => (
                  <MessageBubble
                    key={msg.id}
                    message={msg}
                    onSelectSource={handleSelectSource}
                    selectedSourceId={selectedSourceId}
                  />
                ))}
                <div ref={bottomRef} />
              </div>
            )}
          </div>

          {/* Jump-to-bottom button */}
          {showScrollBtn && (
            <div className="absolute bottom-36 left-1/2 -translate-x-1/2 z-10">
              <button
                type="button"
                onClick={scrollToBottom}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-background/80 backdrop-blur border border-border shadow-sm text-[10px] text-muted-foreground hover:text-foreground transition-colors"
              >
                <ArrowDown className="h-3 w-3" />
                Scroll to bottom
              </button>
            </div>
          )}

          {/* Floating chat input */}
          <div className={`absolute bottom-4 left-0 right-0 z-10 pointer-events-none transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)]`}>
            <div className="pointer-events-auto">
              <ChatInput />
            </div>
          </div>
        </div>

        {/* Right-side source detail panel */}
        <div className={`shrink-0 overflow-hidden transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${selectedSource ? "w-full sm:w-[42vw]" : "w-0"}`}>
          <div className={`w-full sm:w-[42vw] h-full transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${selectedSource ? "translate-x-0 opacity-100" : "translate-x-8 opacity-0"}`}>
            <div className="sm:hidden absolute top-0 right-0 z-10 p-2">
              <Button variant="ghost" size="sm" onClick={handleClosePanel}>
                <PanelRightClose className="h-4 w-4 mr-1" /> Back to chat
              </Button>
            </div>
            <SourceDetailPanel source={selectedSource} onClose={handleClosePanel} />
          </div>
        </div>
      </div>
    </div>
  )
}
