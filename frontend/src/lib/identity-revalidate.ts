/**
 * 身份 / 权限的「焦点重校验」（2026-09-17）。
 *
 * **问题**：管理员改了某把 API Key 的角色、或改了某个库的 ACL 之后，**被调整的那一端
 * 界面不会变**——它的 `identity` 是启动时取了一次就再也不更新的缓存，
 * `collections[].permissions` 也只在 collection 事件到达时才刷新。用户必须手动刷新
 * 整页才看到权限变化。
 *
 * **为什么不用事件流（总线）推**：这件事按现有规则会推给错误的人。
 * 事件投递有个安全过滤（`src/events/routes.py`）——「无作用域键的事件默认不投递给
 * 非管理员」，而权限变更的受影响者**恰恰多半不是管理员**。要为它开口子就得动那道
 * 防泄露的承重墙；而且鸡生蛋：被降权的那把钥匙可能正因为这次降权而失去接收资格。
 * 事件还是尽力而为（断线/缺口会走 resync 丢弃），而"我的权限变了"必须**必然**送达。
 *
 * **所以走重校验**：切回这个标签页或恢复网络时重新拉一次身份、库和会话列表。
 * - 唯一能保证必然的路径，不依赖事件送达；
 * - 顺带自愈其它服务端漂移（别人改了 ACL、钥匙被吊销、页面睡了一觉、bfcache 恢复）；
 * - 刷新各自独立，某个请求失败不影响其他列表恢复。
 *
 * **已知残留**：用户如果就坐在那个页面上一动不动，焦点事件不会触发，界面仍是旧的。
 * 此时他点下去会被后端拦（墙是每请求实时判定的），不会越权——只是看到了不该看到的
 * 按钮。要做到完全实时得靠轮询或真正的推送，代价与收益不成比例。
 */

/** 两次重校验之间的最小间隔，避免快速来回切标签页时打出一串请求。 */
export const REVALIDATE_MIN_INTERVAL_MS = 2000

/**
 * 是否该跑一次重校验（纯函数，便于测试）。
 *
 * `lastMs === 0` 表示从没跑过 → 该跑（首次获得焦点时立即校验一次）。
 */
export function shouldRevalidate(
  lastMs: number,
  nowMs: number,
  minInterval: number = REVALIDATE_MIN_INTERVAL_MS,
): boolean {
  if (lastMs <= 0) return true
  return nowMs - lastMs >= minInterval
}

/** 一次重校验要做的事（注入进来，便于单测不碰真实网络）。 */
export interface RevalidateDeps {
  refreshIdentity: () => Promise<unknown> | unknown
  refreshCollections: () => Promise<unknown> | unknown
  refreshSessions?: () => Promise<unknown> | unknown
  /** 额外刷新（会议/热词/人物等也带权限字段）。可选。 */
  refreshDomains?: () => void
  now?: () => number
}

/**
 * 跑一次重校验。返回是否真的执行了（被节流时为 false）。
 *
 * 请求并发、失败各自吞掉：网络抖动不该让其他列表被牵连——
 * 下一次焦点事件会再试。
 */
export async function revalidate(
  deps: RevalidateDeps,
  state: { lastMs: number },
): Promise<boolean> {
  const now = deps.now ?? (() => Date.now())
  const t = now()
  if (!shouldRevalidate(state.lastMs, t)) return false
  state.lastMs = t
  await Promise.allSettled([
    (async () => deps.refreshIdentity())(),
    (async () => deps.refreshCollections())(),
    (async () => deps.refreshSessions?.())(),
  ])
  try {
    deps.refreshDomains?.()
  } catch {
    /* 刷新域失败不影响身份/库 */
  }
  return true
}

/**
 * 启动焦点重校验（幂等）。返回停止函数。
 *
 * 挂四个入口，缺一个就有漏网场景：
 * - `focus`：切窗口回来；
 * - `visibilitychange`：切标签页（**不触发 focus**，所以必须单独挂）；
 * - `pageshow`：从 bfcache 恢复（`persisted` 时连 load 都不重跑，最容易漏）。
 * - `online`：网络恢复后补拉，避免首次加载失败后一直显示空列表。
 */
export function startIdentityRevalidation(deps: RevalidateDeps): () => void {
  const state = { lastMs: 0 }

  const run = () => {
    // 页面不可见时不跑：切走的瞬间拉一次没有意义，回来时会再触发。
    if (typeof document !== "undefined" && document.visibilityState === "hidden") return
    void revalidate(deps, state)
  }

  const onVisibility = () => {
    if (document.visibilityState === "visible") run()
  }

  window.addEventListener("focus", run)
  document.addEventListener("visibilitychange", onVisibility)
  window.addEventListener("pageshow", run)
  window.addEventListener("online", run)

  return () => {
    window.removeEventListener("focus", run)
    document.removeEventListener("visibilitychange", onVisibility)
    window.removeEventListener("pageshow", run)
    window.removeEventListener("online", run)
  }
}
