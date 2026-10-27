import { rememberConnectionLost, type ConnectionFailureReason } from "./connection-health.ts"
import { hasCriticalOps, onAllOpsFinished } from "./critical-op.ts"
// 注意是**动态 import 避开循环依赖**：hub.ts 反过头来要用本模块的
// isLocalSourceActive / savedConnections（它俩是来源判定的唯一事实源）。
// 静态 import 会形成 origin ⇄ hub 环。镜像本身是尽力而为的旁路，晚一拍加载没影响。
async function mirrorConnectionsToHub(list: SavedConnection[]): Promise<void> {
  try {
    const mod = await import("./hub.ts")
    await mod.mirrorConnectionsToHub(list)
  } catch {
    /* 尽力而为：模块加载失败同样不打扰用户 */
  }
}

/** HUB-7: 当前服务来源管理。切换 = 换 API origin（零拷贝），永远不全量传。 */

export interface SavedConnection {
  addr: string
  token: string
  box_id: string
  box_name: string
  last_checked: string
}

export const LOCAL_SOURCE = "local"

// ── 远程模式（瘦壳）旗标 ────────────────────────────────────────
// 唯一写点是启动时的 health 探测（main.tsx）：本机 /health 带 remote_shell
// 标志 = 这台 App 正以瘦壳形态运行（本机没有后端可回退）。放在 origin.ts
// 是因为「断连不回退本机」（本文件）与 hub 镜像放行（hub.ts）都要读它，
// 而这两个模块不能再引入 store 依赖（环）。
let remoteShellActive = false

export function setRemoteShellActive(v: boolean) {
  remoteShellActive = v
}

export function isRemoteShellActive(): boolean {
  return remoteShellActive
}

/**
 * 页面来源是否是回环地址（桌面壳 / 在盒子本机开的浏览器）。
 *
 * 前端只有两种持久状态（LOCAL_SOURCE / 远端连接），但现实有三种：
 * **局域网设备直连打开盒子页面**时没有任何连接记录，会被归进 LOCAL_SOURCE，
 * 而「本机」这个标签对持机者是谎话。回环 hostname 是唯一能同步、无网络依赖
 * 地区分「真本机」与「直连」的信号——直连态应显示盒子自己的名字。
 */
export function isLoopbackPage(): boolean {
  try {
    const h = window.location.hostname
    return h === "localhost" || h === "[::1]" || h === "::1" || h.startsWith("127.")
  } catch {
    return true
  }
}

const CONNECTIONS_KEY = "sinkduce.pairing.connections"
const ACTIVE_KEY = "sinkduce.pairing.activeSource"

export function savedConnections(): SavedConnection[] {
  try {
    return JSON.parse(localStorage.getItem(CONNECTIONS_KEY) || "[]") as SavedConnection[]
  } catch {
    return []
  }
}

/**
 * 写入连接列表，并**镜像给后端**（Hub 出站钥匙托管）。
 *
 * 镜像放在这里而不是各个调用点：`saveConnections` / `removeConnection` 是
 * 连接列表**唯一的两个写口**（连接弹窗、改地址弹窗、移除按钮都走它们），
 * 放这里等于「所有变更自动被镜像」，将来新增调用点也不会漏。
 *
 * 为什么是 fire-and-forget：见 `hub.ts` 的三条纪律——后端没起来、页面正由远端
 * 盒子提供、网络抖动，都不该影响「连接一台盒子」这个用户操作。
 */
export function saveConnections(list: SavedConnection[]) {
  localStorage.setItem(CONNECTIONS_KEY, JSON.stringify(list))
  void mirrorConnectionsToHub(list)
}

export function getActiveSourceId(): string {
  try {
    return localStorage.getItem(ACTIVE_KEY) || LOCAL_SOURCE
  } catch {
    return LOCAL_SOURCE
  }
}

export function setActiveSourceId(id: string) {
  try {
    localStorage.setItem(ACTIVE_KEY, id)
  } catch {
    /* ignore */
  }
}

export function activeConnection(): SavedConnection | null {
  const id = getActiveSourceId()
  if (id === LOCAL_SOURCE) return null
  return savedConnections().find((c) => c.box_id === id) || null
}

