import { useRef, useState, useCallback } from "react"
import { toast } from "sonner"
import { saveMeetingTranscript, type TranscriptSegment } from "@/api/client"

export interface TranscriptionState {
  isConnected: boolean
  isTranscribing: boolean
  segments: TranscriptSegment[]
  currentPartial: string
  error: string | null
}

interface InternalSegment extends TranscriptSegment {
  __key: string
  __partial: boolean
}

function makeKey(data: { key?: string | null; start?: number; text?: string }): string {
  if (data.key) return data.key
  return `${Math.round((data.start ?? 0) * 1000)}:${(data.text ?? "").slice(0, 32)}`
}

export function useTranscription(meetingId: string | null) {
  const [state, setState] = useState<TranscriptionState>({
    isConnected: false,
    isTranscribing: false,
    segments: [],
    currentPartial: "",
    error: null,
  })

  const segmentMapRef = useRef<Map<string, InternalSegment>>(new Map())
  const sessionCounterRef = useRef(0)
  const wsRef = useRef<WebSocket | null>(null)
  const meetingIdRef = useRef(meetingId)
  meetingIdRef.current = meetingId
  const durationRef = useRef<number>(0)

  /** User wants live captions (set true in start, false in stop) */
  const wantLiveRef = useRef(false)
  /** Suppress reconnect / isTranscribing clear when we intentionally close */
  const intentionalCloseRef = useRef(false)
  /** Language hints for reconnect */
  const languageHintsRef = useRef<string[] | undefined>(undefined)
  /** Toast engine label only once per live session */
  const engineToastedRef = useRef(false)
  /** Reconnect attempts while recording */
  const reconnectAttemptRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** Monotonic id so stale WS handlers no-op */
  const connGenRef = useRef(0)

  const prevMeetingIdRef = useRef(meetingId)
  if (prevMeetingIdRef.current !== meetingId) {
    prevMeetingIdRef.current = meetingId
    segmentMapRef.current.clear()
    sessionCounterRef.current = 0
    durationRef.current = 0
    wantLiveRef.current = false
    intentionalCloseRef.current = true
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    if (wsRef.current) {
      try {
        wsRef.current.close()
      } catch {
        /* ignore */
      }
      wsRef.current = null
    }
    setState({
      isConnected: false,
      isTranscribing: false,
      segments: [],
      currentPartial: "",
      error: null,
    })
  }

  const onFinalizedRef = useRef<((segments: TranscriptSegment[]) => void) | null>(null)
  const setOnFinalized = useCallback((cb: ((segments: TranscriptSegment[]) => void) | null) => {
    onFinalizedRef.current = cb
  }, [])

  const disconnect = useCallback((opts?: { intentional?: boolean }) => {
    if (opts?.intentional) intentionalCloseRef.current = true
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    const ws = wsRef.current
    wsRef.current = null
    if (ws) {
      try {
        ws.close()
      } catch {
        /* ignore */
      }
    }
  }, [])

  const connect = useCallback((languageHints?: string[]) => {
    if (!meetingIdRef.current) return

    languageHintsRef.current = languageHints
    // Tear down previous socket without treating it as "user stopped"
    intentionalCloseRef.current = true
    if (wsRef.current) {
      try {
        wsRef.current.onclose = null
        wsRef.current.onerror = null
        wsRef.current.onmessage = null
        wsRef.current.close()
      } catch {
        /* ignore */
      }
      wsRef.current = null
    }
    intentionalCloseRef.current = false

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    let wsUrl = `${protocol}//${window.location.host}/api/meetings/${meetingIdRef.current}/realtime-transcribe`
    if (languageHints && languageHints.length > 0) {
      const params = languageHints
        .map((h) => `language_hints=${encodeURIComponent(h)}`)
        .join("&")
      wsUrl += `?${params}`
    }

    const offset = durationRef.current
    const session = sessionCounterRef.current
    const connGen = ++connGenRef.current

    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      if (connGen !== connGenRef.current) return
      console.log("[RealtimeTranscription] WebSocket connected")
      reconnectAttemptRef.current = 0
      setState((prev) => ({
        ...prev,
        isConnected: true,
        isTranscribing: wantLiveRef.current,
        error: null,
      }))
    }

    ws.onmessage = (event) => {
      try {
        if (connGen !== connGenRef.current) return
        if (!wsRef.current) return
        const data = JSON.parse(event.data)
        if (data.error) {
          console.error("[RealtimeTranscription] Server error:", data.error)
          setState((prev) => ({ ...prev, error: data.error }))
          toast.error(`Transcription error: ${data.error}`)
          return
        }
        if (data.type === "provider") {
          const adapter = data.adapter || "?"
          const model = data.model || ""
          console.log(
            "[RealtimeTranscription] Using provider",
            data.provider_id,
            adapter,
            model,
          )
          if (!engineToastedRef.current) {
            engineToastedRef.current = true
            const label = adapter.startsWith("funasr_onnx")
              ? `Local FunASR ONNX${model ? ` (${model})` : ""}`
              : adapter.includes("dashscope")
                ? `DashScope cloud${model ? ` (${model})` : ""}`
                : `${data.name || adapter}${model ? ` · ${model}` : ""}`
            toast.message("Realtime transcription", {
              description: `Engine: ${label}`,
            })
          }
          return
        }
        if (data.type === "ready") {
          console.log("[RealtimeTranscription] Session ready", data.message)
          return
        }
        if (data.type === "transcript") {
          const key = `s${session}:${makeKey(data)}`
          const seg: InternalSegment = {
            start: (data.start ?? 0) + offset,
            end: (data.end ?? 0) + offset,
            text: data.text ?? "",
            speaker_id: data.speaker_id,
            __key: key,
            __partial: !data.is_final,
          }
          segmentMapRef.current.set(key, seg)

          const finals = Array.from(segmentMapRef.current.values())
            .filter((s) => !s.__partial)
            .sort((a, b) => a.start - b.start)
            .map(({ __key, __partial, ...rest }) => rest)

          const partialEntries = Array.from(segmentMapRef.current.values())
            .filter((s) => s.__partial)
            .sort((a, b) => b.start - a.start)
          const partialText = partialEntries[0]?.text ?? ""

          setState((prev) => ({
            ...prev,
            segments: finals,
            currentPartial: partialText,
          }))
        }
      } catch (err) {
        console.error(
          "[RealtimeTranscription] Failed to parse message:",
          event.data,
          err,
        )
      }
    }

    ws.onerror = (e) => {
      if (connGen !== connGenRef.current) return
      console.error("[RealtimeTranscription] WebSocket error:", e)
      setState((prev) => ({ ...prev, error: "WebSocket connection error" }))
    }

    ws.onclose = (e) => {
      if (connGen !== connGenRef.current) return
      console.log(
        "[RealtimeTranscription] WebSocket closed:",
        e.code,
        e.reason,
        "intentional=",
        intentionalCloseRef.current,
        "wantLive=",
        wantLiveRef.current,
      )

      // Drop ref only if this is still the active socket
      if (wsRef.current === ws) wsRef.current = null

      setState((prev) => ({ ...prev, isConnected: false }))

      // Intentional stop: do not reconnect and do not clear isTranscribing here
      // (stopTranscription already sets isTranscribing false).
      if (intentionalCloseRef.current) {
        intentionalCloseRef.current = false
        return
      }

      // Unexpected close while user still wants live captions → quiet reconnect
      if (wantLiveRef.current) {
        const attempt = ++reconnectAttemptRef.current
        if (attempt > 12) {
          console.error("[RealtimeTranscription] Too many reconnects, giving up")
          wantLiveRef.current = false
          setState((prev) => ({
            ...prev,
            isTranscribing: false,
            error: "Realtime connection lost",
          }))
          toast.error("Realtime connection lost")
          return
        }
        // Keep isTranscribing true so meeting-view effect does NOT start a
        // second parallel session; we reconnect ourselves.
        const delay = Math.min(4000, 400 * attempt)
        console.log(
          `[RealtimeTranscription] Reconnecting in ${delay}ms (attempt ${attempt})`,
        )
        if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = setTimeout(() => {
          reconnectTimerRef.current = null
          if (wantLiveRef.current && meetingIdRef.current) {
            connect(languageHintsRef.current)
          }
        }, delay)
        return
      }

      // No longer want live: clear transcribing
      setState((prev) => ({ ...prev, isTranscribing: false }))
    }
  }, [])

  const startTranscription = useCallback(
    (languageHints?: string[]) => {
      sessionCounterRef.current += 1
      wantLiveRef.current = true
      engineToastedRef.current = false
      reconnectAttemptRef.current = 0

      const existingFinals = Array.from(segmentMapRef.current.values())
        .filter((s) => !s.__partial)
        .sort((a, b) => a.start - b.start)
      for (const [key, seg] of segmentMapRef.current) {
        if (seg.__partial) segmentMapRef.current.delete(key)
      }
      setState({
        isConnected: false,
        isTranscribing: true,
        segments: existingFinals.map(({ __key, __partial, ...rest }) => rest),
        currentPartial: "",
        error: null,
      })
      connect(languageHints)
    },
    [connect],
  )

  const stopTranscription = useCallback(
    (opts?: { discard?: boolean }) => {
      const discard = opts?.discard ?? false
      console.log(
        "[RealtimeTranscription] Stopping transcription",
        discard ? "(discard)" : "",
      )

      wantLiveRef.current = false
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }

      if (discard) {
        segmentMapRef.current.clear()
        sessionCounterRef.current = 0
        setState({
          isConnected: false,
          isTranscribing: false,
          segments: [],
          currentPartial: "",
          error: null,
        })
        disconnect({ intentional: true })
        return
      }

      for (const seg of segmentMapRef.current.values()) {
        if (seg.__partial) seg.__partial = false
      }
      const finals = Array.from(segmentMapRef.current.values())
        .sort((a, b) => a.start - b.start)
        .map(({ __key, __partial, ...rest }) => rest)
      setState((prev) => ({
        ...prev,
        isTranscribing: false,
        segments: finals,
        currentPartial: "",
      }))

      if (wsRef.current?.readyState === WebSocket.OPEN) {
        try {
          wsRef.current.send(JSON.stringify({ action: "stop" }))
          console.log("[RealtimeTranscription] Sent stop signal, waiting for flush")
        } catch (err) {
          console.warn("[RealtimeTranscription] Failed to send stop signal:", err)
        }
        setTimeout(() => {
          console.log("[RealtimeTranscription] Flush window elapsed, closing WebSocket")
          disconnect({ intentional: true })
        }, 2000)
      } else {
        disconnect({ intentional: true })
      }

      const mid = meetingIdRef.current
      if (mid && finals.length > 0) {
        const text = finals.map((s) => s.text).join(" ")
        saveMeetingTranscript(mid, { segments: finals, text })
          .then(() => {
            console.log(
              "[RealtimeTranscription] Saved %d segments to backend",
              finals.length,
            )
            onFinalizedRef.current?.(finals)
          })
          .catch((err) => {
            console.error("[RealtimeTranscription] Failed to save transcript:", err)
            toast.error(
              `Failed to save transcript: ${err instanceof Error ? err.message : String(err)}`,
            )
          })
      } else {
        onFinalizedRef.current?.(finals)
      }
    },
    [disconnect],
  )

  const sendAudioData = useCallback((data: ArrayBuffer | Blob) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    if (data instanceof Blob) {
      data.arrayBuffer().then((buf) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(buf)
      })
      return
    }
    ws.send(data)
  }, [])

  const setSegments = useCallback((segments: TranscriptSegment[]) => {
    setState((prev) => ({ ...prev, segments }))
  }, [])

  return {
    ...state,
    startTranscription,
    stopTranscription,
    sendAudioData,
    setSegments,
    setOnFinalized,
    durationRef,
  }
}
