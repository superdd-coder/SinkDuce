/** Shared fetch helper for `/api` and `/api/file-mgmt`. */

export const API_BASE = "/api"
export const FILE_MGMT_BASE = "/api/file-mgmt"

export type RequestFlavor = "client" | "file-mgmt"

export type RequestOptions = RequestInit & {
  base?: string
  /** `client` keeps the original Error + JSON Content-Type. */
  flavor?: RequestFlavor
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

export async function request<T>(
  path: string,
  options?: RequestOptions,
): Promise<T> {
  const flavor = options?.flavor ?? "client"
  const base =
    options?.base ?? (flavor === "file-mgmt" ? FILE_MGMT_BASE : API_BASE)
  const { flavor: _flavor, base: _base, ...init } = options ?? {}
  if (flavor === "file-mgmt") {
    return requestFileMgmt<T>(base, path, init)
  }
  return requestClient<T>(`${base}${path}`, init)
}

/** Match historical `client.ts` request() — including header overwrite order. */
async function requestClient<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const body = await res.text()
    throw parseApiErrorBody(res.status, body)
  }
  return res.json()
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
  const res = await fetch(`${base}${path}`, {
    ...options,
    method,
    headers,
    cache: "no-store",
  })
  if (!res.ok) {
    const body = await res.text()
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
