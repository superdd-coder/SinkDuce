/**
 * App-wide event stream transport (server→client change notification).
 *
 * Protocol: development_doc/2026-09-16-multi-device-state-sync-design.md (§6, §7).
 * The backend emits `hello` / `change` / `resync` frames; this module builds the
 * URL, parses frames, tracks the cursor, and reconnects with backoff.
 *
 * Transport: fetch-stream（不再是 EventSource）——fetch 能带请求头，配对钥匙
 * 从 query string 收进 `X-Pairing-Token`（token 收口，2026-09-19）；client id
 * 同理进 `X-Client-Id`。重连/backoff 本来就是这个模块自管的（EventSource 的
 * 自动重试不用），fetch 版只需自己解析 SSE 帧与断流。
 */

import { getClientId } from "./client-id.ts"
import { apiUrl, currentPairingToken } from "./origin.ts"

export interface ChangeEvent {
  seq: number
  ts: number
  type: string
  domain: string
  scope: Record<string, string | null | undefined>
  origin: { client_id?: string | null; key_id?: string | null }
  payload: Record<string, unknown>
}

export type Frame =
  | { kind: "hello"; seq: number; oldest: number | null }
  | { kind: "change"; event: ChangeEvent }
  | { kind: "resync"; reason: "gap" | "overflow" }
  | { kind: "keepalive" }

/** Build the SSE URL. `since` is omitted on a fresh connection (mount is the snapshot).
 * 钥匙/身份走请求头（见 openEventStream），URL 里只剩游标。 */
export function buildStreamUrl(opts: { since?: number; clientId?: string }): string {
  const params: string[] = []
  if (opts.since !== undefined) params.push(`since=${opts.since}`)
  const qs = params.length ? `?${params.join("&")}` : ""
  return apiUrl(`/api/events/stream${qs}`)
}

/**
 * Parse one SSE frame. Returns null for anything unrecognized or malformed:
 * a bad frame must never disturb the stream.
 */
export function parseFrame(name: string, data: string | null): Frame | null {
  if (name === ":") return { kind: "keepalive" }
  if (data === null) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(data)
  } catch {
    return null
  }
  if (typeof parsed !== "object" || parsed === null) return null
  const obj = parsed as Record<string, unknown>

  if (name === "hello") {
    if (typeof obj.seq !== "number") return null
    const oldest = typeof obj.oldest === "number" ? obj.oldest : null
    return { kind: "hello", seq: obj.seq, oldest }
  }
  if (name === "resync") {
    const reason = obj.reason === "overflow" ? "overflow" : "gap"
    return { kind: "resync", reason }
  }
  if (name === "change") {
    if (typeof obj.seq !== "number" || typeof obj.type !== "string") return null
    const event: ChangeEvent = {
      seq: obj.seq,
      ts: typeof obj.ts === "number" ? obj.ts : 0,
      type: obj.type,
      domain: typeof obj.domain === "string" ? obj.domain : "unknown",
      scope: (obj.scope as ChangeEvent["scope"]) ?? {},
      origin: (obj.origin as ChangeEvent["origin"]) ?? {},
      payload: (obj.payload as Record<string, unknown>) ?? {},
    }
    return { kind: "change", event }
  }
  return null
}

export interface StreamHandlers {
  onFrame: (frame: Frame) => void
}

/**
 * 解析 fetch 流里的 SSE 帧（事件名 + 单行 JSON data）。
 * 服务端帧形状：``event: <name>\\ndata: <json>\\n\\n`` 与 ``: keepalive\\n\\n``。
 * 返回 null = 忽略（坏帧永不干扰流）。
 */
export function parseRawFrame(block: string): { name: string; data: string | null } | null {
  let name = "message"
  const dataLines: string[] = []
  for (const line of block.split("\n")) {
    const trimmed = line.replace(/\r$/, "")
    if (trimmed.startsWith(":")) return { name: ":", data: null }
    if (trimmed.startsWith("event:")) name = trimmed.slice(6).trim()
    else if (trimmed.startsWith("data:")) dataLines.push(trimmed.slice(5).trimStart())
  }
  return { name, data: dataLines.length ? dataLines.join("\n") : null }
}

/**
 * Open the stream and keep it open. Returns a disposer.
 *
 * The cursor is tracked internally: reconnects resume with `?since=` so a brief
 * network drop replays what was missed instead of silently losing it. A `resync`
 * frame means replay was impossible (ring eviction or a server restart) and the
 * caller must reconcile fully.
 */
export function openEventStream(handlers: StreamHandlers): () => void {
  let disposed = false
  let since: number | undefined
  let retry = 0
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let controller: AbortController | null = null

  const scheduleReconnect = () => {
    if (disposed) return
    retry += 1
    const delay = Math.min(1000 * 2 ** (retry - 1), 30_000)
    retryTimer = setTimeout(connect, delay)
  }

  const dispatch = (name: string, data: string | null) => {
    const frame = parseFrame(name, data)
    if (!frame) return
    if (frame.kind === "hello") {
      // A fresh connection takes the server head as its baseline; a reconnect
      // keeps our own (further along) cursor.
      if (since === undefined) since = frame.seq
    } else if (frame.kind === "change") {
      if (since === undefined || frame.event.seq > since) since = frame.event.seq
      retry = 0
    } else if (frame.kind === "resync") {
      // Our cursor is unusable; drop it so the next connect is a fresh snapshot.
      since = undefined
    }
    handlers.onFrame(frame)
  }

  async function readStream(response: Response) {
    const reader = response.body?.getReader()
    if (!reader) throw new Error("no stream body")
    const decoder = new TextDecoder()
    let buffer = ""
    const dispatchBlock = (block: string) => {
      const raw = parseRawFrame(block)
      if (raw) dispatch(raw.name, raw.data)
    }
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        if (block.trim()) dispatchBlock(block)
      }
    }
    if (buffer.trim()) dispatchBlock(buffer)
  }

  async function connect() {
    if (disposed) return
    controller = new AbortController()
    const headers: Record<string, string> = {}
    const token = currentPairingToken()
    if (token) headers["X-Pairing-Token"] = token
    const clientId = getClientId()
    if (clientId) headers["X-Client-Id"] = clientId
    try {
      const response = await fetch(buildStreamUrl({ since }), {
        headers,
        signal: controller.signal,
      })
      if (!response.ok || !response.body) throw new Error(`stream ${response.status}`)
      retry = 0
      await readStream(response)
      // 服务端断流（重启/超时）→ 走既有 backoff 重连，游标还在自己手里
    } catch {
      if (disposed) return
    } finally {
      controller = null
    }
    scheduleReconnect()
  }

  connect()
  return () => {
    disposed = true
    if (retryTimer) clearTimeout(retryTimer)
    controller?.abort()
    controller = null
  }
}
