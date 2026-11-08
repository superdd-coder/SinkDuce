/* Workspace roles and the meeting, hot-word, and people permission rules. */

import { useCallback, useEffect, useRef, useState } from "react"
import { Check, Loader2, Lock, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { HelpHint } from "@/components/shared/help-hint"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import {
  createRole,
  deleteRole,
  getCapabilityCatalogue,
  getDomainAcl,
  listRoles,
  putDomainAcl,
  updateRole,
  type CapabilityCatalogue,
  type DomainAcl,
  type RoleInfo,
} from "@/api/client"
import { formatApiError } from "@/api/http"
import { useT } from "@/i18n/use-t"
import "./permission-manager.css"

/** 与后端 roles.ALL_MODULES 对应（顺序即展示顺序）。 */
const MODULE_KEYS = ["chat", "database", "recall", "tasks", "settings"] as const
/** 与后端 roles.ALL_CAPABILITIES 对应。 */
const CAP_KEYS = [
  "config.write",
  "key.manage",
  "acl.manage",
  "collection.create",
  "box.manage",
] as const

/**
 * 领域 ACL 的档位展示顺序（people.read 文案必须是"查看与标注"）。
 *
 * meeting 三档（2026-09-17 用户重定）：read 纯读取 / create 建会议+管自己建的 /
 * manage 管别人的。展示顺序按"由小到大"，与实际 API 返回的 `tiers` 一致。
 */
const DOMAIN_META: Record<string, { tiers: string[] }> = {
  meeting: { tiers: ["read", "create", "manage"] },
  hot_words: { tiers: ["read", "write", "delete"] },
  people: { tiers: ["read", "create", "write", "delete"] },
}

interface PermissionManagerDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 操作者是不是本机（决定 key.manage 是否可勾）。 */
  isLocal: boolean
}

