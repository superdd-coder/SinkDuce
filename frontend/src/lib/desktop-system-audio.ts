import { getHealth } from "@/api/client"

export const SCREEN_RECORDING_HELP =
  "Could not capture system audio. Quit SinkDuce with Cmd+Q, reopen, and try again. If macOS asks to record audio, click Allow."

export const MICROPHONE_HELP =
  "Allow SinkDuce in System Settings → Privacy & Security → Microphone, then quit with Cmd+Q and reopen."

export interface DesktopSystemAudio {
  stream: MediaStream
  stop: () => void
}

export interface DesktopPcmHandlers {
  /** Raw 16 kHz s16le from the native helper — do not round-trip through Web Audio. */
  onPcm?: (pcm: ArrayBuffer) => void
  /** 0–1 RMS for the live waveform. */
  onLevel?: (level: number) => void
}

function isWkLoadFailed(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err)
  return /load failed|failed to fetch/i.test(msg)
}

/** Play 16 kHz PCM into a MediaStream without blob AudioWorklets (WKWebView). */
function attachScriptPcmPlayer(ctx: AudioContext) {
  const dest = ctx.createMediaStreamDestination()
  const queue: Float32Array[] = []
  let offset = 0
  const processor = ctx.createScriptProcessor(1024, 1, 1)
  processor.onaudioprocess = (e) => {
    const out = e.outputBuffer.getChannelData(0)
    let i = 0
    while (i < out.length) {
      if (queue.length === 0) {
        out.fill(0, i)
        break
      }
      const cur = queue[0]
      if (!cur) break
      const remain = cur.length - offset
      const take = Math.min(remain, out.length - i)
      out.set(cur.subarray(offset, offset + take), i)
      i += take
      offset += take
      if (offset >= cur.length) {
        queue.shift()
        offset = 0
      }
    }
  }
  processor.connect(dest)
  // Keep the graph pulling even if the destination stream has no listeners.
  const mute = ctx.createGain()
  mute.gain.value = 0
  processor.connect(mute)
  mute.connect(ctx.destination)
  return {
    stream: dest.stream,
    push(samples: Float32Array) {
      queue.push(samples)
    },
    disconnect() {
      try {
        processor.disconnect()
      } catch {
        /* already gone */
      }
    },
  }
}

/** Prime Core Audio so the first Start Recording click is not a multi-second stall. */
export async function warmupDesktopSystemAudio(): Promise<void> {
  try {
    const health = await getHealth()
    if (health.desktop !== true) return
    const base = (health.system_audio || "").replace(/\/$/, "")
    if (!base) return
    await fetch("/api/desktop/sysaudio-warmup", { method: "POST", cache: "no-store" })
  } catch {
    /* helper not up yet */
  }
}

/** Capture macOS system audio from the desktop helper advertised on /health. */
export async function startDesktopSystemAudio(
  handlers?: DesktopPcmHandlers,
): Promise<DesktopSystemAudio> {
  const health = await getHealth()
  const base = (health.system_audio || "").replace(/\/$/, "")
  if (!base) {
    throw new Error(
      "System audio helper is not running. Quit SinkDuce completely and reopen the app.",
    )
  }

  const ac = new AbortController()
  let res: Response
  try {
    // Same-origin proxy. WKWebView fetch to :18950 throws TypeError: Load failed.
    res = await fetch("/api/desktop/pcm", { signal: ac.signal, cache: "no-store" })
  } catch (err) {
    if (isWkLoadFailed(err)) throw new Error(SCREEN_RECORDING_HELP)
    throw err
  }
  if (!res.ok) {
    let msg = SCREEN_RECORDING_HELP
    try {
      const body = (await res.json()) as { error?: string; message?: string }
      if (body.error === "microphone_permission") {
        msg = (body.message || "").trim() || MICROPHONE_HELP
      } else if ((body.message || "").trim()) {
        msg = body.message as string
      }
    } catch {
      /* keep default */
    }
    throw new Error(msg)
  }
  if (!res.body) {
    throw new Error(SCREEN_RECORDING_HELP)
  }

  const ctx = new AudioContext({ sampleRate: 16000 })
  await ctx.resume()
  const player = attachScriptPcmPlayer(ctx)

  const reader = res.body.getReader()
  let leftover = new Uint8Array(0)
  const pump = async () => {
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        if (!value?.length) continue
        const merged = new Uint8Array(leftover.length + value.length)
        merged.set(leftover)
        merged.set(value, leftover.length)
        const even = merged.length & ~1
        if (even >= 2) {
          const pcmCopy = new ArrayBuffer(even)
          new Uint8Array(pcmCopy).set(merged.subarray(0, even))
          handlers?.onPcm?.(pcmCopy)
          const view = new DataView(pcmCopy)
          const samples = new Float32Array(even / 2)
          let energy = 0
          for (let i = 0; i < samples.length; i++) {
            const s = view.getInt16(i * 2, true) / 32768
            samples[i] = s
            energy += s * s
          }
          const rms = Math.sqrt(energy / samples.length)
          handlers?.onLevel?.(Math.min(1, rms * 4))
          player.push(samples)
        }
        leftover = even < merged.length ? merged.slice(even) : new Uint8Array(0)
      }
    } catch {
      // aborted or helper closed
    }
  }
  void pump()

  let stopped = false
  const stop = () => {
    if (stopped) return
    stopped = true
    ac.abort()
    void reader.cancel().catch(() => undefined)
    void fetch("/api/desktop/sysaudio-stop", { method: "POST", cache: "no-store" }).catch(() => undefined)
    player.disconnect()
    void ctx.close()
  }

  return { stream: player.stream, stop }
}
