import { useReportReviewNavigation } from "@/hooks/use-report-review"
import { ReportViewer } from "./report-viewer"
import { ReportResizeHandle } from "./report-resize-handle"
import { ConversationAssets } from "./conversation-assets"
import type { ReportReference } from "@/api/client"
import { selectedConversationReport } from "@/lib/chat-reports"
import { useEffect, useRef, useState, useCallback, type CSSProperties } from "react"
import { useShallow } from "zustand/react/shallow"
import { useAppStore } from "@/stores/app-store"
import { MessageBubble } from "./message-bubble"
import { ChatInput } from "./chat-input"
import { SessionSidebar } from "./session-sidebar"
import { ArrowDown } from "lucide-react"
import { getLLMProviders } from "@/api/client"
import { cn } from "@/lib/utils"
import { subscribeDomain } from "@/lib/domain-refresh"
import { useT } from "@/i18n/use-t"

const THREAD_FADE_MS = 240

export function ChatView() {
  const t = useT()
  const reportRatio = useAppStore((state) => state.reportChatRatio)
  const {
    messages,
    setProviders,
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
  const [reportSelection, setSelectedReport] = useState<ReportReference | null>(null)
  useReportReviewNavigation(sessionId, setSelectedReport)
  const selectedReport = selectedConversationReport(reportSelection, sessionId, messages)
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
    const loadProviders = async () => {
      try {
        const list = await getLLMProviders()
        setProviders(list)
      } catch {
        // ignore
      }
    }
    loadProviders()
  }, [])

  // Another device changed the provider/model config → refresh this read-only
  // list so the model picker is not stale. Deliberately only refreshes the list:
  // it must not touch an open settings form, which would clobber in-progress
  // edits there.
  useEffect(() => {
    return subscribeDomain("config", () => {
      void (async () => {
        try {
          setProviders(await getLLMProviders())
        } catch {
          // ignore
        }
      })()
    })
  }, [setProviders])

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
    /* Only upward scroll intent breaks the pin — a downward wheel that lands
       past the bottom fires no scroll event, so onScroll could never re-pin
       after one cleared the flag (thread unpinned while visually at bottom). */
    const onWheel = (e: WheelEvent) => {
      if (e.deltaY < 0) unlockStick()
    }
    let lastTouchY = 0
    const onTouchStart = (e: TouchEvent) => {
      lastTouchY = e.touches[0]?.clientY ?? 0
    }
    const onTouchMove = (e: TouchEvent) => {
      const y = e.touches[0]?.clientY ?? 0
      if (y > lastTouchY) unlockStick()
      lastTouchY = y
    }
    el.addEventListener("wheel", onWheel, { passive: true })
    el.addEventListener("touchstart", onTouchStart, { passive: true })
    el.addEventListener("touchmove", onTouchMove, { passive: true })
    return () => {
      el.removeEventListener("wheel", onWheel)
      el.removeEventListener("touchstart", onTouchStart)
      el.removeEventListener("touchmove", onTouchMove)
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

  const currentSession = sessions.find((s) => s.id === sessionId)
  const sessionTitle = currentSession?.title || "New Chat"

  return (
    <div className="pm-chat-report-layout" style={{ "--pm-report-left-w": `${reportRatio * 100}%` } as CSSProperties}>
    <div className="pm-chat">
      <div className="pm-chat-body">
        <SessionSidebar collapsed={!!selectedReport} />

        <div
          className="pm-chat-stage"
        >
          <div className="pm-chat-stage-surface">
            <div className="pm-chat-stage-head">
              <h1 className="pm-chat-stage-title" title={sessionTitle}>
                {sessionTitle}
              </h1>
              <ConversationAssets key={sessionId} messages={sessionHydratedId === sessionId ? messages : []} sessionId={sessionId} refreshKey={`${messages.length}:${isStreaming}`} onOpenReport={setSelectedReport} />
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
                          onOpenReport={setSelectedReport}
                          key={msg.id}
                          message={msg}
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

      </div>
    </div>
    {selectedReport && <><ReportResizeHandle /><ReportViewer key={selectedReport.id} report={selectedReport} embedded onClose={() => setSelectedReport(null)} /></>}
    </div>
  )
}
