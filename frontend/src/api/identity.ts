/**
 * RBAC 身份与权限管理 API（阶段 6）。
 *
 * 全部走统一出口 `request()`（来源感知地址 + 配对钥匙 + 远端回退）。
 */

import { request } from "./http"

// ── 身份画像（UI 化妆用）─────────────────────────────────────

export interface IdentityMe {
  actor_id: string
  actor_kind: string
  role_id: string
  role_name: string
  modules: Record<string, boolean>
  capabilities: Record<string, boolean>
  is_local: boolean
  /** 三域可访问性（会议/热词/人物）——它们没有 modules 开关，看各自 ACL 的 read 档。 */
  domains: Record<string, boolean>
  /**
   * 创建入口的化妆信号（后端用与墙同一引擎 policy.can 现算）。
   * 老后端没有这个字段——严格 `=== true` 判定下按"没有"处理。
   */
  can_create?: { collection: boolean; meeting: boolean }
}

export const getIdentityMe = () => request<IdentityMe>("/identity/me")
