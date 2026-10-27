/**
 * 「权限管理」大弹窗（RBAC 阶段 6）。
 *
 * 两个 tab（用户 2026-09-16 定案）：
 * 1. **角色** —— 左 rail 角色列表 + 右面板权限矩阵；
 * 2. **权限规则** —— meeting / hot_words / people 三份领域 ACL。
 *
 * 刻意不含：三域的模块开关（不存在，它们的 read 档就是"能否访问"）、每库 ACL
 * （在各库自己的设置里）。
 *
 * 形态复用现成配方（pm-settings-hw-dialog + 左 rail 右面板），与 PeopleManager /
 * HotWordsManager 同一骨架，零新 CSS 骨架。
 */

import { useCallback, useEffect, useState } from "react"
import { Loader2, Lock, Plus, Trash2 } from "lucide-react"
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
import { ScrollArea } from "@/components/ui/scroll-area"
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
import { cn } from "@/lib/utils"
import { useT } from "@/i18n/use-t"

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
  const readOnly = !selected || !selected.editable

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
      <DialogContent
        className={cn(
          "pm-dialog pm-dialog--silk pm-settings-hw-dialog",
          "sm:max-w-6xl h-[80vh]",
          "!animate-none data-open:!animate-none data-closed:!animate-none",
        )}
      >
        <DialogHeader className="shrink-0">
          <DialogKicker>{t("nav.settings")}</DialogKicker>
          <DialogTitle>{t("permissions.title")}<HelpHint label={t("permissions.tabRules")}>{t("permissions.rulesHint")}</HelpHint></DialogTitle>
        </DialogHeader>

        <Tabs defaultValue="roles" className="flex flex-col flex-1 min-h-0">
          <TabsList className="shrink-0 px-1">
            <TabsTrigger value="roles">{t("permissions.tabRoles")}</TabsTrigger>
            <TabsTrigger value="rules">{t("permissions.tabRules")}</TabsTrigger>
          </TabsList>

          {/* ── Tab 1：角色 ── */}
          <TabsContent value="roles" className="flex-1 min-h-0">
            <div className="pm-settings-hw h-full">
              {/* 左 rail：角色列表 */}
              <div className="pm-settings-hw-rail">
                <div className="pm-settings-hw-rail-head">
                  <span className="pm-label text-[var(--pm-ink)]">
                    {t("permissions.roles")}
                  </span>
                  <span className="pm-settings-hw-count">{roles.length}</span>
                </div>
                <ScrollArea className="flex-1 min-h-0">
                  <div className="pm-settings-hw-lib-list">
                    {roles.map((role) => (
                      <button
                        key={role.id}
                        type="button"
                        onClick={() => setSelectedId(role.id)}
                        className={cn(
                          "pm-settings-hw-lib",
                          role.id === selectedId && "is-on",
                        )}
                      >
                        <span className="pm-settings-hw-lib-name">
                          {role.builtin && (
                            <Lock className="inline size-3 opacity-50" strokeWidth={1.75} />
                          )}{" "}
                          {role.name}
                        </span>
                        {role.key_count > 0 && (
                          <span className="pm-settings-hw-count">{role.key_count}</span>
                        )}
                      </button>
                    ))}
                  </div>
                </ScrollArea>
                <div className="p-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-full justify-start gap-1.5"
                    onClick={() => void handleCreateRole()}
                  >
                    <Plus className="size-3.5" strokeWidth={1.75} />
                    {t("permissions.newRole")}
                  </Button>
                </div>
              </div>

              {/* 右面板：权限矩阵 */}
              <div className="pm-settings-hw-main">
                {loading ? (
                  <div className="flex h-full items-center justify-center">
                    <Loader2 className="size-5 animate-spin opacity-50" />
                  </div>
                ) : !selected ? (
                  <div className="flex h-full items-center justify-center text-sm opacity-60">
                    {t("permissions.pickRole")}
                  </div>
                ) : (
                  <ScrollArea className="h-full">
                    <div className="space-y-6 p-5">
                      <div className="space-y-1.5">
                        <label className="pm-label">{t("permissions.roleName")}</label>
                        <Input
                          value={selected.name}
                          disabled={readOnly}
                          onChange={(e) => patchSelected({ name: e.target.value })}
                        />
                        {!selected.editable && (
                          <p className="text-xs opacity-60">
                            {t("permissions.builtinHint")}
                          </p>
                        )}
                      </div>

                      <section className="space-y-2">
                        <h4 className="pm-label">{t("permissions.modules")}</h4>
                        {MODULE_KEYS.map((key) => (
                          <CheckRow
                            key={key}
                            label={t(`permissions.module.${key}`)}
                            checked={selected.modules[key] ?? false}
                            disabled={readOnly}
                            onChange={(v) =>
                              patchSelected({
                                modules: { ...selected.modules, [key]: v },
                              })
                            }
                          />
                        ))}
                      </section>

                      <section className="space-y-2">
                        <h4 className="pm-label">{t("permissions.capabilities")}</h4>
                        {CAP_KEYS.map((key) => {
                          const ownerOnly = catalogue?.owner_only_capabilities.includes(key)
                          // 「管钥匙」只有本机能授予（UI 置灰；后端同样强制）
                          const disabled = readOnly || (ownerOnly && !isLocal)
                          return (
                            <CheckRow
                              key={key}
                              label={
                                ownerOnly
                                  ? t("permissions.cap.localOnly")
                                  : t(`permissions.cap.${key}`)
                              }
                              checked={selected.capabilities[key] ?? false}
                              disabled={disabled}
                              highlight={ownerOnly}
                              onChange={(v) =>
                                patchSelected({
                                  capabilities: { ...selected.capabilities, [key]: v },
                                })
                              }
                            />
                          )
                        })}
                      </section>

                      <div className="flex items-center gap-2 pt-2">
                        <Button
                          size="sm"
                          disabled={readOnly || saving}
                          onClick={() => void handleSaveRole()}
                        >
                          {saving && <Loader2 className="size-3.5 animate-spin" />}
                          {t("common.save")}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={readOnly}
                          className="gap-1.5"
                          onClick={() => void handleDeleteRole()}
                        >
                          <Trash2 className="size-3.5" strokeWidth={1.75} />
                          {t("common.delete")}
                        </Button>
                      </div>
                    </div>
                  </ScrollArea>
                )}
              </div>
            </div>
          </TabsContent>

          {/* ── Tab 2：权限规则 ── */}
          <TabsContent value="rules" className="flex-1 min-h-0">
            <ScrollArea className="h-full">
              <div className="space-y-7 p-5">
                {domains &&
                  Object.entries(DOMAIN_META).map(([domain, meta]) => (
                    <section key={domain} className="space-y-2">
                      <h4 className="pm-label">{t(`permissions.domain.${domain}`)}</h4>
                      {(domainTiers[domain] ?? meta.tiers).map((tier) => (
                        <div key={tier} className="space-y-1">
                          <span className="text-xs opacity-70">
                            {t(`permissions.tier.${domain}.${tier}`)}
                          </span>
                          <div className="flex flex-wrap gap-1.5">
                            {pickableRoles.map((role) => {
                              const on = (domains[domain]?.[tier] ?? []).includes(role.id)
                              return (
                                <button
                                  key={role.id}
                                  type="button"
                                  onClick={() => toggleTierRole(domain, tier, role.id)}
                                  className={cn(
                                    "rounded-full border px-2.5 py-0.5 text-xs transition-colors",
                                    on
                                      ? "border-transparent bg-[var(--ze-green)] text-white"
                                      : "opacity-60 hover:opacity-100",
                                  )}
                                >
                                  {role.name}
                                </button>
                              )
                            })}
                          </div>
                        </div>
                      ))}
                    </section>
                  ))}
                <div className="pt-1">
                  <Button
                    size="sm"
                    disabled={!domains || domainSaving}
                    onClick={() => void handleSaveDomains()}
                  >
                    {domainSaving && <Loader2 className="size-3.5 animate-spin" />}
                    {t("common.save")}
                  </Button>
                </div>
              </div>
            </ScrollArea>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}

function CheckRow({
  label,
  checked,
  disabled,
  highlight,
  onChange,
}: {
  label: string
  checked: boolean
  disabled?: boolean
  highlight?: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-center gap-2 text-sm",
        disabled && "cursor-not-allowed opacity-50",
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="size-3.5 accent-[var(--ze-green)]"
      />
      <span>{label}</span>
      {highlight && (
        <span className="text-[10px] uppercase tracking-wide opacity-50">
          {/* 「管钥匙」的注记：只有本机能授予 */}
          local-only
        </span>
      )}
    </label>
  )
}
