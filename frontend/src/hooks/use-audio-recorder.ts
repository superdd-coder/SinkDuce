import { useRef, useState, useCallback, useEffect } from "react"
import { getHealth } from "@/api/client"
import { startDesktopSystemAudio } from "@/lib/desktop-system-audio"
import {
  createCaptureAudioContext,
  microphoneErrorMessage,
  pickRecorderMimeType,
  requestMicrophoneStream,
} from "@/lib/microphone"

/** Number of bars exposed for the live capture waveform UI. */
export const AUDIO_LEVEL_BAR_COUNT = 24

export interface AudioRecorderState {
  isRecording: boolean
  isPaused: boolean
  duration: number
  audioBlob: Blob | null
  audioUrl: string | null
  error: string | null
  /** 0–1 amplitudes for live waveform (real AnalyserNode data). */
  levels: number[]
}

function emptyLevels(): number[] {
  return Array.from({ length: AUDIO_LEVEL_BAR_COUNT }, () => 0)
}

/**
 * Map AnalyserNode frequency bins → bar heights 0–1.
 * Pure helper so tests can lock the scaling without Web Audio.
 */
async function isDesktopRuntime(): Promise<boolean> {
  try {
    const h = await getHealth()
    return h.desktop === true
  } catch {
    return false
  }
}

export function binsToLevels(
  bins: ArrayLike<number>,
  barCount: number = AUDIO_LEVEL_BAR_COUNT,
): number[] {
  const n = bins.length
  if (n === 0 || barCount <= 0) return Array.from({ length: barCount }, () => 0)
  const out: number[] = []
  for (let i = 0; i < barCount; i++) {
    const start = Math.floor((i * n) / barCount)
    const end = Math.max(start + 1, Math.floor(((i + 1) * n) / barCount))
    let sum = 0
    for (let j = start; j < end; j++) sum += bins[j] ?? 0
    const avg = sum / (end - start)
    // 0–255 → 0–1 with soft floor so silence stays flat
    const v = Math.max(0, (avg - 8) / 180)
    out.push(Math.min(1, v))
  }
  return out
}

const WORKLET_CODE = `
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this._buffer = []
    this._chunkSamples = 8000 // 500ms at 16kHz
  }
  process(inputs) {
    const input = inputs[0]
    if (input && input[0]) {
      const channel = input[0]
      for (let i = 0; i < channel.length; i++) {
        this._buffer.push(channel[i])
        if (this._buffer.length >= this._chunkSamples) {
          const chunk = new Float32Array(this._buffer)
          const pcm = new Int16Array(chunk.length)
          for (let j = 0; j < chunk.length; j++) {
            const s = Math.max(-1, Math.min(1, chunk[j]))
            pcm[j] = s < 0 ? s * 0x8000 : s * 0x7FFF
          }
          this.port.postMessage(pcm.buffer, [pcm.buffer])
          this._buffer = []
        }
      }
    }
    return true
  }
}
registerProcessor('pcm-capture', PcmCaptureProcessor)
`

