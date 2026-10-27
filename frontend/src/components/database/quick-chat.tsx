import { useReportReview, useReportReviewNavigation, consumeReportComments, consumeSubmittedReviews, refreshReportComments } from "@/hooks/use-report-review"
import { canSendReview, reviewRequests, reviewSnapshots, type ReviewBatch } from "@/lib/report-review"
import { ReportReviewChip, ReportReviewHistory } from "@/components/chat/report-review-chip"
import { ReportWorkspace } from "@/components/chat/report-workspace"
import { ConversationAssets } from "@/components/chat/conversation-assets"
import { CitationScope } from "@/components/chat/citation-scope"
import { ReportCards } from "@/components/chat/report-cards"
import { SourcesCard } from "@/components/chat/sources-card"
import { applyChatArtifacts, selectedConversationReport, type ReportReference } from "@/lib/chat-reports"
import type { Evidence } from "@/lib/chat-citations"
import { useChatAttachments, type ChatAttachmentsManager } from "@/hooks/use-chat-attachments"
import { ChatAttachmentCards, ChatAttachmentDrafts, ChatAttachmentPicker } from "@/components/chat/chat-attachments"
import type { ChatAttachment } from "@/api/client"
import { useState, useRef, useEffect, useCallback, useLayoutEffect } from "react"
import { createPortal } from "react-dom"
import { Send, Loader2, AlertTriangle, Globe, MessageCircle, BrushCleaning } from "lucide-react"
import { cn } from "@/lib/utils"
import { newId } from "@/lib/uuid"
import { useAppStore } from "@/stores/app-store"
import { createImeEnterGuard } from "@/lib/ime"
import { useT } from "@/i18n/use-t"
import { StreamingAnswerBody } from "@/components/chat/streaming-answer-body"
import { NoteChangeCards } from "@/components/chat/note-change-cards"
import { mergeNoteChanges, type NoteChange } from "@/lib/note-changes"
import { createSession, getSession, deleteSession, iterateSessionSse, postSessionMessage } from "@/api/client"
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
  attachments?: ChatAttachment[]
  thinkingContent?: string
  sources?: { text: string; score: number; metadata: Record<string, unknown> }[]
  noteChanges?: NoteChange[]
  citations?: Evidence[]
  reportChanges?: ReportReference[]
  reportReviews?: ReviewBatch[]
  isStreaming?: boolean
  isNew?: boolean
}

export function QuickChatAnswer({ content, isStreaming, citations, sessionId = "" }: { content: string; isStreaming: boolean; citations?: Evidence[]; sessionId?: string }) {
  return (
    <CitationScope citations={citations} source={content} sessionId={sessionId}><StreamingAnswerBody
      content={content}
      isStreaming={isStreaming}
      className="pm-qc-answer break-words [&_table]:text-[11px] [&_th]:px-1.5 [&_th]:py-0.5 [&_td]:px-1.5 [&_td]:py-0.5 [&_table]:block [&_table]:overflow-x-auto [&_pre]:text-[11px]"
      richContent
    /></CitationScope>
  )
}

