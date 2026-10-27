import { LOCAL_SOURCE, getActiveSourceId, localApiUrl, savedConnections } from "./origin"
import { useAppStore, type SourceHealthState } from "@/stores/app-store"

/**
 * 来源健康统一探测循环（SourcePill）。
 *
 * 旧世界两套探测各说各话：Header 每 30s 探当前来源、切换器只在打开瞬间探远程盒。
 * 这里合成一个循环：本机 + 所有已配对盒子，每 30s 一轮，窗口重聚焦和菜单打开时
 * 立即加探一轮。结果写进 store 的 sourceHealth，药丸 trigger 和菜单读同一份——
 * 药丸上的点永远是真实探测结果，不是「打开菜单才知道」。
 *
 * 每轮重读 savedConnections()：新配对的盒子自动出现，被删的不残留。
 *
 * 探测远程盒必须带各自钥匙打受保护端点（/api/pairing/box）：/health 是门卫豁免的，
 * 钥匙被吊销也会「在线」，那就等于没告诉用户真实情况。
 */

const PROBE_INTERVAL_MS = 30_000
const PROBE_TIMEOUT_MS = 3000

let running = false
let timer: number | null = null
let inFlight = false

/** 单个来源探测结果落库；若它是当前来源，同步派生 isOnline（语义不变）。 */
function commit(id: string, state: Exclude<SourceHealthState, "unknown">) {
  const store = useAppStore.getState()
  store.setSourceHealth(id, state)
  if (id === getActiveSourceId()) {
    store.setOnline(state === "online")
  }
}

/** 立即探测一轮（进行中则跳过，避免叠加）。 */
export function probeSources(): void {
  if (inFlight) return
  inFlight = true

  const jobs: Array<Promise<void>> = []

  // LOCAL 行 = 页面所在的这台盒子：必须探页面自身来源（localApiUrl）。
  // 不能用 getHealth()——它跟当前来源走，切到连不上的远端盒子后探的是那台
  // 远端，探失败会把「本机」也标成离线（实测：全灰）。超时防本机假死卡死循环。
  const localCtrl = new AbortController()
  const localTimeout = window.setTimeout(() => localCtrl.abort(), PROBE_TIMEOUT_MS)
  jobs.push(
    fetch(localApiUrl("/health"), { signal: localCtrl.signal })
      .then((r) => commit(LOCAL_SOURCE, r.ok ? "online" : "offline"))
      .catch(() => commit(LOCAL_SOURCE, "offline"))
      .finally(() => window.clearTimeout(localTimeout)),
  )

  for (const c of savedConnections()) {
    const ctrl = new AbortController()
    const timeout = window.setTimeout(() => ctrl.abort(), PROBE_TIMEOUT_MS)
    jobs.push(
      fetch(`${c.addr.replace(/\/+$/, "")}/api/pairing/box`, {
        headers: { "X-Pairing-Token": c.token },
        signal: ctrl.signal,
      })
        .then((r) =>
          commit(c.box_id, r.ok ? "online" : r.status === 401 ? "revoked" : "offline"),
        )
        .catch(() => commit(c.box_id, "offline"))
        .finally(() => window.clearTimeout(timeout)),
    )
  }

  void Promise.all(jobs).finally(() => {
    inFlight = false
  })
}

function onWindowFocus() {
  probeSources()
}

/** 启动探测循环（幂等）。SourcePill 挂载时调用，与壳同寿。 */
export function startSourceHealthLoop(): void {
  if (running) return
  running = true
  probeSources()
  timer = window.setInterval(probeSources, PROBE_INTERVAL_MS)
  window.addEventListener("focus", onWindowFocus)
}

export function stopSourceHealthLoop(): void {
  if (!running) return
  running = false
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
  window.removeEventListener("focus", onWindowFocus)
}
