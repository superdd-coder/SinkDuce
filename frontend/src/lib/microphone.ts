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
