import { t as translate } from "@/i18n"
import { reviewRequests, reviewSnapshots } from "@/lib/report-review"
import { consumeReportComments, consumeSubmittedReviews, refreshReportComments } from "@/hooks/use-report-review"
import { applyChatArtifacts } from "@/lib/chat-reports"
import { useCallback } from "react"
import {
  useAppStore,
  _registerStream, _abortStream, _unregisterStream, _ownsStream,
  _getCachedMessages, _setCachedMessages,
  type ThinkingSummary,
} from "@/stores/app-store"
import { confirmNoteCreate, confirmTodoDelete, confirmWebSearch, generateSessionTitle, iterateSessionSse, listSessions, postSessionMessage } from "@/api/client"
import type { ChatAttachment } from "@/api/client"
import { newId } from "@/lib/uuid"
import { promptWebSearchConfirm } from "@/lib/web-search-confirm"
import { promptTodoDeleteConfirm } from "@/lib/todo-delete-confirm"
import { promptNoteCreateConfirm } from "@/lib/note-create-confirm"
import { refreshTodosAfterChatTool } from "@/lib/todo-refresh"
import { mergeNoteChanges } from "@/lib/note-changes"
/** Check if sid is the active session; if not, update cache instead of store. */
function _isActive(sid: string) {
  return useAppStore.getState().sessionId === sid
}

/** Append to last message of cached messages for a given session. */
function _cacheAppend(sid: string, token: string) {
  const msgs = _getCachedMessages(sid) ?? []
  if (msgs.length > 0) {
    const last = { ...msgs[msgs.length - 1], content: msgs[msgs.length - 1].content + token }
    msgs[msgs.length - 1] = last
    _setCachedMessages(sid, msgs)
  }
}
function _cacheSetLastSources(sid: string, sources: any[]) {
  const msgs = _getCachedMessages(sid) ?? []
  if (msgs.length > 0) {
    msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], sources }
    _setCachedMessages(sid, msgs)
  }
}
function _cacheAddLastNoteChanges(sid: string, changes: unknown) {
  const msgs = _getCachedMessages(sid) ?? []
  if (msgs.length > 0) {
    const last = msgs[msgs.length - 1]
    msgs[msgs.length - 1] = { ...last, noteChanges: mergeNoteChanges(last.noteChanges, changes) }
    _setCachedMessages(sid, msgs)
  }
}
function _applyArtifacts(sid: string, active: boolean, data: Record<string, unknown>) {
  if (!Array.isArray(data.citations) && !Array.isArray(data.report_changes)) return
  if (active) useAppStore.getState().addLastMessageArtifacts(data)
  else {
    const messages = [...(_getCachedMessages(sid) ?? [])]
    if (messages.length) {
      messages[messages.length - 1] = applyChatArtifacts(messages[messages.length - 1], data)
      _setCachedMessages(sid, messages)
    }
  }
}
function _cacheFinishLast(sid: string) {
  const msgs = _getCachedMessages(sid) ?? []
  if (msgs.length > 0) {
    msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], isStreaming: false }
    _setCachedMessages(sid, msgs)
  }
}

/** Sync cache with current store messages for a session. */
function _syncCacheFromStore(sid: string) {
  const { messages } = useAppStore.getState()
  _setCachedMessages(sid, [...messages])
}

