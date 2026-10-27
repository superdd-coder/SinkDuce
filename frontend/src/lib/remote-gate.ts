// 带扩展名的相对导入：本模块会被 node --test 直接加载（测试），Node 解析器
// 不认 `@/` 别名（与 origin.ts / hub.ts 的写法一致）。类型导入会被剥除，不受限。
import { LOCAL_SOURCE } from "../api/origin.ts"
import type { SourceHealthState } from "@/stores/app-store"

/**
 * 「先去配对」引导态的触发判定（纯函数，tests/ 可直测）。
 *
 * 远程模式下这台 App 只是一个入口壳：没有任何可用的已配对盒子（一台都没配过、
 * 没选中、或选中的那台离线/钥匙失效）时，全屏引导态取代主界面——而不是把用户
 * 扔进一个所有请求都在报错的空壳。
 *
 * 刻意的取舍：
 * - `unknown`（探活还没回来）不算失联——避免启动瞬间闪引导态；
 * - 在线判定只信探活结果（`sourceHealth`），不信任何缓存。
 */

export type RemoteGateReason =
  | "no_connections"
  | "no_active"
  | "unreachable"
  | "revoked"

export interface RemoteGateInput {
  connections: Array<{ box_id: string }>
  activeSourceId: string
  health: Record<string, SourceHealthState>
}

export function evaluateRemoteGate(
  input: RemoteGateInput,
): { show: boolean; reason: RemoteGateReason | null } {
  const { connections, activeSourceId, health } = input
  if (connections.length === 0) return { show: true, reason: "no_connections" }
  if (activeSourceId === LOCAL_SOURCE) return { show: true, reason: "no_active" }
  const st: SourceHealthState = health[activeSourceId] ?? "unknown"
  if (st === "offline") return { show: true, reason: "unreachable" }
  if (st === "revoked") return { show: true, reason: "revoked" }
  return { show: false, reason: null }
}
