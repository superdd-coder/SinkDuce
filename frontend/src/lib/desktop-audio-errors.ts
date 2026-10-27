export class DesktopAudioError extends Error {
  code: string
  constructor(code: string, message: string) {
    super(message)
    this.name = "DesktopAudioError"
    this.code = code
  }
}

export function canRestartDesktopAudio(active: boolean, status: { capturing: boolean } | null): boolean {
  return !active && status?.capturing === false
}

export function parseDesktopAudioError(status: number, body: unknown): DesktopAudioError {
  let value = body
  for (let i = 0; i < 4; i++) {
    if (typeof value === "string") {
      try { value = JSON.parse(value); continue } catch { break }
    }
    if (value && typeof value === "object" && "detail" in value) {
      value = (value as { detail: unknown }).detail
      continue
    }
    break
  }
  const obj = value && typeof value === "object" ? value as Record<string, unknown> : {}
  const code = typeof obj.code === "string" ? obj.code
    : typeof obj.error === "string" ? obj.error
    : status === 403 ? "microphone_permission" : "helper_unavailable"
  const message = typeof obj.message === "string" ? obj.message
    : typeof value === "string" && value.trim() ? value
    : code === "microphone_permission"
      ? "Allow the microphone in System Settings, then check again."
      : "Audio capture is unavailable. Check permissions or restart the app."
  return new DesktopAudioError(code, message)
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted()
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(signal.reason) }
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve() }, ms)
    signal.addEventListener("abort", abort, { once: true })
  })
}

/** Pending OS permission has its own deadline; denial never enters a retry loop. */
export async function waitForDesktopPcm(
  fetchPcm: () => Promise<Response>,
  signal: AbortSignal,
  options: { retryDelayMs?: number; pendingTimeoutMs?: number } = {},
): Promise<Response> {
  const started = Date.now()
  let readinessFailures = 0
  while (true) {
    signal.throwIfAborted()
    let response: Response | undefined
    try { response = await fetchPcm() } catch {
      signal.throwIfAborted()
    }
    if (response?.ok) return response
    if (response && response.status !== 503) {
      let body: unknown
      try { body = await response.json() } catch { /* use status fallback */ }
      const error = parseDesktopAudioError(response.status, body)
      if (error.code === "capture_busy") {
        if (++readinessFailures >= 6) throw error
      } else {
        if (error.code !== "permission_pending") throw error
        if (Date.now() - started >= (options.pendingTimeoutMs ?? 120_000)) throw error
      }
    } else {
      await response?.body?.cancel().catch(() => undefined)
      if (++readinessFailures >= 6) {
        throw new DesktopAudioError("helper_unavailable", "Audio helper is not responding. Wait a moment and try again, or restart the app.")
      }
    }
    await delay(options.retryDelayMs ?? 700, signal)
  }
}