/**
 * 当前是不是「本机后端」。
 *
 * 用在设置页：连到别人的盒子时（访客身份），不该出现连接相关的配置
 * （配对钥匙、MCP 地址等）——那是在管理别人的盒子。切来源的入口在
 * 左上角切换器里，始终可见，不受影响。
 *
 * 连接记录已不存在时（例如刚被回退清理）按本机处理。
 */
export function isLocalSourceActive(): boolean {
  return activeConnection() === null
}

export function activeConnectionName(): string {
  return activeConnection()?.box_name || ""
}

/** API origin：本机 = 同源（空串），远端 = 连接地址（如 http://192.168.1.30:18900） */
export function apiOrigin(): string {
  return (activeConnection()?.addr || "").replace(/\/+$/, "")
}

/** 给 /api/... 相对路径加上当前来源的 origin（本机时原样返回相对路径） */
export function apiUrl(path: string): string {
  return `${apiOrigin()}${path}`
}

/**
 * 永远指向**本机后端**的 URL（同源相对路径），忽略当前来源。
 *
 * 用于「这台电脑的软硬件能力」类请求。系统音频助手（抓 macOS 喇叭声音的
 * 原生程序）装在本机，所以问它「在不在」必须问本机——问远端盒子永远是「没有」，
 * 那会把本机明明可用的能力静默关掉。
 *
 * 页面本身就由某台后端提供，所以相对路径打的就是那台：
 * 桌面 App 的页面来自本机 → 打本机（要的就是这个）；
 * 浏览器打开远端盒子的页面 → 打远端，此时本机能力确实无从谈起，行为也正确。
 */
export function localApiUrl(path: string): string {
  return path
}

/** WebSocket 版本：http(s) → ws(s) */
export function wsApiUrl(path: string): string {
  const origin = apiOrigin()
  if (!origin) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    return `${protocol}//${window.location.host}${path}`
  }
  return `${origin.replace(/^http/, "ws")}${path}`
}

// ── 主人钥匙存储（按盒子 + 全局兜底槽）──
// 背景：来源 IP 在 VPN/代理/Docker NAT 下不可靠，「持有钥匙」才是身份凭证（CONN-6）。
const OWNER_KEYS_STORE = "sinkduce.pairing.ownerKeys"
const LEGACY_KEY = "sinkduce.pairing.ownerKey"
// 本机盒子的 box_id：启动 bootstrap 领到钥匙后记下，取钥匙时优先它。
// 否则一把陈旧钥匙（例如旧方案签发、后来被吊销的）会一直遮蔽新领的那把，
// 每个请求都 401 → 钥匙门反复弹出（用户实测到的现象）。
const LOCAL_BOX_STORE = "sinkduce.pairing.localBoxId"

export function ownerKeys(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(OWNER_KEYS_STORE) || "{}") as Record<string, string>
  } catch {
    return {}
  }
}

export function storeOwnerKey(boxId: string, token: string) {
  const m = ownerKeys()
  m[boxId] = token
  try {
    localStorage.setItem(OWNER_KEYS_STORE, JSON.stringify(m))
  } catch {
    /* 无痕模式等场景忽略 */
  }
}

/** 丢掉某一台盒子的钥匙记录（换盒子、钥匙失效时清理） */
export function clearStoredOwnerKey(boxId: string) {
  const m = ownerKeys()
  delete m[boxId]
  try {
    localStorage.setItem(OWNER_KEYS_STORE, JSON.stringify(m))
  } catch {
    /* ignore */
  }
}

export function setLocalBoxId(boxId: string) {
  try {
    localStorage.setItem(LOCAL_BOX_STORE, boxId)
  } catch {
    /* ignore */
  }
}

export function getLocalBoxId(): string | null {
  try {
    return localStorage.getItem(LOCAL_BOX_STORE) || null
  } catch {
    return null
  }
}

/** 取钥匙：优先指定盒子 → 当前本机盒子 → 全局兜底槽 → 任意一把 */
export function getStoredOwnerKey(boxId?: string): string | null {
  if (boxId) return ownerKeys()[boxId] || null
  const keys = ownerKeys()
  const localBox = getLocalBoxId()
  if (localBox && keys[localBox]) return keys[localBox]
  try {
    return localStorage.getItem(LEGACY_KEY) || Object.values(keys)[0] || null
  } catch {
    return null
  }
}

