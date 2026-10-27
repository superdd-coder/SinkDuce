import { useReportReview, useReportReviewNavigation, consumeReportComments, consumeSubmittedReviews, refreshReportComments } from "@/hooks/use-report-review"
import { canSendReview, reviewRequests, reviewSnapshots, type ReviewBatch } from "@/lib/report-review"
import { ReportReviewChip, ReportReviewHistory } from "@/components/chat/report-review-chip"
import { ReportWorkspace } from "@/components/chat/report-workspace"
import { ConversationAssets } from "@/components/chat/conversation-assets"
import { CitationScope } from "@/components/chat/citation-scope"
import { ReportCards } from "@/components/chat/report-cards"
import { NoteChangeCards } from "@/components/chat/note-change-cards"
import { mergeNoteChanges, type NoteChange } from "@/lib/note-changes"
import { SourcesCard } from "@/components/chat/sources-card"
import { StreamingAnswerBody } from "@/components/chat/streaming-answer-body"
import { applyChatArtifacts, selectedConversationReport, type ReportReference } from "@/lib/chat-reports"
import type { Evidence } from "@/lib/chat-citations"
import { useChatAttachments, type ChatAttachmentsManager } from "@/hooks/use-chat-attachments"
import { ChatAttachmentCards, ChatAttachmentDrafts, ChatAttachmentPicker } from "@/components/chat/chat-attachments"
import type { ChatAttachment } from "@/api/client"
import { useState, useRef, useEffect, useCallback, type ReactNode } from "react"
import { Send, Loader2, AlertTriangle, MessageCircle, BrushCleaning } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { newId } from "@/lib/uuid"
import { createImeEnterGuard } from "@/lib/ime"
import { parseMeetingRefGroups, MEETING_CITE_RE_SOURCE } from "@/lib/meeting-ref-chips"
import {
  displayedGroupCites,
  enrichGroupCites,
  GROUP_CITE_HOVER_DELAY_MS,
  GROUP_CITE_RE_SOURCE,
  groupCitesFromToolTrace,
  mergeGroupCites,
  nextGroupCiteOccurrence,
  parseCitesFromToolBody,
  parseGroupCites,
  resolveGroupCite,
  resolveGroupCiteByRefN,
  type CiteMeetingLookup,
  type DisplayedGroupCite,
  type GroupCite,
} from "@/lib/group-cites"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useScrollEdgeFade } from "@/hooks/use-scroll-edge-fade"
import { createSession, getSession, deleteSession, iterateSessionSse, postSessionMessage } from "@/api/client"
import { getMeeting, startTranscriptIndex, type Meeting } from "@/api/meeting"
import { useAppStore } from "@/stores/app-store"
import { useT } from "@/i18n/use-t"
import { tr } from "@/i18n/tr"
import {
  setWebSearchConfirmAnchor,
  getWebSearchConfirmAnchor,
} from "@/lib/web-search-confirm"

// ── Types ──

interface QAMessage {
  id: string
  role: "user" | "assistant"
  content: string
  attachments?: ChatAttachment[]
  citations?: Evidence[]
  reportChanges?: ReportReference[]
  noteChanges?: NoteChange[]
  reportReviews?: ReviewBatch[]
  thinkingContent?: string
  sources?: { text: string; score: number; metadata: Record<string, unknown> }[]
  groupCites?: GroupCite[]
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
const HINT_KEYS = [
  "meeting.qaHint1",
  "meeting.qaHint2",
  "meeting.qaHint3",
  "meeting.qaHint4",
  "meeting.qaHint5",
  "meeting.qaHint6",
  "meeting.qaHint7",
] as const
const HINT_SHOW_DURATION = 4000
const HINT_INITIAL_DELAY = 1000
const HINT_MIN_INTERVAL = 5000
import { LogoMark } from "../shared/logo-mark"

const HINT_MAX_INTERVAL = 15000

/**
 * QC diamond FAB — flow instead of rotation: hover (closed) or open keeps
 * the lake-orb liquid running; amplitude ease handles the smooth transitions.
 */
export function MeetingQcFab({
  open,
  onOpen,
  onClose,
  className,
}: {
  open: boolean
  onOpen: () => void
  onClose: () => void
  className?: string
}) {
  const t = useT()
  const [hintVisible, setHintVisible] = useState(false)
  const [hintExiting, setHintExiting] = useState(false)
  const [hintMessage, setHintMessage] = useState(() => t(HINT_KEYS[0]))
  const hintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const btnRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (open) {
      setHintVisible(false)
      setHintExiting(false)
      if (hintTimerRef.current) clearTimeout(hintTimerRef.current)
      return
    }
    const scheduleHint = () => {
      const msg = t(HINT_KEYS[Math.floor(Math.random() * HINT_KEYS.length)])
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
  }, [open, t])

