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
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"

const THREAD_FADE_MS = 240

export function ChatView() {
  const t = useT()
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
    setSidebarView,
    setActiveMeeting,
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
      setSidebarView: s.setSidebarView,
      setActiveMeeting: s.setActiveMeeting,
    }))
  )
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  /** Stick to bottom while streaming until user intentionally scrolls. */
  const stickToBottom = useRef(true)
  /** Ignore scroll events caused by our own programmatic scroll. */
  const ignoreScrollEvent = useRef(false)
  const [selectedSource, setSelectedSource] = useState<Source | null>(null)
  /** Keep last source mounted during rail close so width/opacity exit can play. */
  const [panelSource, setPanelSource] = useState<Source | null>(null)
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  /** Edge fades when content scrolls out under title / stage bottom. */
  const [edgeFade, setEdgeFade] = useState({ top: false, bottom: false })

  /**
   * Frozen display while switching sessions — avoids collapsing the thread
   * (layout jitter) when store messages clear mid-load. Fade opacity only.
   */
  const [threadVisible, setThreadVisible] = useState(true)
  const [displayMessages, setDisplayMessages] = useState(messages)
  const [displayEmpty, setDisplayEmpty] = useState(messages.length === 0)
  /** Session whose content is currently painted (undefined = never committed). */
  const paintedSessionRef = useRef<string | null | undefined>(undefined)
  const fadeTimerRef = useRef(0)

  useEffect(() => {
    if (selectedSource) {
      setPanelSource(selectedSource)
      return
    }
    const t = window.setTimeout(() => setPanelSource(null), 420)
    return () => window.clearTimeout(t)
  }, [selectedSource])

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
              setActiveModel(
                defaultP.default_model ||
                  defaultP.selected_models?.[0] ||
                  defaultP.model ||
                  null
              )
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
  useEffect(() => {
    if (!sessionId) return
    if (sessionHydratedId === sessionId) return
    if (sessionLoading) return
    void loadSessionMessages(sessionId)
  }, [sessionId, sessionHydratedId, sessionLoading, loadSessionMessages])

  const pinRaf = useRef(0)
  const pinToBottom = useCallback((behavior: ScrollBehavior = "auto") => {
    const el = scrollRef.current
    if (!el) return
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

  const updateEdgeFade = useCallback(() => {
    const el = scrollRef.current
    if (!el) {
      setEdgeFade({ top: false, bottom: false })
      return
    }
    const threshold = 6
    const top = el.scrollTop > threshold
    const bottom = el.scrollHeight - el.scrollTop - el.clientHeight > threshold
    setEdgeFade((prev) =>
      prev.top === top && prev.bottom === bottom ? prev : { top, bottom }
    )
  }, [])

  const onScroll = useCallback(() => {
    if (ignoreScrollEvent.current) {
      updateEdgeFade()
      return
    }
    const el = scrollRef.current
    if (!el) return
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight
    if (dist > 80) {
      stickToBottom.current = false
      setShowScrollBtn(true)
    } else {
      stickToBottom.current = true
      setShowScrollBtn(false)
    }
    updateEdgeFade()
  }, [updateEdgeFade])

  const scrollToBottom = useCallback(() => {
    stickToBottom.current = true
    setShowScrollBtn(false)
    if (pinRaf.current) {
      cancelAnimationFrame(pinRaf.current)
      pinRaf.current = 0
    }
    pinToBottom("smooth")
  }, [pinToBottom])

  const wasStreaming = useRef(false)
  useEffect(() => {
    if (isStreaming && !wasStreaming.current) {
      stickToBottom.current = true
      setShowScrollBtn(false)
      pinToBottom("auto")
    }
    wasStreaming.current = isStreaming
  }, [isStreaming, pinToBottom])

  const lastPinAt = useRef(0)
  useEffect(() => {
    if (!stickToBottom.current) return
    // Only pin live stream / same-session updates when thread is visible
    if (!threadVisible) return
    if (paintedSessionMismatch()) return
    if (isStreaming) {
      const now = Date.now()
      if (now - lastPinAt.current < 120) return
      lastPinAt.current = now
    }
    pinToBottom("auto")
  }, [messages, pinToBottom, isStreaming, threadVisible])

  function paintedSessionMismatch() {
    return paintedSessionRef.current !== sessionId
  }

  // Session id changed → fade out (keep frozen paint to avoid height collapse)
  useEffect(() => {
    if (paintedSessionRef.current === undefined) return
    if (sessionId === paintedSessionRef.current) return
    setThreadVisible(false)
  }, [sessionId])

  // Ready session → commit display while hidden, pin bottom, fade in
  useEffect(() => {
    const ready =
      !sessionId ||
      (sessionHydratedId === sessionId && !sessionLoading)
    if (!ready) return
    if (paintedSessionRef.current === sessionId) return

    const commit = () => {
      paintedSessionRef.current = sessionId
      const msgs = useAppStore.getState().messages
      setDisplayMessages(msgs)
      setDisplayEmpty(msgs.length === 0)
      stickToBottom.current = true
      setShowScrollBtn(false)
      requestAnimationFrame(() => {
        const el = scrollRef.current
        if (el) {
          ignoreScrollEvent.current = true
          el.scrollTop = el.scrollHeight
          requestAnimationFrame(() => {
            ignoreScrollEvent.current = false
            setThreadVisible(true)
            updateEdgeFade()
          })
        } else {
          setThreadVisible(true)
        }
      })
    }

    // First paint: no out-fade delay
    if (paintedSessionRef.current === undefined) {
      commit()
      return
    }

    setThreadVisible(false)
    if (fadeTimerRef.current) window.clearTimeout(fadeTimerRef.current)
    fadeTimerRef.current = window.setTimeout(commit, THREAD_FADE_MS)

    return () => {
      if (fadeTimerRef.current) window.clearTimeout(fadeTimerRef.current)
    }
  }, [sessionId, sessionHydratedId, sessionLoading, updateEdgeFade])

  // Live streaming / append while this session is painted
  useEffect(() => {
    if (paintedSessionRef.current !== sessionId) return
    if (sessionLoading) return
    if (sessionId && sessionHydratedId !== sessionId) return
    setDisplayMessages(messages)
    setDisplayEmpty(messages.length === 0)
  }, [messages, sessionId, sessionLoading, sessionHydratedId])

  // Keep edge fades in sync when content height changes
  useEffect(() => {
    updateEdgeFade()
    const el = scrollRef.current
    if (!el || typeof ResizeObserver === "undefined") return
    const ro = new ResizeObserver(() => updateEdgeFade())
    ro.observe(el)
    if (el.firstElementChild) ro.observe(el.firstElementChild)
    return () => ro.disconnect()
  }, [displayMessages, sessionId, updateEdgeFade])

  const handleSelectSource = (source: Source) => {
    const meta = source.metadata || {}
    if (meta.source_type === "meeting") {
      const mid = String(meta.meeting_id || "").trim()
      if (mid) {
        setActiveMeeting(mid)
        setSidebarView("meeting")
      }
      return
    }
    setSelectedSource(source)
  }

  const handleClosePanel = useCallback(() => {
    setSelectedSource(null)
  }, [])

  const selectedSourceId = (selectedSource?.metadata?.id as string) || null
  const currentSession = sessions.find((s) => s.id === sessionId)
  const sessionTitle = currentSession?.title || "New Chat"
  const sourceOpen = !!selectedSource

  return (
    <div className="pm-chat">
      <div className="pm-chat-body">
        <SessionSidebar />

        <div
          className={cn("pm-chat-stage", sourceOpen && "hidden sm:flex")}
        >
          <div className="pm-chat-stage-surface">
            <div className="pm-chat-stage-head">
              <h1 className="pm-chat-stage-title" title={sessionTitle}>
                {sessionTitle}
              </h1>
            </div>

            <div className="pm-chat-thread-shell">
              <div
                ref={scrollRef}
                onScroll={onScroll}
                className="pm-chat-thread"
              >
                <div
                  className={cn(
                    "pm-chat-thread-fade",
                    threadVisible ? "is-in" : "is-out"
                  )}
                >
                  {displayEmpty ? (
                    <div className="pm-chat-empty">
                      <p className="pm-chat-empty-title">
                        {t("chat.emptyTitle")}
                      </p>
                      <p className="pm-chat-empty-sub">
                        {t("chat.emptyHint")}
                      </p>
                    </div>
                  ) : (
                    <div className="pm-chat-thread-inner">
                      {displayMessages.map((msg) => (
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
              </div>

              <div
                aria-hidden
                className={cn(
                  "pm-chat-edge-fade pm-chat-edge-fade--top",
                  edgeFade.top && "is-visible"
                )}
              />
            </div>

            <div
              aria-hidden
              className={cn(
                "pm-chat-edge-fade pm-chat-edge-fade--bottom",
                edgeFade.bottom && "is-visible"
              )}
            />

            {showScrollBtn && (
              <button
                type="button"
                onClick={scrollToBottom}
                className="pm-chat-scroll-btn"
              >
                <ArrowDown className="size-3" />
                {t("chat.scrollBottom")}
              </button>
            )}

            <div className="pm-chat-composer-dock">
              <ChatInput />
            </div>
          </div>
        </div>

        <div
          className={cn(
            "pm-chat-source-panel-host",
            sourceOpen && "is-open"
          )}
        >
          <div className="relative h-full min-h-0 overflow-visible">
            {sourceOpen && (
              <div className="sm:hidden absolute top-2 right-2 z-20">
                <Button variant="ghost" size="sm" onClick={handleClosePanel}>
                  <PanelRightClose className="size-4" />
                  Back
                </Button>
              </div>
            )}
            <SourceDetailPanel
              source={panelSource}
              onClose={handleClosePanel}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
