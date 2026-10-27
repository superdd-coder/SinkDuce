/** WebKit/WKWebView often rejects `{ audio: true }` with OverconstrainedError. */

export type GetUserMediaFn = (
  constraints: MediaStreamConstraints,
) => Promise<MediaStream>

export const MIC_CONSTRAINT_ATTEMPTS: MediaStreamConstraints[] = [
  { audio: true, video: false },
  {
    audio: {
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
    },
    video: false,
  },
  { audio: {}, video: false },
]

export function isInvalidMediaConstraint(err: unknown): boolean {
  if (err == null) return false
  const name =
    typeof err === "object" && err !== null && "name" in err
      ? String((err as { name: unknown }).name)
      : ""
  const msg = err instanceof Error ? err.message : String(err)
  return name === "OverconstrainedError" || /invalid constraint/i.test(msg)
}

export function microphoneErrorMessage(err: unknown): string {
  const name =
    typeof err === "object" && err !== null && "name" in err
      ? String((err as { name: unknown }).name)
      : ""
  const msg = err instanceof Error ? err.message : String(err)
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Microphone access was blocked. Allow the microphone for SinkDuce, then try again."
  }
  if (name === "NotFoundError") {
    return "No microphone was found. Connect a microphone and try again."
  }
  if (isInvalidMediaConstraint(err)) {
    return "Could not access the microphone. Allow SinkDuce in System Settings → Privacy & Security → Microphone, connect a mic, then try again."
  }
  if (/load failed|failed to fetch/i.test(msg)) {
    return "Could not start desktop audio capture. Quit SinkDuce with Cmd+Q, reopen, and try again."
  }
  if (msg && !/invalid constraint/i.test(msg)) return msg
  return "Could not start recording. Allow the microphone, then try again."
}

export async function requestMicrophoneStream(
  getUserMedia: GetUserMediaFn,
): Promise<MediaStream> {
  if (typeof getUserMedia !== "function") {
    throw new Error("This browser cannot access the microphone.")
  }
  let last: unknown
  for (const constraints of MIC_CONSTRAINT_ATTEMPTS) {
    try {
      return await getUserMedia(constraints)
    } catch (e) {
      last = e
      if (isInvalidMediaConstraint(e)) continue
      throw e
    }
  }
  throw last
}

const RECORDER_MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/mp4;codecs=mp4a.40.2",
]

export function pickRecorderMimeType(
  isTypeSupported: (mimeType: string) => boolean,
): string {
  for (const t of RECORDER_MIME_CANDIDATES) {
    try {
      if (isTypeSupported(t)) return t
    } catch {
      /* ignore */
    }
  }
  return ""
}

export function createCaptureAudioContext(): AudioContext {
  try {
    return new AudioContext({ sampleRate: 16000 })
  } catch {
    return new AudioContext()
  }
}

/** Encode 16-bit little-endian PCM chunks as a mono WAV (file-ASR friendly). */
export function pcmInt16ToWav(
  chunks: ArrayLike<number>[],
  sampleRate: number = 16000,
): Blob {
  let total = 0
  for (const c of chunks) total += c.length
  const bytes = total * 2
  const buffer = new ArrayBuffer(44 + bytes)
  const view = new DataView(buffer)
  const writeStr = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i))
  }
  writeStr(0, "RIFF")
  view.setUint32(4, 36 + bytes, true)
  writeStr(8, "WAVE")
  writeStr(12, "fmt ")
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeStr(36, "data")
  view.setUint32(40, bytes, true)
  let o = 44
  for (const c of chunks) {
    for (let i = 0; i < c.length; i++) {
      view.setInt16(o, c[i] ?? 0, true)
      o += 2
    }
  }
  return new Blob([buffer], { type: "audio/wav" })
}