/** 钥匙门（401 引导）保存的全局钥匙 */
export function setGateKey(token: string) {
  try {
    localStorage.setItem(LEGACY_KEY, token)
  } catch {
    /* ignore */
  }
}

/** 丢掉陈旧的本机钥匙记录（bootstrap 失败时自愈用） */
export function clearLocalBoxKey() {
  const localBox = getLocalBoxId()
  if (!localBox) return
  const m = ownerKeys()
  delete m[localBox]
  try {
    localStorage.setItem(OWNER_KEYS_STORE, JSON.stringify(m))
  } catch {
    /* ignore */
  }
}

// ── 短票缓存（长短钥匙）：内存热票，localStorage 不存 ─────────────
//
// 长钥匙换来的 10 分钟短票只住内存。出票带 60 秒余量判定：后台标签页的定时器
// 被浏览器节流后，**过期热票绝不出门**——否则一次 401 会被连接失效层误判成
// 「钥匙被吊销」直接退回本机；余量不足时回落长钥匙（门卫双接受），永远安全。

const HOT_ACCESS_MARGIN_MS = 60_000
const hotAccessTokens = new Map<string, { token: string; expMs: number }>()

export function setHotAccessToken(boxId: string, token: string, expiresInMs: number) {
  hotAccessTokens.set(boxId, { token, expMs: Date.now() + expiresInMs })
}

/** 热票：仍在余量期内才出票；将过期/已过期返回 null（调用方回落长钥匙）。 */
export function hotAccessToken(boxId: string): string | null {
  const hot = hotAccessTokens.get(boxId)
  if (!hot) return null
  if (hot.expMs - Date.now() <= HOT_ACCESS_MARGIN_MS) return null
  return hot.token
}

/**
 * 长钥匙（localStorage 里的配对钥匙）。换票、配对管理等**明确要长钥匙**的
 * 场景用这个；日常请求走 `currentPairingToken()`（热票优先）。
 */
export function currentLongToken(): string | null {
  return activeConnection()?.token || getStoredOwnerKey()
}

/**
 * 当前通道该用的钥匙：**当前连接的钥匙优先，否则本机钥匙**。
 *
 * 所有「跟着当前来源走」的通道都必须用它——fetch 头、WS query、EventSource、
 * `<img>`、`<a>` 下载。曾经这些地方各自取钥匙，其中 WS/SSE/图片/下载只取了本机
 * 钥匙，于是连远端盒子时会把自己的本机钥匙发给对方，被拒且症状零散难查。
 *
 * 长短钥匙升级后：内存里有未到期的短票（sda1_）就优先出短票——长钥匙从
 * 「每请求都上线」降为「每 ~200 秒换票一次」，URL 通道烙的也是短命票。
 *
 * 注意与 `getStoredOwnerKey(boxId)` 的区别：后者是「我持有某台盒子的哪把钥匙」，
 * 用于配对管理这类明确指定目标的场景。
 */
export function currentPairingToken(): string | null {
  const boxId = activeConnection()?.box_id ?? getLocalBoxId()
  const hot = boxId ? hotAccessToken(boxId) : null
  return hot ?? currentLongToken()
}

/** WS / EventSource / <img> 等无法带 header 的通道：以 query 参数附钥匙 */
export function withTokenQuery(url: string): string {
  const tk = currentPairingToken()
  if (!tk || url.includes("token=")) return url
  return url + (url.includes("?") ? "&" : "?") + `token=${encodeURIComponent(tk)}`
}

/** fetch 用的钥匙头（当前通道） */
export function pairingHeader(): Record<string, string> {
  const tk = currentPairingToken()
  return tk ? { "X-Pairing-Token": tk } : {}
}

// ── 远端连接失效处理 ────────────────────────────────────────────
//
// 设备 B 连到设备 A 后，若 A 吊销了 B 的钥匙（或 A 下线），B 的所有请求都会失败。
// 各视图会把错误吞掉，界面只是空着 —— 用户既看不到原因也退不回来。
// 这里负责：清掉这把已失效的钥匙（避免下次再被它拖住）+ 退回本机 + 留下提示。

