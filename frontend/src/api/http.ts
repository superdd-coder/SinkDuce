/** Shared fetch helper for `/api` and `/api/file-mgmt`. */

import { classifyConnectionFailure } from "./connection-health.ts"
import { getClientId } from "./client-id.ts"
import { apiOrigin, activeConnection, currentPairingToken, getStoredOwnerKey, handleRemoteConnectionLost } from "./origin.ts"

/** 事件流回声抑制用的稳定客户端 id；后端把它填进事件 origin.client_id。 */
function clientIdHeader(): Record<string, string> {
  const id = getClientId()
  return id ? { "X-Client-Id": id } : {}
}

/** 当前来源是否是远端盒子（本机=同源，不触发连接回退）。 */
function isRemoteSource(): boolean {
  return activeConnection() !== null
}

/** 来源感知的 API base：本机=同源相对路径；连接远端盒子=其地址（HUB-7）。 */
export const API_BASE = (() => {
  const origin = apiOrigin()
  return origin ? `${origin}/api` : "/api"
})()
export const FILE_MGMT_BASE = (() => {
  const origin = apiOrigin()
  return origin ? `${origin}/api/file-mgmt` : "/api/file-mgmt"
})()

export type RequestFlavor = "client" | "file-mgmt"

/** 当前来源该用的配对钥匙（统一出口见 origin.currentPairingToken）。 */
function pairingTokenFor(local: boolean): string | null {
  return local ? getStoredOwnerKey() : currentPairingToken()
}

function baseFor(flavor: RequestFlavor, local: boolean): string {
  if (local) return flavor === "file-mgmt" ? "/api/file-mgmt" : "/api"
  return flavor === "file-mgmt" ? FILE_MGMT_BASE : API_BASE
}

/**
 * 裸请求出口 —— 给 `request()` 覆盖不了的载荷用：`FormData`（文件上传）、
 * 二进制（PCM）、需要自己读流的响应（SSE）。
 *
 * 它只负责「怎么发」：来源感知的地址 + 自动附配对钥匙 + 连接失效回退。
 * 响应状态与 body 由调用方自己解读（所以返回原始 Response），错误处理保持各自原样。
 *
 * 为什么必须有这层：以前这些地方各自写裸 fetch，只继承了「地址」忘了「钥匙」，
 * 于是连远端盒子时会话列表 / 文件上传 / 录音落盘全部静默 401 失败。
 */
export async function apiFetch(
  path: string,
  init?: RequestInit,
  opts?: { flavor?: RequestFlavor; local?: boolean },
): Promise<Response> {
  const flavor = opts?.flavor ?? "client"
  const local = opts?.local === true
  const headers = new Headers(init?.headers as HeadersInit | undefined)
  const token = pairingTokenFor(local)
  if (token) headers.set("X-Pairing-Token", token)
  for (const [k, v] of Object.entries(clientIdHeader())) headers.set(k, v)

  const remote = !local && isRemoteSource()
  let res: Response
  try {
    res = await fetch(`${baseFor(flavor, local)}${path}`, { ...init, headers })
  } catch (err) {
    // 连不上远端盒子（关机 / 换网 / 地址失效）→ 退回本机（回退自己会整页刷新）
    if (!init?.signal?.aborted) {
      const lost = classifyConnectionFailure({ activeRemote: remote, networkError: true })
      if (lost) handleRemoteConnectionLost(lost)
    }
    throw err
  }
  if (res.status === 401) {
    // 远端 401 = 钥匙被吊销 → 回退并清理；本机 401 = 门卫要钥匙 → 弹钥匙门。
    // 两种情况都照常把响应交回调用方（回退已发起整页刷新，页面即将卸载）。
    const lost = classifyConnectionFailure({ activeRemote: remote, status: 401 })
    if (lost) handleRemoteConnectionLost(lost)
    else if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("sinkduce:pairing-required"))
    }
  }
  return res
}