const QUICK_SESSION_PREFIX = "quick_"
const WARN_THRESHOLD = 20
const MAX_MESSAGES = 30
/** Slide duration — keep in sync with CSS --pm-qc-motion if added */
const ANIM_DURATION = 320
const RAIL_ANCHOR_SEL = "[data-pm-rail-anchor]"
/** Stable diamond park on stage — same top-right for all content tabs */
import { LogoMark } from "../shared/logo-mark"

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
   * Show the QC FAB. False on Config (Settings) — icon stays parked
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
  className,
  railActive = true,
  railKey,
  fabVisible = true,
}: QuickChatProps) {
  const t = useT()
  const [messages, setMessages] = useState<QAMessage[]>([])
  const [reportSelection, setPreviewReport] = useState<ReportReference | null>(null)
  const [input, setInput] = useState("")
  const [streamingBySid, setStreamingBySid] = useState<Record<string, boolean>>({})
  const [sessionId, setSessionId] = useState<string | null>(null)
  useReportReviewNavigation(sessionId, setPreviewReport)
  const [msgCount, setMsgCount] = useState(0)
  const [loadingHistory, setLoadingHistory] = useState(true)
  // Web toggle remembered per quick session (quick_<collectionId>); default OFF
  // 与 Chat 一致：住在 store（不再散在组件本地 state）
  const webSearch = useAppStore((s) => s.webSearch)
  const setWebSearch = useAppStore((s) => s.setWebSearch)
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

  const initSession = async (sid: string) => {
    setLoadingHistory(true)
    try {
      const detail = await getSession(sid).catch(() => null)
      // 恢复该会话自己的一套配置（模型/Web/思考/知识库），与 Chat 走同一入口
      if (detail) void useAppStore.getState().applySessionConfig(detail.config)
      if (detail?.messages?.length) {
        // Dialogue only: skip tool rows and empty tool-call placeholders
        const msgs: QAMessage[] = detail.messages
          .filter((m) => {
            if (m.role === "user") return true
            if (m.role === "assistant") {
              const meta = (m.metadata ?? {}) as Record<string, unknown>
              const reports = applyChatArtifacts({}, meta) as { reportChanges?: ReportReference[] }
              if (!m.content && meta.tool_calls && !mergeNoteChanges([], meta.note_changes).length && !reports.reportChanges?.length) return false
              return !!(m.content || "").trim() || mergeNoteChanges([], meta.note_changes).length > 0 || !!reports.reportChanges?.length
            }
            return false
          })
          .map((m) => ({
            id: m.id,
            role: m.role as "user" | "assistant",
            content: m.role === "user" && typeof m.metadata?.display_content === "string" ? m.metadata.display_content : m.content,
            attachments: m.metadata?.attachments as ChatAttachment[] | undefined,
            sources: m.sources ?? undefined,
            noteChanges: mergeNoteChanges([], m.metadata?.note_changes),
            ...applyChatArtifacts({}, (m.metadata || {}) as Record<string, unknown>),
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

  const attachments = useChatAttachments(sessionId, undefined, streaming || loadingHistory)
  const review = useReportReview(sessionId, streaming)

  const send = useCallback(async () => {
    const text = input.trim()
    const sid = sessionId
    if (!canSendReview(input, attachments.drafts, review.hasComments) || !sid || streamingBySidRef.current[sid]) return

    const comments = useAppStore.getState().reportComments[sessionId!] || []
    const reviews = reviewRequests(comments)
    setInput("")
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto"
    }

    // New user message → re-stick to bottom for this stream
    stickToBottom.current = true

    const userMsg: QAMessage = { id: newId(), role: "user", content: text, attachments: attachments.ready, reportReviews: reviewSnapshots(comments,text), isNew: true }
    const assistantId = newId()
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
    const addNoteChangesLocal = (incoming: unknown) => {
      applySid((prev) => prev.map((m) => m.id === assistantId
        ? { ...m, noteChanges: mergeNoteChanges(m.noteChanges, incoming) }
        : m))
    }

    try {
      const resp = await postSessionMessage(
        sid,
        {
          content: text,
          attachment_ids: attachments.ready.map((a) => a.id),
          ...(reviews.length ? {report_reviews:reviews} : {}),
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

      if (reviews.length) consumeReportComments(sid, reviews.flatMap(r=>r.comment_ids))
      if (resp.body) {
        for await (const { event: eventType, data } of iterateSessionSse(resp.body)) {
              if (eventType === "report_review_incomplete") {
                if (!data.message_rendered) appendTokenLocal("\n\n"+t("chat.review.incomplete"))
              } else if (eventType === "report_review_submitted") {
                consumeSubmittedReviews(sid,data.report_reviews)
                applySid(prev=>prev.map(m=>m.id===userMsg.id ? applyChatArtifacts(m,data) : m))
              } else if (eventType === "web_search_confirm") {
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
                const reportLabel = { create_report: "chat.reports.createTool", get_report: "chat.reports.readTool", update_report: "chat.reports.updateTool", revise_report: "chat.reports.updateTool" }[tool]
                const label = reportLabel ? t(reportLabel) : tool
                const q = data.raw_query || data.query
                const line = q
                  ? `\n📡 ${label}: ${String(q).slice(0, 120)}`
                  : `\n📡 ${label}`
                appendThinkingLocal(line)
              } else if (eventType === "searching") {
                const q = String(data.query || "")
                appendThinkingLocal(q ? `\n🔍 ${q}` : `\n🔍 ${t("chat.searching")}`)
              } else if (eventType === "tool_result") {
                applySid((previous) => previous.map((m) => m.id === assistantMsg.id ? applyChatArtifacts(m, data) : m))
                if (data.note_changes) addNoteChangesLocal(data.note_changes)
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
              } else if (eventType === "citations") {
                applySid((previous) => previous.map((m) => m.id === assistantMsg.id ? applyChatArtifacts(m, data) : m))
              } else if (eventType === "done") {
                applySid((previous) => previous.map((m) => m.id === assistantMsg.id ? applyChatArtifacts(m, data) : m))
                if (data.note_changes) addNoteChangesLocal(data.note_changes)
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
      if (reviews.length) {void refreshReportComments(sid).catch(()=>{});if(controller.signal.aborted)setTimeout(()=>void refreshReportComments(sid).catch(()=>{}),500)}
      markStreaming(sid, false)
      abortBySidRef.current.delete(sid)
    }
  }, [input, sessionId, collectionId, webSearch, pinToBottom, t, attachments, review.hasComments])

  const toggleWebSearch = () => {
    const next = !useAppStore.getState().webSearch
    setWebSearch(next)
    if (!next && sessionId) {
      void import("@/lib/web-search-confirm").then((m) =>
        m.clearWebSearchAlwaysAllow(sessionId),
      )
    }
  }

  // ── Clear ──

  const clearContext = async () => {
    if (!sessionId) return
    setPreviewReport(null)
    abortBySidRef.current.get(sessionId)?.abort()
    abortBySidRef.current.delete(sessionId)
    markStreaming(sessionId, false)
    messagesCacheRef.current.delete(sessionId)
    attachments.reset()
    try { await deleteSession(sessionId); useAppStore.getState().clearReportReviewSession(sessionId) } catch { /* ok */ }
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

  const previewReport = selectedConversationReport(reportSelection, sessionId, messages)

  const hasMessages = messages.length > 0

  // ── Panel content (Premium compact chat chrome) ──

  const panelContent = (
    <div
      {...attachments.dropProps} className="pm-qc-panel">
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
          <ConversationAssets key={sessionId} messages={loadingHistory ? [] : messages} sessionId={sessionId} refreshKey={`${messages.length}:${streaming}`} onOpenReport={setPreviewReport} />
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
                  {msg.content.trim() && <div className="pm-qc-bubble pm-qc-bubble--user">
                    <p className="pm-qc-bubble-text">{msg.content}</p>
                  </div>}
                  <ChatAttachmentCards attachments={msg.attachments} />
                  <ReportReviewHistory batches={msg.reportReviews} />
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
                      <QuickChatAnswer
                        content={msg.content}
                        citations={msg.citations}
                        sessionId={sessionId || ""}
                        isStreaming={!!msg.isStreaming}
                      />
                    ) : msg.isStreaming ? (
                      <div className="pm-qc-typing">
                        <Loader2 className="size-3.5 animate-spin" />
                        <span>{t("chat.educing")}</span>
                      </div>
                    ) : (
                      <p className="pm-qc-bubble-text">{msg.content}</p>
                    )}
                    <ReportCards reports={msg.reportChanges} onOpenReport={setPreviewReport} />
                    {!msg.isStreaming && <NoteChangeCards notes={msg.noteChanges} />}
                    {!msg.isStreaming && !!msg.sources?.length && <CitationScope citations={msg.citations} source={msg.content} sessionId={sessionId || ""}>
                      <SourcesCard sources={msg.sources}/>
                    </CitationScope>}
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
          <ReportReviewChip comments={review.comments} disabled={streaming} />
          <ChatAttachmentDrafts manager={attachments} />
          <div
            ref={webConfirmHostRef}
            data-web-confirm-host="inline"
            className="w-full"
          />
          <ChatInputBar
            hasComments={review.hasComments}
            attachments={attachments}
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

  if (previewReport && open) return <ReportWorkspace report={previewReport} chat={panelContent} onClose={() => setPreviewReport(null)} />

  const ease = "cubic-bezier(0.45, 0.05, 0.55, 0.95)"
  const slideMotion = qcFloatSlideMotion(showFloat)
  const floatPanel = (
      <div
        className={cn(
          "pm-qc-float",
          /* In-rail: fill host exactly. Fallback only when this tab has no rail. */
          railHost ? "pm-qc-float--rail" : "pm-qc-float--fallback",
          showFloat && "is-open",
          className
        )}
        style={{
          pointerEvents: showFloat ? "auto" : "none",
        }}
        aria-hidden={!open}
      >
        {/* Shadow layer behind the clipped card — engine-proof (no filter) */}
        <div className="pm-qc-float-shadow" aria-hidden />
        {/* Clip translate here so the shadow layer stays aligned with the card. */}
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
          type="button"
          onClick={open ? onClose : onOpen}
          className="relative shrink-0 transition-all ease-out quick-chat-btn"
          style={{ color: "var(--ze-green, #1A5E3D)" }}
          aria-label={open ? t("meeting.closeQuickQa") : t("library.openQuickQa")}
        >
          <LogoMark className="qc-logo" flowing={open} />
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
  hasComments,
  attachments,
  input, setInput, streaming, webSearch, onToggleWebSearch, onSend, onKeyDown,
  onCompositionStart, onCompositionEnd, textareaRef,
}: {
  hasComments: boolean
  attachments: ChatAttachmentsManager
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
      <ChatAttachmentPicker manager={attachments} />
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
        disabled={!canSendReview(input, attachments.drafts, hasComments) || streaming}
        className={cn(
          "pm-qc-send",
          canSendReview(input, attachments.drafts, hasComments) && !streaming && "is-ready",
          streaming && "is-hidden"
        )}
        aria-label={t("common.send")}
      >
        <Send className="size-3.5" />
      </button>
    </div>
  )
}