export function PermissionManagerDialog({
  open,
  onOpenChange,
  isLocal,
}: PermissionManagerDialogProps) {
  const t = useT()
  const rolesTab = useRef<HTMLButtonElement>(null)
  const [roles, setRoles] = useState<RoleInfo[]>([])
  const [catalogue, setCatalogue] = useState<CapabilityCatalogue | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const [domains, setDomains] = useState<DomainAcl | null>(null)
  const [domainTiers, setDomainTiers] = useState<Record<string, string[]>>({})
  const [implicitRoles, setImplicitRoles] = useState<string[]>([])
  const [domainSaving, setDomainSaving] = useState(false)

  /** 可勾选的角色：隐式角色（owner/admin）永远有每一档，不出现在名单里。 */
  const pickableRoles = roles.filter((r) => !implicitRoles.includes(r.id))

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [rolesRes, cat] = await Promise.all([listRoles(), getCapabilityCatalogue()])
      setRoles(rolesRes.roles)
      setCatalogue(cat)
      setSelectedId((prev) => prev ?? rolesRes.roles[0]?.id ?? null)
    } catch (err) {
      toast.error(formatApiError(err, t))
    } finally {
      setLoading(false)
    }
  }, [t])

  const loadDomains = useCallback(async () => {
    try {
      const res = await getDomainAcl()
      setDomains(res.domains)
      setDomainTiers(res.tiers)
      setImplicitRoles(res.implicit_roles ?? [])
    } catch (err) {
      toast.error(formatApiError(err, t))
    }
  }, [t])

  useEffect(() => {
    if (!open) return
    void refresh()
    void loadDomains()
  }, [open, refresh, loadDomains])

  const selected = roles.find((r) => r.id === selectedId) ?? null
  const readOnly = !selected?.editable
  const roleLocked = readOnly || saving

  const patchSelected = (patch: Partial<RoleInfo>) => {
    if (!selectedId) return
    setRoles((prev) => prev.map((r) => (r.id === selectedId ? { ...r, ...patch } : r)))
  }

  const handleSaveRole = async () => {
    if (!selected || readOnly) return
    setSaving(true)
    try {
      const updated = await updateRole(selected.id, {
        name: selected.name,
        modules: selected.modules,
        capabilities: selected.capabilities,
      })
      setRoles((prev) => prev.map((r) => (r.id === updated.id ? updated : r)))
      toast.success(t("permissions.roleSaved"))
    } catch (err) {
      toast.error(formatApiError(err, t))
    } finally {
      setSaving(false)
    }
  }

  const handleCreateRole = async () => {
    try {
      const role = await createRole({ name: t("permissions.newRoleName") })
      setRoles((prev) => [...prev, role])
      setSelectedId(role.id)
    } catch (err) {
      toast.error(formatApiError(err, t))
    }
  }

  const handleDeleteRole = async () => {
    if (!selected || !selected.editable) return
    try {
      await deleteRole(selected.id)
      setRoles((prev) => prev.filter((r) => r.id !== selected.id))
      setSelectedId(null)
      toast.success(t("permissions.roleDeleted"))
    } catch (err) {
      // 409 role_in_use：说明原因（还有 N 把钥匙在用）
      toast.error(formatApiError(err, t))
    }
  }

  const toggleTierRole = (domain: string, tier: string, roleId: string) => {
    setDomains((prev) => {
      if (!prev) return prev
      const current = prev[domain]?.[tier] ?? []
      const next = current.includes(roleId)
        ? current.filter((r) => r !== roleId)
        : [...current, roleId]
      return { ...prev, [domain]: { ...(prev[domain] ?? {}), [tier]: next } }
    })
  }

  const handleSaveDomains = async () => {
    if (!domains) return
    setDomainSaving(true)
    try {
      const res = await putDomainAcl(domains)
      setDomains(res.domains)
      setDomainTiers(res.tiers)
      toast.success(t("permissions.rulesSaved"))
    } catch (err) {
      toast.error(formatApiError(err, t))
    } finally {
      setDomainSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="pm-permissions-dialog" initialFocus={rolesTab}>
        <DialogHeader>
          <DialogKicker>{t("nav.settings")}</DialogKicker>
          <DialogTitle>
            {t("permissions.title")}
            <HelpHint label={t("permissions.tabRules")}>{t("permissions.rulesHint")}</HelpHint>
          </DialogTitle>
        </DialogHeader>

        <Tabs defaultValue="roles" className="pm-permissions-tabs">
          <TabsList>
            <TabsTrigger value="roles" ref={rolesTab}>{t("permissions.tabRoles")}</TabsTrigger>
            <TabsTrigger value="rules">{t("permissions.tabRules")}</TabsTrigger>
          </TabsList>

          <TabsContent value="roles" className="pm-permissions-tab">
            <div className="pm-permissions-roles">
              <aside className="pm-permissions-rail" aria-label={t("permissions.roles")}>
                <div className="pm-permissions-rail-head">
                  <h3>{t("permissions.roles")}</h3>
                  <Button
                    variant="secondary"
                    size="icon-sm"
                    title={t("permissions.newRole")}
                    aria-label={t("permissions.newRole")}
                    onClick={() => void handleCreateRole()}
                  >
                    <Plus aria-hidden />
                  </Button>
                </div>
                <div className="pm-permissions-role-list">
                  {roles.map((role) => (
                    <button
                      key={role.id}
                      type="button"
                      aria-pressed={role.id === selectedId}
                      className="pm-permissions-role"
                      onClick={() => setSelectedId(role.id)}
                    >
                      {role.builtin && <Lock size={13} aria-hidden />}
                      <span title={role.name}>{role.name}</span>
                      {role.key_count > 0 && <small>{role.key_count}</small>}
                    </button>
                  ))}
                </div>
              </aside>

              <div className="pm-permissions-pane">
                {loading || !selected ? (
                  <div className="pm-permissions-empty" role="status">
                    {loading ? <Loader2 className="size-5 animate-spin" aria-label={t("common.loading")} /> : t("permissions.pickRole")}
                  </div>
                ) : (
                  <>
                    <div className="pm-permissions-scroll pm-permissions-role-body">
                      <section className="pm-permissions-card pm-permissions-role-summary">
                        {readOnly ? (
                          <>
                            <h3>{selected.name}</h3>
                            <p><Lock size={13} aria-hidden />{t("permissions.builtinHint")}</p>
                          </>
                        ) : (
                          <label className="pm-permissions-role-field">
                            <span>{t("permissions.roleName")}</span>
                            <Input
                              value={selected.name}
                              disabled={saving}
                              onChange={(e) => patchSelected({ name: e.target.value })}
                            />
                          </label>
                        )}
                      </section>

                      <div className="pm-permissions-groups">
                        <section className="pm-permissions-card">
                          <h3>{t("permissions.modules")}</h3>
                          {MODULE_KEYS.map((key) => (
                            <CheckRow
                              key={key}
                              label={t(`permissions.module.${key}`)}
                              checked={selected.modules[key] ?? false}
                              disabled={roleLocked}
                              onChange={(value) => patchSelected({
                                modules: { ...selected.modules, [key]: value },
                              })}
                            />
                          ))}
                        </section>

                        <section className="pm-permissions-card">
                          <h3>{t("permissions.capabilities")}</h3>
                          {CAP_KEYS.map((key) => {
                            const ownerOnly = catalogue?.owner_only_capabilities.includes(key)
                            return (
                              <CheckRow
                                key={key}
                                label={t(`permissions.cap.${key}`)}
                                badge={ownerOnly ? t("permissions.cap.localOnly") : undefined}
                                checked={selected.capabilities[key] ?? false}
                                disabled={roleLocked || (ownerOnly && !isLocal)}
                                onChange={(value) => patchSelected({
                                  capabilities: { ...selected.capabilities, [key]: value },
                                })}
                              />
                            )
                          })}
                        </section>
                      </div>
                    </div>
                    <footer className="pm-permissions-footer">
                      {!readOnly && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="pm-permissions-delete"
                          disabled={saving}
                          onClick={() => void handleDeleteRole()}
                        >
                          <Trash2 aria-hidden />{t("common.delete")}
                        </Button>
                      )}
                      <Button size="lg" disabled={roleLocked} onClick={() => void handleSaveRole()}>
                        {saving && <Loader2 className="animate-spin" aria-hidden />}
                        {t("common.save")}
                      </Button>
                    </footer>
                  </>
                )}
              </div>
            </div>
          </TabsContent>

          <TabsContent value="rules" className="pm-permissions-tab">
            <div className="pm-permissions-pane">
              <div className="pm-permissions-scroll pm-permissions-rules">
                {domains && (
                  <table className="pm-permissions-matrix" aria-label={t("permissions.tabRules")}>
                    <thead>
                      <tr>
                        <th scope="col">{t("permissions.rule")}</th>
                        {pickableRoles.map((role) => <th scope="col" key={role.id}>{role.name}</th>)}
                      </tr>
                    </thead>
                    {Object.entries(DOMAIN_META).map(([domain, meta]) => (
                      <tbody key={domain}>
                        <tr className="pm-permissions-domain">
                          <th scope="rowgroup" colSpan={pickableRoles.length + 1}>
                            {t(`permissions.domain.${domain}`)}
                          </th>
                        </tr>
                        {(domainTiers[domain] ?? meta.tiers).map((tier) => {
                          const label = t(`permissions.tier.${domain}.${tier}`)
                          return (
                            <tr className="pm-permissions-rule" key={tier}>
                              <th scope="row">
                                {label}
                                {domain === "meeting" && meta.tiers.includes(tier) && (
                                  <small>{t(`permissions.meetingRuleDetail.${tier}`)}</small>
                                )}
                              </th>
                              {pickableRoles.map((role) => (
                                <td key={role.id}>
                                  <label className="pm-permissions-rule-check">
                                    <PermissionCheck
                                      label={`${label} · ${role.name}`}
                                      checked={(domains[domain]?.[tier] ?? []).includes(role.id)}
                                      disabled={domainSaving}
                                      onChange={() => toggleTierRole(domain, tier, role.id)}
                                    />
                                  </label>
                                </td>
                              ))}
                            </tr>
                          )
                        })}
                      </tbody>
                    ))}
                  </table>
                )}
              </div>
              <footer className="pm-permissions-footer">
                <Button size="lg" disabled={!domains || domainSaving} onClick={() => void handleSaveDomains()}>
                  {domainSaving && <Loader2 className="animate-spin" aria-hidden />}
                  {t("common.save")}
                </Button>
              </footer>
            </div>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}

interface PermissionCheckProps {
  label?: string
  checked: boolean
  disabled?: boolean
  onChange: (value: boolean) => void
}

function PermissionCheck({ label, checked, disabled, onChange }: PermissionCheckProps) {
  return (
    <span className="pm-permissions-check">
      <input
        type="checkbox"
        aria-label={label}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <Check size={12} strokeWidth={2.25} aria-hidden />
    </span>
  )
}

function CheckRow({ label, badge, ...check }: PermissionCheckProps & { label: string; badge?: string }) {
  return (
    <label className="pm-permissions-choice">
      <PermissionCheck {...check} />
      <span>{label}</span>
      {badge && <small>{badge}</small>}
    </label>
  )
}
