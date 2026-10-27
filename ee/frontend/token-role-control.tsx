import { useEffect, useState } from "react"
import { toast } from "sonner"
import { DropdownSelect, type DropdownSelectOption } from "@/components/ui/dropdown-select"
import { formatApiError } from "@/api/http"
import { useT } from "@/i18n/use-t"
import { listRoles, setPairingTokenRole } from "@/api/client"

export function TokenRoleControl({ id, role, boxId, onChanged }: {
  id: string
  role: string
  boxId?: string
  onChanged: (role: string) => void
}) {
  const t = useT()
  const [options, setOptions] = useState<DropdownSelectOption[]>([])
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    let alive = true
    listRoles().then(({ roles }) => {
      if (alive) setOptions(roles.filter((r) => r.assignable).map((r) => ({ value: r.id, label: r.name })))
    }).catch(() => {
      if (alive) setOptions([{ value: "member", label: "Member" }])
    })
    return () => { alive = false }
  }, [])
  return <DropdownSelect size="tag" chevron value={role} disabled={saving || options.length === 0}
    options={options} onChange={(next) => {
      setSaving(true)
      void setPairingTokenRole(id, next, boxId).then(() => {
        onChanged(next)
        toast.success(t("settings.pairing.roleChanged"))
      }).catch((error) => toast.error(formatApiError(error, t))).finally(() => setSaving(false))
    }} />
}
