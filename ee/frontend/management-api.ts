import { request } from "@/api/http"
import { authHeaders } from "@/api/pairing"

// ── 角色 ────────────────────────────────────────────────────

export interface RoleInfo {
  id: string
  name: string
  builtin: boolean
  assignable: boolean
  editable: boolean
  modules: Record<string, boolean>
  capabilities: Record<string, boolean>
  /** 正在使用该角色的钥匙数（删除前提示用）。 */
  key_count: number
}

export interface CapabilityCatalogue {
  modules: string[]
  capabilities: string[]
  /** 只有本机能授予的能力（UI 置灰；后端同样强制）。 */
  owner_only_capabilities: string[]
}

export const getCapabilityCatalogue = () =>
  request<CapabilityCatalogue>("/identity/capabilities")

export const listRoles = () => request<{ roles: RoleInfo[] }>("/identity/roles")

export const createRole = (body: {
  name: string
  modules?: Record<string, boolean>
  capabilities?: Record<string, boolean>
}) => request<RoleInfo>("/identity/roles", { method: "POST", body: JSON.stringify(body) })

export const updateRole = (
  roleId: string,
  body: {
    name?: string
    modules?: Record<string, boolean>
    capabilities?: Record<string, boolean>
  },
) => request<RoleInfo>(`/identity/roles/${roleId}`, { method: "PUT", body: JSON.stringify(body) })

export const deleteRole = (roleId: string) =>
  request<{ ok: boolean }>(`/identity/roles/${roleId}`, { method: "DELETE" })

// ── 领域 ACL（会议 / 热词 / 人物）────────────────────────────

export type DomainAcl = Record<string, Record<string, string[]>>

export interface DomainAclResponse {
  domains: DomainAcl
  tiers: Record<string, string[]>
  /**
   * 隐式角色（owner / admin）：永远拥有每一档，**不由用户勾选**，所以 UI 不渲染它们。
   * 后端在读写两侧都强制并上，过滤器只是化妆。
   */
  implicit_roles: string[]
}

export const getDomainAcl = () => request<DomainAclResponse>("/identity/domain-acl")

export const putDomainAcl = (domains: DomainAcl) =>
  request<DomainAclResponse>("/identity/domain-acl", {
    method: "PUT",
    body: JSON.stringify({ domains }),
  })

// ── 每库 ACL ────────────────────────────────────────────────

export interface CollectionAclResponse {
  acl: Record<string, string[]>
  tiers: string[]
  /** 隐式角色（owner / admin）：永远有每一档，UI 不渲染（见 DomainAclResponse）。 */
  implicit_roles: string[]
}

export const getCollectionAcl = (collectionId: string) =>
  request<CollectionAclResponse>(`/identity/collection-acl/${encodeURIComponent(collectionId)}`)

export const putCollectionAcl = (collectionId: string, acl: Record<string, string[]>) =>
  request<CollectionAclResponse>(
    `/identity/collection-acl/${encodeURIComponent(collectionId)}`,
    { method: "PUT", body: JSON.stringify({ acl }) },
  )

export const setPairingTokenRole = (id: string, role: string, boxId?: string) =>
  request<{ ok: boolean; role: string }>("/pairing/set-role", {
    method: "POST",
    headers: authHeaders(boxId, { "Content-Type": "application/json" }),
    body: JSON.stringify({ id, role }),
  })