export function useStreamChat() {
  // Do NOT subscribe to the whole store — streaming updates `messages` every token
  // and would re-render every consumer of this hook (ChatInput, etc.).
  // Read actions/state via getState() inside sendMessage / stopGeneration.

  const sendMessage = async (content: string, _thinkingArg?: boolean, _webSearchArg?: boolean, attachments: ChatAttachment[] = []) => {
    const store = useAppStore.getState()
    // Hydrate restored sessionId (localStorage) before sending so we never
    // append into an old session while the UI still looks blank.
    // No session → create; load fail → create new via ensureSessionHydrated.
    const sid = await store.ensureSessionHydrated()
    if (!sid) return
    if (attachments.some((a) => a.session_id !== sid)) return

    const comments = useAppStore.getState().reportComments[sid] || []
    const reviews = reviewRequests(comments)

    // Abort previous stream for the SAME session only
    _abortStream(sid)
    const controller = new AbortController()
    _registerStream(sid, controller)

    // Hoisted so catch/finally can flush even if setup fails mid-way
    let flushAnswerTokensNow: () => void = () => {}
    let flushThinkingBufNow: () => void = () => {}
    const finishOwnedStream = () => {
      if (!_ownsStream(sid, controller)) return
      try {
        flushThinkingBufNow()
        flushAnswerTokensNow()
      } finally {
        try {
          if (_isActive(sid)) {
            useAppStore.getState().finishLastMessage()
            _syncCacheFromStore(sid)
          } else {
            _cacheFinishLast(sid)
          }
        } finally {
          try {
            if (_isActive(sid)) useAppStore.getState().setStreaming(false)
          } finally {
            _unregisterStream(sid, controller)
          }
        }
      }
    }
    const onAbort = () => {
      // Stop must release the UI even while a transport read or confirmation waits.
      try { finishOwnedStream() } catch (error) { console.error("Chat cancellation cleanup failed:", error) }
    }
    controller.signal.addEventListener("abort", onAbort, { once: true })

    try {
      const userId = newId()
      store.addMessage({ id: userId, role: "user", content, attachments, reportReviews: reviewSnapshots(comments, content) })
      const assistantId = newId()
      store.addMessage({ id: assistantId, role: "assistant", content: "", isStreaming: true })
      store.setStreaming(true)
      _syncCacheFromStore(sid)

      const {
        selectedCollections,
        activeProvider,
        activeModel,
        // 与上面几项一致：都取自 store（唯一真相源），并随消息存进会话配置
        thinking,
        webSearch,
        appendToLastMessage,
        setLastMessageSources,
        appendTimelineThinking,
        setTimelineToolSummary,
        setTimelineToolStatus,
        startTimelineTool,
        finishTimelineTool,
        closeOpenTimelineTools,
        finishLastMessage,
        flushLastMessageToThinking,
      } = useAppStore.getState()

      const resp = await postSessionMessage(
        sid,
        {
          content,
          attachment_ids: attachments.map((a) => a.id),
          ...(reviews.length ? {report_reviews: reviews} : {}),
          thinking,
          collections: selectedCollections,
          provider_id: activeProvider || undefined,
          model: activeModel || undefined,
          web_search_enabled: webSearch,
        },
        controller.signal,
      )
      if (!_ownsStream(sid, controller)) return

      if (!resp.ok) {
        const err = await resp.text()
        if (_isActive(sid)) appendToLastMessage(`Error: ${resp.status} - ${err}`)
        else _cacheAppend(sid, `Error: ${resp.status} - ${err}`)
        if (_isActive(sid)) finishLastMessage()
        else _cacheFinishLast(sid)
        return
      }

      useAppStore.getState().consumeChatAttachments(sid, attachments.map((a) => a.id))
      if (reviews.length) consumeReportComments(sid, reviews.flatMap(r=>r.comment_ids))

      // Batch answer tokens on a short timer (not rAF): rAF is starved when the
      // main thread is busy with Markdown/layout, which looked like a freeze at
      // "好的" while the backend kept generating.
      let tokenBuf = ""
      let tokenTimer: ReturnType<typeof setTimeout> | 0 = 0
      let closedTimelineTools = false
      const flushTokenBuf = () => {
        tokenTimer = 0
        if (!tokenBuf) return
        const chunk = tokenBuf
        tokenBuf = ""
        if (!_ownsStream(sid, controller)) return
        if (_isActive(sid)) {
          if (!closedTimelineTools) {
            closedTimelineTools = true
            closeOpenTimelineTools()
          }
          appendToLastMessage(chunk)
        } else {
          _cacheAppend(sid, chunk)
        }
      }
      const queueAnswerToken = (text: string) => {
        if (!text) return
        tokenBuf += text
        if (tokenTimer) return
        tokenTimer = setTimeout(flushTokenBuf, 32)
      }
      flushAnswerTokensNow = () => {
        if (tokenTimer) {
          clearTimeout(tokenTimer)
          tokenTimer = 0
        }
        flushTokenBuf()
      }

      // Batch thinking tokens the same way as answer tokens: reasoning models
      // emit very chatty streams and each store write copies messages+timeline
      // and wakes every subscriber. 32ms is imperceptible on a scrolling trail.
      let thinkingBuf = ""
      let thinkingTimer: ReturnType<typeof setTimeout> | 0 = 0
      const flushThinkingBuf = () => {
        thinkingTimer = 0
        if (!thinkingBuf) return
        const chunk = thinkingBuf
        thinkingBuf = ""
        if (!_ownsStream(sid, controller)) return
        // Inactive sessions drop thinking, matching the previous per-event behavior
        if (_isActive(sid)) appendTimelineThinking(chunk)
      }
      flushThinkingBufNow = flushThinkingBuf
      const queueThinkingToken = (text: string) => {
        if (!text) return
        thinkingBuf += text
        if (thinkingTimer) return
        thinkingTimer = setTimeout(flushThinkingBuf, 32)
      }

      if (resp.body) {
        for await (const { event: currentEvent, data } of iterateSessionSse(resp.body)) {
            if (!_ownsStream(sid, controller)) return
            const active = _isActive(sid)

            switch (currentEvent) {
              case "report_review_incomplete": {
                if (data.message_rendered) break
                const warning = "\n\n" + translate(useAppStore.getState().locale,"chat.review.incomplete")
                if (active) appendToLastMessage(warning); else _cacheAppend(sid,warning)
                break
              }
              case "report_review_submitted": {
                consumeSubmittedReviews(sid,data.report_reviews)
                const update = (messages: typeof store.messages) => messages.map(m=>m.id===userId ? applyChatArtifacts(m,data) : m)
                if (active) { useAppStore.setState(s=>({messages:update(s.messages)})); _syncCacheFromStore(sid) }
                else _setCachedMessages(sid,update(_getCachedMessages(sid)||[]))
                break
              }
              case "planning":
                // After a tool Done: next LLM round has started. Trail shows
                // "Deciding next step…" until the next tool_call_start / token.
                break

              case "thinking":
                // Reasoning timeline is plain text — batched like answer tokens
                queueThinkingToken(String(data.content ?? ""))
                break

              case "token":
                // Final answer path (Think OFF sends almost everything here)
                queueAnswerToken(String(data.content ?? ""))
                break

              case "tool_call_start":
                if (active) {
                  // Close the streaming thinking block before the tool block lands
                  flushThinkingBufNow()
                  flushAnswerTokensNow()
                  flushLastMessageToThinking()
                  startTimelineTool({
                    tool: String(data.tool || ""),
                    raw_query: String(data.raw_query || data.query || ""),
                    source_type: data.source_type ? String(data.source_type) : undefined,
                  })
                  // create_note writes the whole note body as tool arguments —
                  // the confirm card only fires after that round completes.
                  if (String(data.tool || "") === "create_note") {
                    setTimelineToolStatus("Drafting note (writing content)…")
                  }
                }
                break

              case "tool_step":
                // Live progress: update tool block status from step events
                if (active) setTimelineToolStatus(String(data.content || data.step || ""))
                break

              case "thinking_summary":
                if (active) setTimelineToolSummary(data as unknown as ThinkingSummary)
                break

              case "searching":
                if (active) {
                  const q = String(data.query || "")
                  setTimelineToolStatus(q ? `Searching: ${q}` : "Searching…")
                }
                break

              case "citations":
                _applyArtifacts(sid, active, data)
                break

              case "tool_result":
                _applyArtifacts(sid, active, data)
                if (data.note_changes) {
                  if (active) useAppStore.getState().addLastMessageNoteChanges(data.note_changes)
                  else _cacheAddLastNoteChanges(sid, data.note_changes)
                }
                refreshTodosAfterChatTool(String(data.tool || ""), {
                  status: String(data.status || "done"),
                  content: data.content,
                })
                if (active) {
                  const st = String(data.status || "done")
                  const mapped =
                    st === "error"
                      ? "error"
                      : st === "declined"
                        ? "declined"
                        : "done"
                  finishTimelineTool({
                    status: mapped as "done" | "error" | "declined",
                    sources_count:
                      typeof data.sources_count === "number"
                        ? data.sources_count
                        : undefined,
                    source_type: data.source_type
                      ? String(data.source_type)
                      : undefined,
                    content:
                      typeof data.content === "string" ? data.content : undefined,
                    tool: data.tool ? String(data.tool) : undefined,
                  })
                }
                break

              case "web_search_confirm": {
                // HITL: pause stream until user answers; must always resolve backend wait
                const confirmId = String(data.confirm_id || "")
                const query = String(data.query || "")
                if (active) {
                  setTimelineToolStatus(
                    query
                      ? `Waiting for web search confirmation: ${query}`
                      : "Waiting for web search confirmation…",
                  )
                }
                if (!confirmId) {
                  console.error("[Chat] web_search_confirm missing confirm_id")
                  break
                }
                let approved = false
                try {
                  approved = await promptWebSearchConfirm(confirmId, query, sid)
                } catch (err) {
                  console.error("[Chat] promptWebSearchConfirm failed:", err)
                  approved = false
                }
                try {
                  await confirmWebSearch(confirmId, approved)
                } catch (err) {
                  console.error("[Chat] web-search-confirm POST failed:", err)
                  // Retry once so backend does not hang on wait()
                  try {
                    await confirmWebSearch(confirmId, approved)
                  } catch (err2) {
                    console.error("[Chat] web-search-confirm retry failed:", err2)
                  }
                }
                if (active && !approved) {
                  finishTimelineTool({ status: "declined", source_type: "web" })
                }
                break
              }

              case "todo_delete_confirm": {
                const confirmId = String(data.confirm_id || "")
                const title = String(data.title || "")
                const collectionName = String(data.collection_name || "")
                if (active) {
                  setTimelineToolStatus(
                    title
                      ? `Waiting to delete to-do: ${title}`
                      : "Waiting to confirm to-do delete…",
                  )
                }
                if (!confirmId) {
                  console.error("[Chat] todo_delete_confirm missing confirm_id")
                  break
                }
                let approved = false
                try {
                  approved = await promptTodoDeleteConfirm(
                    confirmId,
                    title,
                    sid,
                    collectionName,
                  )
                } catch (err) {
                  console.error("[Chat] promptTodoDeleteConfirm failed:", err)
                  approved = false
                }
                try {
                  await confirmTodoDelete(confirmId, approved)
                } catch (err) {
                  console.error("[Chat] todo-delete-confirm POST failed:", err)
                  try {
                    await confirmTodoDelete(confirmId, approved)
                  } catch (err2) {
                    console.error("[Chat] todo-delete-confirm retry failed:", err2)
                  }
                }
                if (active && !approved) {
                  finishTimelineTool({ status: "declined" })
                }
                break
              }

              case "note_create_confirm": {
                const confirmId = String(data.confirm_id || "")
                const title = String(data.title || "")
                const collectionName = String(data.collection_name || "")
                const collectionId = String(data.collection || "")
                const preview = String(data.preview || "")
                if (active) {
                  setTimelineToolStatus(
                    title
                      ? `Waiting to create note: ${title}`
                      : "Waiting to confirm note creation…",
                  )
                }
                if (!confirmId) {
                  console.error("[Chat] note_create_confirm missing confirm_id")
                  break
                }
                let approved = false
                let overrideCollection: string | undefined
                try {
                  ;[approved, overrideCollection] = await promptNoteCreateConfirm(
                    confirmId,
                    title,
                    sid,
                    collectionName,
                    preview,
                    collectionId,
                  )
                } catch (err) {
                  console.error("[Chat] promptNoteCreateConfirm failed:", err)
                  approved = false
                }
                try {
                  await confirmNoteCreate(confirmId, approved, overrideCollection)
                } catch (err) {
                  console.error("[Chat] note-create-confirm POST failed:", err)
                  try {
                    await confirmNoteCreate(confirmId, approved, overrideCollection)
                  } catch (err2) {
                    console.error("[Chat] note-create-confirm retry failed:", err2)
                  }
                }
                if (active && !approved) {
                  finishTimelineTool({ status: "declined" })
                }
                break
              }

              case "done":
                _applyArtifacts(sid, active, data)
                flushThinkingBufNow()
                flushAnswerTokensNow()
                if (data.note_changes) {
                  if (active) useAppStore.getState().addLastMessageNoteChanges(data.note_changes)
                  else _cacheAddLastNoteChanges(sid, data.note_changes)
                }
                if (Array.isArray(data.sources) && data.sources.length) {
                  if (active) setLastMessageSources(data.sources)
                  else _cacheSetLastSources(sid, data.sources)
                }
                if (active) {
                  finishLastMessage()
                  _syncCacheFromStore(sid)
                } else {
                  _cacheFinishLast(sid)
                }
                // Live session list: message_count without waiting for full page refresh
                if (typeof data.message_count === "number") {
                  const st = useAppStore.getState()
                  st.setSessions(
                    st.sessions.map((s) =>
                      s.id === sid ? { ...s, message_count: data.message_count as number } : s,
                    ),
                  )
                }
                if (sid) {
                  const msgs = active ? useAppStore.getState().messages : (_getCachedMessages(sid) ?? [])
                  const userCount = msgs.filter(m => m.role === "user").length
                  if (userCount === 1) {
                    generateSessionTitle(sid)
                      .then(() => listSessions())
                      .then(sessions => {
                        useAppStore.getState().setSessions(sessions)
                      })
                      .catch(err => {
                        console.error("Auto-title failed:", err)
                      })
                  }
                }
                return

              case "error":
                flushThinkingBufNow()
                flushAnswerTokensNow()
                if (active) appendToLastMessage(`Error: ${data.content}`)
                else _cacheAppend(sid, `Error: ${data.content}`)
                if (active) finishLastMessage()
                else _cacheFinishLast(sid)
                return
            }
        }
      }
      // Stream body ended without a done event — still flush pending answer text
      flushThinkingBufNow()
      flushAnswerTokensNow()
    } catch (err: any) {
      if (!_ownsStream(sid, controller)) return
      flushThinkingBufNow()
      flushAnswerTokensNow()
      if (err.name === "AbortError") return
      if (_isActive(sid)) useAppStore.getState().appendToLastMessage(`Error: ${err.message}`)
      else _cacheAppend(sid, `Error: ${err.message}`)
    } finally {
      if (reviews.length) {
        void refreshReportComments(sid).catch(()=>{})
        if(controller.signal.aborted)setTimeout(()=>void refreshReportComments(sid).catch(()=>{}),500)
      }
      controller.signal.removeEventListener("abort", onAbort)
      finishOwnedStream()
    }
  }

  const stopGeneration = useCallback(() => {
    const sid = useAppStore.getState().sessionId
    if (sid) _abortStream(sid)
    useAppStore.getState().finishLastMessage()
    if (sid) _syncCacheFromStore(sid)
  }, [])

  return { sendMessage, stopGeneration }
}