export type RequestOptions = RequestInit & {
    /** 强制走本机同源（配对管理等本机专属页面用） */
    local?: boolean
  base?: string
  /** `client` keeps the original Error + JSON Content-Type. */
  flavor?: RequestFlavor
  /**
   * **后台/尽力而为**请求：失败时不要产生任何**界面副作用**。
   *
   * 关掉两件事：① 401 pairing_required 的「粘贴钥匙」弹窗；② 远端连接失效的回退+刷新。
   *
   * 为什么需要它：有些请求是**用户没发起**的后台同步（例如把「已连接盒子」镜像给
   * 后端供 Hub 用）。它们拿 401/断线是**正常的**（后端老版本、页面由远端盒子伺服、
   * 网络抖动），但如果照常触发弹窗，用户会看到一个自己没做任何操作却冒出来的
   * 「需要配对钥匙」——比不弹更糟：他无从判断该不该填，填了也没用。
   * 调用方自己决定失败怎么办（通常：忽略）。
   */
  silent?: boolean
}

/** Structured 409 body when a same-folder/sibling name already exists. */
export type NameConflictDetail = {
  code: "name_conflict"
  resource: "folder" | "file"
  name: string
  suggested_name: string
  message: string
}

export class ApiError extends Error {
  status: number
  code: string | null
  params: Record<string, string | number>
  detail: unknown
  rawBody: string

  constructor(
    status: number,
    rawBody: string,
    opts?: {
      code?: string | null
      params?: Record<string, string | number>
      message?: string
      detail?: unknown
    },
  ) {
    super(opts?.message ?? `API ${status}: ${rawBody}`)
    this.name = "ApiError"
    this.status = status
    this.rawBody = rawBody
    this.code = opts?.code ?? null
    this.params = opts?.params ?? {}
    this.detail = opts?.detail ?? rawBody
  }
}

function unwrapDetail(json: unknown): unknown {
  let d = json
  if (
    d &&
    typeof d === "object" &&
    "detail" in (d as Record<string, unknown>)
  ) {
    d = (d as { detail: unknown }).detail
  }
  return d
}

export function parseApiErrorBody(status: number, rawBody: string): ApiError {
  const json = parseJsonMaybe(rawBody)
  const detail = unwrapDetail(json)
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const rec = detail as Record<string, unknown>
    const code = typeof rec.code === "string" ? rec.code : null
    const params =
      rec.params && typeof rec.params === "object" && !Array.isArray(rec.params)
        ? (rec.params as Record<string, string | number>)
        : {}
    const message =
      typeof rec.message === "string"
        ? rec.message
        : `API ${status}: ${rawBody}`
    return new ApiError(status, rawBody, { code, params, message, detail })
  }
  if (typeof detail === "string") {
    return new ApiError(status, rawBody, {
      message: detail,
      detail,
    })
  }
  return new ApiError(status, rawBody, { detail: detail ?? rawBody })
}

export function formatApiError(
  err: unknown,
  translate: (key: string, vars?: Record<string, string | number>) => string,
): string {
  if (err instanceof ApiError && err.code) {
    const key = `errors.${err.code}`
    const msg = translate(key, err.params)
    if (msg !== key) return msg
  }
  if (err instanceof Error && err.message) return err.message
  return String(err)
}

export class FileMgmtApiError extends ApiError {
  constructor(status: number, rawBody: string) {
    const parsed = parseApiErrorBody(status, rawBody)
    super(status, rawBody, {
      code: parsed.code,
      params: parsed.params,
      message: parsed.message,
      detail: parsed.detail,
    })
    this.name = "FileMgmtApiError"
  }
}

function asNameConflict(detail: unknown): NameConflictDetail | null {
  let d = detail
  // FastAPI wraps as { detail: ... }; unwrap a couple of times.
  for (let i = 0; i < 2; i++) {
    if (
      d &&
      typeof d === "object" &&
      "detail" in (d as Record<string, unknown>) &&
      (d as NameConflictDetail).code !== "name_conflict"
    ) {
      d = (d as { detail: unknown }).detail
      continue
    }
    break
  }
  if (
    d &&
    typeof d === "object" &&
    (d as NameConflictDetail).code === "name_conflict" &&
    typeof (d as NameConflictDetail).suggested_name === "string"
  ) {
    return d as NameConflictDetail
  }
  return null
}

