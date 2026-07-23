import { useRef, useState, useCallback } from "react"

export interface AudioRecorderState {
  isRecording: boolean
  isPaused: boolean
  duration: number
  audioBlob: Blob | null
  audioUrl: string | null
  error: string | null
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
  })

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const durationRef = useRef(0)
  const onAudioChunkRef = useRef(onAudioChunk)
  onAudioChunkRef.current = onAudioChunk

  const startRecording = useCallback(async () => {
    try {
      // Step 1: mic — silent if already permitted, one-time prompt otherwise.
      const micStream = await navigator.mediaDevices.getUserMedia({ audio: true })

      // Step 2: system audio — required.  Browser forces a picker dialog
      // every time; user MUST pick a tab/window with "Share audio" checked.
      let systemAudioStream: MediaStream
      try {
        const systemStream = await navigator.mediaDevices.getDisplayMedia({
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
        systemAudioStream = new MediaStream(audioTracks)
      } catch (e) {
        // If the inner error is already our "No system audio" message, re-throw it.
        // Otherwise it's a DOMException (user cancelled) — throw a friendlier message.
        if (e instanceof Error && e.message.startsWith("No system audio")) throw e
        throw new Error(
          "Recording requires system audio. Please select a window and enable \"Share audio\"."
        )
      }

      // Mix mic + system audio
      const audioCtx = new AudioContext({ sampleRate: 16000 })
      audioCtxRef.current = audioCtx
      const destination = audioCtx.createMediaStreamDestination()
      const micSource = audioCtx.createMediaStreamSource(micStream)
      micSource.connect(destination)
      const sysSource = audioCtx.createMediaStreamSource(systemAudioStream)
      sysSource.connect(destination)
      const finalStream: MediaStream = destination.stream

      streamRef.current = finalStream

      // Set up real-time PCM capture via AudioWorklet for streaming transcription.
      // ALWAYS build the pipeline — even if Live captions is off at record start.
      // The user may toggle it on mid-recording; without the pipeline PCM chunks
      // would never reach the WebSocket.
      {
        try {
          const workletCtx = new AudioContext({ sampleRate: 16000 })
          audioCtxRef.current = workletCtx
          const source = workletCtx.createMediaStreamSource(finalStream)

          const blob = new Blob([WORKLET_CODE], { type: "application/javascript" })
          const url = URL.createObjectURL(blob)
          await workletCtx.audioWorklet.addModule(url)
          URL.revokeObjectURL(url)

          const node = new AudioWorkletNode(workletCtx, "pcm-capture")
          node.port.onmessage = (e) => {
            if (onAudioChunkRef.current && e.data instanceof ArrayBuffer) {
              console.log("[AudioRecorder] Sending PCM chunk:", e.data.byteLength, "bytes")
              onAudioChunkRef.current(e.data)
            }
          }
          source.connect(node)
          node.connect(workletCtx.destination)
        } catch {
          // AudioWorklet not supported — fall back to ScriptProcessorNode
          try {
            const scriptCtx = audioCtxRef.current || new AudioContext({ sampleRate: 16000 })
            if (!audioCtxRef.current) audioCtxRef.current = scriptCtx
            const source = scriptCtx.createMediaStreamSource(finalStream)
            const processor = scriptCtx.createScriptProcessor(8192, 1, 1)
            let buffer: number[] = []
            const chunkSamples = 8000 // 500ms at 16kHz

            processor.onaudioprocess = (e) => {
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
          } catch {
            // Neither supported — no real-time transcription
          }
        }
      }

      // Set up MediaRecorder for saving the full audio file
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : MediaRecorder.isTypeSupported("audio/webm")
          ? "audio/webm"
          : "audio/wav"

      const recorder = new MediaRecorder(finalStream, { mimeType })
      chunksRef.current = []
      durationRef.current = 0

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }

      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: mimeType })
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

      setState((prev) => ({
        ...prev,
        isRecording: true,
        isPaused: false,
        duration: 0,
        audioBlob: null,
        audioUrl: null,
        error: null,
      }))
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setState((prev) => ({ ...prev, error: msg }))
    }
  }, [])

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop()
    }
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    if (audioCtxRef.current) { audioCtxRef.current.close(); audioCtxRef.current = null }
  }, [])

  const pauseRecording = useCallback(() => {
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.pause()
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
      setState((prev) => ({ ...prev, isPaused: true }))
    }
  }, [])

  const resumeRecording = useCallback(() => {
    if (mediaRecorderRef.current?.state === "paused") {
      mediaRecorderRef.current.resume()
      timerRef.current = setInterval(() => {
        durationRef.current += 1
        setState((prev) => ({ ...prev, duration: durationRef.current }))
      }, 1000)
      setState((prev) => ({ ...prev, isPaused: false }))
    }
  }, [])

  const reset = useCallback(() => {
    if (state.audioUrl) URL.revokeObjectURL(state.audioUrl)
    setState({
      isRecording: false,
      isPaused: false,
      duration: 0,
      audioBlob: null,
      audioUrl: null,
      error: null,
    })
  }, [state.audioUrl])

  return {
    ...state,
    startRecording,
    stopRecording,
    pauseRecording,
    resumeRecording,
    reset,
  }
}