export function useAudioRecorder(onAudioChunk?: (pcm: ArrayBuffer) => void) {
  const [state, setState] = useState<AudioRecorderState>({
    isRecording: false,
    isPaused: false,
    duration: 0,
    audioBlob: null,
    audioUrl: null,
    error: null,
    levels: emptyLevels(),
  })

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const levelRafRef = useRef<number | null>(null)
  /** True while MediaRecorder is paused — freezes levels AND stops PCM → live captions. */
  const capturePausedRef = useRef(false)
  const durationRef = useRef(0)
  const onAudioChunkRef = useRef(onAudioChunk)
  onAudioChunkRef.current = onAudioChunk
  const sysAudioStopRef = useRef<(() => void) | null>(null)

  const stopLevelLoop = useCallback(() => {
    if (levelRafRef.current != null) {
      cancelAnimationFrame(levelRafRef.current)
      levelRafRef.current = null
    }
    analyserRef.current = null
    setState((prev) => {
      const cur = prev.levels ?? emptyLevels()
      return cur.every((v) => v === 0) ? { ...prev, levels: cur } : { ...prev, levels: emptyLevels() }
    })
  }, [])

  const startLevelLoop = useCallback((analyser: AnalyserNode) => {
    analyserRef.current = analyser
    capturePausedRef.current = false
    const data = new Uint8Array(analyser.frequencyBinCount)
    let lastPublish = 0
    // ~12 fps — enough for waveform UI without re-rendering MeetingView every frame
    const MIN_MS = 80

    const tick = (now: number) => {
      if (!analyserRef.current) return
      if (now - lastPublish >= MIN_MS) {
        lastPublish = now
        if (capturePausedRef.current) {
          setState((prev) => {
            const cur = prev.levels ?? emptyLevels()
            return cur.every((v) => v === 0)
              ? { ...prev, levels: cur }
              : { ...prev, levels: emptyLevels() }
          })
        } else {
          analyserRef.current.getByteFrequencyData(data)
          const next = binsToLevels(data, AUDIO_LEVEL_BAR_COUNT)
          setState((prev) => ({ ...prev, levels: next }))
        }
      }
      levelRafRef.current = requestAnimationFrame(tick)
    }
    if (levelRafRef.current != null) cancelAnimationFrame(levelRafRef.current)
    levelRafRef.current = requestAnimationFrame(tick)
  }, [])

  /** @returns null if recording started; otherwise a user-facing error message */
  const startRecording = useCallback(async (): Promise<string | null> => {
    try {
      // Step 1: mic — silent if already permitted, one-time prompt otherwise.
      // WKWebView often rejects `{ audio: true }` with OverconstrainedError:
      // "Invalid constraint". Retry with relaxed constraints.
      const gum = navigator.mediaDevices?.getUserMedia?.bind(
        navigator.mediaDevices,
      )
      if (!gum) {
        throw new Error("This browser cannot access the microphone.")
      }
      const micStream = await requestMicrophoneStream(gum)

      // Step 2: system audio. Browser uses getDisplayMedia "Share audio".
      // Desktop WKWebView cannot do that — mix the ScreenCaptureKit helper instead.
      let finalStream: MediaStream = micStream
      const desktop = await isDesktopRuntime()
      if (desktop) {
        sysAudioStopRef.current?.()
        const sys = await startDesktopSystemAudio()
        sysAudioStopRef.current = sys.stop
        const audioCtxMix = createCaptureAudioContext()
        audioCtxRef.current = audioCtxMix
        const destination = audioCtxMix.createMediaStreamDestination()
        audioCtxMix.createMediaStreamSource(micStream).connect(destination)
        audioCtxMix.createMediaStreamSource(sys.stream).connect(destination)
        finalStream = destination.stream
      } else {
        const display = navigator.mediaDevices?.getDisplayMedia?.bind(
          navigator.mediaDevices
        )
        if (!display) {
          throw new Error(
            "Recording requires system audio. Please select a window and enable \"Share audio\"."
          )
        }
        try {
          const systemStream = await display({
            video: true,
            audio: true,
          })
          systemStream.getVideoTracks().forEach((t) => t.stop())
          const audioTracks = systemStream.getAudioTracks()
          if (audioTracks.length === 0) {
            throw new Error(
              "No system audio captured. Please check \"Share audio\" when selecting a window."
            )
          }
          const audioCtxMix = createCaptureAudioContext()
          audioCtxRef.current = audioCtxMix
          const destination = audioCtxMix.createMediaStreamDestination()
          audioCtxMix.createMediaStreamSource(micStream).connect(destination)
          audioCtxMix
            .createMediaStreamSource(new MediaStream(audioTracks))
            .connect(destination)
          finalStream = destination.stream
        } catch (e) {
          if (e instanceof Error && e.message.startsWith("No system audio")) throw e
          const cancelled =
            e instanceof DOMException &&
            (e.name === "NotAllowedError" || e.name === "AbortError")
          throw new Error(
            cancelled
              ? "Screen share was cancelled. Select a window and enable \"Share audio\" to record."
              : "Recording requires system audio. Please select a window and enable \"Share audio\"."
          )
        }
      }

      streamRef.current = finalStream

      // Set up real-time PCM capture via AudioWorklet for streaming transcription.
      // ALWAYS build the pipeline — even if Live captions is off at record start.
      // The user may toggle it on mid-recording; without the pipeline PCM chunks
      // would never reach the WebSocket.
      {
        try {
          const workletCtx = createCaptureAudioContext()
          audioCtxRef.current = workletCtx
          const source = workletCtx.createMediaStreamSource(finalStream)

          const blob = new Blob([WORKLET_CODE], { type: "application/javascript" })
          const url = URL.createObjectURL(blob)
          await workletCtx.audioWorklet.addModule(url)
          URL.revokeObjectURL(url)

          const node = new AudioWorkletNode(workletCtx, "pcm-capture")
          node.port.onmessage = (e) => {
            // Pause freezes MediaRecorder only — also gate live-caption PCM
            if (capturePausedRef.current) return
            if (onAudioChunkRef.current && e.data instanceof ArrayBuffer) {
              console.log("[AudioRecorder] Sending PCM chunk:", e.data.byteLength, "bytes")
              onAudioChunkRef.current(e.data)
            }
          }
          source.connect(node)
          node.connect(workletCtx.destination)

          // Real waveform: AnalyserNode on the same mixed stream
          const levelSource = workletCtx.createMediaStreamSource(finalStream)
          const analyser = workletCtx.createAnalyser()
          analyser.fftSize = 128
          analyser.smoothingTimeConstant = 0.72
          levelSource.connect(analyser)
          startLevelLoop(analyser)
        } catch {
          // AudioWorklet not supported — fall back to ScriptProcessorNode
          try {
            const scriptCtx = audioCtxRef.current || createCaptureAudioContext()
            if (!audioCtxRef.current) audioCtxRef.current = scriptCtx
            const source = scriptCtx.createMediaStreamSource(finalStream)
            const processor = scriptCtx.createScriptProcessor(8192, 1, 1)
            let buffer: number[] = []
            const chunkSamples = 8000 // 500ms at 16kHz

            processor.onaudioprocess = (e) => {
              if (capturePausedRef.current) {
                buffer = []
                return
              }
              const input = e.inputBuffer.getChannelData(0)
              for (let i = 0; i < input.length; i++) {
                buffer.push(input[i])
                if (buffer.length >= chunkSamples) {
                  const pcm = new Int16Array(buffer.length)
                  for (let j = 0; j < buffer.length; j++) {
                    const s = Math.max(-1, Math.min(1, buffer[j]))
                    pcm[j] = s < 0 ? s * 0x8000 : s * 0x7FFF
                  }
                  if (onAudioChunkRef.current) {
                    onAudioChunkRef.current(pcm.buffer.slice(0) as ArrayBuffer)
                  }
                  buffer = []
                }
              }
            }
            source.connect(processor)
            processor.connect(scriptCtx.destination)

            const levelSource = scriptCtx.createMediaStreamSource(finalStream)
            const analyser = scriptCtx.createAnalyser()
            analyser.fftSize = 128
            analyser.smoothingTimeConstant = 0.72
            levelSource.connect(analyser)
            startLevelLoop(analyser)
          } catch {
            // Neither supported — no real-time transcription / levels
          }
        }
      }

      // Set up MediaRecorder for saving the full audio file.
      // Safari/WKWebView does not support webm — fall back to mp4, then browser default.
      const mimeType = pickRecorderMimeType((t) =>
        MediaRecorder.isTypeSupported(t),
      )

      const recorder = mimeType
        ? new MediaRecorder(finalStream, { mimeType })
        : new MediaRecorder(finalStream)
      chunksRef.current = []
      durationRef.current = 0

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }

      recorder.onstop = () => {
        const blobType = mimeType || recorder.mimeType || "audio/webm"
        const blob = new Blob(chunksRef.current, { type: blobType })
        const url = URL.createObjectURL(blob)
        setState((prev) => ({ ...prev, audioBlob: blob, audioUrl: url, isRecording: false, isPaused: false }))
        if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
      }

      recorder.start(1000)
      mediaRecorderRef.current = recorder

      timerRef.current = setInterval(() => {
        durationRef.current += 1
        setState((prev) => ({ ...prev, duration: durationRef.current }))
      }, 1000)

      capturePausedRef.current = false
      setState((prev) => ({
        ...prev,
        isRecording: true,
        isPaused: false,
        duration: 0,
        audioBlob: null,
        audioUrl: null,
        error: null,
        levels: emptyLevels(),
      }))
      return null
    } catch (err) {
      const msg = microphoneErrorMessage(err)
      sysAudioStopRef.current?.()
      sysAudioStopRef.current = null
      stopLevelLoop()
      setState((prev) => ({ ...prev, error: msg, levels: emptyLevels() }))
      return msg
    }
  }, [startLevelLoop, stopLevelLoop])

  const stopRecording = useCallback(() => {
    capturePausedRef.current = false
    stopLevelLoop()
    sysAudioStopRef.current?.()
    sysAudioStopRef.current = null
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop()
    }
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    if (audioCtxRef.current) { audioCtxRef.current.close(); audioCtxRef.current = null }
  }, [stopLevelLoop])

  const pauseRecording = useCallback(() => {
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.pause()
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
      // Stop PCM → realtime WS so captions freeze; keep WS open for resume
      capturePausedRef.current = true
      setState((prev) => ({ ...prev, isPaused: true, levels: emptyLevels() }))
    }
  }, [])

  const resumeRecording = useCallback(() => {
    if (mediaRecorderRef.current?.state === "paused") {
      mediaRecorderRef.current.resume()
      capturePausedRef.current = false
      timerRef.current = setInterval(() => {
        durationRef.current += 1
        setState((prev) => ({ ...prev, duration: durationRef.current }))
      }, 1000)
      setState((prev) => ({ ...prev, isPaused: false }))
    }
  }, [])

  const reset = useCallback(() => {
    stopLevelLoop()
    if (state.audioUrl) URL.revokeObjectURL(state.audioUrl)
    setState({
      isRecording: false,
      isPaused: false,
      duration: 0,
      audioBlob: null,
      audioUrl: null,
      error: null,
      levels: emptyLevels(),
    })
  }, [state.audioUrl, stopLevelLoop])

  // Cleanup rAF on unmount
  useEffect(() => () => {
    if (levelRafRef.current != null) cancelAnimationFrame(levelRafRef.current)
  }, [])

  return {
    ...state,
    startRecording,
    stopRecording,
    pauseRecording,
    resumeRecording,
    reset,
  }
}
