import { getHealth } from "@/api/client"

const PLAYER_CODE = `
class SystemPcmPlayer extends AudioWorkletProcessor {
  constructor() {
    super()
    this._queue = []
    this._offset = 0
    this.port.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) {
        this._queue.push(new Float32Array(e.data))
      }
    }
  }
  process(_inputs, outputs) {
    const out = outputs[0] && outputs[0][0]
    if (!out) return true
    let i = 0
    while (i < out.length) {
      if (this._queue.length === 0) {
        out.fill(0, i)
        break
      }
      const cur = this._queue[0]
      const remain = cur.length - this._offset
      const take = Math.min(remain, out.length - i)
      out.set(cur.subarray(this._offset, this._offset + take), i)
      i += take
      this._offset += take
      if (this._offset >= cur.length) {
        this._queue.shift()
        this._offset = 0
      }
    }
    return true
  }
}
registerProcessor('system-pcm-player', SystemPcmPlayer)
`

export const SCREEN_RECORDING_HELP =
  "Screen Recording permission is required to capture system audio. Open System Settings → Privacy & Security → Screen Recording, enable SinkDuce, then try again."

export interface DesktopSystemAudio {
  stream: MediaStream
  stop: () => void
}

/** Capture macOS system audio from the desktop helper advertised on /health. */
export async function startDesktopSystemAudio(): Promise<DesktopSystemAudio> {
  const health = await getHealth()
  const base = (health.system_audio || "").replace(/\/$/, "")
  if (!base) {
    throw new Error(
      "System audio helper is not running. Quit SinkDuce completely and reopen the app.",
    )
  }

  const ac = new AbortController()
  const res = await fetch(`${base}/pcm`, { signal: ac.signal, cache: "no-store" })
  if (!res.ok) {
    throw new Error(SCREEN_RECORDING_HELP)
  }
  if (!res.body) {
    throw new Error(SCREEN_RECORDING_HELP)
  }

  const ctx = new AudioContext({ sampleRate: 16000 })
  await ctx.resume()
  const dest = ctx.createMediaStreamDestination()
  const blob = new Blob([PLAYER_CODE], { type: "application/javascript" })
  const url = URL.createObjectURL(blob)
  await ctx.audioWorklet.addModule(url)
  URL.revokeObjectURL(url)
  const node = new AudioWorkletNode(ctx, "system-pcm-player")
  node.connect(dest)

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
          const view = new DataView(merged.buffer, merged.byteOffset, even)
          const samples = new Float32Array(even / 2)
          for (let i = 0; i < samples.length; i++) {
            samples[i] = view.getInt16(i * 2, true) / 32768
          }
          node.port.postMessage(samples.buffer, [samples.buffer])
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
    void fetch(`${base}/stop`, { method: "POST", cache: "no-store" }).catch(() => undefined)
    try {
      node.disconnect()
    } catch {
      /* already gone */
    }
    void ctx.close()
  }

  return { stream: dest.stream, stop }
}
