import { useCallback } from "react"
import {
  useAppStore,
  _registerStream, _abortStream, _unregisterStream,
  _getCachedMessages, _setCachedMessages,
  type ThinkingSummary,
} from "@/stores/app-store"
import { confirmTodoDelete, confirmWebSearch, generateSessionTitle, iterateSessionSse, listSessions, postSessionMessage } from "@/api/client"
import { promptWebSearchConfirm } from "@/lib/web-search-confirm"
import { promptTodoDeleteConfirm } from "@/lib/todo-delete-confirm"
import { refreshTodosAfterChatTool } from "@/lib/todo-refresh"
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

  const sendMessage = async (content: string, thinking = true, webSearchEnabled = false) => {
    const store = useAppStore.getState()
    // Hydrate restored sessionId (localStorage) before sending so we never
    // append into an old session while the UI still looks blank.
    // No session → create; load fail → create new via ensureSessionHydrated.
    const sid = await store.ensureSessionHydrated()
    if (!sid) return

    // Abort previous stream for the SAME session only
    _abortStream(sid)
    const controller = new AbortController()
    _registerStream(sid, controller)

    store.addMessage({ id: crypto.randomUUID(), role: "user", content })
    const assistantId = crypto.randomUUID()
    store.addMessage({ id: assistantId, role: "assistant", content: "", isStreaming: true })
    store.setStreaming(true)
    _syncCacheFromStore(sid)

    // Hoisted so catch/finally can flush even if setup fails mid-way
    let flushAnswerTokensNow: () => void = () => {}

    try {
      const {
        selectedCollections,
        activeProvider,
        activeModel,
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
          thinking,
          collections: selectedCollections,
          provider_id: activeProvider || undefined,
          model: activeModel || undefined,
          web_search_enabled: webSearchEnabled,
        },
        controller.signal,
      )

      if (!resp.ok) {
        const err = await resp.text()
        if (_isActive(sid)) appendToLastMessage(`Error: ${resp.status} - ${err}`)
        else _cacheAppend(sid, `Error: ${resp.status} - ${err}`)
        if (_isActive(sid)) finishLastMessage()
        else _cacheFinishLast(sid)
        return
      }

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

      if (resp.body) {
        for await (const { event: currentEvent, data } of iterateSessionSse(resp.body)) {
            const active = _isActive(sid)

            switch (currentEvent) {
              case "planning":
                // After a tool Done: next LLM round has started. Trail shows
                // "Deciding next step…" until the next tool_call_start / token.
                break

              case "thinking":
                // Reasoning timeline is plain text — keep low latency, no MD cost
                if (active) appendTimelineThinking(String(data.content ?? ""))
                break

              case "token":
                // Final answer path (Think OFF sends almost everything here)
                queueAnswerToken(String(data.content ?? ""))
                break

              case "tool_call_start":
                if (active) {
                  flushAnswerTokensNow()
                  flushLastMessageToThinking()
                  startTimelineTool({
                    tool: String(data.tool || ""),
                    raw_query: String(data.raw_query || data.query || ""),
                    source_type: data.source_type ? String(data.source_type) : undefined,
                  })
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

              case "tool_result":
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

              case "done":
                flushAnswerTokensNow()
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
                _unregisterStream(sid)
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
      flushAnswerTokensNow()
    } catch (err: any) {
      flushAnswerTokensNow()
      if (err.name === "AbortError") return
      if (_isActive(sid)) useAppStore.getState().appendToLastMessage(`Error: ${err.message}`)
      else _cacheAppend(sid, `Error: ${err.message}`)
    } finally {
      flushAnswerTokensNow()
      if (_isActive(sid)) {
        useAppStore.getState().finishLastMessage()
        useAppStore.getState().setStreaming(false)
        _syncCacheFromStore(sid)
      } else {
        _cacheFinishLast(sid)
      }
      _unregisterStream(sid)
    }
  }

  const stopGeneration = useCallback(() => {
    const sid = useAppStore.getState().sessionId
    if (sid) _abortStream(sid)
  }, [])

  return { sendMessage, stopGeneration }
}
