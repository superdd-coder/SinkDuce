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
  // Fallback: use start time + text prefix as a stable key for this segment
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

  // Map of sentence_id -> segment. We mutate this synchronously in the
  // WebSocket onmessage handler, then trigger a single setState per message
  // so React re-renders with the latest data. The Map is the source of
  // truth for "what segments exist"; setState just mirrors it.
  const segmentMapRef = useRef<Map<string, InternalSegment>>(new Map())

  const wsRef = useRef<WebSocket | null>(null)
  const meetingIdRef = useRef(meetingId)
  meetingIdRef.current = meetingId

  // For time offset: the hook needs to know the current recording duration
  // when a new session starts.  Store the ref so the calling component
  // can update it (e.g. from useAudioRecorder.duration).
  const durationRef = useRef<number>(0)

  // Reset segments when meeting changes
  const prevMeetingIdRef = useRef(meetingId)
  if (prevMeetingIdRef.current !== meetingId) {
    prevMeetingIdRef.current = meetingId
    segmentMapRef.current.clear()
    durationRef.current = 0
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

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
  }, [])

  const connect = useCallback((languageHints?: string[]) => {
    if (!meetingIdRef.current) return
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    let wsUrl = `${protocol}//${window.location.host}/api/meetings/${meetingIdRef.current}/realtime-transcribe`
    if (languageHints && languageHints.length > 0) {
      const params = languageHints.map((h) => `language_hints=${encodeURIComponent(h)}`).join("&")
      wsUrl += `?${params}`
    }

    const offset = durationRef.current

    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      console.log("[RealtimeTranscription] WebSocket connected")
      setState((prev) => ({ ...prev, isConnected: true, error: null }))
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.error) {
          console.error("[RealtimeTranscription] Server error:", data.error)
          setState((prev) => ({ ...prev, error: data.error }))
          toast.error(`Transcription error: ${data.error}`)
          return
        }
        if (data.type === "transcript") {
          const key = makeKey(data)
          const seg: InternalSegment = {
            start: (data.start ?? 0) + offset,
            end: (data.end ?? 0) + offset,
            text: data.text ?? "",
            speaker_id: data.speaker_id,
            __key: key,
            __partial: !data.is_final,
          }
          segmentMapRef.current.set(key, seg)
          console.log(
            "[RealtimeTranscription]",
            data.is_final ? "FINAL" : "partial",
            "key=" + key,
            "text=" + seg.text.slice(0, 60),
            "mapSize=" + segmentMapRef.current.size,
          )

          // Build the visible list: only finalized segments, sorted by start
          const finals = Array.from(segmentMapRef.current.values())
            .filter((s) => !s.__partial)
            .sort((a, b) => a.start - b.start)
            .map(({ __key, __partial, ...rest }) => rest)

          // Latest partial text is the most recent entry that is still partial
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
        console.error("[RealtimeTranscription] Failed to parse message:", event.data, err)
      }
    }

    ws.onerror = (e) => {
      console.error("[RealtimeTranscription] WebSocket error:", e)
      setState((prev) => ({ ...prev, error: "WebSocket connection error" }))
    }

    ws.onclose = (e) => {
      console.log("[RealtimeTranscription] WebSocket closed:", e.code, e.reason)
      setState((prev) => ({ ...prev, isConnected: false, isTranscribing: false }))
    }
  }, [])

  const startTranscription = useCallback((languageHints?: string[]) => {
    // Preserve existing segments from previous sessions — we APPEND
    // new segments on toggle-reopen, not replace.
    const existingFinals = Array.from(segmentMapRef.current.values())
      .filter((s) => !s.__partial)
      .sort((a, b) => a.start - b.start)
    // Keep existing finals in the map; clear only partials.
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
  }, [connect])

  const stopTranscription = useCallback(() => {
    console.log("[RealtimeTranscription] Stopping transcription")
    // Promote any still-partial segments to final so the user's last
    // sentence is captured in the transcript when they stop recording.
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

    // Tell the backend to stop the SDK and flush the last sentence before
    // the WebSocket closes. Without this, the last 1-2s of audio loses its
    // recognized text because the SDK never gets a chance to finalize it.
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      try {
        wsRef.current.send(JSON.stringify({ action: "stop" }))
        console.log("[RealtimeTranscription] Sent stop signal, waiting for flush")
      } catch (err) {
        console.warn("[RealtimeTranscription] Failed to send stop signal:", err)
      }
      // Give the backend ~2s to receive the stop, run provider.stop(), and
      // deliver the final segment. The server's finally-block will close
      // the socket after flushing.
      setTimeout(() => {
        console.log("[RealtimeTranscription] Flush window elapsed, closing WebSocket")
        disconnect()
      }, 2000)
    } else {
      disconnect()
    }

    // Persist to the backend so the meeting gets a transcript_path and the
    // Summarize / Allocate buttons become available. Fire-and-forget; the
    // parent will refetch the meeting via the onFinalized callback.
    const mid = meetingIdRef.current
    if (mid && finals.length > 0) {
      const text = finals.map((s) => s.text).join(" ")
      saveMeetingTranscript(mid, { segments: finals, text })
        .then(() => {
          console.log("[RealtimeTranscription] Saved %d segments to backend", finals.length)
          onFinalizedRef.current?.(finals)
        })
        .catch((err) => {
          console.error("[RealtimeTranscription] Failed to save transcript:", err)
          toast.error(`Failed to save transcript: ${err instanceof Error ? err.message : String(err)}`)
        })
    } else {
      onFinalizedRef.current?.(finals)
    }
  }, [disconnect])

  const sendAudioData = useCallback((data: ArrayBuffer | Blob) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(data)
    }
  }, [])

  const setSegments = useCallback((segments: TranscriptSegment[]) => {
    setState((prev) => ({ ...prev, segments }))
  }, [])

  // Intentionally NOT disconnecting on unmount: the user may navigate
  // around (e.g. open backend log) during a long recording. The WebSocket
  // stays alive until stopTranscription() is explicitly called, or the
  // page is closed entirely.

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
