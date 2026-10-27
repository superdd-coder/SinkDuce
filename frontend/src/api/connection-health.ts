/**
 * 远端盒子的连接健康判定。
 *
 * 背景：设备 B 把「服务来源」切到设备 A 后，若 A 吊销了 B 的钥匙（或 A 下线），
 * B 的所有 /api 请求都会 401/连不上。页面各视图把错误吞掉 → 界面空着，
 * 用户既看不到原因也退不回本机。这里集中判定「这次失败是不是说明远端连接断了」，
 * 由调用方负责提示并退回本机。
 *
 * 判定只依赖输入（纯函数），便于单测。
 */

/** 连接断掉的原因。 */
export type ConnectionFailureReason = "revoked" | "unreachable"

export interface FailureInput {
  /** 当前来源是否为远端盒子（false = 本机）。本机的 401 走钥匙门，不在此列。 */
  activeRemote: boolean
  /** HTTP 状态码；网络层失败时省略。 */
  status?: number
  /** 后端错误码，如 pairing_required。 */
  code?: string | null
  /** fetch 直接抛错（连不上 / 超时 / 跨域被拒）。 */
  networkError?: boolean
}

/**
 * 判断一次请求失败是否代表「当前远端盒子连不上了」。
 *
 * 返回 null = 与远端连接无关（例如本机 401 该弹钥匙门），交给原有逻辑。
 * - 网络层失败 → "unreachable"（盒子关机 / 换了网络 / 地址变了）
 * - 401        → "revoked"（钥匙失效或被吊销）
 */
export function classifyConnectionFailure(input: FailureInput): ConnectionFailureReason | null {
  if (!input.activeRemote) return null
  if (input.networkError) return "unreachable"
  if (input.status === 401) return "revoked"
  return null
}

/** 连接断开后要展示给用户的信息（跨 reload 传递：写 sessionStorage）。 */
export interface ConnectionLostNotice {
  box_id: string
  box_name: string
  reason: ConnectionFailureReason
}

const NOTICE_KEY = "sinkduce.pairing.connectionLost"

/** 记下「刚断开」这件事，供 reload 后的页面读取一次。 */
export function rememberConnectionLost(notice: ConnectionLostNotice): void {
  try {
    window.sessionStorage.setItem(NOTICE_KEY, JSON.stringify(notice))
  } catch {
    /* 无痕模式等场景忽略 */
  }
}

/** 读取并清除（只提示一次，避免每次刷新都弹）。 */
export function takeConnectionLostNotice(): ConnectionLostNotice | null {
  try {
    const raw = window.sessionStorage.getItem(NOTICE_KEY)
    if (!raw) return null
    window.sessionStorage.removeItem(NOTICE_KEY)
    const parsed = JSON.parse(raw) as ConnectionLostNotice
    if (!parsed || typeof parsed.box_name !== "string" || !parsed.reason) return null
    return parsed
  } catch {
    return null
  }
}

/**
 * 给「取不了 header 的通道」用的鉴权 URL（预览 / 下载 / `<img>` / `<iframe>`）。
 *
 * 这类请求由浏览器或第三方 viewer 自己发起，没法加 `X-Pairing-Token` 头，
 * 所以钥匙只能走查询参数（后端两种都认，见 middleware 的 `_token_from_query`）。
 * 连的是别人的盒子时缺了这一步，预览就会 401 → 空白。
 *
 * url 为空、已有 token、或 url 不指向 /api（本机同源相对路径由调用方保证同源）时原样返回。
 */
export function authorizePreviewUrl(url: string | null, token: string | null): string | null {
  if (!url || !token) return url
  if (url.includes("token=")) return url
  return url + (url.includes("?") ? "&" : "?") + `token=${encodeURIComponent(token)}`
}
