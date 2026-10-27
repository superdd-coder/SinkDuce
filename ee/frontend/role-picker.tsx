import { useEffect, useState } from "react"
import { FieldLabel } from "@/components/ui/field-label"
import { useT } from "@/i18n/use-t"
import { cn } from "@/lib/utils"
import { listRoles } from "@/api/client"

export function RolePicker({ value, onChange }: { value: string; onChange: (role: string) => void }) {
  const t = useT()
  const [roles, setRoles] = useState<{ id: string; name: string }[]>([])

  useEffect(() => {
    let alive = true
    listRoles()
      .then(({ roles }) => {
        if (alive) setRoles(roles.filter((role) => role.assignable).map(({ id, name }) => ({ id, name })))
      })
      .catch(() => {
        if (alive) setRoles([{ id: "member", name: "Member" }])
      })
    return () => { alive = false }
  }, [])

  return <div className="space-y-1.5">
    <FieldLabel>{t("settings.pairing.roleLabel")}</FieldLabel>
    <div className="flex flex-wrap items-center gap-1.5">
      {roles.map((role) => <button
        key={role.id} type="button"
        className={cn("px-2.5 py-1 rounded-md text-xs leading-5 transition-colors",
          value === role.id ? "bg-[var(--pm-ink)] text-[var(--pm-on)]" : "bg-black/5 text-[var(--pm-text)] hover:bg-black/10")}
        onClick={() => onChange(role.id)}>{role.name}</button>)}
    </div>
    <p className="text-[11px] leading-4 opacity-60">{t("settings.pairing.roleHint")}</p>
  </div>
}
