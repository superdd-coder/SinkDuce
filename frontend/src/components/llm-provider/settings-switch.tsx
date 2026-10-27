import { cn } from "@/lib/utils"

/** 设置页通用的「行内开关」胶囊（从 llm-provider-view 抽出供远程设置页共用）。 */
export function SettingsSwitch({
  checked,
  onCheckedChange,
  label,
  id,
  disabled,
}: {
  checked: boolean
  onCheckedChange: (next: boolean) => void
  label: string
  id: string
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      id={id}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={cn("pm-config-switch", checked && "is-on")}
      onClick={() => !disabled && onCheckedChange(!checked)}
    >
      <span className="pm-config-switch-thumb" aria-hidden />
    </button>
  )
}
