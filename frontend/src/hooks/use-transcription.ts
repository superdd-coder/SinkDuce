import { useRef, useState, useCallback } from "react"
import { toast } from "sonner"
import {
  saveMeetingTranscript,
  type LiveSummaryState,
  type TranscriptSegment,
} from "@/api/client"

export interface TranscriptionState {
  isConnected: boolean
  isTranscribing: boolean
  segments: TranscriptSegment[]
  currentPartial: string
  /** Live-translate partial for the same block as currentPartial. */
  currentPartialTranslation: string
  error: string | null
  liveSummaryEnabled: boolean
  liveSummaryState: LiveSummaryState | null
  liveSummaryEngine: string
  liveSummaryError: string | null
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
    currentPartialTranslation: "",
    error: null,
    liveSummaryEnabled: false,
    liveSummaryState: null,
    liveSummaryEngine: "idle",
    liveSummaryError: null,
  })

  const segmentMapRef = useRef<Map<string, InternalSegment>>(new Map())
  const sessionCounterRef = useRef(0)
  const wsRef = useRef<WebSocket | null>(null)
  const meetingIdRef = useRef(meetingId)
  meetingIdRef.current = meetingId
  const durationRef = useRef<number>(0)

  /** User wants live captions (set true in start, false in stop) */
  const wantLiveRef = useRef(false)
  /** User wants the in-meeting live summary (survives reconnects) */
  const wantLiveSummaryRef = useRef(false)
  /** Suppress reconnect / isTranscribing clear when we intentionally close */
  const intentionalCloseRef = useRef(false)
  /** Language hints for reconnect */
  const languageHintsRef = useRef<string[] | undefined>(undefined)
  /** Translation target for reconnect (live bilingual captions) */
  const translationTargetRef = useRef<string | null>(null)
  /** Toast engine label only once per live session */
  const engineToastedRef = useRef(false)
  /** Reconnect attempts while recording */
  const reconnectAttemptRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** Engine/target swap: stop-flush → intentional close → reconnect */
  const switchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** True while an engine swap is in its flush window — suppresses the
   * onclose auto-reconnect so the swap timer is the only one reconnecting. */
  const engineSwitchingRef = useRef(false)
  /** Crash checkpoint debounce: one partial save per window while finals flow */
  const checkpointTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** Audio captured while the WS is still connecting / switching engines
   * (backend vocabulary setup takes seconds). Flushed on open so the first
   * words — and the words spoken during a toggle — are still transcribed.
   * Capped at ~2min of 500ms chunks. */
  const pendingAudioRef = useRef<ArrayBuffer[]>([])
  const PENDING_AUDIO_CAP = 240
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
    if (switchTimerRef.current) {
      clearTimeout(switchTimerRef.current)
      switchTimerRef.current = null
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
      currentPartialTranslation: "",
      error: null,
      liveSummaryEnabled: false,
      liveSummaryState: null,
      liveSummaryEngine: "idle",
      liveSummaryError: null,
    })
    wantLiveSummaryRef.current = false
  }

  const onFinalizedRef = useRef<((segments: TranscriptSegment[]) => void) | null>(null)
  const setOnFinalized = useCallback((cb: ((segments: TranscriptSegment[]) => void) | null) => {
    onFinalizedRef.current = cb
  }, [])

  /** Crash checkpoint: debounce-post closed segments so a page refresh mid-
   * recording leaves the transcript recoverable on the server. partial=true
   * keeps meeting status untouched; the save at stop stays authoritative. */
  const scheduleTranscriptCheckpoint = useCallback(() => {
    if (checkpointTimerRef.current) return
    checkpointTimerRef.current = setTimeout(() => {
      checkpointTimerRef.current = null
      const mid = meetingIdRef.current
      if (!mid || !wantLiveRef.current) return
      const finals = Array.from(segmentMapRef.current.values())
        .filter((s) => !s.__partial)
        .sort((a, b) => a.start - b.start)
        .map(({ __key, __partial, ...rest }) => rest)
      if (finals.length === 0) return
      const text = finals.map((s) => s.text).join(" ")
      saveMeetingTranscript(mid, { segments: finals, text, partial: true }).catch(() => {
        /* best-effort checkpoint — stop-path save is the authoritative one */
      })
    }, 5000)
  }, [])

  const cancelTranscriptCheckpoint = useCallback(() => {
    if (checkpointTimerRef.current) {
      clearTimeout(checkpointTimerRef.current)
      checkpointTimerRef.current = null
    }
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

  const connect = useCallback((languageHints?: string[], translationTarget?: string | null) => {
    if (!meetingIdRef.current) return

    languageHintsRef.current = languageHints
    translationTargetRef.current = translationTarget ?? null
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
    const params: string[] = []
    if (languageHints && languageHints.length > 0) {
      for (const h of languageHints) params.push(`language_hints=${encodeURIComponent(h)}`)
    }
    if (translationTarget) {
      params.push(`translation_target=${encodeURIComponent(translationTarget)}`)
    }
    if (params.length > 0) wsUrl += `?${params.join("&")}`

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
      // Audio captured while the session was warming up (or mid engine-swap)
      // goes first, in capture order.
      const queued = pendingAudioRef.current
      if (queued.length) {
        pendingAudioRef.current = []
        for (const buf of queued) {
          try {
            ws.send(buf)
          } catch {
            break
          }
        }
      }
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
              : adapter.includes("livetranslate")
                ? `DashScope LiveTranslate${model ? ` (${model})` : ""}`
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
          // Re-send the live-summary intent after a reconnect (idempotent
          // server-side; the engine may have survived on its own thread).
          if (wantLiveSummaryRef.current) {
            try {
              ws.send(JSON.stringify({ action: "live_summary", enabled: true }))
            } catch {
              /* ignore */
            }
          }
          return
        }
        if (data.type === "live_summary") {
          setState((prev) => ({
            ...prev,
            liveSummaryState: data.state ?? null,
            liveSummaryError: null,
          }))
          return
        }
        if (data.type === "live_summary_status") {
          setState((prev) => ({
            ...prev,
            liveSummaryEngine: data.engine ?? "idle",
          }))
          return
        }
        if (data.type === "live_summary_error") {
          console.warn("[RealtimeTranscription] Live summary error:", data.message)
          setState((prev) => ({ ...prev, liveSummaryError: data.message ?? "error" }))
          toast.error(`Live summary: ${data.message ?? "unavailable"}`)
          return
        }
        if (data.type === "transcript") {
          // After stop, ignore late non-finals — they overwrite a finalized
          // utterance and leave the Studio Transcript "Live" card stuck.
          if (!wantLiveRef.current && !data.is_final) return
          const key = `s${session}:${makeKey(data)}`
          const seg: InternalSegment = {
            start: (data.start ?? 0) + offset,
            end: (data.end ?? 0) + offset,
            text: data.text ?? "",
            speaker_id: data.speaker_id,
            translation: data.translation ?? undefined,
            __key: key,
            __partial: !data.is_final,
          }
          segmentMapRef.current.set(key, seg)

          if (data.is_final) scheduleTranscriptCheckpoint()

          const finals = Array.from(segmentMapRef.current.values())
            .filter((s) => !s.__partial)
            .sort((a, b) => a.start - b.start)
            .map(({ __key, __partial, ...rest }) => rest)

          const partialEntries = Array.from(segmentMapRef.current.values())
            .filter((s) => s.__partial)
            .sort((a, b) => b.start - a.start)
          const partialText = partialEntries[0]?.text ?? ""
          const partialTranslation = partialEntries[0]?.translation ?? ""

          setState((prev) => ({
            ...prev,
            segments: finals,
            currentPartial: partialText,
            currentPartialTranslation: partialTranslation,
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

      setState((prev) => ({
        ...prev,
        isConnected: false,
        currentPartial: "",
        currentPartialTranslation: "",
      }))

      // Intentional stop: do not reconnect and do not clear isTranscribing here
      // (stopTranscription already sets isTranscribing false).
      if (intentionalCloseRef.current) {
        intentionalCloseRef.current = false
        return
      }

      // Engine swap owns reconnection during its flush window
      if (engineSwitchingRef.current) return

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
            connect(languageHintsRef.current, translationTargetRef.current)
          }
        }, delay)
        return
      }

      // No longer want live: clear transcribing
      setState((prev) => ({ ...prev, isTranscribing: false }))
    }
  }, [])

  const startTranscription = useCallback(
    (languageHints?: string[], translationTarget?: string | null) => {
      sessionCounterRef.current += 1
      wantLiveRef.current = true
      engineSwitchingRef.current = false
      // Live summary is on by default each session (the ready handler sends
      // the enable action once the WS is up); the user can still turn it off.
      wantLiveSummaryRef.current = true
      engineToastedRef.current = false
      reconnectAttemptRef.current = 0

      const existingFinals = Array.from(segmentMapRef.current.values())
        .filter((s) => !s.__partial)
        .sort((a, b) => a.start - b.start)
      for (const [key, seg] of segmentMapRef.current) {
        if (seg.__partial) segmentMapRef.current.delete(key)
      }
      setState((prev) => ({
        ...prev,
        isConnected: false,
        isTranscribing: true,
        segments: existingFinals.map(({ __key, __partial, ...rest }) => rest),
        currentPartial: "",
        currentPartialTranslation: "",
        error: null,
        liveSummaryEnabled: true,
      }))
      connect(languageHints, translationTarget)
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
      engineSwitchingRef.current = false
      cancelTranscriptCheckpoint()
      pendingAudioRef.current = []
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      if (switchTimerRef.current) {
        clearTimeout(switchTimerRef.current)
        switchTimerRef.current = null
      }

      if (discard) {
        segmentMapRef.current.clear()
        sessionCounterRef.current = 0
        wantLiveSummaryRef.current = false
        setState({
          isConnected: false,
          isTranscribing: false,
          segments: [],
          currentPartial: "",
          currentPartialTranslation: "",
          error: null,
          liveSummaryEnabled: false,
          liveSummaryState: null,
          liveSummaryEngine: "idle",
          liveSummaryError: null,
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
        currentPartialTranslation: "",
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
    [cancelTranscriptCheckpoint, disconnect],
  )

  const sendAudioData = useCallback((data: ArrayBuffer | Blob) => {
    const ws = wsRef.current
    if (data instanceof ArrayBuffer) {
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        // Pipeline still warming up (or mid engine-swap): keep the audio so
        // it is transcribed once the session is live, instead of dropping it.
        if (wantLiveRef.current) {
          const q = pendingAudioRef.current
          q.push(data)
          if (q.length > PENDING_AUDIO_CAP) {
            q.splice(0, q.length - PENDING_AUDIO_CAP)
          }
        }
        return
      }
      ws.send(data)
      return
    }
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    data.arrayBuffer().then((buf) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(buf)
    })
  }, [])

  const setSegments = useCallback((segments: TranscriptSegment[]) => {
    setState((prev) => ({ ...prev, segments }))
  }, [])

  /**
   * Swap the transcription engine mid-session (toggle live translation or
   * change its target language) via a graceful reconnect: ask the server to
   * flush the tail segment ("stop" action → LiveTranslate end_session), then
   * intentionally close and reconnect with the new params. Finals already
   * received are kept; audio recording is a separate PCM consumer and is
   * never interrupted. Pass null to return to the plain ASR engine.
   */
  const reconfigureTranslation = useCallback(
    (translationTarget: string | null) => {
      translationTargetRef.current = translationTarget
      const ws = wsRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN || !wantLiveRef.current) {
        // Not in a live session — the next startTranscription picks the ref up.
        return
      }
      try {
        // finalize:false — this stop only flushes the engine tail before the
        // reconnect; finalizing would idle the live-summary engine and its
        // closing LLM round would race the re-enable on the new session.
        ws.send(JSON.stringify({ action: "stop", finalize: false }))
      } catch {
        /* socket already dying — the swap timer below reconnects */
      }
      engineSwitchingRef.current = true
      if (switchTimerRef.current) clearTimeout(switchTimerRef.current)
      switchTimerRef.current = setTimeout(() => {
        switchTimerRef.current = null
        engineSwitchingRef.current = false
        if (!wantLiveRef.current || !meetingIdRef.current) return
        // Isolate dedup key spaces between engines (numeric sentence ids
        // restart at 0 across plain-ASR sessions).
        sessionCounterRef.current += 1
        disconnect({ intentional: true })
        setState((prev) => ({
          ...prev,
          currentPartial: "",
          currentPartialTranslation: "",
        }))
        connect(languageHintsRef.current, translationTargetRef.current)
      }, 2200)
    },
    [connect, disconnect],
  )

  const setLiveSummaryEnabled = useCallback((enabled: boolean) => {
    wantLiveSummaryRef.current = enabled
    setState((prev) => ({ ...prev, liveSummaryEnabled: enabled }))
    const ws = wsRef.current
    if (ws?.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ action: "live_summary", enabled }))
      } catch (err) {
        console.warn("[RealtimeTranscription] Failed to send live_summary:", err)
      }
    }
  }, [])

  const resetLiveSummary = useCallback(() => {
    const ws = wsRef.current
    if (ws?.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ action: "live_summary_reset" }))
      } catch (err) {
        console.warn("[RealtimeTranscription] Failed to send live_summary_reset:", err)
      }
    }
  }, [])

  return {
    ...state,
    startTranscription,
    stopTranscription,
    sendAudioData,
    setSegments,
    setOnFinalized,
    setLiveSummaryEnabled,
    resetLiveSummary,
    reconfigureTranslation,
    durationRef,
  }
}
