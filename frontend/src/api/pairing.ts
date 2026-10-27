/** Pairing (CONN-2): 盒子身份、配对钥匙、远端盒子测试连接。 */

import { request } from "./http"
// 钥匙存储统一由 origin.ts 管理（避免两处优先级逻辑分叉）。
import {
  activeConnection,
  apiUrl,
  clearStoredOwnerKey,
  getLocalBoxId,
  getStoredOwnerKey,
  setHotAccessToken,
  storeOwnerKey,
} from "./origin"

export { clearStoredOwnerKey, getStoredOwnerKey, storeOwnerKey }

export interface PairingBoxInfo {
  box_id: string
  box_name: string
  addresses: string[]
  /** 用户自查后填写的对外地址（容器部署时自动检测不可信）；未设置则缺省。 */
  advertised_addr?: string
}

export interface PairingTokenItem {
  id: string
  name: string
  /** RBAC 角色（决定这把钥匙能做什么）。后端一直返回，此前前端类型漏了。 */
  role: string
  token_hint: string
  created_at: string | null
  last_seen: string | null
  expires_at: string | null
  revoked_at: string | null
}

export interface IssuedPairingToken {
  token: string
  id: string
  name: string
  box_id?: string
  created_at: string
  expires_at: string | null
}

/** 指定盒子优先；boxId 未知（如刚刷新页面）时退回通用取钥匙顺序。 */
export function authHeaders(boxId: string | undefined, extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...(extra ?? {}) }
  const tk = (boxId ? getStoredOwnerKey(boxId) : null) || getStoredOwnerKey()
  if (tk) h["X-Pairing-Token"] = tk
  return h
}

export const getPairingBox = (boxId?: string) =>
  request<PairingBoxInfo>("/pairing/box", { local: true, headers: authHeaders(boxId) })

