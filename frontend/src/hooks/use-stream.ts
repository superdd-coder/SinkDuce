import { useCallback } from "react"
import {
  useAppStore,
  _registerStream, _abortStream, _unregisterStream,
  _getCachedMessages, _setCachedMessages,
  type ThinkingSummary,
} from "@/stores/app-store"
import { generateSessionTitle, listSessions } from "@/api/client"
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
  const {
    sessionId, initSession,
    addMessage, appendToLastMessage, setLastMessageSources,
    appendTimelineThinking, setTimelineToolSummary, setTimelineToolStatus, startTimelineTool,
    finishLastMessage, flushLastMessageToThinking, setStreaming, selectedCollections,
  } = useAppStore()

  const sendMessage = async (content: string, thinking = true) => {
    let sid = sessionId
    if (!sid) {
      sid = await initSession()
    }

    // Abort previous stream for the SAME session only
    _abortStream(sid)
    const controller = new AbortController()
    _registerStream(sid, controller)

    addMessage({ id: crypto.randomUUID(), role: "user", content })
    const assistantId = crypto.randomUUID()
    addMessage({ id: assistantId, role: "assistant", content: "", isStreaming: true })
    setStreaming(true)
    _syncCacheFromStore(sid)

    try {
      const resp = await fetch(`/api/sessions/${sid}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, thinking, collections: selectedCollections }),
        signal: controller.signal,
      })

      if (!resp.ok) {
        const err = await resp.text()
        if (_isActive(sid)) appendToLastMessage(`Error: ${resp.status} - ${err}`)
        else _cacheAppend(sid, `Error: ${resp.status} - ${err}`)
        if (_isActive(sid)) finishLastMessage()
        else _cacheFinishLast(sid)
        return
      }

      const reader = resp.body!.getReader()
      const decoder = new TextDecoder()
      let buffer = "", currentEvent = ""

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split("\n")
        buffer = lines.pop() ?? ""

        for (const line of lines) {
          const t = line.trimEnd()
          if (t.startsWith("event: ")) {
            currentEvent = t.slice(7).trim()
          } else if (t.startsWith("data: ")) {
            let data: any
            try { data = JSON.parse(t.slice(6)) } catch { continue }

            const active = _isActive(sid)

            switch (currentEvent) {
              case "thinking":
                if (active) appendTimelineThinking(data.content)
                break

              case "token":
                if (active) appendToLastMessage(data.content)
                else _cacheAppend(sid, data.content)
                break

              case "tool_call_start":
                if (active) {
                  flushLastMessageToThinking()
                  startTimelineTool()
                }
                break

              case "tool_step":
                // Live progress: update tool block status from step events
                if (active) setTimelineToolStatus(data.content || data.step || "")
                break

              case "thinking_summary":
                if (active) setTimelineToolSummary(data as ThinkingSummary)
                break

              case "tool_result":
                // Tool execution complete — summary already sent via thinking_summary
                break

              case "done":
                if (data.sources?.length) {
                  if (active) setLastMessageSources(data.sources)
                  else _cacheSetLastSources(sid, data.sources)
                }
                if (active) {
                  finishLastMessage()
                  _syncCacheFromStore(sid)
                } else {
                  _cacheFinishLast(sid)
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
                if (active) appendToLastMessage(`Error: ${data.content}`)
                else _cacheAppend(sid, `Error: ${data.content}`)
                if (active) finishLastMessage()
                else _cacheFinishLast(sid)
                return
            }
            currentEvent = ""
          }
        }
      }
    } catch (err: any) {
      if (err.name === "AbortError") return
      if (_isActive(sid)) appendToLastMessage(`Error: ${err.message}`)
      else _cacheAppend(sid, `Error: ${err.message}`)
    } finally {
      if (_isActive(sid)) {
        finishLastMessage()
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