function parseJsonMaybe(raw: string): unknown {
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

export function getNameConflict(err: unknown): NameConflictDetail | null {
  if (!err || typeof err !== "object") return null
  const e = err as {
    status?: number
    detail?: unknown
    rawBody?: string
    message?: string
    name?: string
  }

  const fromDetail = asNameConflict(e.detail)
  if (fromDetail && (e.status === 409 || e.name === "FileMgmtApiError")) {
    return fromDetail
  }
  if (fromDetail && e.status == null) return fromDetail

  if (typeof e.rawBody === "string" && e.rawBody) {
    const fromRaw = asNameConflict(parseJsonMaybe(e.rawBody))
    if (fromRaw) return fromRaw
  }

  // Historical client flavor: `API 409: {"detail":{...}}`
  const msg = e.message || ""
  const m = msg.match(/^API\s+409:\s*([\s\S]+)$/)
  if (m) {
    const fromMsg = asNameConflict(parseJsonMaybe(m[1]))
    if (fromMsg) return fromMsg
  }
  return null
}

/**
 * In-flight coalescing for identical concurrent GETs. Opening a meeting used
 * to fire the same /api/meetings/{id} five times from independent components
 * (tabs effects, quick chat, editor) — N networks, N parses, N re-render
 * cascades. Joiners receive their own structuredClone so shared-object
 * mutation can't leak between callers.
 */
const inFlightGets = new Map<string, Promise<unknown>>()

export async function request<T>(
  path: string,
  options?: RequestOptions,
): Promise<T> {
  const flavor = options?.flavor ?? "client"
  const local = options?.local === true
  const base = local ? baseFor(flavor, true) : (options?.base ?? baseFor(flavor, false))
  const { flavor: _flavor, base: _base, local: _local, silent: _silent, ...init } = options ?? {}
  const silent = _silent === true
  // 远端来源时自动附上该盒子的配对钥匙（本机请求无需凭证）；
  // 无论有无钥匙都附 X-Client-Id（事件流回声抑制）。
  const pairingToken = pairingTokenFor(local)
  const extraHeaders: Record<string, string> = { ...clientIdHeader() }
  if (pairingToken) extraHeaders["X-Pairing-Token"] = pairingToken
  let finalInit = init
  if (Object.keys(extraHeaders).length) {
    const headers = new Headers(init.headers as HeadersInit | undefined)
    for (const [k, v] of Object.entries(extraHeaders)) headers.set(k, v)
    finalInit = { ...init, headers }
  }
  const method = (init.method ?? "GET").toUpperCase()
  if (method === "GET") {
    const url = `${base}${path}`
    const key = `${url}|${init.body ?? ""}`
    const pending = inFlightGets.get(key)
    if (pending) {
      return pending.then((v) => structuredClone(v)) as Promise<T>
    }
    const p = (
      flavor === "file-mgmt"
        ? requestFileMgmt<T>(base, path, finalInit)
        : requestClient<T>(url, finalInit, silent)
    ).finally(() => {
      if (inFlightGets.get(key) === p) inFlightGets.delete(key)
    })
    inFlightGets.set(key, p)
    return p
  }
  if (flavor === "file-mgmt") {
    return requestFileMgmt<T>(base, path, finalInit)
  }
  return requestClient<T>(`${base}${path}`, finalInit, silent)
}

/** Match historical `client.ts` request() — including header overwrite order. */
async function requestClient<T>(
  url: string,
  options?: RequestInit,
  silent = false,
): Promise<T> {
  // 默认 JSON 头，调用方显式指定的优先。必须用 Headers 合并后再整体传入：
  // 曾经写成 `{ headers: {...default, ...options?.headers}, ...options }`，当带钥匙时
  // options.headers 是只含 token 的 Headers 实例（展开无枚举属性），随后被 `...options`
  // 整体覆盖 → Content-Type 丢失，FastAPI 不解析 JSON 体 → 422
  // "Input should be a valid dictionary or object to extract fields from"。
  // FormData 不能设 Content-Type（要让浏览器自己带 boundary）。
  const headers = new Headers(options?.headers as HeadersInit | undefined)
  if (!headers.has("Content-Type") && !(options?.body instanceof FormData)) {
    headers.set("Content-Type", "application/json")
  }
  let res: Response
  try {
    res = await fetch(url, { ...options, headers })
  } catch (err) {
    // 连不上远端盒子（关机/换网/地址失效）→ 退回本机，别把界面晾空
    const lost =
      silent || options?.signal?.aborted
        ? null
        : classifyConnectionFailure({ activeRemote: isRemoteSource(), networkError: true })
    if (lost && handleRemoteConnectionLost(lost)) {
      // reload 已在处理函数里发起，这里挂住 Promise 等页面卸载
      return new Promise<T>(() => {})
    }
    throw err
  }
  if (!res.ok) {
    const body = await res.text()
    const err = parseApiErrorBody(res.status, body)
    const lost = silent
      ? null
      : classifyConnectionFailure({ activeRemote: isRemoteSource(), status: err.status })
    if (lost && handleRemoteConnectionLost(lost)) {
      return new Promise<T>(() => {})
    }
    if (
      !silent &&
      err.status === 401 &&
      err.code === "pairing_required" &&
      typeof window !== "undefined"
    ) {
      window.dispatchEvent(new CustomEvent("sinkduce:pairing-required"))
    }
    throw err
  }
  // 成功但无响应体：204 No Content（如 DELETE /sessions/{id}），或服务端只回状态。
  // 必须在这里拦住——对空字符串调 JSON.parse 会抛 "The string did not match the
  // expected pattern."（WebKit / V8 措辞不同），表现为「服务端已成功、界面却报失败」。
  if (res.status === 204 || res.status === 205) return undefined as unknown as T
  const text = await res.text()
  if (!text) return undefined as unknown as T
  // 去掉 BOM：res.json() 原本会跳过它，JSON.parse 不会
  return JSON.parse(text.charCodeAt(0) === 0xfeff ? text.slice(1) : text) as T
}

/** Match historical `file-mgmt.ts` req() — 204, HTML guard, structured errors. */
async function requestFileMgmt<T>(
  base: string,
  path: string,
  options?: RequestInit,
): Promise<T> {
  const method = (options?.method ?? "GET").toUpperCase()
  const headers = new Headers(options?.headers)
  if (
    method !== "GET" &&
    method !== "HEAD" &&
    method !== "DELETE" &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json")
  }
  let res: Response
  try {
    res = await fetch(`${base}${path}`, {
      ...options,
      method,
      headers,
      cache: "no-store",
    })
  } catch (err) {
    const lost = options?.signal?.aborted
      ? null
      : classifyConnectionFailure({ activeRemote: isRemoteSource(), networkError: true })
    if (lost && handleRemoteConnectionLost(lost)) {
      return new Promise<T>(() => {})
    }
    throw err
  }
  if (!res.ok) {
    const body = await res.text()
    // 远端盒子的 401 = 钥匙被吊销 → 退回本机而不是抛给各视图（它们会吞掉错误）
    const lost = classifyConnectionFailure({ activeRemote: isRemoteSource(), status: res.status })
    if (lost && handleRemoteConnectionLost(lost)) {
      return new Promise<T>(() => {})
    }
    throw new FileMgmtApiError(res.status, body)
  }
  if (res.status === 204) return undefined as unknown as T
  const ct = res.headers.get("content-type") || ""
  const text = await res.text()
  if (
    text.trimStart().startsWith("<!DOCTYPE") ||
    text.trimStart().startsWith("<!doctype") ||
    text.trimStart().startsWith("<html")
  ) {
    throw new FileMgmtApiError(
      res.status,
      `Expected JSON from ${base}${path} but got HTML (content-type: ${ct || "missing"}). ` +
        `Hard-refresh the page (Cmd+Shift+R). If using Cloudflare, purge cache for /api/*. ` +
        `Verify: curl -sS http://127.0.0.1:18900${base}${path}`,
    )
  }
  if (!text) return undefined as unknown as T
  try {
    return JSON.parse(text) as T
  } catch {
    throw new FileMgmtApiError(
      res.status,
      ct.includes("json")
        ? `Invalid JSON from ${path}`
        : `Non-JSON response from ${path}`,
    )
  }
}