/** 更新盒子档案：名称必带；advertised_addr 传 undefined = 不修改，null/"" = 清除。 */
export const updatePairingBox = (
  body: { box_name: string; advertised_addr?: string | null },
  boxId?: string,
) =>
  request<PairingBoxInfo>("/pairing/box", {
    method: "PUT",
    local: true,
    headers: authHeaders(boxId, { "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  })

export const issuePairingToken = async (
  name: string,
  boxId?: string,
  ttlDays?: number | null,
  role?: string,
) => {
  const result = await request<IssuedPairingToken>("/pairing/issue", {
    method: "POST",
    headers: authHeaders(boxId, { "Content-Type": "application/json" }),
    // RBAC：角色随钥匙走。后端会独立强制签发上限（前端下拉只是化妆）——
    // 例如非本机身份签不出 admin，即使这里传了。
    body: JSON.stringify({ name, ttl_days: ttlDays ?? null, role: role ?? "member" }),
  })
  if (result.box_id) storeOwnerKey(result.box_id, result.token) // 签发者即持有者
  return result
}

export const listPairingTokens = (boxId?: string) =>
  request<{ tokens: PairingTokenItem[]; limit: null }>("/pairing/tokens", {
    local: true,
    headers: authHeaders(boxId),
  })


export const revokePairingToken = (id: string, boxId?: string) =>
  request<{ ok: boolean }>("/pairing/revoke", {
    method: "POST",
    local: true,
    headers: authHeaders(boxId, { "Content-Type": "application/json" }),
    body: JSON.stringify({ id }),
  })

/** 从 sinkduce://pair?box=…&name=…&addr=…&token=… 解出字段；非配对链接返回 null。 */
export function parsePairLink(
  raw: string,
): { box?: string; name?: string; addr?: string; token?: string } | null {
  const s = (raw || "").trim()
  if (!s.startsWith("sinkduce://")) return null
  const params = new URLSearchParams(s.split("?")[1] || "")
  return {
    box: params.get("box") || undefined,
    name: params.get("name") || undefined,
    addr: params.get("addr") || undefined,
    token: params.get("token") || undefined,
  }
}

/** 跨源直连远端盒子做「测试连接」（不走本机 /api 代理）。8 秒超时。错误带 code: unreachable | invalid_token | http_error */export async function testRemoteBox(address: string, token: string): Promise<PairingBoxInfo> {
  const base = address.trim().replace(/\/+$/, "")
  const ctrl = new AbortController()
  const timer = window.setTimeout(() => ctrl.abort(), 8000)
  let resp: Response
  try {
    resp = await fetch(`${base}/api/pairing/box`, {
      headers: { "X-Pairing-Token": token },
      signal: ctrl.signal,
    })
  } catch {
    throw Object.assign(new Error("unreachable"), { code: "unreachable" })
  } finally {
    window.clearTimeout(timer)
  }
  if (resp.status === 401 || resp.status === 403) {
    throw Object.assign(new Error("invalid_token"), { code: "invalid_token" })
  }
  if (!resp.ok) {
    throw Object.assign(new Error(`http_${resp.status}`), { code: "http_error" })
  }
  return (await resp.json()) as PairingBoxInfo
}

// ── 长短钥匙：暖票循环 ──────────────────────────────────────────
//
// 日常请求改带 10 分钟短票（sda1_，门卫双接受），长钥匙从「每请求都上线」
// 降为「每 ~200 秒换票一次」。这里刻意不走 request()：它会把远端失败升级成
// 「连接失效退回本机」，而换票失败应当完全静默——盒子重启/换网络/还是旧版
// 都不该惊动用户（长钥匙照常开门，门卫双接受兜底）。

const WARM_INTERVAL_MS = 200_000 // 后端 ACCESS_TOKEN_TTL_SECONDS(600) / 3

let warmerStarted = false
/** 上一次失败的 HTTP 状态（每段连续失败只打一次 console.warn，避免每 200s 刷屏） */
let warnedFailureAt: number | null = null

/** 用当前通道的长钥匙换一张短票存进内存热票缓存。失败静默，长钥匙继续兜底。 */
export async function warmAccessToken(): Promise<void> {
  try {
    const conn = activeConnection()
    const boxId = conn?.box_id ?? getLocalBoxId()
    const longToken = conn?.token || getStoredOwnerKey()
    if (!boxId || !longToken) return
    const ctrl = new AbortController()
    const timer = window.setTimeout(() => ctrl.abort(), 5000)
    let resp: Response
    try {
      // apiUrl 不补 /api（那是 request() 的活儿），裸 fetch 必须写全路径。
      resp = await fetch(apiUrl("/api/pairing/access-token"), {
        method: "POST",
        headers: { "X-Pairing-Token": longToken },
        signal: ctrl.signal,
      })
    } finally {
      window.clearTimeout(timer)
    }
    if (!resp.ok) {
      // 静默是刻意的（别把换票失败升级成「连接失效」），但**完全**无声会让
      // 路径写错这类 bug 只能靠用户看控制台发现：每段连续失败只提醒一次。
      if (warnedFailureAt !== resp.status) {
        warnedFailureAt = resp.status
        console.warn("[access-token] mint failed:", resp.status)
      }
      return
    }
    warnedFailureAt = null
    const result = (await resp.json()) as { access_token?: string; expires_in?: number }
    if (result.access_token && typeof result.expires_in === "number") {
      setHotAccessToken(boxId, result.access_token, result.expires_in * 1000)
    }
  } catch {
    /* 网络/超时/旧版盒子：静默，长钥匙照常工作 */
  }
}

/** 启动暖票循环（应用启动时调一次；切来源=整页刷新，天然重新开始）。 */
export function startAccessTokenWarmer(): void {
  if (warmerStarted) return
  warmerStarted = true
  void warmAccessToken()
  window.setInterval(() => { void warmAccessToken() }, WARM_INTERVAL_MS)
  // 后台标签页的定时器被节流：回前台立刻补一次，别让首请求拿着过期热票出门
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return
    void warmAccessToken()
    // Hub 镜像补发（2026-09-19 #8）：后端重启常发生在用户离开期间，回前台
    // 是确定性的重试点。内部自带本机来源判定，远端页面是 no-op。
    void import("./hub").then((m) => m.flushPendingMirror())
  })
}

// ── 本机静默领钥匙（CONN-6）────────────────────────────────────
//
// 身份模型：**主端口（默认 18900）= 本机入口**。容器里宿主机只把主端口发布到
// 回环（127.0.0.1），所以「请求落在主端口上」= 来自盒子本机。宿主机上的浏览器
// 访问 localhost:18900 时，页面的来源端口就是主端口，因此可以**同源**领走主人
// 钥匙——用户全程看不到「钥匙」。
//
// 局域网设备访问的是另一个对外端口（默认 18901），请求落在它上面不满足
// 「本机入口」判定，这里会 401，于是走正常的钥匙门（必须由主人发钥匙）。
//
// 必须在首屏任何 /api 请求之前完成，否则门卫会把它们挡成 401。

let bootstrapStarted = false

/**
 * 本机启动引导：同源静默领取主人钥匙并存入 localStorage。best-effort，永不抛错。
 *
 * 只在「当前来源是盒子本机」时成功——非本机（远端盒子或局域网设备）会拿到 401，
 * 静默退出，交给钥匙门。访问远端盒子时不跑，避免把本机钥匙错记到别家盒子上。
 */
export async function bootstrapLocalKey(): Promise<void> {
  if (bootstrapStarted) return
  bootstrapStarted = true
  try {
    // 动态 import 避免 pairing ↔ origin 的循环依赖
    const { getActiveSourceId, LOCAL_SOURCE, setGateKey, setLocalBoxId, storeOwnerKey } =
      await import("./origin")
    if (getActiveSourceId() !== LOCAL_SOURCE) return

    const ctrl = new AbortController()
    const timer = window.setTimeout(() => ctrl.abort(), 1500)
    let resp: Response
    try {
      // 同源：门卫按「请求是否落在本机入口端口」判定，非本机会 401
      resp = await fetch("/api/pairing/local-key", {
        signal: ctrl.signal,
        cache: "no-store",
      })
    } catch {
      return
    } finally {
      window.clearTimeout(timer)
    }
    if (!resp.ok) return
    const data = (await resp.json()) as { token?: string; box_id?: string }
    if (!data.token || !data.box_id) return
    // 三处都写：按盒子存 + 记下当前本机盒子（取钥匙优先它）+ 刷新兜底槽。
    // 刷新兜底槽是为了让旧方案签发的陈旧钥匙（可能已被吊销）不再遮蔽新领的这把。
    storeOwnerKey(data.box_id, data.token)
    setLocalBoxId(data.box_id)
    setGateKey(data.token)
    // Hub 出站钥匙托管：把浏览器里的连接列表同步给后端（Hub 要拿这些钥匙去问别的
    // 盒子）。**放在这里**是因为此刻刚确认「本机来源 + 后端可达」——两个前提都齐了。
    // 增量同步在 saveConnections 里（连接/改地址/移除都会触发），这一次负责
    // **存量对齐**：升级到本版本前已连好的盒子，否则后端永远不知道它们存在。
    const { savedConnections } = await import("./origin")
    void import("./hub").then((m) => m.mirrorConnectionsToHub(savedConnections()))
  } catch {
    /* 引导失败不阻塞应用启动 */
  }
}