  return (
    <div className={cn("pm-meeting-qc-fab relative shrink-0", className)}>
      <div className={cn("qc-float-group relative", open && "qc-float-group--static")}>
        {(hintVisible || hintExiting) && !open && (
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
          className="relative ease-out quick-chat-btn pm-meeting-qc-fab-btn"
          style={{ color: "var(--pm-green, #1A5E3D)", background: "transparent" }}
          aria-label={open ? t("meeting.closeQuickQa") : t("library.openQuickQa")}
        >
          <LogoMark className="qc-logo" flowing={open} />
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
  indexStatus?: Meeting["transcript_index_status"]
  /** group — session group_{id}, citations [n], no index overlay */
  sessionKind?: "meeting" | "group"
  onGroupCite?: (n: number, sentenceId?: string, meetingId?: string) => void
  citeMeetings?: CiteMeetingLookup[]
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
  indexStatus: indexStatusProp,
  sessionKind = "meeting",
  onGroupCite,
  citeMeetings,
}: MeetingQuickChatProps) {
  const t = useT()
  const handleGroupCite = useCallback((
    n: number,
    occurrence = 0,
    cites?: GroupCite[],
    refN?: number,
  ) => {
    const cite = resolveGroupCite(cites || [], n, { refN, occurrence })
    onGroupCite?.(n, cite?.sentence_id, cite?.meeting_id)
  }, [onGroupCite])
  const [indexStatus, setIndexStatus] = useState(indexStatusProp || "")
  const [indexError, setIndexError] = useState("")
  const [indexStarting, setIndexStarting] = useState(false)
  const [messages, setMessages] = useState<QAMessage[]>([])
  const [reportSelection, setPreviewReport] = useState<ReportReference | null>(null)
  const [input, setInput] = useState("")
  const [streaming, setStreaming] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  useReportReviewNavigation(sessionId, setPreviewReport)
  const [msgCount, setMsgCount] = useState(0)
  const [loadingHistory, setLoadingHistory] = useState(true)
  const abortRef = useRef<AbortController | null>(null)
  const messagesScrollRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)
  const ignoreScrollEvent = useRef(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const imeGuardRef = useRef(createImeEnterGuard())
  const webConfirmHostRef = useRef<HTMLDivElement | null>(null)
  // ── Hint bubble state ──
  const [hintVisible, setHintVisible] = useState(false)
  const [hintExiting, setHintExiting] = useState(false)
  const [hintMessage, setHintMessage] = useState(() => t(HINT_KEYS[0]))
  const hintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    setIndexStatus(indexStatusProp || "")
  }, [indexStatusProp])

  // Claim / release web-confirm anchor only while this panel is on screen —
  // ChatInput only registers it for sidebarView=chat, so the meeting page
  // would otherwise hold a stale anchor from main Chat and hide HITL cards.
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

  useEffect(() => {
    if (!open || indexStatus !== "building") return
    let stop = false
    const tick = async () => {
      try {
        const m = await getMeeting(meetingId)
        if (stop) return
        setIndexStatus(m.transcript_index_status || "")
        setIndexError(m.transcript_index_error || "")
      } catch { /* ignore */ }
    }
    const id = setInterval(tick, 2000)
    void tick()
    return () => { stop = true; clearInterval(id) }
  }, [open, indexStatus, meetingId])

