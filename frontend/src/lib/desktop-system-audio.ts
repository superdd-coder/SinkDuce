import { getLocalHealth, fetchDesktopAudioPcm, stopDesktopAudioCapture, warmupDesktopAudioCapture } from "@/api/client"
import { DesktopAudioError, waitForDesktopPcm } from "./desktop-audio-errors"

let pendingStop: Promise<unknown> = Promise.resolve()

export const SCREEN_RECORDING_HELP =
  "Could not capture system audio. Quit SinkDuce with Cmd+Q, reopen, and try again. If macOS asks to record audio, click Allow."

export const MICROPHONE_HELP =
  "Allow SinkDuce in System Settings → Privacy & Security → Microphone, then quit with Cmd+Q and reopen."

/** 助手没在监听（崩溃 / 还没起来 / 被别的副本占了端口）。 */
export const HELPER_DOWN_HELP =
  "System audio helper is not responding. Wait a few seconds and try again; if it persists, quit SinkDuce with Cmd+Q and reopen."

/** 音频服务返回了非预期状态（多半是屏幕录制权限）。 */
export function audioServiceHelp(status: number): string {
  if (status === 403) return MICROPHONE_HELP
  if (status === 503) return HELPER_DOWN_HELP
  return SCREEN_RECORDING_HELP
}

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
    const health = await getLocalHealth()
    if (health.desktop !== true) return
    const base = (health.system_audio || "").replace(/\/$/, "")
    if (!base) return
    await warmupDesktopAudioCapture()
  } catch {
    /* helper not up yet */
  }
}

/** Capture macOS system audio from the desktop helper advertised on /health. */
export async function startDesktopSystemAudio(
  handlers?: DesktopPcmHandlers,
  signal?: AbortSignal,
): Promise<DesktopSystemAudio> {
  // A new capture waits for the preceding stop request to reach the helper.
  await pendingStop
  signal?.throwIfAborted()
  const ac = new AbortController()
  const abort = () => ac.abort(signal?.reason)
  signal?.addEventListener("abort", abort, { once: true })
  let res: Response
  try {
    res = await waitForDesktopPcm(() => fetchDesktopAudioPcm(ac.signal), ac.signal)
    if (!res.body) throw new DesktopAudioError("helper_unavailable", HELPER_DOWN_HELP)
  } catch (error) {
    signal?.removeEventListener("abort", abort)
    ac.abort()
    throw error
  }

  let ctx: AudioContext | undefined
  let player: ReturnType<typeof attachScriptPcmPlayer>
  try {
    ctx = new AudioContext({ sampleRate: 16000 })
    await ctx.resume()
    ac.signal.throwIfAborted()
    player = attachScriptPcmPlayer(ctx)
  } catch (error) {
    ac.abort()
    signal?.removeEventListener("abort", abort)
    void res.body?.cancel().catch(() => undefined)
    pendingStop = stopDesktopAudioCapture().catch(() => undefined)
    // The constructor itself may have failed before there was a context.
    void ctx?.close()
    throw error
  }
  const reader = res.body!.getReader()
  let leftover = new Uint8Array(0)
  const pump = async () => {
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done || ac.signal.aborted) break
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
    signal?.removeEventListener("abort", abort)
    ac.abort()
    void reader.cancel().catch(() => undefined)
    pendingStop = stopDesktopAudioCapture().catch(() => undefined)
    player.disconnect()
    void ctx.close()
  }

  return { stream: player.stream, stop }
}