/** 清掉一台盒子的连接记录（钥匙已失效，留着只会反复失败）。 */
export function removeConnection(boxId: string) {
  saveConnections(savedConnections().filter((c) => c.box_id !== boxId))
  clearStoredOwnerKey(boxId)
}

/** 已经在退回本机的路上：并发请求同时失败时只处理第一次。 */
let revertingToLocal = false

/**
 * 当前远端盒子连不上了：退回本机并留下提示，然后整页刷新。
 *
 * 调用方（请求层）已确认「当前来源是远端且这次失败确实代表连接断了」
 * （判定见 connection-health.ts 的 classifyConnectionFailure）。
 *
 * 返回 true 表示确实处理了（调用方可据此停止后续加载）。
 *
 * 两种原因的处置**都保留连接记录**（2026-09-19 反转）：
 * - 曾经 revoked 会把记录整条删掉——但 401 无法区分「TTL 自然过期」和「被
 *   对方吊销」，而且盒子名/地址是用户资产，删掉=重配对全流程。现在保留记录、
 *   只清热票：切换器里显示「钥匙失效」，重连弹窗可从旧记录预填地址。
 * - 连不上：保留记录（原有行为），切换器里显示「离线」。
 *
 * **关键操作豁免**（2026-09-19 前端加固 #7）：录制/上传进行中不立即回退——
 * reload 会掐断 PCM 流和上传。先进入「降级态」（横幅提示 + 操作照常收尾），
 * 最后一个关键操作结束时补做回退。
 */
export function handleRemoteConnectionLost(reason: ConnectionFailureReason): boolean {
  // 已在回退路上：并发请求同时失败时只处理第一次。必须在 activeConnection
  // 判空**之前**——回退会把当前来源切回本机，并发的第二条失败会在这里被吞掉。
  if (revertingToLocal) return true
  const conn = activeConnection()
  if (!conn) return false
  if (isRemoteShellActive()) {
    // 远程模式没有本机可回退：绝不 reload。标记失联让「先去配对」引导态
    // 接管（探活循环恢复在线后引导态自动消失）。错误照常冒泡，由各调用方
    // 自行 toast——与正常模式的「降级态」同理，但终点是引导态而非回退。
    void markRemoteGateReason(reason === "revoked" ? "revoked" : "unreachable")
    return false
  }
  if (hasCriticalOps()) {
    if (!pendingRevert) {
      pendingRevert = reason
      window.dispatchEvent(new CustomEvent("sinkduce:connection-degraded"))
    }
    return false // 让错误正常冒泡：本次请求失败，但页面不 reload
  }
  revertingToLocal = true
  revertToLocalNow(reason, conn)
  return true
}

let pendingRevert: ConnectionFailureReason | null = null

// 最后一个关键操作（录制/上传）结束时，补做被推迟的回退。
onAllOpsFinished(() => flushPendingRevert())

/** 关键操作全部结束时补做回退（由 critical-op 的结束路径调用）。 */
export function flushPendingRevert(): void {
  if (!pendingRevert) return
  const reason = pendingRevert
  const conn = activeConnection()
  pendingRevert = null
  if (!conn) return
  if (revertingToLocal) return
  revertingToLocal = true
  revertToLocalNow(reason, conn)
}

function revertToLocalNow(reason: ConnectionFailureReason, conn: SavedConnection): void {
  rememberConnectionLost({ box_id: conn.box_id, box_name: conn.box_name, reason })
  hotAccessTokens.delete(conn.box_id)
  setActiveSourceId(LOCAL_SOURCE)
  window.location.reload()
}

// 动态 import 避开 origin ⇄ store 环（同上文 hub.ts 的处理）：store 的依赖树
// 会绕回 http/origin。标记失败是可容忍的——探活循环 30 秒内也会把该盒子标成
// offline/revoked，引导态据此渲染。
async function markRemoteGateReason(
  reason: "unreachable" | "revoked",
): Promise<void> {
  try {
    const { useAppStore } = await import("@/stores/app-store")
    useAppStore.getState().setRemoteGateReason(reason)
  } catch {
    /* 尽力而为 */
  }
}