  const buildIndex = async () => {
    setIndexStarting(true)
    try {
      const m = await startTranscriptIndex(meetingId)
      setIndexStatus(m.transcript_index_status || "building")
      setIndexError(m.transcript_index_error || "")
    } catch (e) {
      setIndexError(e instanceof Error ? e.message : "failed")
    } finally {
      setIndexStarting(false)
    }
  }

  // ── Init session ──

  const prevMeetingIdRef = useRef(meetingId)
  const hasInitializedRef = useRef(false)

  useEffect(() => {
    const idChanged = prevMeetingIdRef.current !== meetingId
    prevMeetingIdRef.current = meetingId
    if (idChanged) {
      abortRef.current?.abort()
      abortRef.current = null
      setStreaming(false)
      setMessages([])
      setMsgCount(0)
      hasInitializedRef.current = false
    }
    // Wait until meetingTitle is available (meeting object loaded) before
    // creating the session. Avoids creating sessions with empty names during
    // the brief transition when the meeting object is still loading.
    if (!meetingId || !meetingTitle || hasInitializedRef.current) return
    hasInitializedRef.current = true
    const sid = `${sessionKind === "group" ? "group_" : MEETING_SESSION_PREFIX}${meetingId}`
    setSessionId(sid)
    initSession(sid)
  }, [meetingId, meetingTitle, sessionKind])

