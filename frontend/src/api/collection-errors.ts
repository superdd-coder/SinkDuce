/**
 * 判定「这次失败只是因为 collection 过期」——用于静默跳过而非弹错。
 *
 * 来源刚切换、或 collection 刚被别的设备删掉时，界面可能还拿着旧 id 去请求，
 * 服务端返回 404/400 `Collection 'X' not found`。这属于**预期内的过期状态**，
 * 不该打扰用户（fetchCollections 的自愈会清掉它）。
 *
 * 注意别把真正的故障一起吞掉：只有「状态码 + 文本」都对得上才算过期。
 */
const STALE_STATUSES = new Set([400, 404])
const NOT_FOUND_RE = /collection\s+['"][^'"]+['"]\s+not\s+found/i

function textOf(err: unknown): string {
  if (!err || typeof err !== "object") return ""
  const e = err as { detail?: unknown; rawBody?: string; message?: string }
  const parts: string[] = []
  if (typeof e.detail === "string") parts.push(e.detail)
  else if (e.detail && typeof e.detail === "object") parts.push(JSON.stringify(e.detail))
  if (typeof e.rawBody === "string") parts.push(e.rawBody)
  if (typeof e.message === "string") parts.push(e.message)
  return parts.join(" ")
}

export function isStaleCollectionError(err: unknown): boolean {
  if (!err || typeof err !== "object") return false
  const status = (err as { status?: unknown }).status
  if (typeof status !== "number" || !STALE_STATUSES.has(status)) return false
  return NOT_FOUND_RE.test(textOf(err))
}
