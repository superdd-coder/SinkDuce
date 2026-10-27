import { HelpHint } from "@/components/shared/help-hint"
import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { getCollectionAcl, listRoles, putCollectionAcl } from "@/api/client"
import { formatApiError } from "@/api/http"
import { useT } from "@/i18n/use-t"
import { cn } from "@/lib/utils"

export function CollectionAccessCard({ collection, canManageAcl }: { collection: string; canManageAcl: boolean }) {
  const t = useT()
  // ── RBAC：该库访问权限 ──
  const [acl, setAcl] = useState<Record<string, string[]>>({})
  const [aclTiers, setAclTiers] = useState<string[]>([])
  const [aclRoles, setAclRoles] = useState<{ id: string; name: string }[]>([])
  const [aclSaving, setAclSaving] = useState(false)
  useEffect(() => {
    if (!canManageAcl) return
    let alive = true
    Promise.all([getCollectionAcl(collection), listRoles()])
      .then(([aclRes, rolesRes]) => {
        if (!alive) return
        setAcl(aclRes.acl)
        setAclTiers(aclRes.tiers)
        // owner / admin 由后端保证永远有每一档，所以不列出来当开关
        // （一个关不掉的开关比没有开关更糟）。
        const implicit = aclRes.implicit_roles ?? []
        setAclRoles(
          rolesRes.roles
            .filter((r) => !implicit.includes(r.id))
            .map((r) => ({ id: r.id, name: r.name })),
        )
      })
      .catch(() => {
        /* 拿不到就不显示这张卡的内容（后端仍是墙） */
      })
    return () => {
      alive = false
    }
  }, [collection, canManageAcl])

  const handleSaveAcl = async () => {
    setAclSaving(true)
    try {
      const saved = await putCollectionAcl(collection, acl)
      setAcl(saved.acl)
      toast.success(t("library.accessPermsSaved"))
    } catch (err) {
      toast.error(formatApiError(err, t))
    } finally {
      setAclSaving(false)
    }
  }

  return <>
        {/* RBAC：该库的访问权限（四档 × 角色）。
            只有能管权限规则的人（acl.manage）才看得到这张卡——它是化妆，
            真正的墙是 /api/identity/collection-acl 端点的 acl.manage 校验。 */}
        {canManageAcl && (
          <section className="pm-config-card">
            <header className="pm-config-card-head">
              <span className="pm-config-card-kicker">{t("library.accessPerms")}<HelpHint label={t("library.accessPerms")}>{t("library.accessPermsDesc")}</HelpHint></span>
            </header>
            {aclTiers.map((tier) => (
              <div key={tier} className="mb-2.5">
                <span className="pm-meta block mb-1">
                  {t(`library.aclTier.${tier}`)}
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {aclRoles.map((r) => {
                    const on = (acl[tier] ?? []).includes(r.id)
                    return (
                      <button
                        key={r.id}
                        type="button"
                        className={cn(
                          "rounded-full border px-2.5 py-0.5 text-xs transition-colors",
                          on
                            ? "border-transparent bg-[var(--pm-green)] text-white"
                            : "opacity-60 hover:opacity-100",
                        )}
                        onClick={() =>
                          setAcl((prev) => {
                            const cur = prev[tier] ?? []
                            const next = cur.includes(r.id)
                              ? cur.filter((x) => x !== r.id)
                              : [...cur, r.id]
                            return { ...prev, [tier]: next }
                          })
                        }
                      >
                        {r.name}
                      </button>
                    )
                  })}
                </div>
              </div>
            ))}
            <div className="pm-config-action-row">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={aclSaving}
                onClick={() => void handleSaveAcl()}
              >
                {aclSaving ? t("common.saving") : t("common.save")}
              </Button>
            </div>
          </section>
        )}
  </>
}