  const initSession = async (sid: string) => {
    setLoadingHistory(true)
    try {
      const detail = await getSession(sid).catch(() => null)
      if (detail?.messages?.length) {
        // Dialogue only (skip system transcript / tool placeholders)
        const msgs: QAMessage[] = []
        let pendingCites: GroupCite[] = []
        for (const m of detail.messages) {
          const meta = (m.metadata ?? {}) as Record<string, unknown>
          const noteChanges = mergeNoteChanges([], meta.note_changes)
          const hasArtifacts = !!noteChanges.length || !!(meta.report_changes as unknown[])?.length
          if (m.role === "user") {
            pendingCites = []
            msgs.push({ id: m.id, role: "user", content: typeof meta.display_content === "string" ? meta.display_content : m.content, attachments: meta.attachments as ChatAttachment[] | undefined, ...applyChatArtifacts({}, meta) })
            continue
          }
          if (String(m.role) === "tool" || (m.role === "assistant" && !m.content && meta.tool_calls && !hasArtifacts)) {
            pendingCites = mergeGroupCites(pendingCites, parseCitesFromToolBody(m.content))
            pendingCites = mergeGroupCites(pendingCites, groupCitesFromToolTrace(meta))
            continue
          }
          if (m.role === "assistant") {
            if (!(m.content || "").trim() && !hasArtifacts) continue
            msgs.push({
              id: m.id,
              role: "assistant",
              content: m.content,
              sources: m.sources ?? undefined,
              noteChanges,
              ...applyChatArtifacts({}, meta),
              groupCites: mergeGroupCites(groupCitesFromToolTrace(meta), pendingCites),
            })
            pendingCites = []
          }
        }
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
      const msg = t(HINT_KEYS[Math.floor(Math.random() * HINT_KEYS.length)])
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
  }, [meetingId, open, t])

  // ── Send ──

  const attachments = useChatAttachments(sessionId, undefined, streaming || loadingHistory)
  const review = useReportReview(sessionId, streaming)

  const send = useCallback(async () => {
    const text = input.trim()
    if (!canSendReview(input, attachments.drafts, review.hasComments) || streaming || !sessionId) return
    if (sessionKind !== "group" && indexStatus !== "ready" && !attachments.ready.length && !review.hasComments) return

    const comments = useAppStore.getState().reportComments[sessionId!] || []
    const reviews = reviewRequests(comments)
    setInput("")
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto"
    }

    // New send → re-stick to bottom for this stream
    stickToBottom.current = true

    const userMsg: QAMessage = { id: newId(), role: "user", content: text, attachments: attachments.ready, reportReviews: reviewSnapshots(comments,text), isNew: true }
    const assistantMsg: QAMessage = { id: newId(), role: "assistant", content: "", isStreaming: true, isNew: true }
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
          attachment_ids: attachments.ready.map((a) => a.id),
          ...(reviews.length ? {report_reviews:reviews} : {}),
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

      useAppStore.getState().consumeChatAttachments(sessionId, attachments.ready.map((a) => a.id))
      if (reviews.length) consumeReportComments(sessionId, reviews.flatMap(r=>r.comment_ids))

      let sources: QAMessage["sources"] = []
      let gotDoneCount: number | null = null

      if (resp.body) {
        for await (const { event: eventType, data } of iterateSessionSse(resp.body)) {
              if (eventType === "report_review_submitted") {consumeSubmittedReviews(sessionId,data.report_reviews);setMessages(prev=>prev.map(m=>m.id===userMsg.id ? applyChatArtifacts(m,data) : m))}
              handleSSEEvent(assistantMsg.id, eventType, data, (s) => { sources = s })
              if (eventType === "note_create_confirm") {
                const confirmId = String(data.confirm_id || "")
                if (confirmId) {
                  const { promptNoteCreateConfirm } = await import("@/lib/note-create-confirm")
                  const { confirmNoteCreate } = await import("@/api/client")
                  const [approved, overrideCollection] = await promptNoteCreateConfirm(
                    confirmId,
                    String(data.title || ""),
                    sessionId,
                    String(data.collection_name || ""),
                    String(data.preview || ""),
                    String(data.collection || ""),
                  )
                  try {
                    await confirmNoteCreate(confirmId, approved, overrideCollection)
                  } catch (err) {
                    console.error("[MeetingChat] note-create-confirm failed:", err)
                  }
                }
              }
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
      if (reviews.length) {void refreshReportComments(sessionId).catch(()=>{});if(controller.signal.aborted)setTimeout(()=>void refreshReportComments(sessionId).catch(()=>{}),500)}
      setStreaming(false)
      abortRef.current = null
    }
  }, [input, streaming, sessionId, meetingId, pinToBottom, indexStatus, sessionKind, attachments, t, review.hasComments])

  const handleSSEEvent = (
    assistantId: string, type: string,
    data: Record<string, unknown>,
    setSources: (s: QAMessage["sources"]) => void,
  ) => {
    if (Array.isArray(data.citations) || Array.isArray(data.report_changes) || Array.isArray(data.note_changes)) {
      setMessages((previous) => previous.map((m) => m.id === assistantId ? {
        ...applyChatArtifacts(m, data),
        noteChanges: mergeNoteChanges(m.noteChanges, data.note_changes),
      } : m))
    }
    switch (type) {
      case "report_review_incomplete": appendToken(assistantId,"\n\n"+t("chat.review.incomplete")); break
      case "thinking": appendThinking(assistantId, data.content as string); break
      case "planning":
        appendThinking(assistantId, `\n${tr("chat.nextStep")}`)
        break
      case "token": appendToken(assistantId, data.content as string); break
      case "searching":
        // Show searching indicator in the assistant message
        setMessages((prev) => prev.map((m) =>
          m.id === assistantId ? { ...m, thinkingContent: (m.thinkingContent || "") + `\n🔍 Searching: ${data.query || ""}...` } : m
        ));
        break
      case "tool_call_start": {
        const tool = String(data.tool || "tool")
        const reportLabel = { create_report: "chat.reports.createTool", get_report: "chat.reports.readTool", update_report: "chat.reports.updateTool", revise_report: "chat.reports.updateTool" }[tool]
        const label = reportLabel ? t(reportLabel) : tool
        const q = data.raw_query || data.query
        const line = q
          ? `\n📡 ${label}: ${String(q).slice(0, 120)}`
          : `\n📡 ${label}`
        const draftLine = tool === "create_note"
          ? `\n📝 ${t("chat.noteDrafting")}`
          : ""
        setMessages((prev) => prev.map((m) =>
          m.id === assistantId ? { ...m, thinkingContent: (m.thinkingContent || "") + line + draftLine } : m
        ));
        break
      }
      case "tool_result": {
        const n = data.sources_count
        const line = data.status === "error"
          ? `\n⚠ ${t("common.error")}`
          : typeof n === "number"
          ? `\n✅ ${tr("meeting.txLookupHits", { n })}`
          : `\n✅ ${t("common.done")}`
        setMessages((prev) => prev.map((m) =>
          m.id === assistantId ? { ...m, thinkingContent: (m.thinkingContent || "") + line } : m
        ));
        const extra = parseGroupCites(data.cites)
        if (extra.length > 0) {
          setMessages((prev) => prev.map((m) =>
            m.id === assistantId
              ? { ...m, groupCites: mergeGroupCites(m.groupCites, extra) }
              : m,
          ))
        }
        break
      }
      case "done":
        if (data.sources) {
          const srcs = data.sources as QAMessage["sources"]
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
    setPreviewReport(null)
    abortRef.current?.abort()
    setStreaming(false)
    attachments.reset()
    try { await deleteSession(sessionId); useAppStore.getState().clearReportReviewSession(sessionId) } catch { /* ok */ }
    await createSession(meetingTitle, [meetingId], sessionId).catch(() => {})
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
  const threadEdgeFade = useScrollEdgeFade(messagesScrollRef, messages.length)

  // Note: meeting chat doesn't use file-level display names

  // ── Panel content — same pm-qc chrome as Collection Quick Chat ──

  const panelContent = (
    <div
      {...attachments.dropProps}
      className={cn(
        "pm-qc-panel relative",
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
              <p className="pm-qc-header-title">{t("library.quickChat")}</p>
            )}
            {layout !== "dock" && (
              <p className="pm-qc-header-sub truncate" title={meetingTitle}>
                {meetingTitle}
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <ConversationAssets key={sessionId} messages={loadingHistory ? [] : messages} sessionId={sessionId} refreshKey={`${messages.length}:${streaming}`} onOpenReport={setPreviewReport} />
          {msgCount > 0 && (
            <span
              className={cn(
                "pm-meta inline-flex items-center gap-1 tabular-nums",
                msgCount >= WARN_THRESHOLD && "text-amber-600",
              )}
              title={t("library.roundsLimit", { n: msgCount, max: MAX_MESSAGES })}
            >
              {msgCount >= WARN_THRESHOLD && <AlertTriangle className="size-3" />}
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
          {layout !== "dock" && (
            <Button
              type="button"
              variant="ghost"
              size="xs"
              onClick={onClose}
              aria-label={t("meeting.closeQuickQa")}
            >
              {t("common.close")}
            </Button>
          )}
        </div>
      </header>

      {sessionKind !== "group" && indexStatus !== "ready" && !attachments.drafts.length && !messages.length ? (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-7">
          <div className="pm-qc-empty">
          {indexStatus === "building" ? (
            <>
              <span className="pm-qc-empty-icon" aria-hidden>
                <Loader2 className="size-5 animate-spin" strokeWidth={1.75} />
              </span>
              <p className="pm-qc-empty-title">{t("meeting.txIndexNeedTitle")}</p>
              <p className="pm-qc-empty-sub">{t("meeting.txIndexBuilding")}</p>
            </>
          ) : (
            <>
              <p className="pm-qc-empty-title">{t("meeting.txIndexNeedTitle")}</p>
              <p className="pm-qc-empty-sub">{t("meeting.txIndexNeedBuild")}</p>
              {indexStatus === "failed" && (
                <p className="pm-qc-empty-sub text-[var(--pm-danger)]">{indexError || t("meeting.txIndexFailed")}</p>
              )}
              <button
                type="button"
                className="pm-meeting-pill is-compact mt-1"
                onClick={() => void buildIndex()}
                disabled={indexStarting}
              >
                {indexStarting ? t("meeting.txIndexBuilding") : t("meeting.txIndexBuild")}
              </button>
            </>
          )}
          </div>
        </div>
      ) : (
      <>
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
                    msg.isNew && "animate-slide-in-right",
                  )}
                >
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
                    msg.isNew && "animate-slide-in-right",
                  )}
                >
                  <span className="pm-qc-msg-role pm-qc-msg-role--ai">{t("chat.assistant")}</span>
                  <div className="pm-qc-bubble pm-qc-bubble--assistant">
                    {msg.thinkingContent && (
                      <details
                        className="pm-qc-thinking"
                        open={msg.isStreaming && !msg.content ? true : undefined}
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
                      <div className="pm-qc-answer break-words">
                        <CitationScope citations={msg.citations} source={msg.content} sessionId={sessionId || ""} onOpenMeeting={(evidence) => {
                          const mid = String(evidence.source.meeting_id || "")
                          const sid = String(evidence.location.sentence_id || "")
                          if (sessionKind === "group" && onGroupCite) {
                            const cite = (msg.groupCites || []).find((c) => c.meeting_id === mid)
                            onGroupCite(cite?.n || 0, sid, mid)
                          } else if (sid) onRefClick?.(sid)
                        }}>
                        {(msg.citations?.length || msg.content.includes("[cite:")) ? <StreamingAnswerBody content={msg.content} isStreaming={!!msg.isStreaming} richContent /> : <TimeContent
                          content={msg.content}
                          onRefClick={onRefClick}
                          onGroupCite={onGroupCite
                            ? (n, occurrence, refN) => handleGroupCite(n, occurrence, msg.groupCites, refN)
                            : undefined}
                          groupCites={enrichGroupCites(msg.groupCites || [], citeMeetings)}
                        />}
                        </CitationScope>
                      </div>
                    ) : msg.isStreaming ? (
                      <div className="pm-qc-typing">
                        <Loader2 className="size-3.5 animate-spin" />
                        <span>{t("chat.educing")}</span>
                      </div>
                    ) : (
                      <p className="pm-qc-bubble-text">{msg.content}</p>
                    )}
                    {!msg.isStreaming && <NoteChangeCards notes={msg.noteChanges} />}
                    <ReportCards reports={msg.reportChanges} onOpenReport={setPreviewReport} />
                    {msg.role === "assistant" && !msg.isStreaming && (() => {
                      const groupShown = displayedGroupCites(msg.content, enrichGroupCites(msg.groupCites || [], citeMeetings))
                      const sources = groupShown.length ? groupShown.map(item => ({
                        text: '', score: 1, metadata: {
                          id: `${item.meeting_id}:${item.sentence_id || item.ref_n || item.displayIndex}`,
                          source_type: 'meeting', meeting_id: item.meeting_id,
                          sentence_id: item.sentence_id, source_label: item.title || t('meeting.thisMeeting'),
                        },
                      })) : msg.sources || []
                      return sources.length ? <CitationScope citations={msg.citations} source={msg.content} sessionId={sessionId || ""}>
                        <SourcesCard sources={sources}/>
                      </CitationScope> : null
                    })()}
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
            <p className="pm-qc-empty-title">{t("meeting.askMeeting")}</p>
            <p className="pm-qc-empty-sub">
              {t("library.qcEmptyBefore")}{" "}
              <em className="not-italic text-[var(--pm-green)]">{meetingTitle || t("meeting.thisMeeting")}</em>
              {". "}
              {t("meeting.qcEmptyAfter")}
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

      </>
      )}
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
            canAsk={sessionKind === "group" || indexStatus === "ready" || attachments.ready.length > 0 || review.hasComments}
            input={input}
            setInput={setInput}
            streaming={streaming}
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
        className="relative transition-all ease-out quick-chat-btn"
        style={{ color: "var(--pm-green, #1A5E3D)" }}
        aria-label={open ? t("meeting.closeQuickQa") : t("library.openQuickQa")}
      >
        <LogoMark className="qc-logo" flowing={open} />
      </button>
    </div>
  )

  if (previewReport && open) return <ReportWorkspace report={previewReport} chat={panelContent} onClose={() => setPreviewReport(null)} />

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
// Clickable [ref:N] chips; ranges expand via parseMeetingRefGroups.

function nextDisplayedCite(
  displayed: DisplayedGroupCite[] | undefined,
  cursor: { i: number } | undefined,
): DisplayedGroupCite | undefined {
  if (!displayed || !cursor) return undefined
  const shown = displayed[cursor.i]
  if (shown) cursor.i += 1
  return shown
}

function GroupCiteChip({
  displayIndex,
  meetingTitle,
  onClick,
}: {
  displayIndex: number
  meetingTitle?: string
  onClick: () => void
}) {
  const btn = (
    <button
      type="button"
      className="pm-meeting-ref-chip"
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
    >
      {String(displayIndex)}
    </button>
  )
  if (!meetingTitle) return btn
  return (
    <Tooltip>
      <TooltipTrigger render={btn} />
      <TooltipContent>{meetingTitle}</TooltipContent>
    </Tooltip>
  )
}

function renderInlineWithRefs(
  text: string,
  onRefClick?: (sentenceId: string) => void,
  onGroupCite?: (n: number, occurrence?: number, refN?: number) => void,
  occ?: Map<number, number>,
  groupCites?: GroupCite[],
  displayed?: DisplayedGroupCite[],
  cursor?: { i: number },
): ReactNode[] {
  const citeOcc = occ ?? new Map<number, number>()
  const cites = groupCites || []
  const parts: ReactNode[] = []
  // [ref:67] / [ref:1-5] / [ref:47, 78-86] and 【ref:…】 variants.
  // Group: [n] / [n:k] / ([n]) — chip shows sequential 1,2,3.
  const regex = new RegExp(
    `(\\*\\*(.+?)\\*\\*)|(\\*(.+?)\\*)|(\`(.+?)\`)|(${MEETING_CITE_RE_SOURCE})|((?:\\[|【)\\s*priority:\\s*(high|medium|low)\\s*(?:\\]|】))` +
      (onGroupCite ? `|(${GROUP_CITE_RE_SOURCE})` : ""),
    "gi",
  )
  let lastIdx = 0
  let match
  regex.lastIndex = 0
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIdx) {
      parts.push(<span key={`t${lastIdx}`}>{text.slice(lastIdx, match.index)}</span>)
    }
    if (match[1]) {
      parts.push(<strong key={`b${lastIdx}`}>{renderInlineWithRefs(match[2], onRefClick, onGroupCite, citeOcc, groupCites, displayed, cursor)}</strong>)
    } else if (match[3]) {
      parts.push(<em key={`i${lastIdx}`}>{renderInlineWithRefs(match[4], onRefClick, onGroupCite, citeOcc, groupCites, displayed, cursor)}</em>)
    } else if (match[5]) {
      // Models sometimes wrap cite markers in backticks (`[ref:5]`). A cite
      // inside a code span must still become a chip — drop the code styling
      // for that span and recurse so the cite branch can pick it up.
      const codeText = match[6] ?? ""
      const citeRe = new RegExp(MEETING_CITE_RE_SOURCE, "i")
      if (citeRe.test(codeText)) {
        parts.push(
          ...renderInlineWithRefs(codeText, onRefClick, onGroupCite, citeOcc, groupCites, displayed, cursor)
        )
      } else {
        parts.push(<code key={`c${lastIdx}`} className="bg-muted px-1 rounded text-xs t-mono-family">{codeText}</code>)
      }
    } else if (match[8]) {
      const groups = parseMeetingRefGroups(match[8])
      for (const [gi, g] of groups.entries()) {
        const refN = /^\d+$/.test(g.label.trim())
          ? parseInt(g.label.trim(), 10)
          : NaN
        const mapped = onGroupCite && Number.isFinite(refN)
          ? resolveGroupCiteByRefN(cites, refN)
          : undefined
        if (onGroupCite) {
          if (!mapped?.sentence_id || mapped.n == null) continue
          const n = mapped.n
          const shown = nextDisplayedCite(displayed, cursor)
          parts.push(
            <GroupCiteChip
              key={`r${lastIdx}${gi}`}
              displayIndex={shown?.displayIndex ?? n}
              meetingTitle={shown?.title || mapped.title}
              onClick={() => onGroupCite(n, 0, mapped.ref_n ?? refN)}
            />,
          )
          continue
        }
        parts.push(
          <button
            type="button"
            key={`r${lastIdx}${gi}`}
            className="pm-meeting-ref-chip"
            onClick={(e) => {
              e.stopPropagation()
              if (g.ids[0]) onRefClick?.(g.ids[0])
            }}
            title={tr("meeting.sourcesIds", { ids: g.ids.join(", ") })}
          >
            {g.label}
          </button>,
        )
      }
    } else if (match[12] && onGroupCite) {
      const n = parseInt(match[12], 10)
      const refN = match[13] ? parseInt(match[13], 10) : undefined
      const occurrence = refN ? 0 : nextGroupCiteOccurrence(citeOcc, n)
      const liveCite = resolveGroupCite(cites, n, { refN, occurrence })
      if (liveCite?.sentence_id) {
        const shown = nextDisplayedCite(displayed, cursor)
        parts.push(
          <GroupCiteChip
            key={`g${lastIdx}`}
            displayIndex={shown?.displayIndex ?? n}
            meetingTitle={shown?.title || liveCite.title}
            onClick={() => onGroupCite(n, occurrence, refN)}
          />,
        )
      }
    } else if (match[10]) {
      const level = match[10].toLowerCase()
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

function TimeContent({ content, onRefClick, onGroupCite, groupCites }: {
  content: string
  onRefClick?: (sentenceId: string) => void
  onGroupCite?: (n: number, occurrence?: number, refN?: number) => void
  groupCites?: GroupCite[]
}) {
  const occ = new Map<number, number>()
  const displayed = onGroupCite ? displayedGroupCites(content, groupCites || []) : undefined
  const cursor = { i: 0 }
  const refs = (
    line: string,
  ) => renderInlineWithRefs(line, onRefClick, onGroupCite, occ, groupCites, displayed, cursor)
  const body = content.split("\n").map((line, i) => {
        if (line.startsWith("### ")) {
          return <h3 key={i} className="text-sm font-semibold mt-3 mb-1">{refs(line.slice(4))}</h3>
        }
        if (line.startsWith("## ")) {
          return <h2 key={i} className="text-base font-semibold mt-3 mb-1">{refs(line.slice(3))}</h2>
        }
        if (line.startsWith("# ")) {
          return <h1 key={i} className="text-lg font-bold mt-3 mb-1">{refs(line.slice(2))}</h1>
        }
        if (!line.trim()) return <div key={i} className="h-2" />
        if (/^\s*[-*+]\s/.test(line)) {
          return <li key={i} className="text-sm leading-relaxed ml-4">{refs(line.replace(/^\s*[-*+]\s/, ""))}</li>
        }
        if (/^>\s/.test(line)) {
          return <blockquote key={i} className="border-l-2 border-muted-foreground/30 pl-3 italic text-muted-foreground text-xs leading-relaxed mb-1">{refs(line.replace(/^>\s*/, ""))}</blockquote>
        }
        return <p key={i} className="text-sm leading-relaxed mb-1">{refs(line)}</p>
      })
  if (!onGroupCite) return <>{body}</>
  return (
    <TooltipProvider delay={GROUP_CITE_HOVER_DELAY_MS} closeDelay={80}>
      <>{body}</>
    </TooltipProvider>
  )
}

function ChatInputBar({
  hasComments,
  attachments,
  canAsk,
  input, setInput, streaming, onSend, onKeyDown,
  onCompositionStart, onCompositionEnd, textareaRef,
}: {
  hasComments: boolean
  attachments: ChatAttachmentsManager
  canAsk: boolean
  input: string
  setInput: (v: string) => void
  streaming: boolean
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
        streaming && "pm-qc-input-row--thinking",
      )}
    >
      <span className="pm-qc-stream-fx" aria-hidden />
      <ChatAttachmentPicker manager={attachments} />
      <div className="pm-qc-input-frame">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          onCompositionStart={onCompositionStart}
          onCompositionEnd={onCompositionEnd}
          placeholder={t("meeting.messageMeeting")}
          disabled={streaming}
          rows={1}
          className="pm-qc-textarea"
        />
      </div>
      <button
        type="button"
        onClick={onSend}
        disabled={!canAsk || !canSendReview(input, attachments.drafts, hasComments) || streaming}
        className={cn(
          "pm-qc-send",
          canSendReview(input, attachments.drafts, hasComments) && !streaming && "is-ready",
          streaming && "is-hidden",
        )}
        aria-label={t("common.send")}
      >
        <Send className="size-3.5" />
      </button>
    </div>
  )
}
