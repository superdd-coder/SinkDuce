import { useEffect, useRef, useState, useCallback } from "react"
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
  const { messages, setProviders, setActiveProvider, setActiveModel, activeProvider, activeModel, sessionId, sessions, isStreaming } = useAppStore()
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const userScrolledUp = useRef(false)
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

  useEffect(() => {
    const { sessionId, loadSessionMessages, messages } = useAppStore.getState()
    // Only load from backend if we don't already have messages for this session
    if (sessionId && messages.length === 0) {
      loadSessionMessages(sessionId)
    }
  }, [])

  // Track scroll position — only auto-scroll if user is near bottom
  const onScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight
    const up = dist > 80
    userScrolledUp.current = up
    setShowScrollBtn(up)
  }, [])

  const scrollToBottom = useCallback(() => {
    userScrolledUp.current = false
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [])

  useEffect(() => {
    if (!userScrolledUp.current) {
      bottomRef.current?.scrollIntoView({ behavior: "instant" as any })
    }
  }, [messages])

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
    <div className={`flex flex-col h-full overflow-hidden relative ${isStreaming ? "sk-reasoning-flow" : ""}`} style={isStreaming ? { border: "1.5px solid transparent" } : undefined}>
      <div className="flex-1 flex min-h-0">
        {/* Session sidebar — left */}
        <SessionSidebar />

        {/* Main chat area */}
        <div className={`flex flex-col flex-1 min-w-0 relative ${selectedSource ? "hidden sm:flex" : ""}`}>
          {/* Session title header */}
          <div className="shrink-0 px-12 pt-5 pb-3 border-b border-border/30">
            <h1
              className="text-[15px] font-[400] tracking-[-0.01em] text-foreground/80 truncate"
              style={{ fontFamily: "var(--font-serif)" }}
            >
              {sessionTitle}
            </h1>
          </div>

          <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto pb-44 relative">
            {messages.length === 0 ? (
              <div
                className="flex flex-col items-center justify-center h-full gap-2 py-20"
                style={{ color: "var(--ze-muted)" }}
              >
                <p
                  className="text-sm font-medium"
                  style={{ color: "var(--ze-ink)", fontFamily: "var(--font-serif)" }}
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

          {/* Floating chat input — positioned within main chat area */}
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
